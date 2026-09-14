"""DataFrame から必須列を取り出す規則（ISSUE-314）。

本モジュールは「列名の大小を問わず必要な列を取り出す」という**規則**だけを持つ
（小文字化した写像で照合し、元の列名で引く）。

時刻系列の解決規則を持たない理由（ISSUE-502 D-7 の収束・2026-09-07）:
    かつては本モジュールが「明示指定の列 > time 列 > date 列 > DatetimeIndex」の解決規則も
    持っており（ISSUE-311）、common_view の描画アダプタにある同一規則と共有層 2 所有者の
    状態が続いていた。実測すると当該規則の利用者は 5 件すべてが指標スライスの描画モジュール
    ＝チャート表示アクターであり、市場データの語彙には属していなかった。SRP に従い所有者を
    表示仕様層へ一本化し、本モジュールからは撤去した。

    向きの根拠: 逆向き（本モジュールが表示層へ委譲する）は、計算層 common に対して機械的に
    禁じられている表示層依存と同型の安定度逆転になる（common 側のパッケージ純度検定が
    「表示層への依存は安定度逆転」として遮断・ISSUE-104）。利用者側の束縛先を替えれば
    パッケージ間の依存辺は 1 本も増えない。一本化は common_view 側の単一規則検定
    （test_resolve_times_single_rule.py）が機械的に固定する。

    挙動不変の実測: 2 実装の差は小文字化の 1 箇所のみ（AST 差分）。観測できる差は非 str 列名時の
    例外型だけで、全 5 入口で非到達である（同じ marketdata の CSV ローダが既定の厳格モードで
    上流から先に AttributeError を投げる。test_csv_loader_policy.py が固定）。

本モジュールは pandas / numpy のみに依存し、描画ライブラリ・指標実装を一切知らない
（最下層＝ marketdata に置く理由）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def extract_columns(
    df: pd.DataFrame, required: "tuple[str, ...]"
) -> "tuple[np.ndarray, ...]":
    """必須列を小文字正規化して抽出する（ISSUE-314）。

    3 指標（``profit_mfi`` / ``profit_mfi_macd`` / ``profit_rmm_macd``）が 1 文字も違わない
    実装を各 src に持っていたため、規則をここへ集約した。

    Args:
        df: 入力 DataFrame（列名の大小不問）。
        required: 必須列名（小文字）。返り値の並びは本引数の順に一致する。

    Returns:
        ``required`` と同じ並びの float64 ndarray タプル。

    Raises:
        KeyError: 必須列のいずれかが欠落している場合。
    """
    lower_map = {str(c).lower(): c for c in df.columns}
    missing = [c for c in required if c not in lower_map]
    if missing:
        raise KeyError(f"必須列が欠落しています: {missing}")
    return tuple(df[lower_map[c]].to_numpy(dtype=np.float64) for c in required)
