"""Composition Root（main 層・CLEAN_ARCH §8 / DESIGN §5.2・§9.4・§11）。

全層を結線する統合点。各 Port 実装（MarketData=Csv / Indicator=PandasIndicatorRegistry
+MADiff / Strategy=TC24051901 / TickModel=設定選択 / Presenter / ResultSink）を選択し
DI、RunBacktestInteractor を組み立てて実行、結果を Presenter/ResultSink へ流す。

公開 API:
    build_interactor(...) -> (controller, request)
        DI 構築のみを行い CLI から分離（__main__ を薄く保つ・単体テスト可能）。
    run_backtest(...) -> (exit_code, result | None)
        1 run を実行。終了コード（ConfigError→2 / BacktestError→1 / 成功→0）は
        BacktestController の翻訳を利用する（DESIGN §9.4）。result は Presenter/
        ResultSink へ流す（出力先指定時）。
    compare_run(result, mt5_stats, tolerances) -> ComparisonReport
        UC-003 compare_stats を BacktestStats(dataclass)→dict 変換して結線する。

main 層は全層を import 可。コミット済 domain/usecase/adapter/framework は変更しない。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from simulator.adapter.controller import BacktestController
from simulator.adapter.execution.tick_model import (
    OhlcExpandTickModel,
    RealTickModel,
)
from simulator.adapter.execution.tick_model_registry import TICK_MODEL_REGISTRY
from simulator.adapter.indicator.ema_adx_di import compute_adx_with_di
from simulator.adapter.indicator.madiff import madiff
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.presenter.json import JsonPresenter
from simulator.adapter.presenter.markdown import MarkdownPresenter
from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from simulator.adapter.strategy.ma_slope import MaSlope
from simulator.adapter.strategy.ma_slope_pending import MaSlopePending
from simulator.adapter.strategy.pro_fit_band import ProFitBand
from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.adapter.strategy.weekly_vol_band import make_weekly_vol_band
from simulator.domain.exceptions import BacktestError, ConfigError, DataError
from simulator.framework.config_loader import load_config
from simulator.main.run_config import RunConfig
from simulator.usecase.compare_stats import ComparisonReport, compare_stats
from simulator.usecase.models import SymbolSpec
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


def _make_session_calendar(session_calendar_key: str) -> Any:
    """config.session_calendar キーから SessionCalendarPort 実装を生成する。

    "jp225" のときのみ Jp225SessionCalendar（日次プレオープン 01:01・金曜 23:55 クローズ）。
    それ以外（既定 "broker"/"none"/未知値）は NullCalendar＝常時開場で既定経路を
    byte-identical に保つ（config_loader の既定 "broker" を変更しないためのフォールバック）。
    """
    from simulator.adapter.calendar.session_calendar import (
        Jp225SessionCalendar,
        NullCalendar,
    )

    if session_calendar_key == "jp225":
        return Jp225SessionCalendar()
    return NullCalendar()


# 本番 tick-store のルート（実 marketdata は gitignore・大容量）。テストは
# tick_store_root を tmp_path に差し替えて小データで検証する（実データ非依存）。
_DEFAULT_TICK_STORE_ROOT = "marketdata/ticks"


def _bar_period(bars: Any) -> "tuple[Any, Any]":
    """Bar 列から実ティック読込区間 [first bar.time, last bar.time + 60s) を導く。

    tick_start/tick_end が未指定（None）のとき、対象バーを覆う半開区間を bar.time
    から導出する（M1=60s 前提）。RealTickModel が各バー区間を [bar.time, bar.time+60s)
    でスライスするため、終端は最終バーの 1 足分先まで確保する。
    """
    bar_list = list(bars)
    first = bar_list[0].time
    last = bar_list[-1].time
    # epoch int は +60s（秒）で次足境界。それ以外（numpy.datetime64 / ISO 文字列）は
    # pandas.Timestamp へ正規化して +60s する。load_ticks（adapter）の _date_predicate は
    # start/end に year/month/day 属性（datetime/Timestamp）を要するため Timestamp で渡す
    # （pandas は composition root=main 内に閉じる。usecase へは漏らさない）。
    if isinstance(last, int) and not isinstance(last, bool):
        return first, last + 60
    start = pd.Timestamp(first)
    end = pd.Timestamp(last) + pd.Timedelta(seconds=60)
    return start, end


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


class _ResultCapturingInteractor(RunBacktestInteractor):
    """Interactor を継承し最後の BacktestResult を保持する（controller は result を返さない）。

    controller.run() は終了コードのみを返すため、Presenter/compare へ流す result を
    main 側で拾うための最小ラッパ。振る舞いは親 execute と同一（result を控えるのみ）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.last_result: Any = None

    def execute(self, request: Any) -> Any:
        result = super().execute(request)
        self.last_result = result
        return result


