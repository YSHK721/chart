"""Composition Root（main 層・CLEAN_ARCH §8 / DESIGN §5.2・§9.4・§11）。

全層を結線する統合点。各 Port 実装（MarketData=Csv / Indicator=PandasIndicatorRegistry
+MADiff / Strategy=TC24051901 / TickModel=設定選択 / Presenter / ResultSink）を選択し
DI、RunBacktestInteractor を組み立てて実行、結果を Presenter/ResultSink へ流す。

公開 API:
    build_interactor(...) -> (controller, request)
        DI 構築のみを行い CLI から分離（__main__ を薄く保つ・単体テスト可能）。
    run_backtest(...) -> (exit_code, result | None)
        1 run を実行。終了コードの規約は `simulator.adapter.exit_codes` が唯一宣言し
        （A-6・DESIGN §9.4）、本モジュールは `exit_code_for` を読むだけで表を複製しない。
        result は Presenter/ResultSink へ流す（出力先指定時）。

main 層は全層を import 可。コミット済 domain/usecase/adapter/framework は変更しない。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from marketdata.tf_ledger import TF_BAR_SEC
from simulator.adapter.calendar.session_calendar import (
    Jp225SessionCalendar,
    NullCalendar,
)
from simulator.adapter.controller import BacktestController
from simulator.adapter.execution.tick_model import (
    OhlcExpandTickModel,
    RealTickModel,
)
from simulator.adapter.execution.tick_model_registry import (
    TICK_MODEL_REGISTRY,
    consumes_market_data,
)
from simulator.adapter.indicator.bar_aligned_registry import align_to_bars
from simulator.adapter.repository import ohlc_frame_cache
from simulator.main.ea_bindings.sources import row_times_for
# A-6: 終了コード翻訳の唯一の宣言場所。main 側で表を再宣言せず読むだけにする。
from simulator.adapter.exit_codes import SUCCESS_EXIT_CODE, exit_code_for
from simulator.adapter.presenter.json import JsonPresenter
from simulator.adapter.presenter.markdown import MarkdownPresenter
from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_marketdata_csv import MarketdataCsvOHLCRepository
# A-3: 取得窓を全 MarketDataPort 実装へ効かせる合成デコレータ（L-2 の解消）。
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import BacktestError, DataError
from simulator.framework.config_loader import load_config
# ISSUE-502 段階 4A: EA ごとの構築知識（registry の作り方・OHLC リーダ・戦略の生成・
#   戦略へ配るパラメータ名）は **EA 側モジュールの宣言**が持つ。Composition Root は
#   宣言から導くだけであり、EA を 1 本足すために本ファイルを開かない（OCP）。
#   `DEFAULT_EA_NAME` / `known_ea_names` は本モジュールの**公開 API の再輸出**である
#   （`from simulator.main import known_ea_names` の呼出点が本番・検定に多数ある）。
#   値と列挙規則の所有者は束縛パッケージただ 1 つであり、ここは名前を通すだけである。
from simulator.main.ea_bindings import (  # noqa: F401  (re-export: 公開 API)
    DEFAULT_EA_NAME,
    build_ea_components,
    known_ea_names,
    plot_declarations,
    spread_dependent_ea_names,
    strategy_param_names,
)
# 規則 S（バー系列の有無と tick_model の整合）の唯一の判定点。ISSUE-502 段階 3 以前は
# 子パッケージ側（main/tester_settings/kwargs_mapper.py）に在り、ここから
# **関数内 import** で呼んでいた。その形は親パッケージ（`simulator.main`）と
# 子パッケージ（`simulator.main.tester_settings`）の双方向 import＝循環であり、
# 関数内 import はそれを隠していただけだった（SOLID 精査台帳 2026-09-06 の C-2）。
# 判定を「どちらでもない第三の点」へ移したので、ここは module 直下 import で足りる
# （循環は構造ごと消えた）。
from simulator.main.engine_data_consistency import verify_engine_data_consistency
from simulator.main.run_config import RunConfig
# ISSUE-525: 保証境界のうち run 自身の引数だけで判定できる宣言と、その適用器。規則 S と
# 同じ「どちらでもない第三の点」に置いてある（親が子パッケージを読むと ISSUE-502 の C-2＝
# パッケージ間の双方向依存が復活し、`test_package_import_acyclicity.py` が落ちる）。
from simulator.main.unsupported_run_scope import (
    apply_run_scope_unsupported_rules,
    run_scope_inputs_for,
)
from simulator.usecase.entry_price_basis import declared_entry_price_basis
from simulator.usecase.pending_order_use import apply_pending_order_use
from simulator.usecase.models import AccountSpec, SymbolSpec
from simulator.usecase.ports import IndicatorPort
from simulator.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest

# TickModel の synthetic 生成・real_ticks 分岐は tick_model 単一レジストリ
# （TICK_MODEL_REGISTRY・adapter/execution/tick_model_registry.py）から導出する
# （ISSUE-097 🟡-5・従来 _TICK_MODELS dict と real_ticks 別分岐の三分散を撤廃）。
# 列挙外キー時の既定（config_loader が列挙を検証済のため通常到達しない防御的既定）。
_DEFAULT_TICK_MODEL = OhlcExpandTickModel


def _make_tick_model(tick_model_key: str, ohlc_order: str = "ohlc") -> Any:
    """決定論 config の tick_model キーから synthetic TickModelPort 実装を生成する。

    レジストリの ``synthetic_builder`` へ委譲する。ohlc_expand は ohlc_order
    （"ohlc"/"olhc"/"auto"）でバー内の極値到達順を切替える（ペンディング/SL/TP の
    同足競合の決済順を MT5 に整合）。他 synthetic モデルは ohlc_order 非対応。
    レジストリ未登録キー・synthetic_builder を持たないキー（real_ticks）は
    防御的既定 OhlcExpandTickModel()（order="ohlc"）へフォールバックする（従来と同一・
    real_ticks は build_interactor が別分岐で処理するため本関数へは到達しない）。
    """
    spec = TICK_MODEL_REGISTRY.get(tick_model_key)
    if spec is not None and spec.synthetic_builder is not None:
        return spec.synthetic_builder(ohlc_order)
    return _DEFAULT_TICK_MODEL()


# SessionCalendarPort 実装の単一レジストリ（_make_tick_model / TICK_MODEL_REGISTRY と
# 対称の形）。キーから実装を選ぶ判断は本表だけが持ち、キーの文字列を条件式で比較する
# 箇所は 0 である。**モジュール定数**なのは、呼び出しのたびに表を組み直さないため
# （`test_session_calendar_registry.py` が発行回数で固定する）。
SESSION_CALENDAR_REGISTRY: dict[str, Any] = {
    "jp225": Jp225SessionCalendar,
}
# 列挙外キー時の既定（config_loader の既定 "broker"/"none"/未知値）。常時開場＝既定経路を
# byte-identical に保つ（_DEFAULT_TICK_MODEL と同じ役割）。
_DEFAULT_SESSION_CALENDAR = NullCalendar


def _make_session_calendar(session_calendar_key: str) -> Any:
    """config.session_calendar キーから SessionCalendarPort 実装を生成する。

    "jp225" のときのみ Jp225SessionCalendar（日次プレオープン 01:01 開場・日次クローズ
    23:59 以降閉鎖。金曜固有ではなく毎日同一＝実装 `daily_close_minute=1439` の実測記述）。
    それ以外（既定 "broker"/"none"/未知値）は NullCalendar＝常時開場で既定経路を
    byte-identical に保つ（config_loader の既定 "broker" を変更しないためのフォールバック）。
    """
    factory = SESSION_CALENDAR_REGISTRY.get(
        session_calendar_key, _DEFAULT_SESSION_CALENDAR
    )
    return factory()


# 本番 tick-store のルート（実 marketdata は gitignore・大容量）。テストは
# tick_store_root を tmp_path に差し替えて小データで検証する（実データ非依存）。
_DEFAULT_TICK_STORE_ROOT = "marketdata/ticks"

# M1（1 分足）の足長秒。値を持つのは時間足台帳 `marketdata.tf_ledger` **だけ**であり、ここは
# 導出のみを行う（手書きの写しが台帳へ追随せず事故になった前例が ISSUE-261 / ISSUE-253。
# 同じ理由で台帳から導出する先例が `simulator/usecase/contact_scan/bar_window.py`）。台帳が
# ``bar_sec`` を「境界計算に使わない」と断るのは名目値を持つ上位足（1W=7日 / 1M=30日）に
# ついてであり、"1m" は再集計の原子＝定義上ちょうど 60 秒である。
_M1_SECONDS = TF_BAR_SEC["1m"]


def _bar_period(bars: Any) -> "tuple[int, int]":
    """Bar 列から実ティック読込区間 [first bar.time, last bar.time + 60s) を導く。

    tick_start/tick_end が未指定（None）のとき、対象バーを覆う半開区間を bar.time
    から導出する（M1=60s 前提）。RealTickModel が各バー区間を [bar.time, bar.time+60s)
    でスライスするため、終端は最終バーの 1 足分先まで確保する。

    事前条件: ``bars`` が 1 本以上あること（区間の両端は先頭・末尾のバーが決める）。
    事後条件: 半開区間 ``[first, last+60s)`` を **epoch 秒（int）** の対で返す。返り値の
        表現は `bar.time` の表現（epoch int / ``numpy.int64`` / ``numpy.datetime64``）に
        依存しない（ISSUE-403・`epoch_seconds` が唯一の正規化規則）。
    例外: ``DataError``（``BacktestError`` 系）。バーが 0 本のとき送出する。

    0 本を例外にする理由（ISSUE-400・症状回避ではなく事実の表明）:
        「空のバー列から期間は決まらない」は本関数が満たせない事前条件そのものであり、
        既定値の捏造（例: epoch 0 起点）でも黙認（空の tick frame で続行）でもなく、
        **翻訳される例外**として表明する。是正前はこの事実が `list[0]` の
        ``IndexError`` として漏れ、`exit_codes.exit_code_for` の翻訳表に載らなかった
        （`exit_code_for` は非 `BacktestError` を再送出する＝終了コードにならない）。
        判定を本関数に置くのは、事前条件を持つ主体がここだからである。呼出側へ移すと
        本関数は空列に対して部分関数のまま残り、別の呼出点が増えた瞬間に同じ欠陥が
        再発する。

    ``bars`` が空であること**自体**は失敗ではない（A-1・ISSUE-397）: バー系列を
    消費しない modelling（`TickModelSpec.requires_market_data is False`）は bars=[] が
    正常状態であり、`requires_real_ticks is False` のため本関数へ到達しない。本関数が
    止めるのは「実ティック区間の導出を要求されたのに導けない」場合だけである。
    """
    bar_list = list(bars)
    if not bar_list:
        raise DataError(
            "実ティック読込区間をバー列から導けません（バーが 0 本です）。"
            "tick_start/tick_end を明示するか、バーが 1 本以上得られる取得窓を指定してください。",
            context={"bar_count": 0},
        )
    first = bar_list[0].time
    last = bar_list[-1].time
    # 時刻表現ごとの手書き分岐を持たない（ISSUE-403）。正規化の規則は
    # `simulator.domain.bar_time.epoch_seconds` が唯一所有し、`load_ticks` も窓デコレータも
    # Candle 段も**同一オブジェクト**を読む。是正前はここに第 2 の規則があり、
    # ``isinstance(np.int64(1), int)`` が **False**（実測・numpy 2.4.6）であるため
    # comma 形式 CSV の実型（``numpy.int64``）が epoch 分岐を外れ、
    # ``pd.Timestamp(np.int64(1704067200))`` = ``1970-01-01 00:00:01.704067200`` へ落ちていた
    # （例外の出ない桁ずれ）。`load_ticks` は境界を同じ `epoch_seconds` で正規化するため、
    # epoch 秒（int）をそのまま渡してよい。
    return epoch_seconds(first), epoch_seconds(last) + _M1_SECONDS


def _build_real_tick_model(
    *,
    symbol: str,
    bars: Any,
    tick_store_root: Any,
    tick_start: Any,
    tick_end: Any,
) -> RealTickModel:
    """ParquetTickRepository から対象期間の tick を load し RealTickModel を構築する。

    tick_start/tick_end 未指定時は bars から [first, last+60s) を導出する。

    メモリ: 検証期間（~952k 行）は period frame をそのまま保持して可とする
    （ユーザー承認）。年規模では per-day streaming への最適化が必要。
    TODO(every-tick perf): 年規模 run では load_ticks の period frame 一括保持を
    避け、バー区間ごとの per-day ストリーミング読みへ最適化する（本 cycle は範囲外）。
    """
    # 遅延 import: 既定経路（real_ticks 以外）では tick-store 依存を持ち込まない。
    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    if tick_start is None or tick_end is None:
        tick_start, tick_end = _bar_period(bars)
    repo = ParquetTickRepository(tick_store_root)
    frame = repo.load_ticks(symbol, tick_start, tick_end)
    return RealTickModel(frame)


def _tick_model_of(config_overrides: "dict | None") -> str:
    """決定論 config から `tick_model` id を得る（`build_interactor` と同じ導出）。

    `build_interactor` は `load_config` で決定論 9 項目を組むため、そこから
    `determinism.tick_model` を読む。`build_ea_indicators` は controller を組まないが、
    既定値・列挙検証を同じ `load_config` に委ねることで、既定 `tick_model` の値を
    こちらへ書き写さずに済む（写した既定は config_loader の変更で取り残される）。
    """
    return load_config(config_overrides or {}).tick_model


def _ea_components(
    *,
    data_path: Any,
    ea_name: str,
    ma_period: int,
    ma_method: str,
    adx_period: int = 8,
    weekly_forecast: Any = None,
    weekly_p_tp: float = 0.50,
    weekly_capital: float = 0.0,
    weekly_f_risk: float = 0.01,
    config_overrides: "dict | None" = None,
    **_unused: Any,
) -> "tuple[Any, Any, Any]":
    """ジョブ仕様から `(strategy, registry, market_data)` を組む**唯一の入口**。

    `build_interactor` と同じジョブ仕様（余分なキーを含んでよい＝`**spec` で丸ごと渡せる）
    を受け、選択規則の唯一の判定点（ea_bindings の select_ea_binding）へそのまま委譲する。
    対応表も選択規則もここへは書き写さない——写した規則は片方だけ改訂されて必ず食い違う。

    公開アクセサ（`build_ea_indicators` / `build_ea_strategy`）が引数の既定値と組み立てを
    **共有**するために private で切り出してある。公開側それぞれに同じ 10 個の引数と既定値を
    並べると、既定値が片方だけ改訂されて 2 つの入口が違う構成を返す（本リポジトリで
    繰り返し起きている壊れ方）。

    `config_overrides` を受ける理由（🔴-1）: 選択規則の判定入力は `tick_model` であり、
    投入仕様ではそれが `config_overrides` に載る。A-1 時点で `build_ea_indicators` は
    この引数を受けず登録表を生で引いていたため、バー系列を消費しない modelling
    （`Math calculations`）でも既定 TC 経路へ落ち、``data_path=None`` の CSV 読みで
    `DataError` になっていた（`sim_ui/main/run_job.py` の `_supply_contacts` 経由で
    report.json が生成されない run を生んでいた）。既定 ``None`` は従来の呼出と同じく
    config_loader の既定（``every_tick``＝バー系列を消費する）に落ちる。

    明示引数は**既定値の置き場**であって EA 私有パラメータの列挙ではない（ISSUE-502
    段階 4A）。EA が読む名前は EA 側の宣言が持ち、値は下の束（``spec``）から引かれる。
    ``**_unused`` に載って来た仕様（`build_interactor` 側にしか宣言の無いパラメータ）も
    束へ合流させるため、新しい私有パラメータを使う EA が本関数を改変させない。

    既定値は `build_interactor` の同名引数と同じ（指標周期を持たない仕様でも呼べる）。
    副作用は無い（`build_interactor` は 1 バイトも変えない）。データ読み込みは束縛の
    ファクトリが行うため、run の実行とは独立に呼べる。
    """
    # 本関数の**仮引数だけ**を束として捉える（関数の最初の実行文であることが条件。
    #   後続で局所変数を作る前に取るので、ここに現れるのは仮引数と ``_unused`` である）。
    declared = dict(locals())
    extra = declared.pop("_unused")
    return build_ea_components(
        ea_name,
        tick_model=_tick_model_of(config_overrides),
        data_path=data_path,
        # 明示引数（既定値つき）が、素通しで来た同名キーより優先する（従来と同一）。
        params={**extra, **declared},
    )


def _windowed_reader(market_data: Any, data_path: Any, marketdata_window: Any) -> Any:
    """EA 束縛が選んだ読み手へ取得窓を効かせた読み手を返す（窓が無ければそのまま）。

    `build_interactor`（run）と `build_ea_indicators`（表示・トレース）が**同じ Bar 列**を
    得るための唯一の規則（ISSUE-509: 指標を足へ時刻で合わせるには、両者が同じ窓の Bar を
    見ていなければならない）。
    """
    if marketdata_window is None:
        return market_data
    if isinstance(market_data, CsvOHLCRepository):
        from marketdata.csv_source import CsvCandleSource

        # C-2: 取得窓 (start,end) 半開は委譲 repo の構築時パラメータ（window）へ隔離する
        # （ISSUE-135 LSP: MarketDataPort.load の source_ref を path 系 3 実装と対称化し、
        # load_source の型別作り分けを除去）。source_ref は全実装で data_path に統一する。
        return MarketDataSourceRepository(
            CsvCandleSource(data_path), window=marketdata_window
        )
    if isinstance(market_data, MarketdataCsvOHLCRepository):
        # marketdata 形式は**フレーム段**で窓を適用する（構築時パラメータへ隔離＝
        # CsvOHLCRepository の委譲と同じ形）。後段の窓デコレータに任せると全行の
        # Bar を作ってから捨てる（実測 4,604,080 行で構築 442.6 秒）ISSUE-450 型の
        # 浪費になる。構築数＝採用数は repository の計算量テストが固定する。
        return MarketdataCsvOHLCRepository(window=marketdata_window)
    # A-3: comma 形式以外（MT5 タブ形式ほか）の MarketDataPort 実装は型で分岐せず
    # 一律に窓デコレータで包む（OCP: 実装が増えても本分岐は改変不要）。
    return WindowedMarketDataRepository(market_data, window=marketdata_window)


def _aligned_to_run_bars(
    registry: Any, reader: Any, data_path: Any, bars: Any, *, tick_model: str
) -> Any:
    """指標の行を run の Bar 列へ**時刻で**対応させる（ISSUE-509）。

    戦略は ``iloc[bar_index]`` で指標を位置参照する。registry はデータ実体の全行（期間前の
    履歴で温まった値）から作られ、Bar 列は取得窓で絞られるため、対応させないと別の足の
    指標値を読む（実測 2026-09-26・実 UI）。対応が取れない実体は推測せず `DataError`。
    バー系列を読まない構成（読む行が無い）には対応させる相手が無い。
    """
    if bars and consumes_market_data(tick_model):
        return align_to_bars(registry, row_times_for(reader, data_path), bars)
    return registry


def known_plots(ea_name: str, *, config_overrides: "dict | None" = None) -> "tuple[Any, ...]":
    """その EA がチャートへ描くと宣言した指標（「`PlotDecl`」 の列）を返す（`known_ea_names` と同じ照会）。

    選択は ea_bindings の唯一の判定点を通す（判定入力 `tick_model` は `build_interactor` と
    同じ導出）。宣言の無い EA は空（描く系列を推測で選ばない）。
    """
    return plot_declarations(ea_name, tick_model=_tick_model_of(config_overrides))


def build_ea_indicators(**spec: Any) -> IndicatorPort:
    """その EA が**実行に使う指標系列**（IndicatorPort）を返す（Phase 5 R-3・追加のみ）。

    なぜ公開するか: 表示スライス（sim / report_ui）は「価格×MA の接点」のように**EA が
    見ていた系列そのもの**を要る。これが無いと外側が私有名（EA 束縛の登録表・構築入力型）
    を越境 import するか、EA ごとの指標を推測で書き写すことになる。

    ``spec``: `build_interactor` と同じジョブ仕様（`**spec` で丸ごと渡せる）。引数と既定値は
    `_ea_components` が単一ソースとして持つ。

    戻り値は `IndicatorPort`（LSP）: バー系列を消費しない構成では系列を 1 本も持たない
    NullIndicatorRegistry を返す。系列の未登録はどちらの実装でも同じ公開エラー契約
    （`IndicatorBufferError`・context の ``available``）で呼び出し側へ届く。
    """
    _strategy, registry, _market_data = _ea_components(**spec)
    return registry


def build_run_indicators(*, run_bars: Any = None, **spec: Any) -> IndicatorPort:
    """その run が**実際に読んだ値**の指標系列を返す（系列の位置 i ＝ run の Bar 列の i 本目）。

    `build_ea_indicators` との違い（ISP・用途で口を分ける）: あちらは「その EA が何の系列を
    登録するか」を問う口であり、系列名のカタログは Bar を組めないデータ（気配幅の列が無い
    探索用データ等）でも呼ぶ。こちらは**値を使う**口（接点・実行トレース・チャートの重ね描き）
    であり、実行（`build_interactor`）と**同じ**窓と時刻の対応づけを掛ける（ISSUE-509）。
    掛けないと、期間を指定した run で値の利用者が実行と別の足の値を読む。

    ``spec``: `build_interactor` と同じジョブ仕様。
    ``run_bars``: その run が**実行した** Bar 列（`build_interactor` の request.bars）。渡すと
        それへ対応づけ、Bar 列を組み立て直さない（ISSUE-553 項目 2: 組み立て直すと 215 万本で
        変換と時刻の対応づけがもう 1 回ずつ走る）。省略時は同じ窓で読み直す（従来どおり）。
    """
    _strategy, registry, reader = _ea_components(**spec)
    data_path = spec.get("data_path")
    tick_model = _tick_model_of(spec.get("config_overrides"))
    if data_path is None or not consumes_market_data(tick_model):
        return registry
    bars = run_bars
    if bars is None:
        bars = _windowed_reader(reader, data_path, spec.get("marketdata_window")).load(
            data_path, None, None
        )
    return _aligned_to_run_bars(registry, reader, data_path, bars, tick_model=tick_model)


def build_ea_strategy(**spec: Any) -> Any:
    """その EA が**実行に使う戦略実体**（StrategyPort）を返す（ISSUE-405・追加のみ）。

    `build_ea_indicators` と**同じ仕様・同じ選択規則**（選択の唯一の判定点への委譲）で、
    3 点組のうち戦略だけを返す。

    なぜ公開するか（ISSUE-405 実測）: 表示スライスの受付検証（§12.8「戦略設定が SL を
    保証するか」）は「その ea_name はどの戦略クラスか」を要る。これが無いと外側が
    getattr(simulator.main, "_EA_FACTORIES", {}) で表を覗き、既定 TC 経路への
    フォールバック規則を書き写した上で、factory 関数の**ソース文字列**から戦略クラス名を
    推測することになる（実際にそうなっていた。WeeklyVolBand のファクトリはビルダ関数
    `make_weekly_vol_band(...)` を呼ぶため、その推測は WeeklyVolBand で失敗していた）。

    `tick_model` を要求しない: 呼出側の問い（「その EA はどの戦略を持つか」）は run の
    modelling に依存しない。既定 ``config_overrides=None`` で config_loader の既定
    （``every_tick``＝バー系列を消費する）に落ち、従来の表引きと同じ factory が選ばれる。
    バー系列を消費しない modelling を明示した場合だけ NullStrategy になる（規則は
    選択の唯一の判定点 1 箇所のまま）。

    戻り値は `StrategyPort`（LSP）: engine が呼ぶ `on_init` / `on_new_bar` /
    `on_position_check` を持つ実体。データ読み込みは factory が行うため、``data_path`` は
    その factory が読める形式の実在ファイルである必要がある。
    """
    strategy, _registry, _market_data = _ea_components(**spec)
    return strategy


def build_interactor(
    *,
    data_path: Any,
    symbol: str,
    period: str,
    ea_name: str,
    initial_deposit: float,
    contract_size: float,
    volume_min: float,
    volume_max: float,
    volume_step: float,
    stops_level: int,
    digits: int,
    point_size: float,
    leverage: float,
    ma_period: int,
    ma_method: str,
    lot_size: float,
    stop_loss_points: float,
    take_profit_points: float,
    config_overrides: dict | None = None,
    stop_out_level: float = 0.0,
    slope_shift: int = 1,
    slope_min_points: float = 1.0,
    entry_offset_points: float = 50.0,
    entry_type: str = "limit",
    trading_start: Any = None,
    tick_store_root: Any = None,
    tick_start: Any = None,
    tick_end: Any = None,
    weekly_forecast: Any = None,
    weekly_p_tp: float = 0.50,
    weekly_capital: float = 0.0,
    weekly_f_risk: float = 0.01,
    adx_min: float = 22.0,
    adx_period: int = 8,
    # MarginProbe_EA（証拠金維持率の動作確認・2026-09-27）が読む狙いの維持率（%）。
    #   注入専用ブロック（strategy_decorator〜run_tracer）は末尾固定なので、ジョブ仕様の
    #   パラメータはその**前**へ足す（test_run_tracer_injection_path が末尾を固定する）。
    margin_level_target: float = 95.0,
    marketdata_window: Any = None,
    strategy_decorator: "Callable[[Any], Any] | None" = None,
    strategy_override: "Any | None" = None,
    position_manager: "Any | None" = None,
    run_tracer: "Any | None" = None,
) -> tuple[BacktestController, RunBacktestRequest]:
    """各 Port 実装を選択・DI して controller と request を構築する（CLI から分離）。

    決定論 config は config_loader（pydantic 検証）で構築し、列挙外値は ConfigError を
    送出する（DESIGN §9.4 の exit 2 経路）。戦略パラメータは RunConfig の subscript で
    供給し、Interactor／戦略の双方の config 契約を満たす（run_config.py 参照）。

    **公開シグネチャは事実上の HTTP スキーマ**である: `sim_ui` の受付検証
    （allowed_backtest_keys / required_backtest_keys）・Settings 写像
    （kwargs_mapper.interactor_key_sets）・.ini 入力束縛（ea_input_map）の 3 つが
    inspect.signature(build_interactor) を反射して許容キー・必須キー・型を導く。
    したがって引数名・並び・既定値の変更は投入 API の変更である。

    本体が持つのは**結線だけ**である（ISSUE-502 段階 4A）。EA ごとの知識（どの registry を
    作るか・どの OHLC リーダを使うか・どのパラメータを戦略へ配るか）は EA 側モジュールの
    宣言（`simulator/main/ea_bindings/`）が持ち、ここは宣言から導く。
    """
    # 本関数の**仮引数だけ**を 1 つの束として捉える（ここが最初の実行文であることが条件。
    #   まだ局所変数を作っていないので、`locals()` に現れるのは仮引数だけである）。
    #   束を作る理由: EA が読むパラメータ名は EA 側の宣言が持ち、値はここから名前で引く。
    #   是正前は同じ名前を本体に 3 回（戦略パラメータの dict リテラル・EA 構築入力型の
    #   8 引数・シグネチャ）書いており、EA 追加のたびに 3 箇所を同期させていた。
    #   束が仮引数と一致することは tests/integration/
    #   test_ea_bindings_are_declaration_driven.py が inspect.signature と突合して固定する
    #   （`locals()` の位置ずれを機械で赤にする）。
    job = dict(locals())
    # 決定論 9 項目（config_loader の pydantic 検証経由・列挙外は ConfigError）
    determinism = load_config(config_overrides or {})
    # 🟡-1: 規則 S を**この境界**で効かせる。`to_interactor_kwargs` を通らない投入経路
    # （`POST /sim/jobs` → `run_backtest`）は `config_overrides` を素通しで渡すため、
    # そこを通ると A-1 が開いた経路が A-1 の守る不変条件（バー系列の有無と modelling の
    # 整合）の外側になっていた（実測: math + 実在 CSV で bars=0・exit=0・trades=0 と
    # 警告も拒否も無く完走した）。判定の宣言は `engine_data_consistency` の 1 箇所に
    # 置いたままで、ここは呼ぶだけである（判定を二重化しない）。既存 4 モード（全て
    # `requires_market_data=True`）は `data_path` を伴うため素通りする＝byte 等価。
    verify_engine_data_consistency(
        tick_model=determinism.tick_model, has_data=data_path is not None
    )
    # ISSUE-525: 保証境界のうち **run 自身の引数だけで判定できるもの**を、どの投入経路も
    #   必ず通るこの合流点で適用する。是正前の適用点は写像層
    #   （`main/tester_settings/kwargs_mapper.effective_to_interactor_kwargs`）の 1 箇所だけ
    #   であり、「`settings`」 ブロックを持たない投入（`run_backtest` を直接呼ぶ経路）は保証境界の
    #   外で完走できた（実測 2026-09-25・実 UI: 同じフォームの既定値で、schema が取れれば
    #   N-17 で exit 2、取れなければ受理されて 46 秒以上走り続けた）。
    #   どの規則をここで適用するかは**人手で列挙しない**——宣言（「`UnsupportedRule.reads`」）と
    #   合流点が解決できる入力（`RUN_SCOPE_INPUTS`）の包含が決める。
    apply_run_scope_unsupported_rules(
        run_scope_inputs_for(job, tick_model_id=determinism.tick_model)
    )
    # 戦略へ配るパラメータ。**名前は EA 側の宣言が持つ**（`EaBinding.strategy_params`）。
    #   是正前はここに 14 行の dict リテラルが在り、EA が参照するパラメータを 1 つ増やす
    #   たびに本ファイルを開いていた（EA 追加の 8 編集点のうち 1 つ・OCP 違反）。配る集合は
    #   従来どおり**全 EA 宣言の和**であり（EA ごとに絞らない）、並びも従来と同じである。
    strategy_params = {name: job[name] for name in strategy_param_names()}

    # ea_name で戦略・指標・入力フォーマットを選択（config gated・既定は従来 TC 経路）。
    #   選択規則（未登録 ea_name → 既定 TC 経路 / データを消費しない modelling → 読まない
    #   構成）は ea_bindings の select_ea_binding 1 箇所が持ち、ここは呼ぶだけである。
    # A-1: データ供給の要否は tick_model レジストリの宣言（requires_market_data）だけで
    # 決まる。既定 True のため既存 4 モードは従来と同じ EA 束縛を引く（byte 等価）。
    strategy, registry, market_data = build_ea_components(
        ea_name,
        tick_model=determinism.tick_model,
        data_path=data_path,
        params=job,
    )
    # EA 束縛が形式を判定して選んだ読み手（窓で包む前）。指標の行時刻の解釈はここから引く
    #   （形式を判定し直さない・ISSUE-509）。
    reader = market_data
    # Phase 6 F-8（依頼者承認済み・注入方式＝専用 param 新設）: spec 由来の汎用戦略
    # （GenericConditionStrategy）で EA 束縛が選んだ戦略を置き換える拡張点。
    # 既定 None は素通り＝既存挙動と byte 等価（MT5 突合の回帰ゼロ）。registry・
    # market_data・tick_model の選択（ea_name＝指標セット）は override の有無で変えない。
    # 置換は strategy_decorator（sizing）適用の**前**に行う＝sizing wrap は override へ
    # 適用され合成順が両立する（指示書 §「注入方式」）。
    if strategy_override is not None:
        strategy = strategy_override
    # E-2（基本設計書 §12.4・依頼者承認済み）: 戦略を外から包む拡張点。既定 None は
    # 素通り＝既存と byte 等価（MT5 突合の回帰ゼロ）。sim モードのサイジング（F-4）は
    # ここへ SizingDecorator を差し込み、戦略 6 本と run_backtest.py を無改変に保つ。
    strategy = strategy_decorator(strategy) if strategy_decorator else strategy

    # ISSUE-533 段階 1/2: 判定の瞬間を知っているのは戦略だけなので、建値基準は戦略が名乗る。
    #   段階 2 で設定からの供給を撤去したため、ここで問うのは**宣言があるか**だけになった
    #   （「設定と宣言が食い違っていないか」は、供給が無くなった時点で原理的に起きない）。
    #   問える点がここしか無いのは、エンジンへ渡る実体が `strategy_override` と
    #   `strategy_decorator` を通った後にしか確定しないからである。宣言が無ければ run を
    #   **始めない**——エンジン側の読み取り点まで遅らせると、走り出してから落ちる。
    declared_entry_price_basis(strategy)
    # ISSUE-557: 待機注文（指値・逆指値）の使い方も戦略が名乗る。呼び出し側が渡さなくても、
    #   待機注文を出す戦略の run は足の途中で注文を評価する（是正前は sim の Settings 経路が
    #   「`pending_lifecycle`」 を渡さず、待機注文がすべて足境界の成行として約定していた）。
    #   建値基準と同じく、エンジンへ渡る実体が確定するここで 1 回だけ読む。
    determinism = apply_pending_order_use(strategy, determinism)
    run_config = RunConfig(determinism, strategy_params)

    # S5 strangler（marketdata 委譲）: marketdata_window=(start,end) 指定時、comma 形式戦略
    # （既定 TC・WeeklyVolBand＝spread 非依存・H-4）の OHLC 取得を marketdata.CandleSource へ
    # 委譲し Candle→Bar 写像する経路へ切り替える（§10.1 C-2）。registry 用 DataFrame は従来
    # どおり data_path から構築（U6 解決＝併存）。spread 依存戦略（MA_Slope/MA_Slope_Pending/
    # StopEntryProbe＝Mt5CsvOHLCRepository）は委譲対象外で委譲分岐に入らず、report.json 再現性
    # （StopEntryProbe 経路無改変）を保つ。usecase IF（RunBacktestRequest.bars）は不変。
    #
    # A-3（L-2 の解消）: 取得窓は**全 MarketDataPort 実装**で効かせる。従来は委譲分岐が真の
    # ときだけ窓が効き、Mt5CsvOHLCRepository では黙って無視されていた（実測: MA_Slope_EA +
    # JP225 M1 2025-01 で窓あり／なしの bars が同一 sha256・28097 本）。委譲経路へ寄せる案は
    # 棄却する——MarketDataSourceRepository は spread=0 固定（marketdata_source.py:51）であり
    # spread 依存戦略の約定価格式が壊れる（H-4）。代わりに WindowedMarketDataRepository で
    # 包み、窓を load の外側＝合成で適用する（各 repository と _ohlc_frame は無改変）。
    # 新しい語彙は増やさない（窓は marketdata_window 一語のまま）。既定 None は両分岐とも
    # 素通り＝既存 4 モードと byte 等価。
    market_data = _windowed_reader(market_data, data_path, marketdata_window)

    # bars は committed 公開 IF（market_data.load）で構築する。source_ref は全 MarketDataPort
    # 実装で data_path に統一する（委譲 repo は取得窓を構築時に保持し source_ref を参照しない・
    # ISSUE-135）。registry 用の DataFrame 読みと bars 用の load が分かれる（=読み複数回）のは
    # committed adapter/usecase の IF（registry は系列・Interactor は Bar 列・controller は path
    # 再読み）に起因する。1 回読みへの統合は committed IF 変更が要るため範囲外＝申し送り
    # （DESIGN 申し送り）。every-tick 経路は bars から実ティック読込区間を導出するため先に load する。
    bars = market_data.load(data_path, None, None)

    # ISSUE-509: 戦略は ``iloc[bar_index]`` で指標を位置参照するので、指標の行と Bar 列を
    #   **時刻で**対応させる。registry はデータ実体の全行（期間前の履歴で温まった値）から
    #   作られ、Bar 列は取得窓で絞られるため、対応させないと別の足の指標値を読む
    #   （実測 2026-09-26・実 UI）。対応が取れない実体は推測せず `DataError` で止める。
    #   バー系列を読まない構成（読む行が無い）には対応させる相手が無い。
    registry = _aligned_to_run_bars(
        registry, reader, data_path, bars, tick_model=determinism.tick_model
    )

    # tick_model 選択（config gated）。real_ticks（requires_real_ticks=True）のときのみ
    # ParquetTickRepository から対象期間の実ティックを load し RealTickModel に供給する
    # （every-tick #6）。それ以外（every_tick/ohlc_expand/open_only）は従来どおり合成
    # TickModel（build 不変）。real_ticks/synthetic の分岐は tick_model 単一レジストリの
    # requires_real_ticks フラグから導出する（ISSUE-097 🟡-5・従来 == "real_ticks" 直書き
    # 分岐と同一分岐先）。
    _tick_spec = TICK_MODEL_REGISTRY.get(determinism.tick_model)
    if _tick_spec is not None and _tick_spec.requires_real_ticks:
        tick_model_impl: Any = _build_real_tick_model(
            symbol=symbol,
            bars=bars,
            tick_store_root=tick_store_root or _DEFAULT_TICK_STORE_ROOT,
            tick_start=tick_start,
            tick_end=tick_end,
        )
    else:
        tick_model_impl = _make_tick_model(
            determinism.tick_model, ohlc_order=determinism.ohlc_order
        )

    # 市場開閉カレンダー（config gated・既定 broker→NullCalendar で既定経路不変）。
    session_calendar_impl = _make_session_calendar(determinism.session_calendar)

    interactor = RunBacktestInteractor(
        strategy=strategy,
        indicators=registry,
        tick_model=tick_model_impl,
        session_calendar=session_calendar_impl,
        # Phase 7（依頼者承認済み・注入方式＝専用 param）: 建玉変更の適用器。既定 None は
        # 素通り＝既存挙動と byte 等価（MT5 突合の回帰ゼロ）。sim モードは run_job が spec 由来の
        # PositionManager を構築してここへ注入する（strategy_override と同型の拡張点）。
        position_manager=position_manager,
        # ISSUE-508 段階 3（RUN_TRACE_BASIC_DESIGN §6.6.3）: 実行トレースの観測口。
        #   既定 None は素通り＝既存挙動と byte 等価（観測は実行に必要な境界ではない）。
        #   JSON スカラーでは表現できない実体なので、`position_manager` と同じく
        #   **実体で**渡す拡張点にする（投入 JSON からは渡させない＝注入専用キー集合（`simulator/sim_ui/main/composition_root_jobs.py`））。
        run_tracer=run_tracer,
    )
    controller = BacktestController(market_data=market_data, interactor=interactor)

    symbol_spec = SymbolSpec(
        contract_size=contract_size,
        volume_min=volume_min,
        volume_max=volume_max,
        volume_step=volume_step,
        stops_level=stops_level,
        digits=digits,
        point_size=point_size,
    )

    # 口座の契約（ISSUE-445 段階 3-D3・設計書 §3.4）。銘柄の契約（`symbol_spec`）と
    # 対称に、口座属性を 1 つの型へ束ねてから usecase へ渡す。**組み立てはここ
    # （Composition Root）だけ**であり、`build_interactor` の引数名・並びは不変である
    # （フラットな 3 引数を受け取り、内側の 2 軸へ写すのが本関数の責務）。
    account_spec = AccountSpec(
        initial_deposit=initial_deposit,
        leverage=leverage,
        stop_out_level=stop_out_level,
    )

    request = RunBacktestRequest(
        config=run_config,
        bars=bars,
        symbol_spec=symbol_spec,
        account=account_spec,
        # warmup/trading_start（既定 None=全バー取引＝後方互換）。warmup 込み CSV を
        # data_path に与え trading_start を指定すると、開始前のバーは指標 seed 収束のみ。
        trading_start=trading_start,
    )
    return controller, request


def _present_outputs(result: Any, output_dir: Path, *, ea_name: str, symbol: str) -> None:
    """Presenter/ResultSink へ結果を流す（stats.json / report.md を生成）。

    出力 I/O 失敗（mkdir/write の OSError 等）は内側 DataError（BacktestError 系統）へ
    翻訳し、run_backtest の終了コード翻訳（→ exit 1）に載せる。トレースバックを呼出側へ
    漏らさない（DESIGN §9.4・CLEAN_ARCH §6 外側例外の内側翻訳）。
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        # report 用メタを result に付与（Presenter が getattr で参照・CLEAN_ARCH §8 違反②解消後の表示変換）
        setattr(result, "ea_name", ea_name)
        setattr(result, "symbol", symbol)
        JsonPresenter().present_json(result, output_dir / "stats.json")
        markdown = MarkdownPresenter().present_markdown(result)
        (output_dir / "report.md").write_text(markdown, encoding="utf-8")
    except BacktestError:
        raise  # 既に内側例外（presenter/result_sink が翻訳済）はそのまま伝播
    except Exception as exc:  # mkdir/write 等の外側 I/O 例外を内側へ翻訳
        raise DataError(
            f"結果の出力に失敗しました: {output_dir}",
            context={"output_dir": str(output_dir), "cause": repr(exc)},
        ) from exc


