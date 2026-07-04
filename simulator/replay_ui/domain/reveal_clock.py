"""E-1 RevealClock — 因果リビール時計（domain・依存ゼロ）。

再生の「その時点」``until_t`` までのバーのみを残す純関数。未来リーク（時刻 > until_t の
バーの露出）を構造的に禁止する因果不変を担う。proto_server._truncate に bit 一致:

    df[[int(pd.Timestamp(i).timestamp()) <= until for i in df.index]]

バーは plain な Mapping（``{"time": int, ...}``）の昇順列。pandas/numpy を import しない。
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence


def truncate(bars: "Sequence[Mapping[str, Any]]", until_t: "int | None") -> "list[dict]":
    """``time <= until_t`` のバーのみを新しい list で返す（因果不変）。

    ``until_t is None`` のとき無変更（全バーの浅いコピー）。順序・値を保存する。
    """
    if until_t is None:
        return [dict(b) for b in bars]
    return [dict(b) for b in bars if int(b["time"]) <= until_t]
