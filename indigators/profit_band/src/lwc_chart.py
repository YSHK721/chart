"""lightweight-charts 用の出力アダプタ（matplotlib 版 ``plot.py`` と並列の描画層）。

``build_bands`` が算出した始値基準バンドを、TradingView Lightweight Charts
(`lightweight-charts-python`) のチャート上に Line シリーズとして追加する。

設計方針:
  * 本モジュールは ``lightweight_charts`` を import しない。層境界は
    ``typing.Protocol``（``@runtime_checkable``）の ``_Chart`` / ``_Line`` で宣言し
    （PORTING_GUIDE §2）、``create_line`` を備えたオブジェクト（``AbstractChart`` 系）
    であればダックタイピングで受ける。``legend`` は任意（``hasattr`` で判定するため
    Protocol には含めない）。これにより profit_band パッケージは numpy / pandas のみの
    依存を維持する。
  * ラッパーの公開 API は ``create_line`` のみで、2 線間の塗り(fill)は非対応。
    そのため MT5 の塗りバンドは上端/下端ラインで表現する:
      - ``nOH``(下) / ``pOL``(上): 塗りバンド端 → 実線
      - ``pOH``(上) / ``nOL``(下): 外側 → 点線
    パーセンタイルが外側(99)ほど不透明度を下げ、内側(51)ほど濃くする
    （``plot.py`` の ``_FILL_ALPHA`` に対応する濃淡）。

典型的な使い方:
    >>> from lightweight_charts import Chart
    >>> from src.lwc_chart import add_profit_band
    >>> chart = Chart()
    >>> chart.set(df)                  # ローソク足
    >>> add_profit_band(chart, df)     # バンドを重ねる
    >>> chart.show(block=True)
"""

from __future__ import annotations

from typing import Dict, Iterable, Optional, Protocol, Union, runtime_checkable

import pandas as pd
from common_view.lwc_adapter import SeriesLike  # noqa: E402
# ISSUE-187（案 b・2026-07-30 の実測に基づく一本化）: 参照実装である本モジュールだけが
#   `_resolve_times` をローカル定義し、DatetimeIndex 経路で `df.index.to_series()` を返して
#   **系列名を `time` に揃えない**少数派だった。差が観測可能かを先に実測した:
#     - 戻り値の消費者 21 本を全数調査 → `.name` を読む消費者は 0 件
#       （用法は dict のキー `"time"` による列名上書き・`to_numpy()`・位置スライスのみ）
#     - index.name × tz-aware / 重複 / 非単調 の全ケースで値・dtype・index が一致し、
#       `emit_line` が作る DataFrame と JS へ渡る JSON は byte 一致
#     - 例外文言をアサートするテストは全体で 0 件
#   ＝**挙動不変**。よって共有実装（多数派 20 本と同一）へ寄せ、二重実装を解消する。
from common_view.lwc_adapter import resolve_times as _resolve_times  # noqa: E402

from .bands import build_bands
from .core import PROBABILITIES

# 系統ごとの基準色・線種（plot.py の色/線種に対応）。
#   pOL/nOH: 塗りバンド端（実線・navy 系）
#   pOH    : 上側 外側点線（teal）
#   nOL    : 下側 外側点線（darkred）
_BAND_STYLE: Dict[str, Dict[str, object]] = {
    "nOH": {"base": "#1565C0", "style": "solid", "width": 1},
    "pOL": {"base": "#1565C0", "style": "solid", "width": 1},
    "pOH": {"base": "#00897B", "style": "dotted", "width": 1},
    "nOL": {"base": "#C62828", "style": "dotted", "width": 1},
}

# パーセンタイル(百分率 int) -> 不透明度。外側ほど薄く（plot.py の _FILL_ALPHA 準拠の順序）。
_ALPHA: Dict[int, float] = {51: 0.95, 80: 0.80, 85: 0.70, 90: 0.60, 95: 0.50, 98: 0.40, 99: 0.32}

# 既定の描画系統と順序（下端→上端→外側点線）。
DEFAULT_BUCKETS = ("nOH", "pOL", "pOH", "nOL")


_Line = SeriesLike  # 共有 Protocol の別名（要求は ``set`` のみ・構造的部分型）


@runtime_checkable
class _Chart(Protocol):
    def create_line(self, name: str, **kwargs) -> _Line: ...


def _percent_tag(probability: float) -> str:
    """0.95 -> '95' のように百分率表記へ変換する。"""
    return str(int(round(probability * 100)))


