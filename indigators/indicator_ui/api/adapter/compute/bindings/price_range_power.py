"""price_range_power の呼出規約フック（interval のバンド爆発対策）。

interval は**絶対価格刻み**（``price_range_power/src/core.py`` の ``bands += interval``）である。
指数等の高価格帯で catalog 既定 0.1 のままだとバンド数（価格レンジ / interval）が数十万に達し、
計算が事実上停止する。catalog/src の選択肢（``core.py`` INTERVAL_CHOICES）は変更せず
（パリティ契約を保つ）、**バンド数が上限超過の場合のみ**価格規模へ自動適応する。

本モジュールは call_binding から分離した協働子である（ISSUE-479 Wave2 I-1・SRP）。
call_binding は ``_TABLE`` の ``preprocess`` 宣言で本モジュールの ``preprocess`` を参照するだけで、
price_range_power 固有の定数（上限/目標バンド数）も丸め規則も知らない。既存の参照面
（``call_binding._nice_step`` / ``_adapt_prp_interval`` / ``_prp_preprocess``）は
call_binding 側の再エクスポートで維持する。
"""

from __future__ import annotations

import math
from typing import Any

#: バンド数（価格レンジ / interval）の上限。超えたときだけ刻みを粗くする。
MAX_BANDS = 20000
#: 適応後に狙うバンド数。
TARGET_BANDS = 3000


def nice_step(value: float) -> float:
    """1/2/5×10^n の見やすい刻みへ丸める（最低 0.1）。

    この丸め規則の実装は repo 内で本関数だけである（逐語複製は取り残しを生むため単一ソース化した。
    ``api/tests/test_call_binding_open_closed.py`` が実装数 1 件を AST 走査で固定する）。
    """
    if value <= 0:
        return 0.1
    exp = math.floor(math.log10(value))
    base = value / (10 ** exp)
    nice = 1.0 if base <= 1 else 2.0 if base <= 2 else 5.0 if base <= 5 else 10.0
    return max(round(nice * (10 ** exp), 4), 0.1)


def adapt_interval(df: Any, kw: dict[str, Any]) -> float:
    """interval を価格規模へ適応させる（爆発時のみ粗刻み化）。

    バンド数 = 価格レンジ / interval が ``MAX_BANDS`` を超える場合のみ、目標バンド数に収まる
    見やすい刻みへ置換する。低価格帯（sample 等）では元の interval をそのまま保つ。
    レンジは add_price_range_power と同じく range_from / range_to 優先・無指定時は df の low/high。

    計算量: 下限・上限の統計量は各 1 回だけ発行する（range_from / range_to が与えられた側は
    そもそも発行しない）。``api/tests/test_call_binding_complexity.py`` が固定する。
    """
    interval = kw.get("interval")
    if interval is None or interval <= 0:
        return interval  # 0/None は core 側の検証へ委ねる（挙動を変えない）。
    cols = {str(c).lower(): c for c in df.columns}
    if "low" not in cols or "high" not in cols:
        return interval
    rf, rt = kw.get("range_from"), kw.get("range_to")
    lo = float(rf) if rf is not None else float(df[cols["low"]].min())
    hi = float(rt) if rt is not None else float(df[cols["high"]].max())
    rng = hi - lo
    if rng > 0 and rng / interval > MAX_BANDS:
        return nice_step(rng / TARGET_BANDS)
    return interval


def preprocess(df: Any, kw: dict[str, Any]) -> dict[str, Any]:
    """呼出前の kw 変換フック（ISSUE-097 🟡-7 / ISSUE-098 🟡-6・LSP）。

    従来 invoke 内に直書きされていた ``if compute_id == "price_range_power" and
    "interval" in kw`` の指標名判定を _BindingSpec の宣言的フックへ昇格したもの。挙動は従来と
    同一（interval があるときのみバンド爆発を防ぐ自動適応を行い、無いときは触らない）。
    """
    if "interval" in kw:
        kw["interval"] = adapt_interval(df, kw)
    return kw