#: 出力段の**公開名**（Phase 8 裁定 T-1・追加のみ）。`_present_outputs` と同一実体であり
#: 2 つ目の実装を作らない。公開する理由: Settings 経路の実行 facade
#: （`main/tester_settings/run_settings_job.py`）が成果物（stats.json / report.md）を
#: 書くために同じ出力段を通る必要がある。`run_backtest` を呼べば出力は得られるが、
#: その経路は Settings 由来の窓検証（N-15）と拡張注入を持たない。私有名を外から掴むのは
#: カプセル化の破れ（ISSUE-398 と同型）なので、公開名を 1 つ足す。
present_outputs = _present_outputs


# 1 run の読みはデータ実体ごとに 1 回の読みへ固定する（ISSUE-551）。追記され続ける実体
#   （MT5 常駐・export の常駐が毎分書く）を読む run で、run 内の読みごとに行数がずれない。
@ohlc_frame_cache.pinned_entities()
def run_backtest(
    *,
    output_dir: Any = None,
    **meta: Any,
) -> tuple[int, Any]:
    """1 run を実行し (exit_code, result|None) を返す。

    終了コードの規約（成功値・例外 → コードの対応・評価順）は本モジュールでは宣言せず、
    `simulator.adapter.exit_codes` が唯一宣言する（A-6）。`build_interactor` 段階
    （config_loader が `ConfigError` を送出し得る）も、実行段（`controller.execute`）も、
    出力段（`_present_outputs`）も、同一の `exit_code_for` に載せる。`BacktestError` 以外は
    捕捉せずそのまま送出する（未知の失敗を終了コードに化けさせない）。
    """
    ea_name = meta.get("ea_name", "Backtest")
    symbol = meta.get("symbol", "-")
    # ISSUE-398: `build_interactor` が組んだ request を**そのまま**実行する。
    # 従来は `controller.run(request.config, meta["data_path"], ...)` を呼んでいたため、
    #   (a) `build_interactor` が読んだ bars を捨てて同じファイルを再読込していた（二重ロード）
    #   (b) `run()` が request を組み直すため `request.trading_start` が黙って None に落ちた
    # の 2 点が生じていた。`controller.execute(request)` は検証した request をそのまま
    # 実行し結果を返すため、両方が同時に消える。終了コードの翻訳は従来どおり
    # `exit_code_for`（唯一の宣言場所）へ委譲する。
    # `build_interactor` 段と実行段は同じ翻訳・同じ戻り値（コード, None）を返すため、
    # 1 つのハンドラに畳んでも観測挙動は変わらない（実行段の失敗時、従来拾っていた
    # 結果保持用の属性は execute が値を返す前に例外へ抜けるので常に None だった）。
    # ISSUE-479 Wave2b: その結果保持ラッパ自体を削除した。結果の取り出し口は
    # `execute` の戻り値ただ 1 つであり、2 通りの経路という誤読が構造から消える。
    try:
        controller, request = build_interactor(**meta)
        result = controller.execute(request)
    except BacktestError as error:
        return exit_code_for(error), None

    exit_code = SUCCESS_EXIT_CODE
    if result is not None and output_dir is not None:
        # 出力 I/O 失敗は BacktestError へ翻訳済（_present_outputs）→ 同じ翻訳に載せる。
        try:
            _present_outputs(result, Path(output_dir), ea_name=ea_name, symbol=symbol)
        except BacktestError as error:
            return exit_code_for(error), result
    return exit_code, result
