"""AppliedInstance 値オブジェクト（内部設計書 §3.1.4・申し送り点5）。

generation（再計算世代）の単調増加と accepts（現行世代の応答のみ採用）を domain の
不変ルールとして集約する。AbortController 等 HTTP/JS の都合は混入させない。

標準ライブラリのみ。`@dataclass(frozen=True)`（DTO は不変）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AppliedInstance:
    """チャート上に追加された 1 インスタンス（内部設計書 §3.1.4）。

    instance_id: "{indicatorId}#{seq}"（§5.7）。
    params: 不変化した {name:value}（dict は frozen 化のため tuple-of-pairs）。
    generation: 再計算世代（単調増加）。HTTP/JS 由来でない純粋整数。
    created_at: ISO8601 文字列（domain は時計を持たない・値で受ける）。
    """

    instance_id: str
    indicator_id: str
    variant: str
    params: tuple[tuple[str, object], ...]
    visible: bool
    generation: int
    seq: int
    created_at: str

    def next_generation(self) -> "AppliedInstance":
        """generation を +1 した新インスタンス（frozen のため複製）を返す（単調増加）。

        自身は不変（frozen）のため変更せず、generation だけを進めた複製を返す。
        """
        return replace(self, generation=self.generation + 1)

    def accepts(self, response_generation: int) -> bool:
        """応答の generation が現行と一致する時のみ描画反映を許す（§6.6 レース対策）。

        判定は等値（==）。現行 generation と完全一致した応答のみ採用し、古い応答
        （response_generation < generation）も未来の応答（>）も破棄する（§6.6・申し送り5）。
        範囲比較（>=）ではない点が本不変ルールの核心（複数連続再計算のレース対策）。
        """
        return response_generation == self.generation
