"""common — レベルカウント系ヒストグラムの値→色（緑→赤・絶対値ベース）マッピング。

特定の指標・描画ライブラリに属さない純粋ロジック（numpy のみ）。バーの値の
**中心からの距離（絶対値＝市場の過熱度）** で色を決める。中心（穏やか）を緑、
両極端（買われすぎ＝＋大 / 売られ過ぎ＝−大 の**両方**＝過熱）を赤にする 2 色の
連続グラデーション（黒は使用しない）。matplotlib（plot.py）・lightweight-charts
（lwc_chart.py）双方から同一規則で利用する。

公開 API:
    level_colors : 値配列 → 各バーの HEX 色のリスト（緑→赤・|中心からの距離| ベース）。

典型的な使い方:
    >>> import numpy as np
    >>> from common_view import level_colors
    >>> level_colors(np.array([-3.0, 0.0, 3.0]))  # 両極=赤, 中心=緑
    ['#d32f2f', '#2e7d32', '#d32f2f']
"""

from __future__ import annotations

import numpy as np

# 緑（穏やか＝中心）→ 赤（過熱＝両極端）の端点（Material 系）。
# レベルカウントの中心からの距離が大きいほど赤（買われすぎ・売られ過ぎの両方）。
_CALM = "#2e7d32"  # 緑（中心＝穏やか）
_HOT = "#d32f2f"   # 赤（両極端＝過熱）


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    """``#rrggbb`` → (r, g, b) の 0..255 float タプル。"""
    s = h.lstrip("#")
    return (float(int(s[0:2], 16)), float(int(s[2:4], 16)), float(int(s[4:6], 16)))


def _rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    """(r, g, b)（0..255）→ ``#rrggbb``（0..255 へクリップして丸め）。"""
    r, g, b = (int(round(min(255.0, max(0.0, c)))) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def _lerp(a: tuple[float, float, float], b: tuple[float, float, float],
          f: float) -> tuple[float, float, float]:
    """色 a→b を比率 f（0..1）で線形補間する。"""
    return (a[0] + f * (b[0] - a[0]),
            a[1] + f * (b[1] - a[1]),
            a[2] + f * (b[2] - a[2]))


def level_colors(
    values: np.ndarray,
    *,
    center: float = 0.0,
    calm: str = _CALM,
    hot: str = _HOT,
) -> list[str]:
    """値配列を緑→赤（``center`` からの距離＝過熱度）の HEX 色へ写像する。

    各値 ``v`` の ``center`` からの距離 ``|v-center|`` を、全系列の最大距離
    ``vmax`` で ``[0, 1]`` に正規化する。``m = |v-center|/vmax`` として:

        m = 0（中心＝穏やか）→ 緑(``calm``)
        m = 1（両極端＝買われすぎ/売られ過ぎ＝過熱）→ 赤(``hot``)
        その間は緑→赤を ``m`` で線形補間する。

    **符号は問わない**（＋大・−大の両方が赤になる）。``vmax == 0``（全値が center）
    または ``NaN`` の要素は ``calm``（緑）にする。``NaN`` は ``vmax`` の算出からも除外する。

    Args:
        values: バー値の系列（1 次元・昇順想定だが順序非依存）。
        center: 過熱度を測る中心（既定 0.0）。
        calm: 中心（穏やか）側の HEX 色（緑）。
        hot: 両極端（過熱）側の HEX 色（赤）。

    Returns:
        ``values`` と同数の HEX 文字列リスト（各バーの色）。
    """
    v = np.asarray(values, dtype=np.float64) - center
    dist = np.abs(v)
    finite = dist[np.isfinite(dist)]
    vmax = float(np.max(finite)) if finite.size else 0.0

    c_calm = _hex_to_rgb(calm)
    c_hot = _hex_to_rgb(hot)

    colors: list[str] = []
    for d in dist:
        if not np.isfinite(d) or vmax == 0.0:
            colors.append(calm)
            continue
        m = d / vmax  # [0, 1]
        colors.append(_rgb_to_hex(_lerp(c_calm, c_hot, m)))
    return colors
