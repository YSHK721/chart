"""ジョブ結果 → 上のチャートへ重ねる成果物（adapter 層・2026-09-26 依頼者指示）。

何を書くか（job-dir へ 2 ファイル）:
    「`trade_markers.json`」: 売買マーク。形式はライブチャートの既存描画部品
        （「`TradeMarkersRenderer.load`」）がそのまま読むもので、変換は既存の
        `TradeMarkersPresenter` が唯一持つ（ここへ写さない）。
    「`chart_overlay.json`」: 売買のトリガーになる指標（EA の 「`PlotDecl`」 宣言に従う）と、
        足ごとの口座（残高・有効証拠金・DD・必要証拠金・証拠金維持率）。

時間足:
    run が読む Bar 列は marketdata の 1 分足系列であり（`simulator.main` の 「`_M1_SECONDS`」 が
    時間足台帳 「`marketdata.tf_ledger`」 の「1m」を引いて前提にしている）、チャート側の台帳コードも
    「1m」である。MT5 の Period ラベル（「`M1`」）はチャートの語彙ではないので載せない。

指標の時刻:
    ``indicators`` は run と同じ対応づけ（ISSUE-509: Bar 列へ時刻で合わせた系列）を受け取る。
    したがって系列の位置 i は ``bars[i]`` の時刻である。未成立（NaN）の位置は 「`null`」。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter
from simulator.domain.bar_time import epoch_seconds

#: 売買マークのファイル名（sim core の `/data/{job_id}/{file}` が配信する名前）。
TRADE_MARKERS_FILENAME = "trade_markers.json"
#: 指標と口座のファイル名。
CHART_OVERLAY_FILENAME = "chart_overlay.json"
#: run の Bar 列の時間足（チャート側の台帳コード）。
RUN_TIMEFRAME = "1m"


@dataclass(frozen=True)
class _MarkerSymbol:
    """`TradeMarkersPresenter` が読む銘柄の 2 属性（名前と表示桁）。"""

    name: str
    digits: int


def drawdown(equity: Sequence[float]) -> "tuple[list[float], list[float]]":
    """有効証拠金の最高値からの下落（金額, %）を足ごとに返す（1 回の走査・O(n)）。"""
    peak = -math.inf
    amount: "list[float]" = []
    percent: "list[float]" = []
    for value in equity:
        peak = max(peak, value)
        amount.append(peak - value)
        percent.append((peak - value) / peak * 100.0 if peak > 0 else 0.0)
    return amount, percent


def profit_and_loss(
    balance: Sequence[float], equity: Sequence[float], initial_deposit: float
) -> "tuple[list[float], list[float]]":
    """足ごとの損益（確定損益の累計＝残高 − 初期資金, 含み損益＝有効証拠金 − 残高）。"""
    realized = [b - initial_deposit for b in balance]
    floating = [e - b for b, e in zip(balance, equity)]
    return realized, floating


def _finite_or_none(value: Any) -> "float | None":
    number = float(value)
    return number if math.isfinite(number) else None


def write(
    job_dir: Any,
    *,
    result: Any,
    bars: Sequence[Any],
    symbol: str,
    digits: int,
    ea_name: str,
    indicators: Any,
    plots: Sequence[Any],
    account: Any,
    initial_deposit: float,
) -> "tuple[Path, Path]":
    """``job_dir`` へ 2 ファイルを書き、そのパスを返す。

    ``account``: 足ごとの口座記録（`AccountCurveRecorder` と同じ属性を持つもの）。
    ``plots``: EA が宣言した 「`PlotDecl`」 の列（宣言の無い EA は空＝指標を描かない）。
    """
    directory = Path(job_dir)
    markers_path = directory / TRADE_MARKERS_FILENAME
    TradeMarkersPresenter().present_markers(
        result,
        markers_path,
        symbol=_MarkerSymbol(name=symbol, digits=digits),
        ea_name=ea_name,
        timeframe=RUN_TIMEFRAME,
    )

    bar_times = [epoch_seconds(bar.time) for bar in bars]
    series = []
    for plot in plots:
        values = indicators.get(plot.series)
        if len(values) != len(bar_times):
            # 系列が run の Bar 列へ対応していない（ISSUE-509 の対応づけを経ていない）。
            #   時刻を推測して描くと別の足の値を見せることになるので書かない。
            raise ValueError(
                f"指標 {plot.series} の長さ {len(values)} が Bar 列 {len(bar_times)} と一致しません"
            )
        series.append(
            {
                "series": plot.series,
                "placement": plot.placement,
                "time": bar_times,
                "value": [_finite_or_none(v) for v in values],
            }
        )
    dd_amount, dd_percent = drawdown(account.equity)
    realized, floating = profit_and_loss(account.balance, account.equity, initial_deposit)
    payload = {
        "timeframe": RUN_TIMEFRAME,
        "ea_name": ea_name,
        "indicators": series,
        "account": {
            "time": list(account.times),
            "balance": list(account.balance),
            "equity": list(account.equity),
            "drawdown": dd_amount,
            "drawdown_pct": dd_percent,
            "realized_pnl": realized,
            "floating_pnl": floating,
            "margin": list(account.margin),
            "margin_level": list(account.margin_level),
        },
    }
    overlay_path = directory / CHART_OVERLAY_FILENAME
    overlay_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return markers_path, overlay_path