def _load_dataframe(data_path: Any) -> pd.DataFrame:
    """指標 registry の事前計算用に価格 CSV を DataFrame として読み込む。

    registry は系列（pandas）を要するため DataFrame が必須。一方 Interactor は Bar 列を
    消費し、controller.run は committed IF（source_ref パス）上で再度 load する。registry
    の DataFrame 需要・Interactor の Bar 需要・controller の path 再読みを 1 回の読み込みへ
    統合するには committed adapter/usecase の IF 変更が要るため範囲外＝申し送り。
    外側（pandas/OS）例外は内側 DataError へ翻訳し漏出を防ぐ（CLEAN_ARCH §6）。
    """
    try:
        return pd.read_csv(data_path)
    except Exception as exc:
        raise DataError(
            f"指標計算用 CSV の読み込みに失敗しました: {data_path}",
            context={"data_path": str(data_path), "cause": repr(exc)},
        ) from exc


def _load_mt5_dataframe(data_path: Any) -> pd.DataFrame:
    """MT5 エクスポート形式（タブ区切り）を指標 registry 用 DataFrame として読み込む。

    `<CLOSE>` 等の MT5 列名を registry/EMA 計算が参照する小文字列名（close 等）へ
    正規化する（_build_ma_slope_registry は df["close"] を参照）。外側例外は内側
    DataError へ翻訳する（CLEAN_ARCH §6）。
    """
    try:
        df = pd.read_csv(data_path, sep="\t")
    except Exception as exc:
        raise DataError(
            f"指標計算用 MT5 CSV の読み込みに失敗しました: {data_path}",
            context={"data_path": str(data_path), "cause": repr(exc)},
        ) from exc
    return df.rename(
        columns={
            "<OPEN>": "open",
            "<HIGH>": "high",
            "<LOW>": "low",
            "<CLOSE>": "close",
        }
    )


def _build_registry(df: pd.DataFrame, *, ma_period: int, ma_method: str) -> PandasIndicatorRegistry:
    """MADiff 系列と close 系列を登録した IndicatorPort 実装を構築する。

    TC24051901 は indicators.get("madiff") と indicators.get("close") を参照する
    （tc24051901.py を Read で実証）。両系列を事前計算して登録する。
    """
    madiff_series = madiff(df, period=ma_period, method=ma_method)
    return PandasIndicatorRegistry({"madiff": madiff_series, "close": df["close"]})


def ema_series(price: pd.Series, period: int) -> pd.Series:
    """MQL 忠実 EMA(period) を price 系列へ適用して返す（seed=price[0]）。

    madiff.py と同じ共有実装 ``exponential_ma_on_buffer``（α=2/(period+1)・index0 シード）
    を再利用する。MaSlope が indicators.get("ema") で参照する確定足 EMA を供給する。
    report_ui（別スライス）が EA 同一 EMA の再現に用いるため公開 API とする（ISSUE-091 #3:
    private 名の越境 import を解消）。
    """
    import numpy as np

    from simulator.adapter.indicator.madiff import (  # 共有 moving_averages を sys.path 登録済
        _ma_series,
    )

    values = price.to_numpy(dtype=float)
    ema = _ma_series(values, period, "ema")
    return pd.Series(ema, index=price.index)


_ema_series = ema_series  # 後方互換の旧名（simulator 内部の既存参照・テスト経路を温存）。


def _build_ma_slope_registry(df: pd.DataFrame, *, ma_period: int) -> PandasIndicatorRegistry:
    """EMA(ma_period, close) を "ema" として登録した IndicatorPort 実装を構築する。

    MaSlope は indicators.get("ema") を参照する（ma_slope.py を Read で実証）。
    """
    ema = _ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry({"ema": ema})


def _build_ma_slope_pending_registry(
    df: pd.DataFrame, *, ma_period: int
) -> PandasIndicatorRegistry:
    """EMA に加え当該バー始値 "open" と "spread"（ポイント）を登録した IndicatorPort。

    MaSlopePending は確定足 EMA（"ema"）でシグナルを出しつつ、ペンディング価格を当該バー
    始値クォート（bid=open / ask=open+spread×point）から算出するため "open"/"spread" 系列を
    参照する（ma_slope_pending.py を Read で実証）。spread は MT5 CSV の <SPREAD>（ポイント）。
    """
    ema = _ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            "open": df["open"].astype(float).reset_index(drop=True),
            "spread": df["<SPREAD>"].astype(float).reset_index(drop=True),
        }
    )


