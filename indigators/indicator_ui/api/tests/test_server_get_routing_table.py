"""GET 経路の解決が表引きであることを固定する（ISSUE-479 Wave2 I-2）。

固定するのは「経路を 1 本足すときに GET 殻を改変しなくてよい」構造そのものである。
GET 殻の ``if parsed.path == "..."`` 連鎖は「経路名を知っている場所」であり、経路が増える
たびに殻が伸び、殻の中に経路固有の知識（クエリを取るか否か・どの計算へ送るか）が散る。

  R1: GET 殻に分岐（``if``）が 0 件（経路解決は ``_GET_ROUTES`` の表引き）。
  R2: 表へ 1 行足すだけで新しい経路が 200 を返す（OCP の実証・殻は無改変）。
  R3: 未知パスは静的配信へ落ち、無い資源は 404（従来不変）。
  R4: ``parse_qs`` の呼出点が source 上 1 か所（8 経路で手書き複製しない）。

計算量（別ファイルの方針と同じ「発行 − 使用 = 0」）:
  クエリを使わない経路（/catalog・静的・404）では ``parse_qs`` を 1 度も発行しない。
  表の行数を増やしても 1 要求あたりの発行は増えない（経路 8/9 の 2 点で固定）。

様式は ``test_mp_worker_io_separation.py``（AST 構造ガード＋実行時ガード）を踏襲する。
"""

from __future__ import annotations

import ast
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import framework.server as server_mod
from framework.server import IndicatorUIRequestHandler

_SERVER_PY = Path(server_mod.__file__).resolve()
_TREE = ast.parse(_SERVER_PY.read_text(encoding="utf-8"))


def _method(class_name: str, method: str) -> ast.FunctionDef:
    for node in _TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method:
                    return sub
    raise AssertionError(f"{class_name}.{method} が見つからない（テストの前提崩壊）")


# --------------------------------------------------------------------------- #
# R1 / R4: 構造ガード
# --------------------------------------------------------------------------- #
def test_do_get_has_no_branches():
    fn = _method("IndicatorUIRequestHandler", "do_GET")
    offenders = [
        f"{_SERVER_PY.name}:{n.lineno}: {ast.unparse(n).splitlines()[0]}"
        for n in ast.walk(fn)
        if isinstance(n, (ast.If, ast.IfExp))
    ]
    assert not offenders, (
        "GET 殻が経路名で分岐している（_GET_ROUTES 表引きにすること）:\n" + "\n".join(offenders)
    )


def _call_sites(func_name: str) -> list[int]:
    return [
        n.lineno
        for n in ast.walk(_TREE)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == func_name
    ]


def test_query_parsing_has_a_single_call_site():
    """``parse_qs`` の呼出点は 1 か所（経路ごとに手書き複製しない）。"""
    sites = _call_sites("parse_qs")
    assert len(sites) == 1, f"parse_qs の呼出点が複数ある: {_SERVER_PY.name}:{sites}"


def test_the_route_table_covers_every_json_get_endpoint():
    """表の鍵集合が JSON 配信経路の全数（静的配信は既定フォールバック）。"""
    assert set(server_mod._GET_ROUTES) == {
        "/candles", "/forming_bar", "/market_profile", "/market_profile_forming",
        "/tf_period_profile", "/live_ticks", "/tickvol_profile", "/catalog",
    }


