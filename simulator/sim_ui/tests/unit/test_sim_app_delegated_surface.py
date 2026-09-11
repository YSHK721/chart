"""sim core を包む 6 本の**委譲面の宣言**を固定する（ISSUE-502 段階 5B・中 14/15）。

段階 4（ISSUE-508・§9.2）で trace 層が 6 本目として加わった。本モジュールの `_WRAPPERS` /
`_SURFACE_CHAIN` は「層を足したらここへ 1 行足す」拡張点であり、足し忘れは
規則 5（面は単調に増える）と規則 4（宣言と実体の一致）が赤にする。

是正前の形と、それが害である理由:
    各層（indicators / ea-series / run-options / settings-schema / trace / display）は
    __getattr__ で「自分が持たない属性はすべて内側へ」渡していた。転送する面が
    どこにも宣言されていないため、**内側が面を失っても包み手は無言で組み上がり**、
    そのルートへ最初のリクエストが来たときに初めて ``AttributeError`` になる
    （受け口はあるのに結線が死ぬ・ISSUE-291 の形）。

    同型の欠陥は ``sim_ui/adapter/causal_compute_ports.py`` が対照実験つきで既に
    裁定している（実測 2026-09-03: 動的フォールバックがあると period_start の
    明示委譲を落としても **99 を返して成功**し、面の委譲を確かめる既存検定も緑のまま
    通った）。本モジュールは同じ規律を全層へ適用したことを機械的に固定する。

固定する規則:
    1. どの層も __getattr__ を持たない（透過委譲の再出現を Red にする）。
    2. 宣言に無い名は解決しない（内側が持っていても素通ししない）。
    3. 宣言した名が内側に無ければ **構築時に** 落ちる（起動時 fail-stop）。
    4. 本番の合成根で組んだ App から、宣言した全名が実際に引ける（宣言と実体の一致）。
    5. 層を重ねるほど面は単調に増える（内側の面を落とさない＝narrowing しない）。

計算量検定（絶対命令 2026-08-28）: 委譲面の検査は **App の生成 1 回につき 1 回**。
    属性アクセス・リクエストのたびには走らない。「発行した検査 − 生成数 = 0」と
    「アクセスを増やしても検査は増えない」を 2 点ずつで固定する（回数リテラルは
    焼き込まず、要求数から導出する）。
"""

from __future__ import annotations

import ast
import inspect
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from simulator.sim_ui.framework import (
    serve_sim_display,
    serve_sim_ea_series,
    serve_sim_indicators,
    serve_sim_run_options,
    serve_sim_settings_schema,
    serve_sim_trace,
)
from simulator.sim_ui.framework.serve_sim_display import (
    SIM_DISPLAY_SURFACE,
    SimDisplayApp,
)
from simulator.sim_ui.framework.serve_sim_ea_series import (
    SIM_EA_SERIES_SURFACE,
    SimEaSeriesApp,
)
from simulator.sim_ui.framework.serve_sim_indicators import (
    SIM_CORE_SURFACE,
    SIM_INDICATOR_SURFACE,
    SimIndicatorApp,
)
from simulator.sim_ui.framework.serve_sim_run_options import (
    SIM_RUN_OPTIONS_SURFACE,
    SimRunOptionsApp,
)
from simulator.sim_ui.framework.serve_sim_settings_schema import (
    SIM_SETTINGS_SCHEMA_SURFACE,
    SimSettingsSchemaApp,
)
from simulator.sim_ui.framework.serve_sim_trace import SIM_TRACE_SURFACE, SimTraceApp
from simulator.sim_ui.main.composition_root_display import build_sim_display_app

#: 包み手 5 本（モジュール, クラス, 内側へ要求する宣言面）。層を足したらここへ 1 行足す。
_WRAPPERS = (
    (serve_sim_indicators, SimIndicatorApp, SIM_CORE_SURFACE),
    (serve_sim_ea_series, SimEaSeriesApp, SIM_INDICATOR_SURFACE),
    (serve_sim_run_options, SimRunOptionsApp, SIM_EA_SERIES_SURFACE),
    (serve_sim_settings_schema, SimSettingsSchemaApp, SIM_RUN_OPTIONS_SURFACE),
    # ISSUE-508 段階 4（§9.2）: 実行トレース分析 API の層。
    (serve_sim_trace, SimTraceApp, SIM_SETTINGS_SCHEMA_SURFACE),
    (serve_sim_display, SimDisplayApp, SIM_TRACE_SURFACE),
)