def _build_open_registry(df: pd.DataFrame) -> PandasIndicatorRegistry:
    """セグメント先頭 open を "open" として登録した IndicatorPort 実装を構築する。

    WeeklyVolBand は indicators.get("open").iloc[0] でセグメント先頭バー open（=O）を
    参照する（weekly_vol_band.py を Read で実証）。registry IF を満たすため open 系列の
    みを登録する（他指標は未参照）。pandas は composition root=main 内に閉じる。
    """
    return PandasIndicatorRegistry({"open": df["open"].astype(float).reset_index(drop=True)})


def _build_pro_fit_band_registry(
    df: pd.DataFrame, *, ma_period: int, adx_period: int
) -> PandasIndicatorRegistry:
    """EMA(ma_period, close)・ADX(adx_period)/+DI/−DI・close を登録した IndicatorPort。

    ProFitBand は indicators.get("ema"/"adx"/"plus_di"/"minus_di"/"close") を参照する
    （pro_fit_band.py を Read で実証）。EMA は共有 ``_ema_series``、ADX/±DI は
    ``compute_adx_with_di``（原典 iADX 再現・SPEC §3.5）で事前計算して登録する。
    close は df["close"] をそのまま登録する（TC 既定 registry と同形）。
    """
    ema = _ema_series(df["close"], ma_period)
    adx, plus_di, minus_di = compute_adx_with_di(
        df["high"], df["low"], df["close"], period=adx_period
    )
    return PandasIndicatorRegistry(
        {
            "ema": ema,
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "close": df["close"],
        }
    )


@dataclass(frozen=True)
class _EaBuildContext:
    """EA ファクトリが参照する構築入力（ISSUE-097 🟡-3）。

    build_interactor から各 EA ファクトリへ渡す構築パラメータを 1 つに束ねる。各
    ファクトリは自分が必要とするフィールドのみ参照する（未参照フィールドは無害）。
    """

    data_path: Any
    ma_period: int
    ma_method: str
    adx_period: int
    weekly_forecast: Any
    weekly_p_tp: float
    weekly_capital: float
    weekly_f_risk: float


def _factory_ma_slope(ctx: "_EaBuildContext") -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # MA_Slope_EA は MT5 エクスポート形式（タブ区切り・<DATE>/<TIME>/<SPREAD>）を読む。
    df = _load_mt5_dataframe(ctx.data_path)
    registry = _build_ma_slope_registry(df, ma_period=ctx.ma_period)
    return MaSlope(), registry, Mt5CsvOHLCRepository()


def _factory_ma_slope_pending(
    ctx: "_EaBuildContext",
) -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # 指値/逆指値版。MA_Slope_EA と同じ MT5 CSV を読み、open/spread も registry に載せる。
    df = _load_mt5_dataframe(ctx.data_path)
    registry = _build_ma_slope_pending_registry(df, ma_period=ctx.ma_period)
    return MaSlopePending(), registry, Mt5CsvOHLCRepository()


def _factory_stop_entry_probe(
    ctx: "_EaBuildContext",
) -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # 逆指値プローブ（両建て BuyStop+SellStop・OCO・足途中ティック再アーム）。MT5 CSV を読む。
    #   発注クォートは engine が on_tick へ渡すティック bid/ask を使うため指標非依存だが、
    #   registry IF を満たすため pending 用 registry（ema/open/spread）を共用する（戦略は未参照）。
    df = _load_mt5_dataframe(ctx.data_path)
    registry = _build_ma_slope_pending_registry(df, ma_period=ctx.ma_period)
    return StopEntryProbe(), registry, Mt5CsvOHLCRepository()


def _factory_weekly_vol_band(
    ctx: "_EaBuildContext",
) -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # 週次ボラ・バンド戦略（詳細設計 §5.1・§11 D1）。comma 形式 CSV を読み、セグメント
    # 先頭 open のみを "open" registry に載せる。構築は共有ファクトリへ一元化（🟡-3）。
    df = _load_dataframe(ctx.data_path)
    registry = _build_open_registry(df)
    strategy = make_weekly_vol_band(
        forecast=ctx.weekly_forecast,
        p_tp=ctx.weekly_p_tp,
        capital=ctx.weekly_capital,
        f_risk=ctx.weekly_f_risk,
    )
    return strategy, registry, CsvOHLCRepository()


