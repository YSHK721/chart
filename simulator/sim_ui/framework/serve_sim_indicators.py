"""serve_sim_indicators — 指標一覧 API を足した sim コア（framework 層・Phase 3 F-5）。

Phase 2 の `serve_sim_jobs` を **継承ではなく委譲で包む**（OCP）。`serve_sim_jobs` は
1 バイトも変えない。

    SimJobApp ──(委譲)── SimIndicatorApp
                          static_server だけ GetRouteResponder へ差し替える
                          それ以外の面は**宣言した名だけ**を内側へ転送する

**継承しない理由**: Phase 2 は Phase 1 を継承で拡張した（`SimJobApp(SimApp)` /
`JobHandler(Base)`）。ここでさらに継承を重ねると、Handler が 3 段になり
`make_handler` / `make_server` / `serve` の 15 行を三重に複製することになる
（ISSUE-374-1 と同型の壊れ方）。**Handler もサーバ生成も Phase 2 のものをそのまま
再利用する**（下の re-export）。Handler は `app.static_server` / `app.controller` /
`app.result_server` を属性で引くだけなので、同じ属性を出せる包み手で足りる。

エンドポイント（sim core は prefix 除去後のパスを受ける）:
    GET /indicators   因果性検定の結果を**系列単位**で返す一覧（未通過も reason つきで
                      含む・503 は台帳不在）
POST は作らない（YAGNI・§11.4）。検定は CLI が行い、結果は台帳が持つ。

委譲面の宣言（ISSUE-502 段階 5B・中 14/15 の是正）:
    本モジュールは sim core を包む 5 本（indicators / ea-series / run-options /
    settings-schema / display）が共有する**委譲の機構と宣言表**を持つ。以前は 5 本とも
    __getattr__ で「自分が持たない属性はすべて内側へ」渡していたが、それは
    **転送する面がどこにも宣言されていない**ということであり、委譲の書き落としが
    リクエスト時（＝実運用）まで露見しない。同型の欠陥は
    ``sim_ui/adapter/causal_compute_ports.py`` が対照実験つきで既に禁じている
    （実測 2026-09-03: 動的フォールバックがあると period_start の明示委譲を
    落としても 99 を返して成功し、面の委譲を確かめる検定も緑のまま通った）。

    置き換えた形:
      - 転送する名を ``@delegates_to_inner(...)`` で**宣言**する（読めば面が分かる）。
      - 宣言名は class 生成時に property として生える（機構の実体は本モジュールの 1 箇所）。
      - ``__init__`` で内側の保有を検査し、欠落はその場で ``TypeError``（起動時 fail-stop）。
      - 宣言に無い名は ``AttributeError``（透過しない）。
"""
from __future__ import annotations

from typing import Any

from api_shared.json_get_routes import GetRouteResponder

# Handler・サーバ生成・起動は Phase 2 の実体をそのまま使う（複製しない）。
from simulator.sim_ui.framework.serve_sim_jobs import (  # noqa: F401
    make_handler,
    make_server,
    serve,
)

#: 指標一覧のパス（sim core が受ける prefix 除去後の形）。
INDICATORS_PATH = "/indicators"

#: sim core（`SimJobApp`）が包み手へ差し出す面。``static_server`` は**含めない**——
#: 包み手はそれぞれ自分の ``static_server`` を据えるので、転送してはならない名である。
SIM_CORE_SURFACE: "tuple[str, ...]" = (
    "controller",       # JobApiController（Handler が POST /jobs・GET /jobs/{id} で引く）
    "launcher",         # ジョブ起動器（合成根の検定が実体を確かめる）
    "ledger",           # ジョブ台帳（data_root の出所）
    "result_server",    # /data/{job}/{file} の配信器（Handler が引く）
    "shared_js_root",   # 静的配信のフォールバック根
    "web_dir",          # 静的配信の根
)


