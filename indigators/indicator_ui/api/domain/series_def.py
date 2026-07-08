"""SeriesDef 値オブジェクトと列挙（内部設計書 §3.1.2・申し送り点3 是正の核心）。

source_column（計算結果 DataFrame の値列名）と series_name（描画系列名 = create_line
の name）を別属性として保持する。両者を 1 属性に潰すと F3 系列名照合で正常系を
誤検出するため分離する（§0.2 D-1/D-2、profit_band/src/lwc_chart.py:136-137 が根拠）。

標準ライブラリのみ。`@dataclass(frozen=True)`（DTO は不変）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SeriesKind(Enum):
    """系列種別（基本設計 §5.2：line / horizontal_line）。"""

    LINE = "line"
    HORIZONTAL_LINE = "horizontal_line"


class LineStyle(Enum):
    """線種（内部設計書 §3.1.2）。"""

    SOLID = "solid"
    DOTTED = "dotted"
    DASHED = "dashed"


@dataclass(frozen=True)
class SeriesDef:
    """出力 1 系列（または系列群）の描画宣言（内部設計書 §3.1.2）。

    source_column: 計算結果 DataFrame の値列名（例 "pOL_99"）。dynamic 時は None で
      source_column_pattern を使う。
    series_name: 描画系列名 = create_line(name=...) の値（例 "pOL 99%"）。F3 照合の基準。
    dynamic: params 依存で本数可変か。
    """

    kind: SeriesKind
    source_column: str | None
    series_name: str | None
    dynamic: bool
    source_column_pattern: str | None = None
    series_name_pattern: str | None = None
    style: LineStyle | None = None
    width: int | None = None
    color_rule: str | None = None
    price_scale_id: str | None = None
    axis_label_visible: bool = False

    def resolve_series_name(self, column: str) -> str | None:
        """値列名 column に対応する描画系列名を返す（F3 照合基準＝series_name）。

        照合は series_name のみを基準とし source_column では行わない（§3.1.2 D-1/D-2、
        §205 「source_column には一致を要求しない」）。よって現状の static 系列では
        引数 column の値に関わらず常に series_name を返す。

        引数 column は将来の dynamic 系列（source_column_pattern / series_name_pattern
        展開）で「どの展開系列に対応する系列名か」を解決する拡張点として受け取る。
        現状は最小実装（static 系列のみ・pattern 展開は未実装）であり column を消費しない。
        汎用化（pattern 展開）はスコープ外のため本メソッドでは行わない。
        """
        # static 系列: 照合基準は series_name 固定。column は dynamic 展開用の予約引数。
        del column  # 現状未使用であることを明示（pattern 展開で消費予定）。
        return self.series_name
