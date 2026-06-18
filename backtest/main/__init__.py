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

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from backtest.adapter.controller import BacktestController
from backtest.adapter.execution.tick_model import (
    EveryTickModel,
    OhlcExpandTickModel,
    OpenOnlyTickModel,
)
from backtest.adapter.indicator.madiff import madiff
from backtest.adapter.indicator.registry import PandasIndicatorRegistry
from backtest.adapter.presenter.json import JsonPresenter
from backtest.adapter.presenter.markdown import MarkdownPresenter
from backtest.adapter.repository.ohlc_csv import CsvOHLCRepository
from backtest.adapter.repository.ohlc_mt5_csv import Mt5CsvOHLCRepository
from backtest.adapter.strategy.ma_slope import MaSlope
from backtest.adapter.strategy.tc24051901 import TC24051901
from backtest.domain.exceptions import BacktestError, ConfigError, DataError
from backtest.framework.config_loader import load_config
from backtest.main.run_config import RunConfig
from backtest.usecase.compare_stats import ComparisonReport, compare_stats
from backtest.usecase.models import SymbolSpec
from backtest.usecase.run_backtest import RunBacktestInteractor, RunBacktestRequest

# TickModel ファクトリ（config_loader の Literal キーと一致・CLEAN_ARCH §6.3）
_TICK_MODELS = {
    "every_tick": EveryTickModel,
    "ohlc_expand": OhlcExpandTickModel,
    "open_only": OpenOnlyTickModel,
}
# 列挙外キー時の既定（config_loader が列挙を検証済のため通常到達しない防御的既定）
_DEFAULT_TICK_MODEL = OhlcExpandTickModel


def _make_tick_model(tick_model_key: str) -> Any:
    """決定論 config の tick_model キーから TickModelPort 実装を生成する。"""
    return _TICK_MODELS.get(tick_model_key, _DEFAULT_TICK_MODEL)()


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


def _ema_series(price: pd.Series, period: int) -> pd.Series:
    """MQL 忠実 EMA(period) を price 系列へ適用して返す（seed=price[0]）。

    madiff.py と同じ共有実装 ``exponential_ma_on_buffer``（α=2/(period+1)・index0 シード）
    を再利用する。MaSlope が indicators.get("ema") で参照する確定足 EMA を供給する。
    """
    import numpy as np

    from backtest.adapter.indicator.madiff import (  # 共有 moving_averages を sys.path 登録済
        _ma_series,
    )

    values = price.to_numpy(dtype=float)
    ema = _ma_series(values, period, "ema")
    return pd.Series(ema, index=price.index)


def _build_ma_slope_registry(df: pd.DataFrame, *, ma_period: int) -> PandasIndicatorRegistry:
    """EMA(ma_period, close) を "ema" として登録した IndicatorPort 実装を構築する。

    MaSlope は indicators.get("ema") を参照する（ma_slope.py を Read で実証）。
    """
    ema = _ema_series(df["close"], ma_period)
    return PandasIndicatorRegistry({"ema": ema})


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
    trading_start: Any = None,
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
    }
    run_config = RunConfig(determinism, strategy_params)

    # ea_name で戦略・指標・入力フォーマットを選択（config gated・既定は従来 TC 経路）。
    if ea_name == "MA_Slope_EA":
        # MA_Slope_EA は MT5 エクスポート形式（タブ区切り・<DATE>/<TIME>/<SPREAD>）を読む。
        market_data = Mt5CsvOHLCRepository()
        df = _load_mt5_dataframe(data_path)
        registry = _build_ma_slope_registry(df, ma_period=ma_period)
        strategy: Any = MaSlope()
    else:
        # 既定経路（TC24051901・comma 形式・MADiff 指標）= 従来挙動を不変に保つ。
        market_data = CsvOHLCRepository()
        df = _load_dataframe(data_path)
        registry = _build_registry(df, ma_period=ma_period, ma_method=ma_method)
        strategy = TC24051901()

    interactor = _ResultCapturingInteractor(
        strategy=strategy,
        indicators=registry,
        tick_model=_make_tick_model(determinism.tick_model),
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
        # bars は committed 公開 IF（market_data.load）で構築する。registry 用の DataFrame
        # 読みと bars 用の load が分かれる（=読み複数回）のは committed adapter/usecase の
        # IF（registry は系列・Interactor は Bar 列・controller は path 再読み）に起因する。
        # 1 回読みへの統合は committed IF 変更が要るため範囲外＝申し送り（DESIGN 申し送り）。
        bars=market_data.load(data_path, None, None),
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
