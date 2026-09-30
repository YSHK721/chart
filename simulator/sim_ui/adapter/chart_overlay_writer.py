"""ジョブ結果 → 売買履歴チャートへ描く成果物（adapter 層・2026-09-26 依頼者指示）。

何を書くか（job-dir へ。どれも読み手が名指しで読む。誰も読まないファイルは書かない・ISSUE-554）:
    「`trade_markers.json`」: 売買マーク。形式はライブチャートの既存描画部品
        （「`TradeMarkersRenderer.load`」）がそのまま読むもので、変換は既存の
        `TradeMarkersPresenter` が唯一持つ（ここへ写さない）。
    「`chart_bars.parquet`」: 足（位置・時刻・OHLC）＋口座の列（残高・有効証拠金・DD・損益・必要証拠金・
        証拠金維持率）＋指標の列（売買のトリガーになる指標。EA の 「`PlotDecl`」 宣言に従う）
        （ISSUE-552/554 段階 2-1）。
        画面は表示する範囲だけを位置（「`bar_index`」）の区間で読む。**足はジョブの成果物の
        ここ 1 か所にだけ在る**。書き方は `simulator/adapter/trace/parquet_trace_store.py` が持つ。
    「`chart_bars.json`」: 上の成果物の宣言（時間足・EA・系列名・列名・指標と列の対応・行数）。
        足ごとの値を持たないので、大きさは足の本数で変わらない。**最後に書く**——これが在れば
        足の成果物は書き終えている。

    足ごとの値（口座・指標）を JSON 1 本で丸ごと書く成果物は無い（ISSUE-554: 実ジョブで 276MB・
    書出し 6.5 秒を書いていたが、front が足の成果物を範囲で読むようになり読み手が 0 になった）。

時間足:
    run が読む Bar 列は marketdata の 1 分足系列であり（`simulator.main` の 「`_M1_SECONDS`」 が
    時間足台帳 「`marketdata.tf_ledger`」 の「1m」を引いて前提にしている）、チャート側の台帳コードも
    「1m」である。MT5 の Period ラベル（「`M1`」）はチャートの語彙ではないので載せない。

時刻（ISSUE-552/554 段階 1・段階 2-1）:
    足の時刻の列はジョブの成果物の 1 か所（chart_bars.parquet の 「`time`」 列）にだけ持つ。
    値の列は位置 i が ``bars[i]`` の値である。
    ``indicators`` は run と同じ対応づけ（ISSUE-509: Bar 列へ時刻で合わせた系列）を受け取り、
    長さが Bar 列と違えば書かない。口座の行も Bar 列と同じ時刻の並びでなければ書かない
    （口座の行の時刻は書かないので、画面はずれを検出できない＝ここが最後の照合点）。
    未成立（NaN）の位置は値なし。

観測の境界（検査側の設計・絶対命令 2026-09-25）:
    `set_observer` は照合に使った足の時刻の列の実体を知らせる。変換が返した列の実体
    （「`simulator.usecase.bar_times.set_result_observer`」）と同一性で突き合わせ、作って使わない変換が
    無いことを検定する注入点であり、既定なし。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from simulator.adapter.presenter.trade_markers import TradeMarkersPresenter
from simulator.adapter.trace import parquet_trace_store
from simulator.usecase.bar_times import bar_epoch_seconds

#: 売買マークのファイル名（sim core の `/data/{job_id}/{file}` が配信する名前）。
TRADE_MARKERS_FILENAME = "trade_markers.json"
#: run の Bar 列の時間足（チャート側の台帳コード）。
RUN_TIMEFRAME = "1m"
#: 足の成果物（足＋口座＋指標を範囲で読める 1 本）のファイル名。
CHART_BARS_FILENAME = "chart_bars.parquet"
#: 足の成果物の宣言のファイル名。
CHART_BARS_DECLARATION_FILENAME = "chart_bars.json"
#: 範囲の指定に使う列（Bar 列の中の位置・0 始まり・連番）。
INDEX_COLUMN = "bar_index"
#: 足の列（宣言順＝成果物の列順）。時刻は epoch 秒。
BAR_COLUMNS: "tuple[str, ...]" = (INDEX_COLUMN, "time", "open", "high", "low", "close")
#: 口座の列（宣言順）。
ACCOUNT_COLUMNS: "tuple[str, ...]" = (
    "balance", "equity", "drawdown", "drawdown_pct",
    "realized_pnl", "floating_pnl", "margin", "margin_level",
)
#: 足の時刻の単位（宣言が名乗る。読み手に推測させない）。
TIME_UNIT = "epoch_seconds"

_observer: "Callable[[list[int]], None] | None" = None


def set_observer(observer: "Callable[[list[int]], None] | None") -> None:
    """照合に使った足の時刻の列の観測口を差し替える（``None`` で外す）。検定の注入点。"""
    global _observer
    _observer = observer


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
    dataset_ref: "str | None",
) -> "tuple[Path, Path]":
    """``job_dir`` へ成果物を書き、売買マークと足の成果物（chart_bars.parquet）のパスを返す。

    足の成果物とその宣言は、照合（指標の長さ・口座の行の時刻）を通った後に書く。

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

    bar_times = bar_epoch_seconds(bars)
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
                "value": [_finite_or_none(v) for v in values],
            }
        )
    if _observer is not None:
        _observer(bar_times)
    if list(account.times) != list(bar_times):
        # 口座の行が run の Bar 列と別の足に付いている。値の列だけを書くと別の足の値を見せる。
        raise ValueError(
            f"口座の行の時刻（{len(account.times)} 行）が Bar 列（{len(bar_times)} 本）の時刻と一致しません"
        )
    dd_amount, dd_percent = drawdown(account.equity)
    realized, floating = profit_and_loss(account.balance, account.equity, initial_deposit)
    account_columns = {
        "balance": list(account.balance),
        "equity": list(account.equity),
        "drawdown": dd_amount,
        "drawdown_pct": dd_percent,
        "realized_pnl": realized,
        "floating_pnl": floating,
        "margin": list(account.margin),
        "margin_level": list(account.margin_level),
    }
    write_chart_bars(
        directory, bars=bars, bar_times=bar_times, account_columns=account_columns,
        series=series, ea_name=ea_name, dataset_ref=dataset_ref,
    )
    return markers_path, directory / CHART_BARS_FILENAME


