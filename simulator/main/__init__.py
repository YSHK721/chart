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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from marketdata.tf_ledger import TF_BAR_SEC
from simulator.adapter.controller import BacktestController
from simulator.adapter.execution.tick_model import (
    OhlcExpandTickModel,
    RealTickModel,
)
from simulator.adapter.execution.tick_model_registry import (
    TICK_MODEL_REGISTRY,
    consumes_market_data,
)
# A-6: 終了コード翻訳の唯一の宣言場所。main 側で表を再宣言せず読むだけにする。
from simulator.adapter.exit_codes import SUCCESS_EXIT_CODE, exit_code_for
from simulator.adapter.indicator.ema_adx_di import compute_adx_with_di
from simulator.adapter.indicator.madiff import madiff
from simulator.adapter.indicator.null_registry import NullIndicatorRegistry
from simulator.adapter.indicator.registry import PandasIndicatorRegistry
from simulator.adapter.presenter.json import JsonPresenter
from simulator.adapter.presenter.markdown import MarkdownPresenter
from simulator.adapter.repository.marketdata_source import MarketDataSourceRepository
# A-1: バー系列を消費しない modelling 用の Null 実装（`requires_market_data is False`）。
from simulator.adapter.repository.null_market_data import NullMarketDataRepository
from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository
from simulator.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
# A-3: 取得窓を全 MarketDataPort 実装へ効かせる合成デコレータ（L-2 の解消）。
from simulator.adapter.repository.windowed_market_data import WindowedMarketDataRepository
from simulator.adapter.strategy.ma_slope import MaSlope
from simulator.adapter.strategy.ma_slope_pending import MaSlopePending
from simulator.adapter.strategy.null_strategy import NullStrategy
from simulator.adapter.strategy.pro_fit_band import ProFitBand
from simulator.adapter.strategy.stop_entry_probe import StopEntryProbe
from simulator.adapter.strategy.tc24051901 import TC24051901
from simulator.adapter.strategy.weekly_vol_band import make_weekly_vol_band
from simulator.domain.bar_time import epoch_seconds
from simulator.domain.exceptions import BacktestError, DataError
from simulator.framework.config_loader import load_config
from simulator.main.run_config import RunConfig
from simulator.usecase.models import SymbolSpec
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


def _make_session_calendar(session_calendar_key: str) -> Any:
    """config.session_calendar キーから SessionCalendarPort 実装を生成する。

    "jp225" のときのみ Jp225SessionCalendar（日次プレオープン 01:01 開場・日次クローズ
    23:59 以降閉鎖。金曜固有ではなく毎日同一＝実装 `daily_close_minute=1439` の実測記述）。
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


class _ResultCapturingInteractor(RunBacktestInteractor):
    """Interactor を継承し最後の BacktestResult を保持する。

    元の役割: `controller.run()` は終了コードのみを返すため、Presenter/compare へ流す
    result を main 側で拾うための最小ラッパだった。振る舞いは親 execute と同一
    （result を控えるのみ）。

    ISSUE-398 以降: `run_backtest` は `controller.execute(request)` の**戻り値**を直接
    使うため、`last_result` を参照する本番コードは 0 件である（実測）。本ラッパは
    既存公開 API の削除が承認事項であるため**残している**（削除は別途承認）。
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


def _factory_dataless(_ctx: "_EaBuildContext") -> "tuple[Any, Any, Any]":
    """バー系列を消費しない modelling の構成（A-1・ISSUE-397）。

    `ctx.data_path` を**参照しない**（読むものが無いのが本経路の実体である）。既存の
    EA ファクトリは全て `_load_dataframe` / `_load_mt5_dataframe` で `data_path` を読むため
    （実測: `data_path=None` は `market_data.load` より前に `_factory_*` の CSV 読みで
    `DataError` になる）、データ供給の有無は **market_data 実体だけでなく本 3 点組の
    選択**で表す必要がある。返す 3 点は既存の Null 実装（Port ABC の実装＝LSP 維持）。
    """
    return NullStrategy(), NullIndicatorRegistry(), NullMarketDataRepository()


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


