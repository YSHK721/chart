"""E-2 FormingBar — 形成中バーの末尾差し込み（domain・依存ゼロ）。

フロントが足内アニメで描く最新足の暫定 OHLC を末尾へ set/replace する純関数。
proto_server._apply_forming（＝本番 forming_bar.apply_forming_bar）に bit 一致する規則:

    - forming.time == 末尾 time  → その足を暫定 OHLC で置換
    - forming.time  > 末尾 time  → 新しい足として追加
    - forming.time  < 末尾 time  → 触らない（異常時の防御）
    - forming が None/非 dict、time が欠落/不正 → 無変更
    - 列名は大小無視で照合（open/high/low/close/volume）
    - forming に存在するキーのみ更新（他フィールドは保存）

バーは plain な Mapping の昇順列。pandas/numpy を import しない。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

_FIELDS = ("open", "high", "low", "close", "volume")


def apply(
    bars: "Sequence[Mapping[str, Any]]", forming: "Mapping[str, Any] | None"
) -> "list[dict]":
    """形成中バー ``forming`` を ``bars`` 末尾へ set/replace した新しい list を返す。"""
    out = [dict(b) for b in bars]
    if not isinstance(forming, Mapping) or len(out) == 0:
        return out
    try:
        t = int(forming["time"])
    except (KeyError, TypeError, ValueError):
        return out
    if t < int(out[-1]["time"]):  # 末尾より過去 → 触らない（防御）
        return out

    # forming のキーを大小無視で引くための小文字マップ。
    lower_forming = {str(k).lower(): v for k, v in forming.items()}

    if t == int(out[-1]["time"]):  # 末尾を置換
        target = out[-1]
    else:  # t > 末尾 → 追加
        target = {"time": t}
        out.append(target)

    for key in _FIELDS:
        if key in lower_forming:
            target[key] = float(lower_forming[key])
    return out