def write_chart_bars(
    directory: Path,
    *,
    bars: Sequence[Any],
    bar_times: "list[int]",
    account_columns: "dict[str, list]",
    series: "list[dict]",
    ea_name: str,
    dataset_ref: "str | None",
) -> None:
    """足の成果物（parquet 1 本）とその宣言を書く。

    事前条件: 呼び手が照合（指標の長さ・口座の行の時刻）を済ませている。ここでは照合しない
        ——列の長さが揃わなければ書き口（`parquet_trace_store.write_columns`）が落とす。
    事後条件: 列は 「`BAR_COLUMNS`」 → 「`ACCOUNT_COLUMNS`」 → 指標の順。指標の列名は位置から作る
        （系列名から作ると、足や口座の列と同じ名前の系列がその列を上書きする）。系列名との
        対応は宣言が持つ。
    受け取った列は**写さずそのまま**渡す（``bar_times`` は照合に使った実体）。
    """
    columns: "dict[str, list]" = {
        INDEX_COLUMN: list(range(len(bar_times))),
        "time": bar_times,
        "open": [bar.open for bar in bars],
        "high": [bar.high for bar in bars],
        "low": [bar.low for bar in bars],
        "close": [bar.close for bar in bars],
    }
    for name in ACCOUNT_COLUMNS:
        columns[name] = account_columns[name]
    indicators = []
    for position, entry in enumerate(series):
        column = f"indicator_{position}"
        columns[column] = entry["value"]
        indicators.append(
            {"series": entry["series"], "placement": entry["placement"], "column": column}
        )
    rows = parquet_trace_store.write_columns(directory / CHART_BARS_FILENAME, columns)
    declaration = {
        "timeframe": RUN_TIMEFRAME,
        "ea_name": ea_name,
        "dataset_ref": dataset_ref,
        "rows": rows,
        "index_column": INDEX_COLUMN,
        "time_unit": TIME_UNIT,
        "columns": list(columns),
        "indicators": indicators,
    }
    (directory / CHART_BARS_DECLARATION_FILENAME).write_text(
        json.dumps(declaration, ensure_ascii=False), encoding="utf-8"
    )