#: 未登録 ea_name が落ちる既定 TC 経路（`_factory_tc24051901`）の EA 名。
#:
#: 「実行可能な EA 名」は登録表のキーだけでは表せない——未登録名は既定 TC 経路へ
#: フォールバックするため、この 1 名だけが表の外側にある実行可能名である。名前を
#: 表の所有者（本モジュール）に置く理由（ISSUE-405 実測）: 従来は
#: `sim_ui/adapter/symbol_spec_catalog._DEFAULT_EA` と
#: `simulator/tests/tester_settings_engine_fixtures.DEFAULT_EA_NAME` に同じ文字列が
#: 写されており、フォールバック先を変えると 2 箇所が同時に腐る配置だった。
DEFAULT_EA_NAME = "TC24051901"


def known_ea_names() -> "tuple[str, ...]":
    """実行可能な EA 名を昇順で返す（登録表のキー＋既定 TC 経路の名前）。

    **列挙**であって選択ではない。表を引く式（`.get(ea_name, 既定)`）はここに無く、
    選択規則は従来どおり `_select_ea_factory` の 1 箇所に閉じている（AST 検定が
    両者の役割分担を固定する）。

    なぜ公開するか（ISSUE-405）: 表示スライス（`sim_ui`）の実行指示フォームは「どの EA を
    選べるか」の一覧を要る。これが無いと外側が私有名（`_EA_FACTORIES`）を越境 import して
    `set(_EA_FACTORIES) | {"TC24051901"}` という**同じ列挙を書き写す**ことになり、実際に
    そうなっていた（`symbol_spec_catalog.ea_names`）。

    `tick_model` を要求しない: 「どの EA が実行可能か」は run の modelling に依存しない。
    要求すると呼出側が値を捏造することになる。

    戻り値は決定的順（昇順・重複なし）。
    """
    return tuple(sorted(set(_EA_FACTORIES) | {DEFAULT_EA_NAME}))


