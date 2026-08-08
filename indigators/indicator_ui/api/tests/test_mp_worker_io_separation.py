"""ISSUE-259: MP 3 経路の「計算はワーカー / 応答書き出しはリクエストスレッド」を固定する。

破れていた不変条件（是正前）:
  ``framework/server.py`` は ``/market_profile`` / ``/market_profile_forming`` /
  ``/tf_period_profile`` の 3 経路を ``_MP_WORKER.run(lambda: self._handle_*(...))`` の形で呼び、
  各 ``_handle_*`` の終端が ``self._send_json(...)`` だったため、**ソケット書き込みが単一ワーカー
  スレッド内**で起きていた。``/compute`` は「計算のみをワーカーへ渡し、応答書き出しはリクエスト
  スレッド側」と明記・実装されており、MP 3 経路だけがこの設計から外れていた。

  帰結: 遅いクライアント 1 本の受信待ち（``wfile.write`` のブロック）が MP 全経路を直列停止させる。
  ワーカーの目的は「MP 内部状態を守る計算の直列化」であり、I/O は無関係な責務である（SRP 違反）。

本テストが固定する不変条件（2 層）:

1. 実行時（識別力の本体）
   - MP 経路の計算は「常に同一の専用スレッド」で走る（直列化の維持）。
   - その応答書き出し（``_send_json``）は **計算スレッドとは別の**リクエストスレッドで起きる。
   - ワーカー内で発生した例外は、リクエストスレッド側で従来と同一の 500 nested error になる。

2. 構造（再発を構文的に不可能にする）
   - ワーカーへ渡す引数式が ``self``（＝handler・ソケット）を捕獲しない。
   - ``_MP_WORKER`` の参照点は 1 箇所のみ（3 経路で手書き複製しない・単一ソース）。
   - ワーカーへ渡す計算は **module 関数**であり、その本体に ``self`` もソケット書き込み API も
     現れない。module 関数のスコープには ``self`` 束縛が存在しないため、ソケット書き込みを
     ワーカーへ持ち込むことが構文的に不可能になる。
   - それら module 関数の ``return`` は必ず ``(status, payload)`` の 2 要素タプルである。

様式は同リポジトリの構造ガード（``test_no_usecase_dependency.py`` 等）を踏襲する。
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
_SOURCE = _SERVER_PY.read_text(encoding="utf-8")
_TREE = ast.parse(_SOURCE)

#: 計算をワーカー / プールへ送致する呼び出し（``X.run(...)`` / ``X.submit(...)``）の受け手。
_WORKER_NAMES = {"_MP_WORKER", "_COMPUTE_WORKER", "_COMPUTE_POOL"}
_DISPATCH_ATTRS = {"run", "submit"}

#: 3 経路の共通殻（ワーカー送致と応答書き出しの分担を単一ソース化する handler メソッド）。
_DISPATCH_METHOD = "_respond_mp_via_worker"

#: ワーカーへ渡す純計算に現れてはならない識別子（handler・ソケット書き込み API）。
_SOCKET_IDENTIFIERS = {
    "self",
    "_send_json",
    "_send_bytes",
    "wfile",
    "send_response",
    "send_header",
    "end_headers",
}


# --------------------------------------------------------------------------- #
# 構造ガード（再発を構文的に不可能にする）
# --------------------------------------------------------------------------- #
def _dispatch_calls() -> list[ast.Call]:
    """``_MP_WORKER.run(...)`` 等、ワーカー / プールへ計算を送致する Call を列挙する。"""
    out = []
    for node in ast.walk(_TREE):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if (
            isinstance(fn, ast.Attribute)
            and fn.attr in _DISPATCH_ATTRS
            and isinstance(fn.value, ast.Name)
            and fn.value.id in _WORKER_NAMES
        ):
            out.append(node)
    return out


def _module_functions() -> dict[str, ast.FunctionDef]:
    return {
        n.name: n
        for n in _TREE.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _find_method(class_name: str, method_name: str) -> ast.FunctionDef | None:
    for node in _TREE.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == method_name:
                    return sub
    return None


def _names_in(node: ast.AST) -> set[str]:
    ids = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            ids.add(n.id)
        elif isinstance(n, ast.Attribute):
            ids.add(n.attr)
    return ids


def test_worker_dispatch_arguments_never_capture_handler_self():
    """ワーカーへ渡す引数式に ``self`` が現れない（＝ソケットを捕獲しない）。

    是正前の ``_MP_WORKER.run(lambda: self._handle_market_profile(...))`` を検出する。
    """
    calls = _dispatch_calls()
    assert calls, "ワーカー送致の呼び出しが 1 件も見つからない（テストの前提崩壊）"
    offenders = []
    for call in calls:
        args = list(call.args) + [kw.value for kw in call.keywords]
        for arg in args:
            if "self" in _names_in(arg):
                offenders.append(f"{_SERVER_PY.name}:{call.lineno}: {ast.unparse(call)}")
                break
    assert not offenders, (
        "ワーカーへ渡す関数が handler（self＝ソケット）を捕獲している。"
        "応答書き出しはリクエストスレッド側で行うこと（ISSUE-259）:\n" + "\n".join(offenders)
    )


def test_mp_worker_is_referenced_from_exactly_one_site():
    """``_MP_WORKER`` の参照は 1 箇所（共通殻）のみ＝3 経路で手書き複製しない。"""
    method = _find_method("IndicatorUIRequestHandler", _DISPATCH_METHOD)
    assert method is not None, (
        f"3 経路の共通殻 IndicatorUIRequestHandler.{_DISPATCH_METHOD} が存在しない"
    )
    inside = {id(n) for n in ast.walk(method)}
    loads = [
        n
        for n in ast.walk(_TREE)
        if isinstance(n, ast.Name) and n.id == "_MP_WORKER" and isinstance(n.ctx, ast.Load)
    ]
    assert len(loads) == 1, (
        "_MP_WORKER の参照点が単一でない（経路ごとの手書き複製は取り残しを生む）: "
        f"{[n.lineno for n in loads]}"
    )
    assert id(loads[0]) in inside, (
        f"_MP_WORKER の参照が共通殻 {_DISPATCH_METHOD} の外にある: line {loads[0].lineno}"
    )


def _dispatched_compute_names() -> list[str]:
    """``self._respond_mp_via_worker(<fn>, ...)`` の第 1 引数（計算関数）の名前を集める。"""
    names = []
    for node in ast.walk(_TREE):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == _DISPATCH_METHOD
        ):
            assert node.args, f"line {node.lineno}: 計算関数が渡されていない"
            first = node.args[0]
            assert isinstance(first, ast.Name), (
                f"line {node.lineno}: 計算は module 関数名で渡すこと（lambda / 属性参照は不可）: "
                f"{ast.unparse(first)}"
            )
            names.append(first.id)
    return names


def test_mp_computes_are_module_level_functions_free_of_socket_access():
    """ワーカーへ渡す計算は module 関数であり、handler / ソケットに一切触れない。

    module 関数のスコープには ``self`` 束縛が存在しないため、応答書き出しをワーカースレッドへ
    持ち込むことが構文的に不可能になる（ISSUE-259 の再発防止）。
    """
    names = _dispatched_compute_names()
    assert len(names) == 3, f"MP 3 経路すべてが共通殻を通っていない: {names}"
    mods = _module_functions()
    offenders = []
    for name in names:
        fn = mods.get(name)
        assert fn is not None, f"{name} が module 関数として定義されていない（handler メソッド不可）"
        used = _names_in(fn) & _SOCKET_IDENTIFIERS
        if used:
            offenders.append(f"{name}: {sorted(used)}")
    assert not offenders, (
        "ワーカーへ渡す計算が handler / ソケット書き込みに触れている:\n" + "\n".join(offenders)
    )


def test_mp_computes_return_status_payload_pairs():
    """ワーカーへ渡す計算の ``return`` は必ず ``(status, payload)`` の 2 要素タプル。"""
    mods = _module_functions()
    names = _dispatched_compute_names()
    assert len(names) == 3, f"MP 3 経路すべてが共通殻を通っていない: {names}"
    offenders = []
    for name in names:
        fn = mods[name]
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert returns, f"{name} が値を返していない"
        for r in returns:
            if not (isinstance(r.value, ast.Tuple) and len(r.value.elts) == 2):
                offenders.append(
                    f"{name}:{r.lineno}: {ast.unparse(r)}"
                )
    assert not offenders, (
        "計算は (status, payload) を返すこと（応答書き出しは呼び出し元）:\n" + "\n".join(offenders)
    )


# --------------------------------------------------------------------------- #
# 実行時ガード（スレッド分担の実測）
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


def _get(base: str, path: str):
    try:
        with urllib.request.urlopen(base + path, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


#: (controller 名, 要求 path, stub payload) — MP 3 経路。
_ROUTES = [
    (
        "handle_market_profile",
        "/market_profile?datasetRef=jp225_tick&timeframe=1D&bins=60",
        {"ok": True, "profile": {"bins": []}},
        "market_profile 取得に失敗しました",
    ),
    (
        "handle_market_profile_forming",
        "/market_profile_forming?datasetRef=jp225_tick&timeframe=1m&base=1&now=1785909600",
        {"ok": True, "ticks": [], "formingStart": 1785909600, "now": 1785909600},
        "market_profile_forming 取得に失敗しました",
    ),
    (
        "handle_tf_period_profile",
        "/tf_period_profile?datasetRef=jp225_tick&timeframe=5m&from=1000&to=2000",
        {"ok": True, "tf": "5m", "columns": []},
        "tf_period_profile 取得に失敗しました",
    ),
]


@pytest.mark.parametrize(
    "controller,path,payload,_msg", _ROUTES, ids=[r[0] for r in _ROUTES]
)
def test_compute_runs_on_worker_thread_and_response_is_written_on_request_thread(
    server, monkeypatch, controller, path, payload, _msg
):
    """計算は単一の専用スレッド・応答書き出しはリクエストスレッド（ISSUE-259 の本体）。

    是正前は ``_send_json`` がワーカースレッドで走っていたため、書き出しスレッド ident が
    計算スレッド ident と一致して Red になる。
    """
    compute_idents: list[int] = []
    send_idents: list[int] = []

    def _stub(*_a, **_k):
        compute_idents.append(threading.get_ident())
        return 200, dict(payload)

    monkeypatch.setattr(server_mod, controller, _stub)

    original = IndicatorUIRequestHandler._send_json

    def _spy(self, status, body):
        send_idents.append(threading.get_ident())
        return original(self, status, body)

    monkeypatch.setattr(IndicatorUIRequestHandler, "_send_json", _spy)

    # 2 回叩く: 計算スレッドは同一（直列化の維持）・書き出しスレッドは計算スレッドと別。
    for _ in range(2):
        status, body = _get(server, path)
        assert status == 200
        assert body.get("ok") is True

    assert len(compute_idents) == 2 and len(send_idents) == 2
    assert compute_idents[0] == compute_idents[1], (
        "MP 計算が単一の専用スレッドで直列化されていない（MP 内部状態の保護が失われる）"
    )
    assert not (set(send_idents) & set(compute_idents)), (
        "応答書き出しが計算ワーカースレッドで起きている（遅いクライアント 1 本で MP 全経路が"
        f"直列停止する・ISSUE-259）: send={send_idents} compute={compute_idents}"
    )


@pytest.mark.parametrize(
    "controller,path,_payload,message", _ROUTES, ids=[r[0] for r in _ROUTES]
)
def test_worker_exception_becomes_same_500_nested_error_on_request_thread(
    server, monkeypatch, controller, path, _payload, message
):
    """ワーカー内の例外は従来と同一の 500 nested error になり、書き出しはリクエストスレッド。"""
    compute_idents: list[int] = []
    send_idents: list[int] = []

    def _boom(*_a, **_k):
        compute_idents.append(threading.get_ident())
        raise RuntimeError("boom")

    monkeypatch.setattr(server_mod, controller, _boom)

    original = IndicatorUIRequestHandler._send_json

    def _spy(self, status, body):
        send_idents.append(threading.get_ident())
        return original(self, status, body)

    monkeypatch.setattr(IndicatorUIRequestHandler, "_send_json", _spy)

    status, body = _get(server, path)

    assert status == 500
    assert body == server_mod._nested_error("internal", f"{message}: boom")
    assert compute_idents and send_idents
    assert not (set(send_idents) & set(compute_idents)), (
        "500 応答の書き出しがワーカースレッドで起きている（ISSUE-259）"
    )
