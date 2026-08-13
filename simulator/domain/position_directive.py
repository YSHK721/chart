"""E-PositionDirective: 建玉変更指示（Value Object・CLEAN_ARCH §4 / Phase 7 FR-07/08）.

不変 DTO。PositionManagerPort.evaluate が 1 評価点で「保有玉をどう変えるか」を表す:
    new_sl       … 新しい SL 価格（トレーリング更新後）。None は据え置き。
    new_tp       … 新しい TP 価格。None は据え置き（Phase 7 では常に None＝TP 更新なし）。
    close_volume … 部分決済する数量。None は決済なし。

全フィールド None は「無変更」（:meth:`is_noop` が True）。Interactor は is_noop の
指示を適用せず、既定経路（トレーリング/部分決済なし）と byte 等価を保つ。

domain 層は外部依存ゼロ（pandas/JSON を import しない）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PositionDirective:
    new_sl: "float | None"
    new_tp: "float | None"
    close_volume: "float | None"
    # 部分決済のフィル価格（Phase 7・bar 粒度＝トリガー水準／tick 粒度＝現在価格）。
    # None は「呼出側の既定価格を用いる」。変更指示ではないため is_noop の判定には含めない。
    close_price: "float | None" = None

    def is_noop(self) -> bool:
        """全アクション項目（new_sl/new_tp/close_volume）が None（無変更）なら True。

        close_price はフィル価格のメタ情報（部分決済の付随データ）であり変更指示ではない
        ため、判定に含めない（close_volume が None なら close_price 有無に関わらず無変更）。
        """
        return self.new_sl is None and self.new_tp is None and self.close_volume is None