# --------------------------------------------------------------------------- #
# 実行時ガード
# --------------------------------------------------------------------------- #
@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), IndicatorUIRequestHandler)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(base: str, path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(base + path, timeout=60) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_a_new_route_is_served_by_adding_one_table_row(server, monkeypatch):
    """R2（OCP の実証）: 表へ 1 行足すだけで 200 を返す（GET 殻は無改変）。"""
    # Arrange
    route_cls = type(server_mod._GET_ROUTES["/catalog"])
    added = dict(server_mod._GET_ROUTES)
    added["/fake_probe"] = route_cls(
        server_mod._query_of,
        lambda h, q: h._send_json(200, {"ok": True, "echo": q.get("x", [])}),
    )
    monkeypatch.setattr(server_mod, "_GET_ROUTES", added)

    # Act
    status, body = _get(server, "/fake_probe?x=7")

    # Assert
    assert status == 200
    assert json.loads(body.decode()) == {"ok": True, "echo": ["7"]}


def test_an_unknown_path_falls_through_to_static_and_404s(server):
    """R3: 未知パスは静的配信へ落ち、無い資源は 404（従来不変）。"""
    status, body = _get(server, "/definitely_not_a_route")
    assert status == 404
    assert body == b"404 Not Found"


def test_a_known_static_asset_is_still_served(server):
    status, body = _get(server, "/index.html")
    assert status == 200
    assert body.lower().startswith(b"<!doctype html")


# --------------------------------------------------------------------------- #
# 計算量テスト: クエリ解析の発行 − 使用 = 0
# --------------------------------------------------------------------------- #
class _QuerySpy:
    def __init__(self, original):
        self._original = original
        self.issued: list[str] = []

    def __call__(self, query):
        self.issued.append(query)
        return self._original(query)


#: (要求パス, その経路がクエリを使うか)
_QUERY_USE = [
    ("/candles?datasetRef=sample&timeframe=1m&limit=10", 1),
    ("/live_ticks?since=0", 1),
    ("/catalog", 0),
    ("/index.html", 0),
    ("/definitely_not_a_route", 0),
]


@pytest.mark.parametrize("path,used", _QUERY_USE, ids=[p.split("?")[0] for p, _ in _QUERY_USE])
def test_query_parsing_is_issued_only_where_it_is_used(server, monkeypatch, path, used):
    """発行した ``parse_qs`` − その経路が使うクエリ数 = 0。

    使わない経路（クエリを取らない /catalog・静的・404）で発行すると、作って捨てる計算になる。
    回数を焼き込まず**無駄の不在**を固定する。
    """
    # Arrange
    spy = _QuerySpy(server_mod.parse_qs)
    monkeypatch.setattr(server_mod, "parse_qs", spy)

    # Act
    _get(server, path)

    # Assert
    assert len(spy.issued) - used == 0, f"{path}: parse_qs 発行 {len(spy.issued)} / 使用 {used}"


@pytest.mark.parametrize("extra_rows", [0, 1])
def test_query_parsing_per_request_does_not_grow_with_the_table_size(
    server, monkeypatch, extra_rows
):
    """オーダーの表明: 表の行数（経路 8/9）を変えても 1 要求あたりの発行は変わらない。"""
    # Arrange
    route_cls = type(server_mod._GET_ROUTES["/catalog"])
    table = dict(server_mod._GET_ROUTES)
    for i in range(extra_rows):
        table[f"/fake_row_{i}"] = route_cls(
            server_mod._no_query, lambda h, q: h._send_json(200, {"ok": True})
        )
    monkeypatch.setattr(server_mod, "_GET_ROUTES", table)
    spy = _QuerySpy(server_mod.parse_qs)
    monkeypatch.setattr(server_mod, "parse_qs", spy)

    # Act
    _get(server, "/candles?datasetRef=sample&timeframe=1m&limit=10")

    # Assert
    assert len(spy.issued) - 1 == 0


def test_the_complexity_gate_detects_a_double_parse_mutation(server, monkeypatch):
    """負の対照: クエリを二度解析する変異は上の検査で赤になる（検出力の実測）。"""
    # Arrange
    spy = _QuerySpy(server_mod.parse_qs)
    monkeypatch.setattr(server_mod, "parse_qs", spy)
    route_cls = type(server_mod._GET_ROUTES["/catalog"])
    table = dict(server_mod._GET_ROUTES)
    table["/candles"] = route_cls(
        # 捨てられる解析を 1 回混ぜる（浪費の再現）。
        lambda parsed: (server_mod.parse_qs(parsed.query), server_mod.parse_qs(parsed.query))[1],
        lambda h, q: h._handle_candles(q),
    )
    monkeypatch.setattr(server_mod, "_GET_ROUTES", table)

    # Act
    _get(server, "/candles?datasetRef=sample&timeframe=1m&limit=10")

    # Assert
    assert len(spy.issued) - 1 != 0, "変異を検出できていない（検査が空振り）"