def _rgba(hex_color: str, alpha: float) -> str:
    """``#RRGGBB`` と不透明度から lightweight-charts 用の ``rgba(...)`` 文字列を作る。"""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def add_profit_band(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    probabilities: Iterable[float] = PROBABILITIES,
    buckets: Iterable[str] = DEFAULT_BUCKETS,
    time_column: Optional[str] = None,
    require_full: bool = True,
    legend: bool = False,
    bands: Optional[pd.DataFrame] = None,
) -> Dict[str, object]:
    """OHLC から profit_band を計算し、チャートへ Line として重ねる。

    Args:
        chart: ``create_line(name, color, style, width, price_line, price_label)``
            を備えたチャート（lightweight_charts の ``AbstractChart`` 系）。
        df: ``open/high/low/close`` 列を持つ OHLC DataFrame（列名の大小不問）。
            ``chart.set(df)`` に渡したものと同一を想定。時刻は time/date 列または
            DatetimeIndex から解決する。
        probabilities: 描画する確率の並び（既定は全 7 水準 PROBABILITIES）。
        buckets: 描画する系統（既定 nOH/pOL/pOH/nOL）。
        time_column: 時刻列名を明示する場合に指定。
        require_full: build_bands に渡す。必須バケットが空なら ValueError。
        legend: True で凡例表示（系統数が多い場合は注意）。
        bands: 事前計算済みのバンド DataFrame（``{bucket}_{percent}`` 列）。指定時は
            ``build_bands`` を呼ばず these を描画する（頑健版 ``build_robust_bands`` の
            結果を渡す用途。NaN 行は自動で除外される）。

    Returns:
        ``{"{bucket}_{percent}": Line}`` の辞書（生成した Line への参照）。

    Raises:
        KeyError: OHLC 列または時刻列が解決できない場合。
        ValueError: require_full=True で必須バケットが空の場合。
    """
    probabilities = tuple(probabilities)
    times = _resolve_times(df, time_column)
    if bands is None:
        bands = build_bands(df, probabilities=probabilities, require_full=require_full)

    lines: Dict[str, object] = {}
    for bucket in buckets:
        if bucket not in _BAND_STYLE:
            raise KeyError(f"未知の系統です: {bucket}（有効: {list(_BAND_STYLE)}）")
        st = _BAND_STYLE[bucket]
        for prob in probabilities:
            tag = _percent_tag(prob)
            col = f"{bucket}_{tag}"
            name = f"{bucket} {tag}%"
            line = chart.create_line(
                name=name,
                color=_rgba(str(st["base"]), _ALPHA[int(tag)]),
                style=str(st["style"]),
                width=int(st["width"]),
                price_line=False,
                price_label=False,
            )
            line_df = pd.DataFrame(
                {"time": times.to_numpy(), name: bands[col].to_numpy()}
            ).dropna()
            line.set(line_df)
            lines[col] = line

    if legend and hasattr(chart, "legend"):
        chart.legend(visible=True)

    return lines


def add_robust_profit_band(
    chart: _Chart,
    df: pd.DataFrame,
    *,
    probabilities: Iterable[float] = PROBABILITIES,
    buckets: Iterable[str] = DEFAULT_BUCKETS,
    normalize: str = "return",
    window: Union[str, int] = "expanding",
    atr_period: int = 14,
    min_obs: int = 30,
    time_column: Optional[str] = None,
    legend: bool = False,
) -> Dict[str, object]:
    """頑健版バンド（正規化＋因果窓）を計算してチャートへ重ねる。

    `build_robust_bands` で時変バンドを求め、`add_profit_band` で描画する薄いラッパー。
    引数は `build_robust_bands` に準じる（`normalize` / `window` / `atr_period` / `min_obs`）。
    初期の確定不能区間（NaN）は自動で除外される。

    Returns:
        ``{"{bucket}_{percent}": Line}`` の辞書。
    """
    from .robust_bands import build_robust_bands

    probabilities = tuple(probabilities)
    buckets = tuple(buckets)
    bands = build_robust_bands(
        df,
        probabilities=probabilities,
        buckets=buckets,
        normalize=normalize,
        window=window,
        atr_period=atr_period,
        min_obs=min_obs,
    )
    return add_profit_band(
        chart, df,
        probabilities=probabilities,
        buckets=buckets,
        time_column=time_column,
        legend=legend,
        bands=bands,
    )