def _select_ea_factory(
    ea_name: str, *, tick_model: str
) -> "Callable[[_EaBuildContext], tuple[Any, Any, Any]]":
    """(strategy, registry, market_data) を作るファクトリを選ぶ**唯一の判定点**。

    規則は 1 つだけである: **バー系列を消費しない modelling は、データを読まない構成を
    採る**。判定入力は `TickModelSpec.requires_market_data`（レジストリの宣言）であり、
    tick_model の id を列挙しない——新しい modelling が増えても本関数は改変不要
    （既定 ``requires_market_data=True`` によって従来どおり EA 表を引く）。

    `if math` を書かない理由（OCP）: 「math かどうか」は Settings 層の語彙であり
    Composition Root の関心ではない。ここで見るのは「データを消費するか」だけである。

    `_EA_FACTORIES` を**引く式は本関数にしか無い**（🔴-1）。A-1 時点では
    `build_ea_indicators` が同じ式を生で持ち、data-less 規則を知らないまま
    `_factory_tc24051901` へ落ちて `DataError`（``data_path=None`` の CSV 読み）になって
    いた。呼出側は判定入力（`tick_model` id）を渡すだけにし、規則の複製を作らない。
    式の個数は `simulator/tests/integration/test_ea_factory_selection_rule.py` が AST で
    機械的に固定する（目視規約にしない）。
    """
    if not consumes_market_data(tick_model):
        return _factory_dataless
    return _EA_FACTORIES.get(ea_name, _factory_tc24051901)


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
    を受け、`_select_ea_factory`（選択規則の唯一の判定点）へそのまま委譲する。対応表も
    選択規則もここへは書き写さない——写した規則は片方だけ改訂されて必ず食い違う。

    公開アクセサ（`build_ea_indicators` / `build_ea_strategy`）が引数の既定値と組み立てを
    **共有**するために private で切り出してある。公開側それぞれに同じ 10 個の引数と既定値を
    並べると、既定値が片方だけ改訂されて 2 つの入口が違う構成を返す（本リポジトリで
    繰り返し起きている壊れ方）。

    `config_overrides` を受ける理由（🔴-1）: 選択規則の判定入力は `tick_model` であり、
    投入仕様ではそれが `config_overrides` に載る。A-1 時点で `build_ea_indicators` は
    この引数を受けず `_EA_FACTORIES` を生で引いていたため、バー系列を消費しない modelling
    （`Math calculations`）でも `_factory_tc24051901` へ落ち、``data_path=None`` の CSV 読みで
    `DataError` になっていた（`sim_ui/main/run_job.py` の `_supply_contacts` 経由で
    report.json が生成されない run を生んでいた）。既定 ``None`` は従来の呼出と同じく
    config_loader の既定（``every_tick``＝バー系列を消費する）に落ちる。

    既定値は `build_interactor` の同名引数と同じ（指標周期を持たない仕様でも呼べる）。
    副作用は無い（`build_interactor` は 1 バイトも変えない）。データ読み込みは factory が
    行うため、run の実行とは独立に呼べる。
    """
    context = _EaBuildContext(
        data_path=data_path,
        ma_period=ma_period,
        ma_method=ma_method,
        adx_period=adx_period,
        weekly_forecast=weekly_forecast,
        weekly_p_tp=weekly_p_tp,
        weekly_capital=weekly_capital,
        weekly_f_risk=weekly_f_risk,
    )
    factory = _select_ea_factory(ea_name, tick_model=_tick_model_of(config_overrides))
    return factory(context)


def build_ea_indicators(**spec: Any) -> IndicatorPort:
    """その EA が**実行に使う指標系列**（IndicatorPort）を返す（Phase 5 R-3・追加のみ）。

    なぜ公開するか: 表示スライス（sim / report_ui）は「価格×MA の接点」のように**EA が
    見ていた系列そのもの**を要る。これが無いと外側が私有名（`_EA_FACTORIES` /
    `_EaBuildContext`）を越境 import するか、EA ごとの指標を推測で書き写すことになる。

    ``spec``: `build_interactor` と同じジョブ仕様（`**spec` で丸ごと渡せる）。引数と既定値は
    `_ea_components` が単一ソースとして持つ。

    戻り値は `IndicatorPort`（LSP）: バー系列を消費しない構成では系列を 1 本も持たない
    `NullIndicatorRegistry` を返す。系列の未登録はどちらの実装でも同じ公開エラー契約
    （`IndicatorBufferError`・context の ``available``）で呼び出し側へ届く。
    """
    _strategy, registry, _market_data = _ea_components(**spec)
    return registry


def build_ea_strategy(**spec: Any) -> Any:
    """その EA が**実行に使う戦略実体**（StrategyPort）を返す（ISSUE-405・追加のみ）。

    `build_ea_indicators` と**同じ仕様・同じ選択規則**（`_select_ea_factory` への委譲）で、
    3 点組のうち戦略だけを返す。

    なぜ公開するか（ISSUE-405 実測）: 表示スライスの受付検証（§12.8「戦略設定が SL を
    保証するか」）は「その ea_name はどの戦略クラスか」を要る。これが無いと外側が
    `getattr(simulator.main, "_EA_FACTORIES", {})` で表を覗き、`_factory_tc24051901` への
    フォールバック規則を書き写した上で、factory 関数の**ソース文字列**から戦略クラス名を
    推測することになる（実際にそうなっていた。`_factory_weekly_vol_band` はビルダ関数
    `make_weekly_vol_band(...)` を呼ぶため、その推測は WeeklyVolBand で失敗していた）。

    `tick_model` を要求しない: 呼出側の問い（「その EA はどの戦略を持つか」）は run の
    modelling に依存しない。既定 ``config_overrides=None`` で config_loader の既定
    （``every_tick``＝バー系列を消費する）に落ち、従来の表引きと同じ factory が選ばれる。
    バー系列を消費しない modelling を明示した場合だけ `NullStrategy` になる（規則は
    `_select_ea_factory` の 1 箇所のまま）。

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
    marketdata_window: Any = None,
    strategy_decorator: "Callable[[Any], Any] | None" = None,
    strategy_override: "Any | None" = None,
    position_manager: "Any | None" = None,
) -> tuple[BacktestController, RunBacktestRequest]:
    """各 Port 実装を選択・DI して controller と request を構築する（CLI から分離）。

    決定論 config は config_loader（pydantic 検証）で構築し、列挙外値は ConfigError を
    送出する（DESIGN §9.4 の exit 2 経路）。戦略パラメータは RunConfig の subscript で
    供給し、Interactor／戦略の双方の config 契約を満たす（run_config.py 参照）。
    """
    # 決定論 9 項目（config_loader の pydantic 検証経由・列挙外は ConfigError）
    determinism = load_config(config_overrides or {})
    # 🟡-1: 規則 S を**この境界**で効かせる。`to_interactor_kwargs` を通らない投入経路
    # （`POST /sim/jobs` → `run_backtest`）は `config_overrides` を素通しで渡すため、
    # そこを通ると A-1 が開いた経路が A-1 の守る不変条件（バー系列の有無と modelling の
    # 整合）の外側になっていた（実測: math + 実在 CSV で bars=0・exit=0・trades=0 と
    # 警告も拒否も無く完走した）。判定の宣言は `kwargs_mapper` の 1 箇所に置いたままで、
    # ここは呼ぶだけである（判定を二重化しない）。既存 4 モード（全て
    # `requires_market_data=True`）は `data_path` を伴うため素通りする＝byte 等価。
    #
    # 関数内 import の理由: `main.tester_settings` パッケージの `__init__` は
    # `run_from_settings` 経由で `simulator.main` を module 直下 import する
    # （run_from_settings.py:44）。ここを module 直下 import にすると
    # `simulator.main` が部分初期化のまま参照され ImportError になる（実測済み）。
    # 逆向き（`kwargs_mapper.interactor_key_sets` → `build_interactor`）でも同じ理由で
    # 関数内 import が使われており、本呼出はその既存の取り決めに合わせる。
    from simulator.main.tester_settings.kwargs_mapper import (
        verify_engine_data_consistency,
    )

    verify_engine_data_consistency(
        tick_model=determinism.tick_model, has_data=data_path is not None
    )
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
    # A-1: データ供給の要否は tick_model レジストリの宣言（requires_market_data）だけで
    # 決まる。既定 True のため既存 4 モードは従来と同じ EA ファクトリを引く（byte 等価）。
    _ea_factory = _select_ea_factory(ea_name, tick_model=determinism.tick_model)
    strategy, registry, market_data = _ea_factory(_ea_ctx)
    # Phase 6 F-8（依頼者承認済み・注入方式＝専用 param 新設）: spec 由来の汎用戦略
    # （GenericConditionStrategy）で _EA_FACTORIES が選んだ戦略を置き換える拡張点。
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
    if marketdata_window is not None:
        if isinstance(market_data, CsvOHLCRepository):
            from marketdata.csv_source import CsvCandleSource

            # C-2: 取得窓 (start,end) 半開は委譲 repo の構築時パラメータ（window）へ隔離する
            # （ISSUE-135 LSP: MarketDataPort.load の source_ref を path 系 3 実装と対称化し、
            # load_source の型別作り分けを除去）。source_ref は全実装で data_path に統一する。
            market_data = MarketDataSourceRepository(
                CsvCandleSource(data_path), window=marketdata_window
            )
        else:
            # A-3: comma 形式以外（MT5 タブ形式ほか）の MarketDataPort 実装は型で分岐せず
            # 一律に窓デコレータで包む（OCP: 実装が増えても本分岐は改変不要）。
            market_data = WindowedMarketDataRepository(market_data, window=marketdata_window)

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
        # Phase 7（依頼者承認済み・注入方式＝専用 param）: 建玉変更の適用器。既定 None は
        # 素通り＝既存挙動と byte 等価（MT5 突合の回帰ゼロ）。sim モードは run_job が spec 由来の
        # PositionManager を構築してここへ注入する（strategy_override と同型の拡張点）。
        position_manager=position_manager,
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
    # `last_result` は execute が値を返す前に例外へ抜けるので常に None だった）。
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
