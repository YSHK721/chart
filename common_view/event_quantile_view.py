"""イベント分位水準線の表示仕様（色・線種・系列名サフィックスの単一情報源）。

①層名/責務:
    表示仕様層。外れ値イベント分位（正常バンド超のイベントから求めた典型深度・極端深度）を
    チャートへ描くときの見え方——色・線種・系列名——を定義する。値そのものの算出は
    計算層（common パッケージ）が担い、本モジュールは一切計算しない。

②含む構造:
    EVQ_COLOR                : 水準線の色（外れ値系の赤）。
    EVQ_LINE_SPECS           : 系列名サフィックスと線種の対応（描画順もこの順）。
    emit_event_quantile_lines: 水準線 4 本を定型 emit する表示層ヘルパー。

③配置の理由（ISSUE-479 Wave2 C-1）:
    元は計算層に同居していたが、計算仕様（MQL 移植の数値仕様）と表示仕様（UI の視認性
    仕様）は変更を要求するアクターが異なる（SRP 違反）。計算層は表示層を import できない
    ——安定度逆転になるため純度検定が機械的に禁じている——ので、計算層側に後方互換の
    再エクスポートは置かず、参照側の import を差し替える形で移設した。値は無改変。

④依存:
    なし（描画 API にも依存しない。emit の実体は呼び出し側から注入する）。
"""

from __future__ import annotations

# 表示規約（単一情報源）: 系列名サフィックス＝評価キー、中央値＝実線・極端分位＝破線・赤系。
EVQ_COLOR: str = "rgba(210, 67, 58, 1)"    # btlm_trail _COLOR_OFFSET と同系（外れ値系の赤）
EVQ_LINE_SPECS: tuple[tuple[str, str], ...] = (
    ("med_hi", "solid"), ("med_lo", "solid"),
    ("ext_hi", "dashed"), ("ext_lo", "dashed"),
)


def emit_event_quantile_lines(prefix: str, times, evq: dict, emit_line) -> list:
    """イベント分位水準線 4 本を定型 emit する表示層ヘルパー（採用指標間の表示規約の単一情報源）。

    系列名は ``{prefix}_evq_{med|ext}_{hi|lo}``、色は ``EVQ_COLOR``、中央値＝実線・極端分位
    ＝破線（``EVQ_LINE_SPECS``）。emit の実体（chart への追加・NaN 除外）は呼び出し側の
    ``emit_line(name, times, values, color, style)`` に注入する（本モジュールは描画 API に
    依存しない）。全履歴（_all）系列は描画しない（認知負荷削減・ユーザー裁定 2026-07-21）。

    Returns:
        emit_line の戻り値のリスト（4 要素・EVQ_LINE_SPECS 順）。
    """
    return [
        emit_line(f"{prefix}_evq_{key}", times, evq[key], EVQ_COLOR, style)
        for key, style in EVQ_LINE_SPECS
    ]
