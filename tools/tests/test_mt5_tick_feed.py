"""VM 側 feed の検定（ISSUE-447 段階 1 / 検定 A-1〜A-8・E-7・E-8・N-6）。

``tools/mt5_tick_feed.py`` は**リポジトリごと配布せず単体ファイルとして Windows VM へ持ち込む**
（MT5 端末は VM 側にしかない）。接続先は実弾のライブ口座であり、外部から到達しうる HTTP を
開く。よって次の 2 つを宣言ではなく**機械検査**で固定する。

1. 発注 API に触れない・許可した読み取り API しか触れない（A-1〜A-3）
2. 定義を持たない — tick 木レイアウト・marketdata の列名・DST 変換を VM 側に置かない（A-4）

本検定は MetaTrader5 にも実ネットワークにも依存しない（fake 注入・ソケット無し）。
"""
from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from marketdata.mt5_ticks import wire

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _REPO_ROOT / "tools" / "mt5_tick_feed.py"

from tools import mt5_tick_feed as feed  # noqa: E402  （A-1 が成り立つので import できる）

_SECRET = b"unit-test-secret"
_KEY_ID = "k1"

_MT5_TICK_DTYPE = np.dtype([
    ("time", "<i8"), ("bid", "<f8"), ("ask", "<f8"), ("last", "<f8"),
    ("volume", "<u8"), ("time_msc", "<i8"), ("flags", "<u4"), ("volume_real", "<f8"),
])


def _ticks(*specs) -> np.ndarray:
    arr = np.zeros(len(specs), dtype=_MT5_TICK_DTYPE)
    for i, (msc, bid, ask) in enumerate(specs):
        arr[i]["time"] = msc // 1000
        arr[i]["time_msc"] = msc
        arr[i]["bid"] = bid
        arr[i]["ask"] = ask
    return arr


class FakeMt5:
    """MetaTrader5 モジュールの代役（読み取りのみ・端末に触れない）。"""

    COPY_TICKS_INFO = 1

    def __init__(self, ticks=None, *, select_ok=True, error=(-10005, "boom"), server="OANDA-Japan MT5 Live"):
        self._ticks = ticks
        self._select_ok = select_ok
        self._error = error
        self._server = server
        self.calls: "list[str]" = []

    def symbol_select(self, symbol, enable=True):
        self.calls.append("symbol_select")
        return self._select_ok

    def copy_ticks_from(self, symbol, frm, count, flags):
        self.calls.append("copy_ticks_from")
        return None if self._ticks is None else self._ticks[:count]

    def copy_ticks_range(self, symbol, frm, to, flags):
        self.calls.append("copy_ticks_range")
        return self._ticks

    def last_error(self):
        return self._error

    def account_info(self):
        return SimpleNamespace(server=self._server)