def _forwarding_property(name: str) -> property:
    """``self._inner.<name>`` をそのまま返す読み取り専用 property を作る（機構の実体）。"""

    def _get(self: Any) -> Any:
        return getattr(self._inner, name)

    _get.__name__ = name
    return property(
        _get,
        doc=f"内側 App の ``{name}`` を返す（宣言済みの委譲面・`delegates_to_inner`）。",
    )


def delegates_to_inner(*names: str):
    """宣言した名だけを ``self._inner`` へ転送する property をクラスへ生やす class デコレータ。

    転送の実装をここ 1 箇所に閉じる（5 本へ手書き複製しない）。生えるのは class 生成時に
    1 回だけで、属性アクセスのたびに機構が走ることはない。

    自分で定義している名を宣言に混ぜた場合は ``TypeError``——宣言と実体が食い違ったまま
    どちらかが黙って勝つ状態を作らないため、定義時点で落とす。
    """
    declared = tuple(dict.fromkeys(names))

    def _decorate(cls):
        collision = [n for n in declared if n in vars(cls)]
        if collision:
            raise TypeError(
                f"{cls.__name__}: 自分で持つ名を委譲面として宣言しています: {collision}。"
                " 宣言表から外すか、自前の定義を消してください。"
            )
        for name in declared:
            setattr(cls, name, _forwarding_property(name))
        cls.DELEGATED_SURFACE = declared
        return cls

    return _decorate


def verify_delegated_surface(owner: Any, inner: Any) -> None:
    """宣言した委譲面が ``inner`` に実在することを**構築時に 1 回**確かめる。

    欠落を起動時に落とすためにある。__getattr__ 透過だった頃は、内側が面を失っても
    包み手は無言で組み上がり、そのルートへ最初のリクエストが来たとき初めて
    ``AttributeError`` になった（受け口はあるのに結線が死ぬ・ISSUE-291 の形）。
    """
    declared = getattr(type(owner), "DELEGATED_SURFACE", ())
    missing = [name for name in declared if not hasattr(inner, name)]
    if missing:
        raise TypeError(
            f"{type(owner).__name__}: 内側 App に委譲面がありません: {missing}。"
            f" 内側は {type(inner).__name__} です。宣言表（DELEGATED_SURFACE）と"
            " 内側の実体を突き合わせてください。"
        )


#: `SimIndicatorApp` が差し出す面（core の面 ＋ 本層の追加）。外側の包み手はこれを転送する。
SIM_INDICATOR_SURFACE: "tuple[str, ...]" = SIM_CORE_SURFACE + (
    "indicator_controller",
    "causality_ledger",
)


@delegates_to_inner(*SIM_CORE_SURFACE)
class SimIndicatorApp:
    """`SimJobApp` を包み、GET の JSON ルートを 1 本足したアプリケーション面。

    ``inner``: `SimJobApp`（配信面 ＋ ジョブ実行系）。
    ``controller``: `IndicatorApiController`（`list() -> ApiResponse`・系列単位の一覧）。

    内側へ転送する面は `SIM_CORE_SURFACE`（宣言）。宣言に無い名は解決しない。
    """

    def __init__(self, *, inner: Any, controller: Any) -> None:
        self._inner = inner
        self._controller = controller
        verify_delegated_surface(self, inner)
        # 静的面の前に JSON ルートを挟む。静的配信そのもの（許可根・応答 byte・
        # CWE-22 防御）は内側の StaticFileServer が単一ソースのまま担う。
        self.static_server = GetRouteResponder(
            routes={INDICATORS_PATH: lambda _path: controller.list()},
            fallback=inner.static_server,
        )

    @property
    def inner(self) -> Any:
        """包んでいる `SimJobApp`（結線を複製していないことを確かめる面）。"""
        return self._inner

    @property
    def indicator_controller(self) -> Any:
        """指標一覧の controller（合成根の検定が実物の結線を確かめるための面）。"""
        return self._controller

    @property
    def causality_ledger(self) -> Any:
        """一覧の出所となる因果性台帳（`SimJobApp.ledger` と対称の公開面）。"""
        return self._controller.ledger
