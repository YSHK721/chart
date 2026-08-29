"""``tools/capture_mt5_symbol_spec.py`` の単体検定（MetaTrader5 不在の Linux で全緑）。

本検定は fake の mt5 モジュール相当オブジェクトを注入して振る舞いを固定する。
実端末（Windows VM）は要らない。固定する不変条件は 4 つ:

1. **丸ごと落とし**: ``symbol_info()._asdict()`` の全フィールドがスナップショットに入る。
   フィールドを増やした fake でも通ること（＝人がフィールドを選ぶ余地が無いことの実証）。
   ISSUE-445 の根本原因 RC-1 は「供給元が出していない値を人が台帳へ書き足せた」ことなので、
   選別が入り込まないことを検定側で固定する。
2. **許可リスト**: ``account`` は含める側の列挙だけを通す。``login`` / ``balance`` /
   ``equity`` 等の識別子・変動値が混入しない（負の対照）。
3. **決定性**: 同じ入力なら同じバイト列。再取得の差分がノイズにならない。
4. **Fail-Stop**: 前提が崩れたら非 0 終了し、空ファイルを書かない。

加えて、機械的検査で 2 つの禁止事項を施行する（宣言だけを残さない・ISSUE-262 と同型）:
- 発注系 API（``order_*``）を 1 つも呼ばない（接続先は実弾のライブ口座）。
- ``MetaTrader5`` をトップレベル import しない（コンテナで import・``--help`` が通ること）。
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tools import capture_mt5_symbol_spec as cap

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _REPO_ROOT / "tools" / "capture_mt5_symbol_spec.py"

_AT = datetime(2026, 8, 25, 12, 34, 56, tzinfo=timezone.utc)

#: ISSUE-445 が実測した `mt5.symbol_info('JP225')` のフィールド（2026-08-25・OANDA-Japan MT5 Live）。
#: 本検定は値の正しさではなく「欠落なく落ちること」だけを主張する。
_SYMBOL_FIELDS = {
    "name": "JP225",
    "digits": 1,
    "point": 0.1,
    "trade_contract_size": 1.0,
    "trade_tick_size": 0.1,
    "trade_tick_value": 0.1,
    "volume_min": 1.0,
    "volume_step": 1.0,
    "volume_max": 10000.0,
    "spread": 100,
    "trade_mode": 4,
    "description": "Japan 225",
}

_ACCOUNT_FIELDS = {
    # 含める（仕様であり、かつ機微でない）
    "leverage": 10,
    "currency": "JPY",
    "trade_mode": 2,
    "company": "OANDA Corporation",
    "server": "OANDA-Japan MT5 Live",
    # 除外する（識別子）
    "login": 900005560,
    "name": "Yamada Taro",
    # 除外する（変動値＝仕様ではない）
    "balance": 3826.1,
    "equity": 3826.1,
    "margin": 3832.57,
    "margin_free": 0.0,
    "margin_level": 99.95,
    "profit": -6173.9,
    "credit": 0.0,
    "assets": 0.0,
    "liabilities": 0.0,
}

_TERMINAL_FIELDS = {
    "company": "OANDA Corporation",
    "name": "OANDA MetaTrader 5",
    "build": 5833,
    "path": "C:\\Program Files\\OANDA MetaTrader 5",
}

#: 混入したら事故になるキー（負の対照）。`account` セクションに 1 つも現れてはならない。
_FORBIDDEN_ACCOUNT_KEYS = (
    "login", "name", "balance", "equity", "margin", "margin_free",
    "margin_level", "profit", "credit", "assets", "liabilities",
)


#: 「既定を使う」と「明示的に None を返させる」を区別する番兵。
_UNSET = object()


class _Record:
    """``mt5`` が返す namedtuple 相当（``_asdict()`` を持つ）。"""

    def __init__(self, fields: dict):
        self._fields = dict(fields)

    def _asdict(self) -> dict:
        return dict(self._fields)


class FakeMt5:
    """mt5 モジュール相当の fake。読み取り系だけを持つ（発注系は定義しない）。"""

    def __init__(
        self,
        *,
        symbol_fields: "dict | None" = None,
        account_fields: "dict | None" = None,
        terminal_fields: "dict | None" = _UNSET,
        initialize_ok: bool = True,
        symbol_info_none: bool = False,
        account_info_none: bool = False,
        select_ok: bool = True,
        deals: "tuple | None" = (),
        version: "str | None" = "5.0.45",
    ):
        self._symbol_fields = _SYMBOL_FIELDS if symbol_fields is None else symbol_fields
        self._account_fields = _ACCOUNT_FIELDS if account_fields is None else account_fields
        self._terminal_fields = (
            _TERMINAL_FIELDS if terminal_fields is _UNSET else terminal_fields
        )
        self._initialize_ok = initialize_ok
        self._symbol_info_none = symbol_info_none
        self._account_info_none = account_info_none
        self._select_ok = select_ok
        self._deals = deals
        self.calls: "list[str]" = []
        self.shutdown_count = 0
        if version is not None:
            self.__version__ = version

    # --- 読み取り系 -----------------------------------------------------
    def initialize(self, *a, **kw):
        self.calls.append("initialize")
        return self._initialize_ok

    def shutdown(self):
        self.calls.append("shutdown")
        self.shutdown_count += 1

    def last_error(self):
        return (-10005, "IPC timeout")

    def symbol_select(self, symbol, enable=True):
        self.calls.append(f"symbol_select:{symbol}:{enable}")
        return self._select_ok

    def symbol_info(self, symbol):
        self.calls.append(f"symbol_info:{symbol}")
        if self._symbol_info_none:
            return None
        return _Record(self._symbol_fields)

    def account_info(self):
        self.calls.append("account_info")
        if self._account_info_none:
            return None
        return _Record(self._account_fields)

    def terminal_info(self):
        self.calls.append("terminal_info")
        if self._terminal_fields is None:
            return None
        return _Record(self._terminal_fields)

    def history_deals_get(self, date_from, date_to):
        self.calls.append("history_deals_get")
        if self._deals is None:
            return None
        return tuple(_Record(d) for d in self._deals)


def _snapshot(**kw) -> dict:
    """本番（``main``）と同じ形で読む: セッションを開き、その中で ``read_snapshot`` を呼ぶ。"""
    fake = kw.pop("fake", None) or FakeMt5()
    with cap.mt5_session(fake) as term:
        return cap.read_snapshot(term, kw.pop("symbol", "JP225"), captured_at=_AT, **kw)


# =====================================================================
# 1. symbol セクションは丸ごと落とす（RC-1 の再発防止）
# =====================================================================

def test_symbol_section_carries_every_field_without_selection():
    """``symbol_info()._asdict()`` の全フィールドがそのまま入る。"""
    snap = _snapshot()
    assert snap["symbol"] == _SYMBOL_FIELDS


def test_symbol_section_carries_unknown_future_fields():
    """MT5 がフィールドを増やしても落ちる（＝人が選ぶ余地が無いことの実証）。"""
    extended = dict(_SYMBOL_FIELDS)
    extended["swap_rollover3days"] = 3
    extended["some_field_added_in_future_build"] = "x"
    snap = _snapshot(fake=FakeMt5(symbol_fields=extended))
    assert snap["symbol"] == extended
    assert set(extended) - set(snap["symbol"]) == set()


# =====================================================================
# 2. account セクションは許可リストのみ（機微値・変動値の負の対照）
# =====================================================================

def test_account_section_contains_only_allowlisted_keys():
    snap = _snapshot()
    assert set(snap["account"]) == {"company", "currency", "leverage", "server", "trade_mode"}


def test_account_section_excludes_identifier_and_volatile_values():
    """``login`` / ``balance`` / ``equity`` 等が 1 つも混入しない。"""
    snap = _snapshot()
    leaked = [k for k in _FORBIDDEN_ACCOUNT_KEYS if k in snap["account"]]
    assert leaked == [], f"機微値・変動値が account に混入しました: {leaked}"


def test_account_section_excludes_future_sensitive_fields():
    """将来 MT5 がフィールドを増やしても、許可リスト外は落ちる側に倒れる。"""
    fields = dict(_ACCOUNT_FIELDS)
    fields["some_new_sensitive_field"] = "secret"
    snap = _snapshot(fake=FakeMt5(account_fields=fields))
    assert "some_new_sensitive_field" not in snap["account"]


def test_forbidden_values_do_not_appear_anywhere_in_serialized_output():
    """直列化した全文にも口座番号・残高が現れない（セクション外への漏れの負の対照）。"""
    text = cap.serialize(_snapshot())
    assert "900005560" not in text
    assert "3826.1" not in text


def test_account_section_omits_missing_allowlisted_keys():
    """許可リストのキーが供給元に無いときは、値を捏造せず省略する。"""
    snap = _snapshot(fake=FakeMt5(account_fields={"leverage": 10, "currency": "JPY"}))
    assert snap["account"] == {"leverage": 10, "currency": "JPY"}


# =====================================================================
# 3. 取得メタ・自動生成マーカー
# =====================================================================

def test_generated_marker_is_present_at_top_level():
    snap = _snapshot()
    assert "_generated" in snap
    marker = json.dumps(snap["_generated"], ensure_ascii=False)
    assert "手で編集しない" in marker
    assert "tools/capture_mt5_symbol_spec.py" in marker


def test_meta_records_capture_time_symbol_and_terminal():
    snap = _snapshot()
    meta = snap["meta"]
    assert meta["captured_at_utc"] == "2026-08-25T12:34:56Z"
    assert meta["symbol"] == "JP225"
    assert meta["terminal"] == {
        "company": "OANDA Corporation",
        "name": "OANDA MetaTrader 5",
        "build": 5833,
    }
    assert meta["mt5_package_version"] == "5.0.45"


def test_capture_time_key_is_distinguishable_from_market_time():
    """``captured_at`` が相場時刻でないことがキー名／注記から判別できる。"""
    snap = _snapshot()
    assert "captured_at_utc" in snap["meta"]
    note = json.dumps(snap["_generated"], ensure_ascii=False)
    assert "相場時刻ではない" in note


def test_mt5_package_version_is_null_when_absent():
    snap = _snapshot(fake=FakeMt5(version=None))
    assert snap["meta"]["mt5_package_version"] is None


def test_terminal_is_null_when_terminal_info_unavailable():
    """``terminal_info()`` が None のとき、値を捏造せず null を記録する。"""
    snap = _snapshot(fake=FakeMt5(terminal_fields=None))
    assert snap["meta"]["terminal"] is None


def test_top_level_keys_are_fixed():
    assert sorted(_snapshot()) == ["_generated", "account", "meta", "symbol"]


# =====================================================================
# 4. 決定的な直列化
# =====================================================================

def test_serialization_is_byte_identical_for_identical_input():
    a = cap.serialize(_snapshot())
    b = cap.serialize(_snapshot())
    assert a.encode("utf-8") == b.encode("utf-8")


def test_serialization_is_sorted_indented_and_newline_terminated():
    text = cap.serialize(_snapshot())
    assert text.endswith("\n")
    assert not text.endswith("\n\n")
    assert "\n  " in text  # indent=2
    top = [ln for ln in text.splitlines() if ln.startswith('  "')]
    assert top == sorted(top), "sort_keys=True で安定順序になっていません"


def test_serialization_does_not_escape_non_ascii():
    fields = dict(_SYMBOL_FIELDS)
    fields["description"] = "日経225"
    text = cap.serialize(_snapshot(fake=FakeMt5(symbol_fields=fields)))
    assert "日経225" in text
    assert "\\u" not in text


def test_written_file_uses_lf_newlines(tmp_path):
    """Windows で実行しても CRLF にしない（再取得の差分をノイズにしない）。"""
    out = tmp_path / "JP225.json"
    cap.write_text_lf(out, cap.serialize(_snapshot()))
    assert b"\r\n" not in out.read_bytes()


# =====================================================================
# 5. Fail-Stop（空ファイルを書かない）
# =====================================================================

def test_initialize_failure_is_fail_stop():
    fake = FakeMt5(initialize_ok=False)
    with pytest.raises(cap.CaptureError) as ei:
        with cap.mt5_session(fake):
            pass
    assert "initialize" in str(ei.value)
    assert "-10005" in str(ei.value), "last_error() が添えられていません"


def test_session_shuts_down_even_when_body_raises():
    fake = FakeMt5()
    with pytest.raises(ValueError):
        with cap.mt5_session(fake):
            raise ValueError("boom")
    assert fake.shutdown_count == 1


def test_symbol_info_none_is_fail_stop():
    with pytest.raises(cap.CaptureError) as ei:
        _snapshot(fake=FakeMt5(symbol_info_none=True))
    assert "symbol_info" in str(ei.value)
    assert "-10005" in str(ei.value)


def test_account_info_none_is_fail_stop():
    with pytest.raises(cap.CaptureError) as ei:
        _snapshot(fake=FakeMt5(account_info_none=True))
    assert "account_info" in str(ei.value)
    assert "-10005" in str(ei.value)


def test_symbol_select_failure_is_fail_stop():
    with pytest.raises(cap.CaptureError) as ei:
        _snapshot(fake=FakeMt5(select_ok=False))
    assert "symbol_select" in str(ei.value)


def test_shutdown_runs_even_on_failure():
    fake = FakeMt5(symbol_info_none=True)
    with pytest.raises(cap.CaptureError):
        _snapshot(fake=fake)
    assert fake.shutdown_count == 1


def test_main_opens_exactly_one_session_even_with_deals(tmp_path):
    """再 initialize の可否は未検証。仮定を持ち込まないよう、接続は 1 回に限る。"""
    fake = FakeMt5(deals=({"ticket": 1},))
    rc = cap.main(
        ["--symbol", "JP225", "--out", str(tmp_path / "s.json"),
         "--with-deals", "--deals-out", str(tmp_path / "out" / "d.json")],
        mt5=fake,
        now=_AT,
    )
    assert rc == 0
    assert fake.calls.count("initialize") == 1, fake.calls
    assert fake.shutdown_count == 1, fake.calls


def test_main_writes_nothing_on_failure(tmp_path):
    out = tmp_path / "spec.json"
    rc = cap.main(
        ["--symbol", "JP225", "--out", str(out)],
        mt5=FakeMt5(symbol_info_none=True),
        now=_AT,
    )
    assert rc != 0
    assert not out.exists(), "Fail-Stop なのにファイルを書きました"


# =====================================================================
# 6. 出力先の解決（<server> 置換規則）
# =====================================================================

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("OANDA-Japan MT5 Live", "OANDA-Japan-MT5-Live"),
        ("Broker.com-Demo", "Broker.com-Demo"),
        ("a/b\\c", "a-b-c"),
        ("A:B*C?D", "A-B-C-D"),
        ("日本 Live", "---Live"),
        ("Trim  Me", "Trim--Me"),
    ],
)
def test_server_sanitize_rule(raw, expected):
    assert cap.sanitize_path_component(raw) == expected


@pytest.mark.parametrize("bad", ["", "   ", ".", ".."])
def test_server_sanitize_rejects_unsafe_components(bad):
    with pytest.raises(cap.CaptureError):
        cap.sanitize_path_component(bad)


def test_default_out_path_uses_server_and_symbol():
    path = cap.default_out_path("OANDA-Japan MT5 Live", "JP225")
    assert path == _REPO_ROOT / "marketdata" / "symbol_specs" / "OANDA-Japan-MT5-Live" / "JP225.json"


def test_default_out_path_is_not_created_by_resolution():
    """パス解決は副作用を持たない（段階 2 で実在する台帳の有無に依存しない検定にする）。"""
    path = cap.default_out_path("Probe Server For Test", "PROBE")
    assert not path.exists()
    assert not path.parent.exists(), (
        "パス解決だけでリポジトリ配下にディレクトリを作ってはいけません"
    )


# =====================================================================
# 7. 約定履歴はリポジトリ外にしか書けない
# =====================================================================

@pytest.mark.parametrize(
    "rel",
    ["marketdata/deals.json", "deals.json", "tools/tests/deals.json", "a/../deals.json"],
)
def test_deals_out_inside_repository_is_rejected(rel):
    with pytest.raises(cap.CaptureError) as ei:
        cap.resolve_deals_out(str(_REPO_ROOT / rel))
    assert "リポジトリ" in str(ei.value)


def test_deals_out_equal_to_repo_root_is_rejected():
    with pytest.raises(cap.CaptureError):
        cap.resolve_deals_out(str(_REPO_ROOT))


def test_deals_out_outside_repository_is_accepted(tmp_path):
    target = tmp_path / "deals.json"
    assert cap.resolve_deals_out(str(target)) == target.resolve()


# --- リポジトリ根は「.git の実在」で判定する（実測 2026-08-25 の事故の回帰固定）-------
#
# 当初は `Path(__file__).parents[1]` をリポジトリ根と決め打ちしていた。本スクリプトは
# 単体ファイルとして Windows VM へ持ち込む運用（MT5 端末は VM 側にしかない）であり、
# そこでは親ディレクトリがリポジトリではない。デスクトップ等の正当な出力先が
# 「リポジトリ配下」と誤判定されて中断した。


def test_find_repo_root_detects_this_repository():
    assert cap.find_repo_root(_SOURCE) == _REPO_ROOT


def test_find_repo_root_is_none_outside_any_repository(tmp_path):
    assert cap.find_repo_root(tmp_path / "nested" / "deals.json") is None


def test_find_repo_root_detects_a_foreign_repository(tmp_path):
    """判定が本リポジトリ固有でないこと（`.git` を持つ任意の木を検出する）。"""
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    assert cap.find_repo_root(tmp_path / "repo" / "sub" / "deals.json") == tmp_path / "repo"


def test_deals_out_rejected_inside_a_foreign_repository(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    with pytest.raises(cap.CaptureError) as ei:
        cap.resolve_deals_out(str(tmp_path / "repo" / "deals.json"))
    assert "リポジトリ" in str(ei.value)


def test_deals_out_accepted_when_script_sibling_tree_is_not_a_repository(tmp_path):
    """VM 単体配布の再現: スクリプトの親がリポジトリでないとき、その配下も拒否しない。"""
    home = tmp_path / "Users" / "yoshi"
    (home / "Desktop").mkdir(parents=True)
    target = home / "Desktop" / "jp225_deals.json"
    assert cap.resolve_deals_out(str(target)) == target.resolve()


def test_snapshot_base_dir_is_repo_root_when_inside_a_repository():
    assert cap._snapshot_base_dir() == _REPO_ROOT


def test_with_deals_requires_deals_out():
    rc = cap.main(["--symbol", "JP225", "--with-deals"], mt5=FakeMt5(), now=_AT)
    assert rc != 0


def test_with_deals_rejects_repo_path_before_touching_terminal(tmp_path):
    fake = FakeMt5()
    rc = cap.main(
        ["--symbol", "JP225", "--out", str(tmp_path / "s.json"),
         "--with-deals", "--deals-out", str(_REPO_ROOT / "deals.json")],
        mt5=fake,
        now=_AT,
    )
    assert rc != 0
    assert fake.calls == [], "経路検査より先に端末へ接続しています"
    assert not (_REPO_ROOT / "deals.json").exists()


def test_history_deals_none_is_fail_stop():
    fake = FakeMt5(deals=None)
    with pytest.raises(cap.CaptureError):
        with cap.mt5_session(fake) as term:
            cap.read_deals(term, captured_at=_AT, days=30)


def test_deals_are_written_outside_repo_and_not_mixed_into_snapshot(tmp_path):
    deals_out = tmp_path / "outside" / "deals.json"
    snap_out = tmp_path / "JP225.json"
    fake = FakeMt5(deals=({"ticket": 1, "volume": 1.0, "price": 39412.0, "profit": -32.0},))
    rc = cap.main(
        ["--symbol", "JP225", "--out", str(snap_out),
         "--with-deals", "--deals-out", str(deals_out)],
        mt5=fake,
        now=_AT,
    )
    assert rc == 0
    snapshot = json.loads(snap_out.read_text(encoding="utf-8"))
    assert "deals" not in json.dumps(snapshot)
    assert sorted(snapshot) == ["_generated", "account", "meta", "symbol"]
    deals = json.loads(deals_out.read_text(encoding="utf-8"))
    assert deals["deals"] == [{"ticket": 1, "volume": 1.0, "price": 39412.0, "profit": -32.0}]


def test_snapshot_is_written_without_deals_by_default(tmp_path):
    out = tmp_path / "JP225.json"
    fake = FakeMt5()
    rc = cap.main(["--symbol", "JP225", "--out", str(out)], mt5=fake, now=_AT)
    assert rc == 0
    assert "history_deals_get" not in fake.calls
    assert out.read_text(encoding="utf-8") == cap.serialize(_snapshot())


# =====================================================================
# 8. 機械的検査（宣言だけを残さない）
# =====================================================================

def test_module_does_not_import_metatrader5_at_top_level():
    """コンテナ（MetaTrader5 不在）でも import が通ることを構造で担保する。"""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    offenders = []
    for node in tree.body:  # トップレベルのみ
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name.startswith("MetaTrader5")]
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith("MetaTrader5"):
            offenders.append(node.module)
    assert offenders == [], f"MetaTrader5 をトップレベル import しています: {offenders}"


def test_module_never_calls_order_apis():
    """接続先は実弾のライブ口座。発注系 API を 1 つも参照しない。"""
    tree = ast.parse(_SOURCE.read_text(encoding="utf-8"))
    offenders = sorted({
        n.attr for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr.startswith("order_")
    })
    assert offenders == [], f"発注系 API を参照しています: {offenders}"


def test_module_imports_without_metatrader5_installed():
    with pytest.raises(ModuleNotFoundError):
        __import__("MetaTrader5")
    assert cap.__name__ == "tools.capture_mt5_symbol_spec"


def test_cli_help_succeeds_as_a_script():
    """``python3 tools/capture_mt5_symbol_spec.py --help`` がコンテナで成功する。"""
    proc = subprocess.run(
        [sys.executable, str(_SOURCE), "--help"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--symbol" in proc.stdout


def test_cli_keeps_option_surface_minimal():
    """認知負荷を上げるフラグを増やさない（増やすときは本表を更新して裁定する）。"""
    opts = {a for act in cap.build_parser()._actions for a in act.option_strings}
    assert opts == {"-h", "--help", "--symbol", "--out", "--with-deals",
                    "--deals-out", "--deals-days"}