#: 外側ほど面が広くなる順（規則 5 の突き合わせ対象）。
_SURFACE_CHAIN = (
    ("SIM_CORE_SURFACE", SIM_CORE_SURFACE),
    ("SIM_INDICATOR_SURFACE", SIM_INDICATOR_SURFACE),
    ("SIM_EA_SERIES_SURFACE", SIM_EA_SERIES_SURFACE),
    ("SIM_RUN_OPTIONS_SURFACE", SIM_RUN_OPTIONS_SURFACE),
    ("SIM_SETTINGS_SCHEMA_SURFACE", SIM_SETTINGS_SCHEMA_SURFACE),
    ("SIM_TRACE_SURFACE", SIM_TRACE_SURFACE),
    ("SIM_DISPLAY_SURFACE", SIM_DISPLAY_SURFACE),
)


class _Marker:
    """内側の面の身代わり（同一性で「本当に内側のものが出たか」を見る）。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def serve(self, handler: Any, path: str) -> None:  # 配信器の代役（呼ばれない）
        raise AssertionError("この fake は配信しない")


def _fake_inner(surface: "tuple[str, ...]", *, omit: "str | None" = None) -> Any:
    """``surface`` を満たす内側 App の代役。``omit`` を指定するとその面だけ欠く。"""
    inner = _Marker("inner")
    # static_server は宣言面ではない（各包み手が自分で据える）が、包み手の __init__ が
    #   fallback として引くので必ず持たせる。
    inner.static_server = _Marker("static_server")
    for name in surface:
        if name != omit:
            setattr(inner, name, _Marker(name))
    return inner


def _construct(app_class, inner) -> Any:
    """包み手を最小の引数で組む（クラスごとの差は第 2 引数だけ）。"""
    if app_class is SimDisplayApp:
        return app_class(inner=inner, static_routes={})
    return app_class(inner=inner, controller=_Marker("controller"))


# --------------------------------------------------------------------------------------
# 1. 透過委譲の再出現を Red にする
# --------------------------------------------------------------------------------------
def _class_node(module, class_name: str) -> ast.ClassDef:
    src = inspect.getsourcefile(module)
    assert src is not None, f"ソースが取れない: {module!r}"
    tree = ast.parse(Path(src).read_text(encoding="utf-8"))
    node = next(
        (n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name),
        None,
    )
    assert node is not None, f"クラスが見つからない: {class_name} in {src}"
    return node


def _defines_getattr(node: ast.ClassDef) -> bool:
    """クラス本体に __getattr__ の定義があるか（検出器の実体）。"""
    return any(
        isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name == "__getattr__"
        for m in node.body
    )


@pytest.mark.parametrize(
    "module,app_class",
    [(m, c) for m, c, _s in _WRAPPERS],
    ids=[c.__name__ for _m, c, _s in _WRAPPERS],
)
def test_包み手は透過委譲の__getattr__を持たない(module, app_class) -> None:
    """拾い先があると、委譲の書き落としが実行時まで隠れる（causal_compute_ports の裁定）。"""
    assert not _defines_getattr(_class_node(module, app_class.__name__)), (
        f"{app_class.__name__} に __getattr__ が復活している。"
        " 転送する面は DELEGATED_SURFACE の宣言で表明する規約である。"
    )


def test_getattr検出器が空振りしていない() -> None:
    """変異体（__getattr__ を持つクラス）を与えたら検出できること。

    検出器が壊れて常に False を返すと、上のテストは無条件に緑になる（ガードが死ぬ）。
    """
    mutant = ast.parse(
        "class Mutant:\n"
        "    def __getattr__(self, name):\n"
        "        return getattr(self._inner, name)\n"
    ).body[0]
    assert isinstance(mutant, ast.ClassDef)
    assert _defines_getattr(mutant), "検出器が __getattr__ を見落としている（ガードが空虚）"

    clean = ast.parse("class Clean:\n    pass\n").body[0]
    assert isinstance(clean, ast.ClassDef)
    assert not _defines_getattr(clean)


# --------------------------------------------------------------------------------------
# 2. 宣言に無い名は解決しない（透過しない）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "app_class,surface",
    [(c, s) for _m, c, s in _WRAPPERS],
    ids=[c.__name__ for _m, c, _s in _WRAPPERS],
)
def test_宣言に無い名は内側が持っていても解決しない(app_class, surface) -> None:
    """是正前は素通ししていた。宣言が面の唯一の定義であることを固定する。"""
    inner = _fake_inner(surface)
    inner.undeclared_face = _Marker("undeclared_face")   # 内側には在る
    app = _construct(app_class, inner)

    assert hasattr(inner, "undeclared_face")
    with pytest.raises(AttributeError):
        app.undeclared_face


@pytest.mark.parametrize(
    "app_class,surface",
    [(c, s) for _m, c, s in _WRAPPERS],
    ids=[c.__name__ for _m, c, _s in _WRAPPERS],
)
def test_宣言した名は内側の実体をそのまま返す(app_class, surface) -> None:
    """転送が「値を作り直さず内側のものを返す」ことを同一性で見る。"""
    inner = _fake_inner(surface)
    app = _construct(app_class, inner)
    for name in surface:
        assert getattr(app, name) is getattr(inner, name), name


# --------------------------------------------------------------------------------------
# 3. 委譲の欠落は構築時に落ちる（起動時 fail-stop）
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "app_class,surface,missing",
    [(c, s, name) for _m, c, s in _WRAPPERS for name in s],
    ids=[f"{c.__name__}-{name}" for _m, c, s in _WRAPPERS for name in s],
)
def test_宣言面の欠落は構築時に落ちる(app_class, surface, missing: str) -> None:
    """宣言した**すべての**名が実際に検査されていること（1 つずつ落として確かめる）。

    是正前はここで例外が出ず、そのルートへ最初のリクエストが来るまで欠落が隠れた。
    """
    inner = _fake_inner(surface, omit=missing)
    with pytest.raises(TypeError) as excinfo:
        _construct(app_class, inner)
    assert missing in str(excinfo.value), (
        f"欠落名 {missing} が失敗メッセージに出ない（どの面が無いか分からない）"
    )


def test_面が揃っていれば構築は通る() -> None:
    """上の fail-stop が「常に落ちる」わけではないこと（検定の空振り防止）。

    面が揃っているときは例外を出さずに組み上がり、宣言した面がすべて内側の実体を
    指していること（＝構築が中途で終わっていないこと）まで見る。
    """
    for _m, app_class, surface in _WRAPPERS:
        inner = _fake_inner(surface)
        app = _construct(app_class, inner)
        assert app.inner is inner
        assert tuple(type(app).DELEGATED_SURFACE) == tuple(surface)
        assert [getattr(app, n) for n in surface] == [getattr(inner, n) for n in surface]
        assert app.static_server is not inner.static_server  # 自分の配信面を据えている


# --------------------------------------------------------------------------------------
# 4. 宣言と実体の一致（本番の合成根）
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def display_app():
    return build_sim_display_app()


def test_本番合成根の全宣言面が実際に引ける(display_app) -> None:
    """宣言だけして結線されていない面が無いこと（宣言と実体の乖離を作らない）。"""
    unresolved = []
    for name in SIM_DISPLAY_SURFACE:
        try:
            getattr(display_app, name)
        except AttributeError as exc:  # noqa: PERF203 — どの面が落ちたかを全部集める
            unresolved.append((name, str(exc)))
    assert unresolved == [], f"宣言済みなのに引けない面: {unresolved}"


def test_合成根のHandlerが引く3面が解決する(display_app) -> None:
    """Handler は `static_server` / `controller` / `result_server` を属性で引く。"""
    assert display_app.static_server is not None
    assert display_app.controller is not None
    assert display_app.result_server is not None


@pytest.mark.parametrize(
    "outer_name,outer,inner_name,inner",
    [
        (_SURFACE_CHAIN[i + 1][0], _SURFACE_CHAIN[i + 1][1],
         _SURFACE_CHAIN[i][0], _SURFACE_CHAIN[i][1])
        for i in range(len(_SURFACE_CHAIN) - 1)
    ],
    ids=[f"{_SURFACE_CHAIN[i][0]}->{_SURFACE_CHAIN[i + 1][0]}"
         for i in range(len(_SURFACE_CHAIN) - 1)],
)
def test_層を重ねても面は狭まらない(outer_name, outer, inner_name, inner) -> None:
    """外側は内側の面をすべて含む（包んだ瞬間に面が消える＝narrowing を禁じる）。"""
    lost = [n for n in inner if n not in outer]
    assert lost == [], f"{outer_name} が {inner_name} の面を落としている: {lost}"


def _core_of(app: Any) -> Any:
    """包みを剥がして最内の sim core（SimJobApp）まで降りる。"""
    node = app
    while getattr(node, "_inner", None) is not None:
        node = node._inner
    return node


def test_宣言面はsim_coreの公開面を1つも落としていない(display_app) -> None:
    """`SIM_CORE_SURFACE` を実体（SimJobApp の公開属性）へ突き合わせる。

    宣言表だけを見ていると、ある面を**宣言から丸ごと落とす**変更が誰にも気付かれない
    （外側の面は宣言の写しなので、揃って消えれば整合してしまう）。宣言の正しさは
    宣言の外——core が実際に持っている面——から確かめる必要がある。

    ``static_server`` だけは意図的な除外（各包み手が自分で据える面）。
    """
    core = _core_of(display_app)
    presented = {k for k in vars(core) if not k.startswith("_")}
    expected = presented - {"static_server"}
    assert set(SIM_CORE_SURFACE) == expected, (
        f"宣言 {sorted(SIM_CORE_SURFACE)} と core の公開面 {sorted(expected)} が食い違う"
        f"（core: {type(core).__name__}）"
    )


def test_宣言面に配信面を混ぜていない() -> None:
    """``static_server`` は各包み手が自分で据える面であり、転送してはならない。"""
    for name, surface in _SURFACE_CHAIN:
        assert "static_server" not in surface, name


# --------------------------------------------------------------------------------------
# 5. 計算量検定（Test Spy・発行 − 使用 = 0）
# --------------------------------------------------------------------------------------
def _spy_verifications(monkeypatch) -> "list[str]":
    """5 本が呼ぶ検査関数を差し替えて発行回数を数える（各モジュールの束縛を押さえる）。"""
    seen: "list[str]" = []
    original = serve_sim_indicators.verify_delegated_surface

    def _spy(owner: Any, inner: Any) -> None:
        seen.append(type(owner).__name__)
        return original(owner, inner)

    for module, _c, _s in _WRAPPERS:
        monkeypatch.setattr(module, "verify_delegated_surface", _spy)
    return seen


@pytest.mark.parametrize("builds_requested", [1, 3], ids=["build_1", "build_3"])
def test_委譲面の検査は生成1回につき1回(monkeypatch, builds_requested: int) -> None:
    """生成 1 個 / 3 個の 2 点で「検査回数 − 生成数 × 包み手数 = 0」。

    生成のたびに検査を作り直して捨てる／層ごとに何度も走る、という形になっていない
    ことを固定する（回数リテラルは焼き込まず、要求数から導出する）。
    """
    # Arrange
    seen = _spy_verifications(monkeypatch)
    # Act
    apps = [build_sim_display_app() for _ in range(builds_requested)]
    # Assert
    assert len(apps) == builds_requested
    wrappers_per_build = len(_WRAPPERS)
    assert len(seen) - builds_requested * wrappers_per_build == 0, (
        f"生成 {builds_requested} 回に対し検査が {len(seen)} 回発行された: {seen}"
    )


@pytest.mark.parametrize("accesses_requested", [10, 100], ids=["access_10", "access_100"])
def test_属性アクセスを増やしても検査は増えない(monkeypatch, accesses_requested: int) -> None:
    """アクセス 10 回 / 100 回の 2 点で「構築後に増えた検査 = 0」（オーダーの表明）。

    検査を属性アクセスのたびに走らせる実装（毎回 hasattr を舐める等）だと、ここが
    アクセス数に比例して増える。
    """
    # Arrange
    seen = _spy_verifications(monkeypatch)
    app = build_sim_display_app()
    after_build = len(seen)
    # Act
    for _ in range(accesses_requested):
        for name in SIM_DISPLAY_SURFACE:
            getattr(app, name)
    # Assert
    assert after_build > 0, "構築時の検査が 1 回も発行されていない（Spy が空振り）"
    assert len(seen) - after_build == 0, (
        f"属性アクセス {accesses_requested * len(SIM_DISPLAY_SURFACE)} 回で"
        f" 検査が {len(seen) - after_build} 回増えた"
    )


@pytest.mark.parametrize("requests_requested", [2, 8], ids=["request_2", "request_8"])
def test_リクエストを増やしても検査は増えない(monkeypatch, tmp_path, requests_requested: int) -> None:
    """実 HTTP 2 回 / 8 回の 2 点で「構築後に増えた検査 = 0」。

    委譲面の検査は起動時の関門であって、要求ごとの費用ではない。
    """
    # Arrange
    seen = _spy_verifications(monkeypatch)
    app = build_sim_display_app(data_root=tmp_path / "data")
    after_build = len(seen)
    server = serve_sim_display.make_server(app, "127.0.0.1", None)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Act
    try:
        for i in range(requests_requested):
            path = "/settings-schema" if i % 2 == 0 else "/run-options"
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}{path}", timeout=5
                ) as response:
                    response.read()
            except urllib.error.HTTPError as exc:
                exc.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    # Assert
    assert after_build > 0, "構築時の検査が 1 回も発行されていない（Spy が空振り）"
    assert len(seen) - after_build == 0, (
        f"リクエスト {requests_requested} 回で検査が {len(seen) - after_build} 回増えた"
    )


# --------------------------------------------------------------------------------------
# 6. 機構そのものの規約
# --------------------------------------------------------------------------------------
def test_自前で持つ名を委譲面に宣言したら定義時に落ちる() -> None:
    """宣言と実体が食い違ったままどちらかが黙って勝つ状態を作らない。"""
    with pytest.raises(TypeError) as excinfo:

        @serve_sim_indicators.delegates_to_inner("controller")
        class _Conflicting:  # noqa: D401 — 変異体（定義できないことが期待）
            @property
            def controller(self):
                return None

    assert "controller" in str(excinfo.value)


def _modules_defining(function_name: str) -> "list[str]":
    """5 本のうち、その関数を**自分で定義している**モジュール名（import は数えない）。"""
    owners = []
    for module, _c, _s in _WRAPPERS:
        tree = ast.parse(
            Path(inspect.getsourcefile(module)).read_text(encoding="utf-8")
        )
        if any(
            isinstance(n, ast.FunctionDef) and n.name == function_name for n in tree.body
        ):
            owners.append(module.__name__)
    return owners


@pytest.mark.parametrize(
    "function_name", ["delegates_to_inner", "verify_delegated_surface"]
)
def test_機構の実体は1箇所にしかない(function_name: str) -> None:
    """転送の実装が 5 本へ手書き複製されていないこと（複製禁止・プロジェクト厳命）。

    __getattr__ を消した代わりに転送 property を各本へ書き写すと、5 本が同じ理由で
    食い違う元の問題に戻る。定義は 1 本だけ、他は import で使う。
    """
    owners = _modules_defining(function_name)
    assert owners == [serve_sim_indicators.__name__], (
        f"{function_name} の定義を持つモジュール: {owners}（1 本であるべき）"
    )


def test_機構の所在検出器が空振りしていない() -> None:
    """存在しない関数名では 0 本、実在する関数名では 1 本を返す（検出器の自己検定）。"""
    assert _modules_defining("this_function_does_not_exist") == []
    assert len(_modules_defining("delegates_to_inner")) == 1
