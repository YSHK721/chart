"""GET の JSON ルート骨格（中立共有・ISSUE-479 Wave2b で sim_ui/framework から移設）。

StaticFileServer と **同一の面**（``serve(handler, path)``）を持つ。GET のうち登録済み
prefix に一致するものを JSON で応答し、それ以外は fallback（既存の StaticFileServer
インスタンス）へそのまま委譲する。

LSP: 呼び出し側（serve_sim.make_handler が返す Handler）は ``app.static_server.serve``
としか書いていない。同じ面を満たす限り、差し替えても静的配信の挙動は変わらない
（応答 byte・許可根・CWE-22 防御は StaticFileServer の単一ソースのまま）。

prefix の一致は**区切り境界**で判定する。``str.startswith`` だけで判定すると
/indicators-extra.js のような別資産まで JSON 経路へ吸い込む。

**なぜ中立パッケージが所有するか**（ISSUE-479 Wave2b）:
    本骨格は sim_ui/framework に置かれていたが、replay_ui/framework の 4 つの App
    （candles / catalog / intraday / profiles）が借用しており、同格であるべき 2 つの
    配信殻の間に **replay_ui → sim_ui** の辺を作っていた。実測ではその辺の全件
    （4/4）が本骨格 1 個の借用だった。骨格は「prefix で選び、外れたら静的配信へ落とす」
    という、どちらの殻のアクターにも属さない純粋物なので、http_contract と同格の
    共有物として中立パッケージが所有する。辺が 0 件であることは
    `simulator/replay_ui/tests/unit/test_no_sim_ui_dependency.py` が AST で固定する。

**依存純度**: 本モジュールは stdlib のみに依存する。移設前は型注釈のためだけに
    simulator.sim_ui.adapter.job_api_controller.ApiResponse を import していたが、
    replay 側のルート関数は ApiResponse ではなく ``(status, payload)`` タプルを
    返しており、この注釈は**実体と食い違っていた**（骨格は元から書き出し器に対して
    構造的である）。移設にあたり注釈を構造的プロトコル `JsonResponse` へ是正した。
    実行時の挙動は 1 バイトも変わらない（`from __future__ import annotations` の下で
    旧注釈も評価されていない）。純度は
    `api_shared/tests/test_json_get_routes_neutrality.py` が構造・実行の 2 段で固定する。
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Protocol


class JsonResponse(Protocol):
    """既定の書き出し器 :func:`write_json` が必要とする**構造**だけを表す面。

    具象を名指ししない（配信殻の DTO を中立パッケージが知らないため）。
    simulator.sim_ui.adapter.job_api_controller.ApiResponse はこれを構造的に満たす。
    """

    @property
    def status(self) -> int: ...

    def to_bytes(self) -> bytes: ...


def write_json(handler: Any, response: JsonResponse) -> None:
    """`JsonResponse` を HTTP 応答として書き出す（ジョブ API の書き出しと同一の形）。"""
    body = response.to_bytes()
    handler.send_response(response.status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class GetRouteResponder:
    """JSON ルート（prefix → 応答関数）と静的 fallback を束ねた ``serve`` 面。

    ``routes``: ``{prefix: (path) -> 応答}``。応答の型は注入された ``writer`` が決める
        （既定の :func:`write_json` は `JsonResponse` を、replay 側の
        write_replay_json は ``(status, payload)`` タプルを受ける）。
    ``fallback``: ``serve(handler, path)`` を持つもの（既定は StaticFileServer）。
    ``writer``: ``(handler, 応答) -> None``。既定は :func:`write_json`。

    **書き出し器を注入で受ける理由**（ISSUE-479 Wave2 3-3・加法）:
        replay 側の JSON 応答ヘッダは sim 側と異なる（``Content-Type: application/json`` に
        charset が付かない）。この違いは front と検定が見ている実体なので、骨格を共有する
        ついでに統一してはならない（応答 byte が変わる）。一方「prefix で選び、外れたら
        静的配信へ落とす」骨格は完全に同じで、2 つ書く理由が無い。**書き出し方だけ**を
        差し替え点にすれば、規則の第 2 実装を作らずに両方を賄える。
        渡さなければ現行と 1 ビットも変わらない。

    **クエリの扱い**（同上・加法）:
        prefix の一致判定だけがクエリを落とし、**転送する値は書き換えない**（ルート関数へも
        fallback へも元の path をそのまま渡す）。sim 側の呼び出しは呼ぶ前にクエリを落として
        いるため両者は同一で、挙動は変わらない。

        転送値を書き換えない理由（実測した壊れ方）: 応答器を数珠つなぎにすると、外側が
        「落とした値」を内側へ渡してしまい、内側のルートがクエリを失う。実際この形で
        ``/intraday?start=..&end=..`` が ``start/end required``（400）になった
        （分割前 golden が検出）。落とす責務は**終端**（静的配信の直前）に 1 つだけ置く。

    prefix の一致は**区切り境界**で判定する。``str.startswith`` だけで判定すると
    /indicators-extra.js のような別資産まで JSON 経路へ吸い込む。
    """

    def __init__(
        self,
        *,
        routes: "Mapping[str, Callable[[str], Any]]",
        fallback: Any,
        writer: "Callable[[Any, Any], None] | None" = None,
    ) -> None:
        self._routes = dict(routes)
        self._fallback = fallback
        self._writer = writer if writer is not None else write_json

    def serve(self, handler: Any, path: str) -> None:
        route_path = path.split("?", 1)[0]
        for prefix, responder in self._routes.items():
            if route_path == prefix or route_path.startswith(prefix + "/"):
                return self._writer(handler, responder(path))
        return self._fallback.serve(handler, path)