def _factory_pro_fit_band(
    ctx: "_EaBuildContext",
) -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # PRO!fit_Band（#5・my_first_ea）。comma 形式 CSV を読み、EMA/ADX/±DI/close registry を
    # 供給する。従来 build_interactor に分岐が無く生成不能だった件を 1 エントリで解消（🟡-3）。
    df = _load_dataframe(ctx.data_path)
    registry = _build_pro_fit_band_registry(
        df, ma_period=ctx.ma_period, adx_period=ctx.adx_period
    )
    return ProFitBand(), registry, CsvOHLCRepository()


def _factory_tc24051901(
    ctx: "_EaBuildContext",
) -> "tuple[Any, PandasIndicatorRegistry, Any]":
    # 既定経路（TC24051901・comma 形式・MADiff 指標）= 従来挙動を不変に保つ。
    df = _load_dataframe(ctx.data_path)
    registry = _build_registry(df, ma_period=ctx.ma_period, ma_method=ctx.ma_method)
    return TC24051901(), registry, CsvOHLCRepository()


# ea_name → ファクトリの登録表（ISSUE-097 🟡-3・従来の if/elif 5 分岐を置換）。
# 各ファクトリは (strategy, registry, market_data) を返す。未登録 ea_name は
# _factory_tc24051901（既定 TC 経路）へフォールバックする（従来 else 分岐と同一）。
# 新 EA 追加は本表への 1 エントリ追加のみで済む（import 行・専用 registry ビルダの
# 追加は伴うが、分岐追記は不要）。
_EA_FACTORIES: "dict[str, Callable[[_EaBuildContext], tuple[Any, PandasIndicatorRegistry, Any]]]" = {
    "MA_Slope_EA": _factory_ma_slope,
    "MA_Slope_Pending_EA": _factory_ma_slope_pending,
    "StopEntryProbe_EA": _factory_stop_entry_probe,
    "WeeklyVolBand_EA": _factory_weekly_vol_band,
    "PRO_fit_Band_EA": _factory_pro_fit_band,
}


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
    marketdata_window: Any = None,
) -> tuple[BacktestController, RunBacktestRequest]:
    """各 Port 実装を選択・DI して controller と request を構築する（CLI から分離）。

    決定論 config は config_loader（pydantic 検証）で構築し、列挙外値は ConfigError を
    送出する（DESIGN §9.4 の exit 2 経路）。戦略パラメータは RunConfig の subscript で
    供給し、Interactor／戦略の双方の config 契約を満たす（run_config.py 参照）。
    """
    # 決定論 9 項目（config_loader の pydantic 検証経由・列挙外は ConfigError）
    determinism = load_config(config_overrides or {})
    strategy_params = {
        "lot_size": lot_size,
        "stop_loss_points": stop_loss_points,
        "take_profit_points": take_profit_points,
        "point_size": point_size,
        # MaSlope が参照する追加パラメータ（TC24051901 は未参照のため無害）。
        "slope_shift": slope_shift,
        "slope_min_points": slope_min_points,
        # MaSlopePending が参照する追加パラメータ（MaSlope/TC は未参照のため無害）。
        "digits": digits,
        "stops_level": stops_level,
        "entry_offset_points": entry_offset_points,
        "entry_type": entry_type,
        # ProFitBand が参照する追加パラメータ（他戦略は未参照のため無害）。既定は
        # 原典 .mq5 の Adx_Min=22.0（🟡-3）。
        "adx_min": adx_min,
    }
    run_config = RunConfig(determinism, strategy_params)

    # ea_name で戦略・指標・入力フォーマットを選択（config gated・既定は従来 TC 経路）。
    # ea_name → ファクトリの登録表（_EA_FACTORIES）へ委譲する（ISSUE-097 🟡-3・従来の
    # if/elif 5 分岐を置換）。ファクトリは (strategy, registry, market_data) を返す。未登録
    # ea_name は _factory_tc24051901（既定 TC 経路）へフォールバックする（従来 else と同一）。
    _ea_ctx = _EaBuildContext(
        data_path=data_path,
        ma_period=ma_period,
        ma_method=ma_method,
        adx_period=adx_period,
        weekly_forecast=weekly_forecast,
        weekly_p_tp=weekly_p_tp,
        weekly_capital=weekly_capital,
        weekly_f_risk=weekly_f_risk,
    )
    _ea_factory = _EA_FACTORIES.get(ea_name, _factory_tc24051901)
    strategy, registry, market_data = _ea_factory(_ea_ctx)

    # S5 strangler（marketdata 委譲）: marketdata_window=(start,end) 指定時、comma 形式戦略
    # （既定 TC・WeeklyVolBand＝spread 非依存・H-4）の OHLC 取得を marketdata.CandleSource へ
    # 委譲し Candle→Bar 写像する経路へ切り替える（§10.1 C-2）。registry 用 DataFrame は従来
    # どおり data_path から構築（U6 解決＝併存）。spread 依存戦略（MA_Slope/MA_Slope_Pending/
    # StopEntryProbe＝Mt5CsvOHLCRepository）は委譲対象外で本分岐に入らず、report.json 再現性
    # （StopEntryProbe 経路無改変）を保つ。usecase IF（RunBacktestRequest.bars）は不変。
    if marketdata_window is not None and isinstance(market_data, CsvOHLCRepository):
        from marketdata.csv_source import CsvCandleSource

        # C-2: 取得窓 (start,end) 半開は委譲 repo の構築時パラメータ（window）へ隔離する
        # （ISSUE-135 LSP: MarketDataPort.load の source_ref を path 系 3 実装と対称化し、
        # load_source の型別作り分けを除去）。source_ref は全実装で data_path に統一する。
        market_data = MarketDataSourceRepository(
            CsvCandleSource(data_path), window=marketdata_window
        )

    # bars は committed 公開 IF（market_data.load）で構築する。source_ref は全 MarketDataPort
    # 実装で data_path に統一する（委譲 repo は取得窓を構築時に保持し source_ref を参照しない・
    # ISSUE-135）。registry 用の DataFrame 読みと bars 用の load が分かれる（=読み複数回）のは
    # committed adapter/usecase の IF（registry は系列・Interactor は Bar 列・controller は path
    # 再読み）に起因する。1 回読みへの統合は committed IF 変更が要るため範囲外＝申し送り
    # （DESIGN 申し送り）。every-tick 経路は bars から実ティック読込区間を導出するため先に load する。
    bars = market_data.load(data_path, None, None)

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

    interactor = _ResultCapturingInteractor(
        strategy=strategy,
        indicators=registry,
        tick_model=tick_model_impl,
        session_calendar=session_calendar_impl,
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
        leverage=leverage,
    )

    request = RunBacktestRequest(
        config=run_config,
        bars=bars,
        symbol_spec=symbol_spec,
        initial_deposit=initial_deposit,
        stop_out_level=stop_out_level,
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


def run_backtest(
    *,
    output_dir: Any = None,
    **meta: Any,
) -> tuple[int, Any]:
    """1 run を実行し (exit_code, result|None) を返す。

    終了コードは BacktestController の翻訳を利用する（成功 0 / ConfigError 2 /
    BacktestError 1）。ConfigError は build_interactor（config_loader）でも送出され得る
    ため、build_interactor 段階の ConfigError/BacktestError も同じ翻訳で扱う。
    """
    ea_name = meta.get("ea_name", "Backtest")
    symbol = meta.get("symbol", "-")
    try:
        controller, request = build_interactor(**meta)
    except ConfigError:
        return 2, None
    except BacktestError:
        return 1, None

    exit_code = controller.run(
        request.config,
        meta["data_path"],
        symbol_spec=request.symbol_spec,
        initial_deposit=request.initial_deposit,
        stop_out_level=request.stop_out_level,
    )
    result = getattr(controller._interactor, "last_result", None)
    if exit_code == 0 and result is not None and output_dir is not None:
        # 出力 I/O 失敗は BacktestError へ翻訳済（_present_outputs）→ exit 1 に載せる。
        try:
            _present_outputs(result, Path(output_dir), ea_name=ea_name, symbol=symbol)
        except BacktestError:
            return 1, result
    return exit_code, result


def compare_run(result: Any, *, mt5_stats: dict, tolerances: dict) -> ComparisonReport:
    """UC-003 compare_stats を結線する（BacktestStats dataclass → dict 変換）。

    実 MT5 期待値は未入手（ISSUE-013）。自己整合 fixture で突合機構が動くことのみを固定し、
    実 MT5 突合は TBD（呼出側が mt5_stats に実 MT5 値を渡せばそのまま機能する）。
    """
    py_stats = asdict(result.stats)
    return compare_stats(py_stats=py_stats, mt5_stats=mt5_stats, tolerances=tolerances)
