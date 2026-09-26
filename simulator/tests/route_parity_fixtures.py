"""2 経路（`settings` あり／なし）へ**同じ実行仕様**を流すための組み立て器（テストヘルパ）。

なぜ在るか:
    ISSUE-525 の欠陥は「経路によって保証境界が効くか効かないかが変わる」ことである。
    それを測る検定は、同じ実行仕様を 2 経路へ流して結果を比べる必要がある。投入引数を
    経路ごとに手で書き並べると、経路の差ではなく**写し間違い**を測ってしまう。

    そこで投入引数の出所を 1 つにする: `.ini`（「`TesterSettings`」）→ 写像層
    （`to_interactor_kwargs`）で組んだ引数束を、そのまま現行経路（「`run_backtest`」 /
    「`build_interactor`」）へも渡す。差し替えるのは測りたい 1 つの量（EA 名・データ実体）
    だけである。

含む構造:
    SYNTHETIC_OPEN / SYNTHETIC_CLOSES : 合成足の始値と終値の並び（宣言）
    synthetic_rows                    : 合成足を ``count`` 本
    write_marketdata_csv              : marketdata 形式（気配幅あり 9 列 / なし 6 列）で書く
    run_kwargs_for                    : 現行経路（`settings` 不在）へ渡す引数束
    MA_SLOPE_EA / WEEKLY_VOL_BAND_EA  : 判定の瞬間を ``current_open`` と名乗る EA の名前
    weekly_forecast                   : `WeeklyVolBand_EA` を走らせるための予測（注入専用）

本モジュールは既定のデータ木を読み書きしない（書込先は呼出側が渡す 「``tmp_path``」 のみ）。
**テストではない**（ファイル名が test で始まらないため pytest は収集しない）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from simulator.domain.variance_forecast import VarianceForecast
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.tests.ohlc_header_fixtures import MD6, MD9
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_NAME,
    engine_binding,
    runnable_settings,
)

#: 2024-01-01T00:00:00Z。marketdata 形式の 「`date`」 列は naive 文字列で時刻系は UTC である。
EPOCH_2024_01_01: int = 1_704_067_200

#: 始値（全足で固定する）。終値だけを動かすことで「どちらの建値で約定したか」が
#: 約定価格 1 つで判別できる。
SYNTHETIC_OPEN: float = 40000.0

#: 終値の並び。既定 EA（MADiff ゼロクロス）の判定は ``MA(close) − MA(open)`` の符号反転で
#: 起きるため、上げ 2 本・下げ 2 本を繰り返し、**上げ幅と下げ幅を違える**。
SYNTHETIC_CLOSES: "tuple[float, ...]" = (
    SYNTHETIC_OPEN + 30.0,
    SYNTHETIC_OPEN + 30.0,
    SYNTHETIC_OPEN - 50.0,
    SYNTHETIC_OPEN - 50.0,
)

#: 判定の瞬間を ``current_open`` と名乗る EA（気配幅を読む側）。
MA_SLOPE_EA: str = "MA_Slope_EA"
WEEKLY_VOL_BAND_EA: str = "WeeklyVolBand_EA"

#: 「`MaSlope`」 は SL/TP を受けない（「`ConfigError`」）ため 0 を渡す。保証境界の検定が
#: 「SL/TP 未サポート」で落ちると、測りたい理由と別の理由で赤になる。
NO_SL_TP: "dict[str, float]" = {"stop_loss_points": 0.0, "take_profit_points": 0.0}


def synthetic_rows(count: int, *, spread: int = 0) -> "list[tuple]":
    """始値を固定し終値だけを動かした合成足を ``count`` 本返す。"""
    rows: "list[tuple]" = []
    for index in range(count):
        open_ = SYNTHETIC_OPEN
        close = SYNTHETIC_CLOSES[index % len(SYNTHETIC_CLOSES)]
        rows.append(
            (
                EPOCH_2024_01_01 + 60 * index,
                open_,
                max(open_, close) + 1.0,
                min(open_, close) - 1.0,
                close,
                1.0,
                spread,
            )
        )
    return rows


def write_marketdata_csv(
    path: Path, *, with_spread: bool, bars: int = 40, spread: int = 0
) -> Path:
    """気配幅の列を持つ／持たない 2 実体を**同じ足**から書き出す。

    列の有無以外は 1 バイトも変えない——測りたいのは「気配幅を供給するか」であって
    形式差ではない。ヘッダの宣言は `simulator/tests/ohlc_header_fixtures.py` が唯一源。
    """
    lines = [MD9 if with_spread else MD6]
    for time, open_, high, low, close, volume, bar_spread in synthetic_rows(
        bars, spread=spread
    ):
        stamp = datetime.fromtimestamp(time, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        values = [stamp, f"{open_}", f"{high}", f"{low}", f"{close}", f"{volume}"]
        if with_spread:
            values.extend(["0", "0", str(bar_spread)])
        lines.append(",".join(values))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def run_kwargs_for(data_path: Path, *, ea_name: str | None = None, **extra: Any) -> "dict":
    """現行経路（`settings` 不在）へ渡す引数束を組む。

    出所は settings 経路と同じ写像層である（引数を手で並べない）。``config_overrides``
    は現行経路が実際に渡すもの——データセット側の供給——に置き、写像層が権威として
    供給すると宣言している 2 項目（「``tick_model``」 と 「``stop_out_action``」）だけを
    揃える。揃えないと modelling と証拠金の分岐が経路ごとに変わり、測りたい量と別の
    理由で結果が動く。

    ``ea_name`` を差し替えられるのは、縮退した投入面がまさにそれを送るからである
    （`settings` ブロックを持たない 「`backtest`」 ブロックは EA 名を素通しする）。
    """
    settings = runnable_settings(Dates="0", Expert=f"{DEFAULT_EA_NAME}.ex5")
    binding = engine_binding(data_path=str(data_path), config_overrides=None)
    kwargs = dict(to_interactor_kwargs(settings, binding))
    supplied = kwargs["config_overrides"]
    kwargs["config_overrides"] = {
        "tick_model": supplied["tick_model"],
        "stop_out_action": supplied["stop_out_action"],
    }
    if ea_name is not None:
        kwargs["ea_name"] = ea_name
    kwargs.update(extra)
    return kwargs


def weekly_forecast() -> VarianceForecast:
    """`WeeklyVolBand_EA` が要る半実現ボラ予測（「`build_interactor`」 へ実体で注入する）。

    値は `simulator/tests/integration/test_weekly_vol_band_segments.py` と同じ水準
    （σ̂⁺=σ̂⁻=0.05）。予測が無いと戦略が ``AttributeError`` で落ち、保証境界の検定が
    測りたい理由と別の理由で赤になる。
    """
    return VarianceForecast(
        week_id="2024-W01",
        sigma_plus=0.05,
        sigma_minus=0.05,
        sigma_total_prev=0.05,
        estimable=True,
    )


def weekly_params() -> "dict[str, Any]":
    """`WeeklyVolBand_EA` を走らせる引数束（予測・利確確率・資本・リスク率）。"""
    return {
        "weekly_forecast": weekly_forecast(),
        "weekly_p_tp": 0.5,
        "weekly_capital": 1_000_000.0,
        "weekly_f_risk": 0.01,
    }