def _request(path="/ticks", *, query=None, mt5=None, now=1_700_000_000, nonce="n1",
             secret=_SECRET, sign_with=None, ts=None, nonces=None, key_id=_KEY_ID):
    """署名付き要求を組み立てて :func:`feed.handle_request` に渡す。"""
    query = {"symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "10"} if query is None else query
    ts = now if ts is None else ts
    sig = wire.sign(
        sign_with if sign_with is not None else secret,
        method="GET", path=path, query=query, ts=ts, nonce=nonce,
    )
    headers = {"Authorization": wire.authorization_header(
        key_id=key_id, ts=ts, nonce=nonce, sig=sig
    )}
    target = path + ("?" + wire.sorted_query(query) if query else "")
    return feed.handle_request(
        target, headers,
        mt5=mt5 if mt5 is not None else FakeMt5(_ticks((1000, 66020.1, 66035.1))),
        secret=secret, key_id=_KEY_ID,
        nonces=feed.NonceCache() if nonces is None else nonces,
        now=now,
    )


# =====================================================================
# N-6 署名往復 + 応答の往復
# =====================================================================

def test_a_signature_made_by_the_container_is_accepted_by_the_vm(monkeypatch):
    """N-6: ``wire.sign`` → VM 側 verify が通る（正準文字列が食い違っていない）。"""
    got = _request()
    assert got.status == 200


def test_the_response_parses_back_through_the_container_side_parser():
    """VM が組んだヘッダ + body を ``wire.parse_response`` がそのまま解ける。"""
    arr = _ticks((1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2))
    got = _request(mt5=FakeMt5(arr))
    parsed = wire.parse_response(got.status, got.headers, got.body)
    assert parsed.rows == [(1000, 66020.1, 66035.1), (1001, 66020.2, 66035.2)]
    assert parsed.latest_msc == 1001
    assert parsed.server == "OANDA-Japan MT5 Live"


def test_the_body_is_the_raw_bytes_of_the_array():
    """body は ``tobytes()`` 無加工（VM 側で組み直さない）。"""
    arr = _ticks((1000, 1.0, 2.0), (1001, 1.0, 2.0))
    got = _request(mt5=FakeMt5(arr))
    assert got.body == arr.tobytes()
    assert json.loads(got.headers["X-MT5-Dtype"]) == [list(f) for f in arr.dtype.descr]


def test_an_empty_result_is_a_valid_200():
    got = _request(mt5=FakeMt5(_ticks()))
    assert got.status == 200
    assert wire.parse_response(got.status, got.headers, got.body).rows == []


def test_a_truncated_result_is_flagged():
    arr = _ticks(*[(1000 + i, 1.0, 2.0) for i in range(12)])
    got = _request(mt5=FakeMt5(arr), query={
        "symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "5"
    })
    assert got.headers["X-MT5-Truncated"] == "1"
    assert wire.parse_response(got.status, got.headers, got.body).count == 5


def test_health_answers_without_touching_the_terminal():
    """``/health`` は端末に触れない（生存確認で発注口座を触らない）。"""
    mt5 = FakeMt5(_ticks())
    got = _request("/health", query={}, mt5=mt5)
    assert got.status == 200
    assert mt5.calls == []


# =====================================================================
# E-7 認証（すべて 401・書込 0）
# =====================================================================

def test_a_wrong_signature_is_rejected():
    """E-7: 署名不正 → 401。"""
    got = _request(sign_with=b"wrong-secret")
    assert got.status == 401


def test_a_stale_timestamp_is_rejected():
    """E-7: ts 差 120 秒超 → 401（再生攻撃を拒む）。"""
    assert _request(now=1_700_000_000, ts=1_700_000_000 - 121).status == 401
    assert _request(now=1_700_000_000, ts=1_700_000_000 + 121).status == 401


def test_a_timestamp_at_the_edge_of_the_window_is_accepted():
    """境界 120 秒ちょうどは通す（境界で勝手に厳しくしない）。"""
    assert _request(now=1_700_000_000, ts=1_700_000_000 - 120).status == 200


def test_a_replayed_nonce_is_rejected():
    """E-7: nonce 再使用 → 401。"""
    nonces = feed.NonceCache()
    assert _request(nonce="same", nonces=nonces).status == 200
    assert _request(nonce="same", nonces=nonces).status == 401


def test_a_missing_authorization_header_is_rejected():
    got = feed.handle_request(
        "/ticks?symbol=JP225", {}, mt5=FakeMt5(_ticks()),
        secret=_SECRET, key_id=_KEY_ID, nonces=feed.NonceCache(), now=1,
    )
    assert got.status == 401


def test_an_unknown_key_id_is_rejected():
    got = _request(key_id="someone-else")
    assert got.status == 401


def test_authentication_is_required_on_every_endpoint():
    """``/health`` も認証必須（無認証の入口を 1 つも作らない）。"""
    got = feed.handle_request(
        "/health", {}, mt5=FakeMt5(_ticks()), secret=_SECRET, key_id=_KEY_ID,
        nonces=feed.NonceCache(), now=1,
    )
    assert got.status == 401


def test_failed_authentication_never_reaches_the_terminal():
    """認証前に端末を触らない。"""
    mt5 = FakeMt5(_ticks())
    _request(sign_with=b"wrong", mt5=mt5)
    assert mt5.calls == []


# =====================================================================
# E-8 端末異常（502・last_error を落とさない）
# =====================================================================

def test_a_none_result_from_the_terminal_is_a_502_carrying_last_error():
    """E-8: ``copy_ticks_*`` が None → 502・``last_error`` が detail に載る。"""
    got = _request(mt5=FakeMt5(None, error=(-10005, "no ipc connection")))
    assert got.status == 502
    payload = json.loads(got.body)
    assert payload["error"] == "terminal"
    assert "no ipc connection" in json.dumps(payload)


def test_a_failed_symbol_select_is_a_502_carrying_last_error():
    got = _request(mt5=FakeMt5(_ticks(), select_ok=False, error=(-2, "unknown symbol")))
    assert got.status == 502
    assert "unknown symbol" in got.body.decode("utf-8")


# =====================================================================
# 引数不正（400・Fail-Stop・再試行しない）
# =====================================================================

@pytest.mark.parametrize("query", [
    {"from_msc": "1000", "to_msc": "", "max_rows": "10"},                      # symbol 欠落
    {"symbol": "JP225", "from_msc": "abc", "to_msc": "", "max_rows": "10"},    # 数でない
    {"symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "0"},    # 0 行要求
    {"symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "-1"},
    {"symbol": "", "from_msc": "1000", "to_msc": "", "max_rows": "10"},        # 空 symbol
])
def test_bad_arguments_are_400(query):
    assert _request(query=query).status == 400


def test_an_unreasonably_large_max_rows_is_rejected():
    """際限のない要求を通さない（端末を 1 発で詰まらせない）。"""
    got = _request(query={
        "symbol": "JP225", "from_msc": "1000", "to_msc": "", "max_rows": "99999999"
    })
    assert got.status == 400


def test_an_unknown_path_is_404():
    assert _request("/admin", query={}).status == 404


# =====================================================================
# A-1〜A-3 発注に触れない・許可 API のみ
# =====================================================================

def _tree() -> ast.AST:
    return ast.parse(_SOURCE.read_text(encoding="utf-8"))


def _top_level_imports_of(tree: ast.AST, package: str) -> "list[str]":
    """トップレベルで ``package`` を import している名前を集める（純関数・検定本体を分岐させない）。"""
    plain = [
        a.name for node in tree.body if isinstance(node, ast.Import)
        for a in node.names if a.name.startswith(package)
    ]
    froms = [
        node.module for node in tree.body
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(package)
    ]
    return plain + froms


def test_module_does_not_import_metatrader5_at_top_level():
    """A-1: コンテナ（MetaTrader5 不在）でも import が通ることを構造で担保する。"""
    offenders = _top_level_imports_of(_tree(), "MetaTrader5")
    assert offenders == [], f"MetaTrader5 をトップレベル import しています: {offenders}"


def test_module_never_references_order_apis():
    """A-2: 接続先は実弾のライブ口座。発注系 API を 1 つも参照しない。"""
    offenders = sorted({
        n.attr for n in ast.walk(_tree())
        if isinstance(n, ast.Attribute) and n.attr.startswith("order_")
    })
    assert offenders == [], f"発注系 API を参照しています: {offenders}"


def test_only_allowlisted_terminal_apis_are_touched():
    """A-3: ``mt5.*`` の属性参照が許可集合の部分集合である。"""
    used = {
        n.attr for n in ast.walk(_tree())
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name) and n.value.id == "mt5"
    }
    assert used <= feed.ALLOWED_TERMINAL_APIS, (
        f"許可外の端末 API を参照しています: {sorted(used - feed.ALLOWED_TERMINAL_APIS)}"
    )


def test_the_allowlist_matches_the_design():
    """許可集合を勝手に広げない（広げるときは設計 §4 と一緒に変える）。"""
    assert feed.ALLOWED_TERMINAL_APIS == frozenset({
        "initialize", "shutdown", "last_error", "version", "terminal_info",
        "symbol_select", "symbol_info", "symbol_info_tick", "account_info",
        "copy_ticks_range", "copy_ticks_from", "COPY_TICKS_INFO",
    })


def test_the_module_imports_without_metatrader5_installed():
    with pytest.raises(ModuleNotFoundError):
        __import__("MetaTrader5")
    assert feed.__name__ == "tools.mt5_tick_feed"


# =====================================================================
# A-4 定義を持たない（既存 2 検定と同じ正規表現の独立検定）
# =====================================================================

#: ``marketdata/tests/test_tick_tree_layout_authority.py`` と同一の式。
_TICK_ROOT = re.compile(r"""/\s*["']ticks["']""")
_YMD_TREE = re.compile(r"""%Y["']\s*/\s*f?["']\{?\w*:?%m""")
_TICK_FILENAME = re.compile(r"""["'][A-Za-z0-9_]+_ticks\.(parquet|empty)["']""")


def _code_lines_matching(pattern) -> "list[str]":
    """``pattern`` に当たるコード行（コメント・docstring 開始行は説明なので除く）を集める。

    走査そのものを純関数へ出し、検定本体は「収集結果 == 期待」の 1 主張だけにする
    （検定本体に分岐を置くと、どの経路を通ったのかが落ちたときに分からない）。
    """
    out: "list[str]" = []
    for i, line in enumerate(_SOURCE.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""'):
            continue
        if pattern.search(line):
            out.append(f"{i}: {stripped[:90]}")
    return out


@pytest.mark.parametrize(
    "pattern,what",
    [(_TICK_ROOT, "tick 木の基点"), (_YMD_TREE, "YYYY/MM/DD の階層"),
     (_TICK_FILENAME, "日別ファイル名")],
    ids=["tick_root", "ymd_tree", "filename"],
)
def test_the_vm_side_does_not_know_the_tick_tree_layout(pattern, what):
    """A-4: VM 側は保存レイアウトを 1 つも持たない。"""
    offenders = _code_lines_matching(pattern)
    assert offenders == [], f"{what} を VM 側が組んでいます:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize("column", ["bidPrice", "askPrice"])
def test_the_vm_side_does_not_know_the_marketdata_column_names(column):
    """A-4: marketdata の列名を VM 側に置かない（列の定義はコンテナ側の権威が持つ）。"""
    offenders = [
        f"{i}: {line.strip()[:90]}"
        for i, line in enumerate(_SOURCE.read_text(encoding="utf-8").splitlines(), 1)
        if column in line and not line.strip().startswith("#")
    ]
    assert not offenders, f"{column} を VM 側が持っています:\n  " + "\n  ".join(offenders)


@pytest.mark.parametrize(
    "token", ["zoneinfo", "ZoneInfo", "EEST", "astimezone", "utcoffset", "dst("]
)
def test_the_vm_side_does_not_convert_time_zones(token):
    """A-4: DST・UTC 変換を VM 側に置かない（時刻の権威は `marketdata/mt5_ticks/server_clock.py` 1 箇所）。"""
    text = _SOURCE.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.strip().startswith("#"))
    # A-4 は「その構文を持たない」という構造禁止であり、実行時に観測できる振る舞いが存在しない。
    #   既存の marketdata/tests/test_tick_tree_layout_authority.py と同型の走査検定である。
    # di-ok(C2): 構造禁止（A-4）は被検査ソースの走査でしか固定できない
    assert token not in code, f"VM 側が時刻変換を持っています: {token}"


def test_the_epoch_conversion_lives_in_exactly_one_function():
    """12h ずれ罠の閉じ込め: epoch → datetime の変換関数が 1 つだけ存在する。"""
    tree = _tree()
    _conversion_names = {"fromtimestamp", "utcfromtimestamp", "timedelta"}

    def _converts(node) -> bool:
        for c in ast.walk(node):
            if isinstance(c, ast.Attribute) and c.attr in _conversion_names:
                return True
            if isinstance(c, ast.Name) and c.id in _conversion_names:
                return True
        return False

    converters = [
        n.name for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _converts(n)
    ]
    assert len(converters) == 1, f"epoch 変換が複数箇所にあります: {converters}"


# =====================================================================
# A-5 ファイル配信機構を使わない
# =====================================================================

@pytest.mark.parametrize("token", ["SimpleHTTPRequestHandler", "translate_path", "send_file",
                                   "shutil", "os.listdir", "glob"])
def test_no_file_serving_machinery_is_present(token):
    """A-5: ファイル配信の機構を持たない（VM のファイルを外へ出す経路を作らない）。"""
    code = "\n".join(
        l for l in _SOURCE.read_text(encoding="utf-8").splitlines()
        if not l.strip().startswith("#")
    )
    # 「持っていない」ことは「呼べない」ことでは示せない（存在しない機構は実行できない）。
    # di-ok(C2): 構造禁止（A-5）は被検査ソースの走査でしか固定できない
    assert token not in code, f"ファイル配信の機構を参照しています: {token}"


def test_only_two_endpoints_are_served():
    """エンドポイントは ``/ticks`` と ``/health`` の 2 本のみ。"""
    assert feed.ENDPOINTS == ("/health", "/ticks")


def test_the_server_is_single_threaded():
    """stdlib ``http.server`` の単一スレッド（並行で端末を叩かない）。"""
    code = _SOURCE.read_text(encoding="utf-8")
    # di-ok(C2): 並行化の不在は構造禁止であり、単一スレッドであることを実行時に観測する手段が無い。
    assert "ThreadingHTTPServer" not in code
    # di-ok(C2): 同上（混入経路が 2 つあるため両方を固定する）。
    assert "ThreadingMixIn" not in code


# =====================================================================
# A-6 コンテナで --help が通る
# =====================================================================

def test_cli_help_succeeds_without_metatrader5_installed():
    """A-6: ``python3 tools/mt5_tick_feed.py --help`` がコンテナで成功する。"""
    proc = subprocess.run(
        [sys.executable, str(_SOURCE), "--help"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--bind" in proc.stdout


def test_cli_keeps_the_option_surface_minimal():
    """認知負荷を上げるフラグを増やさない（増やすときは本表を更新して裁定する）。"""
    opts = {a for act in feed.build_parser()._actions for a in act.option_strings}
    assert opts == {"-h", "--help", "--bind", "--port", "--key-id"}


# =====================================================================
# A-8 秘密を埋め込まない・bind を絞る
# =====================================================================

def test_no_long_hex_literal_is_embedded():
    """A-8: 32 文字以上の hex リテラルが 0（鍵を埋め込まない）。"""
    text = _SOURCE.read_text(encoding="utf-8")
    offenders = re.findall(r"""["'][0-9a-fA-F]{32,}["']""", text)
    assert offenders == [], f"秘密らしきリテラルが埋め込まれています: {offenders}"


def test_the_secret_comes_only_from_the_environment(monkeypatch):
    """秘密は環境変数のみ（引数にもファイルにも置かない）。"""
    assert feed.SECRET_ENV == "MT5_BRIDGE_SECRET"
    monkeypatch.delenv(feed.SECRET_ENV, raising=False)
    with pytest.raises(feed.FeedError):
        feed.load_secret()
    monkeypatch.setenv(feed.SECRET_ENV, "x" * 16)
    assert feed.load_secret() == b"x" * 16


def test_a_short_secret_is_refused(monkeypatch):
    monkeypatch.setenv(feed.SECRET_ENV, "short")
    with pytest.raises(feed.FeedError):
        feed.load_secret()


def test_the_default_bind_is_the_specific_interface():
    assert feed.DEFAULT_BIND == "172.16.162.129"
    assert feed.DEFAULT_PORT == 8771


@pytest.mark.parametrize("bad", ["0.0.0.0", "::", ""])
def test_binding_to_every_interface_is_refused(bad):
    """特定 IF bind のみ（``0.0.0.0`` 禁止）。"""
    with pytest.raises(feed.FeedError):
        feed.validate_bind(bad)


def test_the_specific_interface_is_accepted():
    assert feed.validate_bind("172.16.162.129") == "172.16.162.129"
    assert feed.validate_bind("127.0.0.1") == "127.0.0.1"
