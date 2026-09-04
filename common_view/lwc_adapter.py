"""lwc_adapter — lightweight-charts 出力アダプタの共有プリミティブ（指標横断の単一実装）。

各指標パッケージの ``src/lwc_chart.py`` は「lightweight_charts を import せず duck typing で
chart を受ける」出力アダプタである（PORTING_GUIDE §2/§6）。その中で

    - :func:`resolve_times` … 時刻系列の解決順序（PORTING_GUIDE §5）
    - :func:`emit_line`     … 折れ線 1 本の生成 + NaN 除外 + ``set``
    - :class:`SeriesLike`   … 系列オブジェクトの構造的契約（``set`` のみ）

の 3 つは全パッケージで同一の規約であり、ISSUE-179（横断コピペ重複）時点で
``_resolve_times`` は 21 箇所、``_emit_line`` は 2 箇所、系列 Protocol（``_Line`` /
``_Histogram`` / ``_Series``）は 20 箇所へ複製されていた。規約の変更が最大 21 ファイルの
同時改変を要求する状態（拡張ではなく改変＝OCP 違反）を解消するため、ここへ 1 本化する。

配置（``common`` ではなく ``common_view``）:
    ``common`` は「純粋な価格計算（numpy のみ依存）」を担う層であり（``common/__init__.py``・
    ``common/marod_bands.py`` が明示）、pandas 依存の描画アダプタ規約を置くと当該層の依存契約が
    壊れる。本モジュールは表示仕様層 ``common_view``（``level_colors`` / ``LEVEL_LINE_WIDTH``）と
    同じアクター（チャート表示仕様）に属するためこちらへ置く。モジュール様式（単体ファイル・
    ``__init__`` へは再エクスポートしない・出自と依存を docstring に明記）は
    ``common/marod_bands.py`` / ``common/module_loader.py`` の慣行に合わせる。

出自と挙動不変:
    実体は ``profit_arctan/src/lwc_chart.py`` ほか 12 パッケージが持っていた ``_resolve_times``
    （``str(c).lower()`` 版・戻り値 ``pd.Series``）と、``btlm_trail_marod`` / ``ma_marod`` の
    ``_emit_line`` を **無改変で** 移設したもの。移設に伴う数値・例外挙動の変更は無い。

    なお ISSUE-179 時点で以下は本モジュールへ寄せていない（挙動差が実測されたため）:
        - ``lower_map`` を ``c.lower()`` で作る 7 パッケージ（非 str 列名で AttributeError）
        - ``list`` を返す ``moving_averages``
        - DatetimeIndex 経路の系列名・例外文言が異なる ``profit_band``
    いずれも寄せると当該パッケージの観測可能な挙動が変わるため、採否の裁定を待つ。

依存: numpy / pandas のみ（指標パッケージ・描画ライブラリへは依存しない）。
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

import numpy as np
import pandas as pd


@runtime_checkable
class SeriesLike(Protocol):
    """chart が返す系列オブジェクトの構造的契約（``set`` のみを要求する）。

    各 ``lwc_chart.py`` の ``_Line`` / ``_Histogram`` / ``_Series`` の共通部。要求メソッドが
    異なる ``_Chart`` 側（``create_line`` のみ / ``horizontal_line`` を要求 等）は各パッケージ
    固有のままとし、ここへは潰さない。
    """

    def set(self, data: pd.DataFrame) -> None: ...


@runtime_checkable
class LineChartLike(Protocol):
    """:func:`emit_line` が要求する chart の構造的契約（``create_line`` のみ）。"""

    def create_line(self, name: str, **kwargs) -> SeriesLike: ...


def resolve_times(df: pd.DataFrame, time_column: Optional[str]) -> pd.Series:
    """時刻系列を解決する（明示指定 > time 列 > date 列 > DatetimeIndex の順）。

    Args:
        df: 対象 DataFrame。
        time_column: 時刻列の明示指定（大小不問）。None なら探索する。

    Returns:
        index を 0..n-1 へ reset した datetime の ``pd.Series``。

    Raises:
        KeyError: 指定の時刻列が無い、または time/date/DatetimeIndex が解決できない場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    if time_column is not None:
        tcol = lower_map.get(time_column.lower(), time_column)
        if tcol not in df.columns:
            raise KeyError(f"指定された時刻列が存在しません: {time_column}")
        return pd.to_datetime(df[tcol]).reset_index(drop=True)
    if "time" in lower_map:
        return pd.to_datetime(df[lower_map["time"]]).reset_index(drop=True)
    if "date" in lower_map:
        return pd.to_datetime(df[lower_map["date"]]).reset_index(drop=True)
    if isinstance(df.index, pd.DatetimeIndex):
        return pd.Series(df.index, name="time").reset_index(drop=True)
    raise KeyError("時刻を解決できません（time/date 列、または DatetimeIndex が必要）。")


def emit_line(
    chart: LineChartLike,
    name: str,
    times: pd.Series,
    values,
    color: str,
    style: str,
) -> object:
    """chart に折れ線 1 本を追加し、NaN 行を除外した系列を ``set`` する。

    値列名は系列名と完全一致させる（PORTING_GUIDE §5）。

    Returns:
        生成した系列オブジェクト。
    """
    line = chart.create_line(
        name=name, color=color, style=style, width=1, price_line=False, price_label=False
    )
    series = pd.DataFrame({"time": times, name: np.asarray(values, dtype=float)}).dropna()
    line.set(series)
    return line


__all__ = ["SeriesLike", "LineChartLike", "resolve_times", "emit_line"]
