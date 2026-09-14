"""indicator_ui / marketdata の read-only import 境界（proto_server:29-37 と同一方式）。

本モジュールは ``indicator_ui/api`` と repo 根（``marketdata`` パッケージ用）を ``sys.path`` へ
挿入し、``full_compute`` / ``latest_compute`` / ``dataset`` 等を **読むだけ**で
再利用する。cwd 非依存（絶対パス insert）にして、bash 呼出間の cwd リセットに影響されない。

既存 indicator_ui コードは無改変（import して呼ぶのみ）。

所有者について（ISSUE-479 Wave2 X-1 / Wave2b）: 本モジュールは replay_ui の adapter 配下に
あった私有モジュールからの逐語移設である。消費スライスは 3 つ（replay_ui / dashboard_ui /
sim_ui）あり、所有者がその 1 人だと他の 2 人が私有名（先頭がアンダースコアのモジュール）を
越境 import することになる。供給しているものの置き場所は供給側である。移行中は旧位置を
再公開層として残していたが、Wave2b で削除した——経路は本モジュールただ 1 本である。
移設で変えたのは根の導出（ファイル位置に応じた parents の段数）1 点のみで、それは
simulator/replay_ui/tests/unit/test_api_loader_owns_the_bridge.py が固定する。

なぜ _ensure_paths の insert を台帳（tools/dev_paths.txt）へ移さないのか:
    ここが挿すのは indicator_ui api の**汎用名**（adapter / framework / domain）を含む
    ツリーであり、replay_ui の同名群とスライス間で衝突する。台帳へ載せられるのは
    衝突しない固有名だけ（dev_paths.txt :17-19）。載せられないものを載せると、
    どちらのスライスの adapter が解決されるかが import 順で決まってしまう。

ISSUE-136（ISP）: 旧 ``load()`` は dataset・compute・MP controller を 1 つの太い namespace に
まとめて **無条件 eager import** していたため、dataset のみ使う経路（intrabar / causal_candle）まで
MP controller の import 健全性へ巻き込まれていた。粒度別アクセサ（:func:`load_dataset` /
:func:`load_compute` / :func:`load_mp_handlers`）へ分割し、各クライアントは自分が使う面のみを
import する。``load()`` は全面を束ねた後方互換 API として温存する（既存呼出元・テスト非破壊）。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

# repo 根 = indigators/indicator_ui/api_loader.py の parents[2]。
_DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]

# アクセサ種別ごとに独立キャッシュ（(api, root) キー）。種別を跨いだ instance 共有はしない。
_CACHE: "dict[tuple[str, str, str], SimpleNamespace]" = {}


def _ensure_paths(api_path: Any, repo_root: Any) -> "tuple[Path, Path]":
    """``indicator_ui/api``（＋フォールバックで repo 根・MP api）を ``sys.path`` へ挿入する（冪等）。

    ``(api, root)`` を返す。挙動は旧 ``load()`` の sys.path 準備と byte 等価（同じ探索順・同じ
    フォールバック条件）。cwd 非依存（絶対パス insert）。import 実体は各アクセサが必要分のみ行う。
    """
    root = Path(repo_root).resolve() if repo_root is not None else _DEFAULT_REPO_ROOT
    api = (
        Path(api_path).resolve()
        if api_path is not None
        else root / "indigators" / "indicator_ui" / "api"
    )

    import sys

    # MP backend は別モジュール（indigators/market_profile/api）へ切り出し済み。固有名トップパッケージ
    # ``market_profile_api`` の解決用に MP api/ も sys.path へ追加する（MP は共有インフラ
    # ``adapter.compute`` を indicator_ui の api/ 経由で参照する＝結線の一貫性）。
    mp_api = root / "indigators" / "market_profile" / "api"

    # ISSUE-087 🟡-3: 固有名（marketdata / market_profile_api）は venv の .pth（tools/
    #   install_dev_paths.py）が恒久解決する。本 loader は結線点として汎用名パッケージ
    #   ``adapter``（indicator_ui api）のみを追加する。.pth 未登録環境はフォールバックで従来どおり。
    paths = [str(api)]
    try:
        import marketdata as _md  # noqa: F401
        import market_profile_api as _mp  # noqa: F401
    except ImportError:  # フォールバック（未登録環境）。
        paths += [str(root), str(mp_api)]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    return api, root


def load_dataset(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """dataset 面のみを束ねた namespace を返す（MP controller を import しない・ISSUE-136 ISP）。

    dataset のみ使うクライアント（intrabar / causal_candle / composition の ref 検証）向け。
    dataset 実体は marketdata へ移設済み（最下層 peer 依存）。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("dataset", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    from marketdata import dataset  # noqa: E402

    ns = SimpleNamespace(dataset=dataset)
    _CACHE[key] = ns
    return ns


def load_compute(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """dataset ＋ indicator 計算面（adapter / full_compute / latest_compute）を束ねた namespace を返す。

    IndicatorComputeAdapter / full_compute / latest_compute は indicator_ui の安定公開 Facade
    ``adapter.compute``（ISSUE-092 ②）1 点から import する＝compute の内部モジュール構成へ密結合しない。
    /compute 経路（causal_compute）向け。MP controller は import しない（ISSUE-136 ISP）。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("compute", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    from marketdata import dataset  # noqa: E402
    from adapter.compute import (  # noqa: E402
        ComputeError,
        IndicatorComputeAdapter,
        catalog_param_scopes,
        full_compute,
        latest_compute,
    )
    from adapter.compute.mtf_causal import causal_mtf_series  # noqa: E402
    from adapter.compute.mtf_causal_memo import memo_for as causal_mtf_memo_for  # noqa: E402
    from adapter.compute import forming_bar as forming_bar_module  # noqa: E402

    ns = SimpleNamespace(
        dataset=dataset,
        adapter=IndicatorComputeAdapter(),
        full_compute=full_compute,
        latest_compute=latest_compute,
        # 検定エラーの型（安定 Facade `adapter.compute` の公開物）。呼び出し側が
        #   core の内部モジュールを import せずに except できるようにする（ISSUE-459）。
        compute_error=ComputeError,
        # ISSUE-466: variant ごとの受理 param 集合（`GET /catalog` の paramScopes と同一物）。
        #   受理しない param を送ると core は無言で捨てず validation エラーにする
        #   （ISSUE-278 #8）。呼び出し側が送る前に絞れるよう、単一ソースをそのまま公開する。
        catalog_param_scopes=catalog_param_scopes,
        # ISSUE-295: 上位足の因果系列はライブ core と**同一実装**を再利用する（規則を写さない）。
        #   ライブ側 compute_controller もこの関数を通る＝両モードで規約も値も同一になる。
        causal_mtf_series=causal_mtf_series,
        # ISSUE-297: バー単位の記憶もライブ core と同一実装（記憶の実体はプロセス内）。
        causal_mtf_memo_for=causal_mtf_memo_for,
        # dashboard の時間基準統一（依頼者指示 2026-08-31）: 表示遅延時点の形成中バーを
        #   in-process で組み直すための時点指定 fold（`forming_bar(ref, tf, now_unix)` /
        #   `apply_forming_bar(df, ..., now_unix)`）。live 側 controller と同じ供給元を
        #   公開するだけで、既存の属性・挙動は変えない（追加のみ）。
        forming_bar_module=forming_bar_module,
    )
    _CACHE[key] = ns
    return ns


def load_mp_handlers(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """MarketProfile controller の純ロジック（handle_market_profile{,_forming}）を束ねた namespace を返す。

    MP normal/sessions/replay/forming 経路向け。dataset / compute Facade は import しない
    （ISSUE-136 ISP: MP 経路だけが MP controller の import 健全性に依存する）。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("mp", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # MP サブバー tick 逐次成長: forming controller の純ロジックを read-only 再利用（無改変・DRY）。
    from market_profile_api.controller.market_profile_forming_controller import (  # noqa: E402
        handle_market_profile_forming,
    )
    # MP normal/sessions/replay: market_profile controller の純ロジックを read-only 再利用（無改変・DRY）。
    from market_profile_api.controller.market_profile_controller import (  # noqa: E402
        handle_market_profile,
    )

    ns = SimpleNamespace(
        handle_market_profile_forming=handle_market_profile_forming,
        handle_market_profile=handle_market_profile,
    )
    _CACHE[key] = ns
    return ns


def load_tickvol_handler(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """取引密度プロファイル controller の純ロジック（handle_tickvol_profile）を束ねて返す。

    背景色ハイライトの帯定義はライブ側 controller が単一実装であり、リプレイはそれを read-only
    再利用する（DRY・ライブと byte 一致）。dataset / compute Facade は import しない
    （ISSUE-136 ISP: 本経路だけが当該 controller の import 健全性に依存する）。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("tickvol", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    # 当該 controller は usecase.dataset_port（DIP の注入シーム）から DatasetPort を解決する。
    #   その既定結線は「各エントリポイントの責務」として framework.server / api/tests/conftest が
    #   1 回だけ行う規約であり、リプレイプロセスは第 3 のエントリポイントに当たる。ここで登録
    #   しないと未結線 RuntimeError（500 internal）になる（実測）。冪等なので重複呼出は無害。
    from adapter.gateway.composition import install_default_ports  # noqa: E402

    install_default_ports()
    from adapter.controller.tickvol_profile_controller import (  # noqa: E402
        handle_tickvol_profile,
    )

    ns = SimpleNamespace(handle_tickvol_profile=handle_tickvol_profile)
    _CACHE[key] = ns
    return ns


def load_catalog_handler(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """指標 param スキーマ controller の純ロジック（handle_catalog）を束ねて返す。

    ISSUE-278 #8: param 既定値と **variant ごとの受理 param（paramScopes）** の単一情報源は
    ライブ側 back（``call_binding._TABLE``）にあり、front はそれを ``GET /catalog`` で受け取って
    「表示するコントロール」「送信する params」を決める。standalone replay はこの経路を持たず
    （ISSUE-278 #4）、受理しない param を送って validation エラーになる。ライブ controller を
    read-only 再利用して同一応答を返す（DRY・ライブと byte 一致）。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("catalog", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    from adapter.controller.catalog_controller import handle_catalog  # noqa: E402

    ns = SimpleNamespace(handle_catalog=handle_catalog)
    _CACHE[key] = ns
    return ns


def load(api_path: Any = None, repo_root: Any = None) -> SimpleNamespace:
    """dataset ＋ compute ＋ MP handlers を束ねた後方互換 namespace を返す（結果はキャッシュ）。

    ISSUE-136 以降の推奨は粒度別アクセサ（:func:`load_dataset` / :func:`load_compute` /
    :func:`load_mp_handlers`）。本関数は全面を束ねる旧 API を温存する（既存呼出元・テスト非破壊）。
    dataset/adapter 実体は各アクセサのキャッシュを共有し、同一 instance を保つ。
    """
    api, root = _ensure_paths(api_path, repo_root)
    key = ("all", str(api), str(root))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    compute = load_compute(api_path, repo_root)
    mp = load_mp_handlers(api_path, repo_root)
    # ISSUE-131/132: resample 系（resample_ohlc/resample_ohlc_tf/TIMEFRAME_RULES/is_known_timeframe）の
    #   export は撤去（replay 側の自前足生成の全廃で利用ゼロ化。足の集合・値は dataset 経由で一元）。
    ns = SimpleNamespace(
        dataset=compute.dataset,
        adapter=compute.adapter,
        full_compute=compute.full_compute,
        latest_compute=compute.latest_compute,
        handle_market_profile_forming=mp.handle_market_profile_forming,
        handle_market_profile=mp.handle_market_profile,
    )
    _CACHE[key] = ns
    return ns
