"""forming_window — 形成中バーの末尾差し込みと、足内推移の窓分割（中立共有核）。

なぜ共有核か（ISSUE-250 Phase 1）:
    リプレイは足内の各時点を「共通の確定プレフィクス ＋ 時点ごとの末尾差分」に分けて
    計算側へ渡し、窓の再変換を 1 回に畳んでいる（ISSUE-233）。ライブを毎ティック更新に
    するには**同じ分割**が要る。両アプリが同じ規則を持つと二重定義になり、ずれた瞬間に
    描画状態と指標値が食い違う（ISSUE-232 で実際に起きた失敗モード）。したがって規則は
    本モジュール 1 箇所に置き、``simulator.replay_ui``（リプレイ）と ``indicator_ui``
    （ライブ）の双方がここへ依存する。

    依存方向: 本モジュールはどちらのアプリも知らない（ISSUE-091 A1 と同じ規律＝アプリ間の
    側方依存を作らず、中立核へ抽出する）。

規則の出典:
    ``apply_forming`` は ``simulator/replay_ui/domain/forming_bar.apply`` の移設であり、
    proto_server._apply_forming（＝本番 forming_bar.apply_forming_bar）に bit 一致する:
      - forming.time == 末尾 time  → その足を暫定 OHLC で置換
      - forming.time  > 末尾 time  → 新しい足として追加
      - forming.time  < 末尾 time  → 触らない（異常時の防御）
      - forming が None/非 Mapping、time が欠落/不正 → 無変更
      - 列名は大小無視で照合（open/high/low/close/volume）
      - forming に存在するキーのみ更新（他フィールドは保存）

依存: 標準ライブラリのみ（pandas/numpy を import しない）。
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

_FIELDS = ("open", "high", "low", "close", "volume")


def apply_forming(
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


def split_prefix_tails(
    bars: "Sequence[Mapping[str, Any]]", formings: "Iterable[Mapping[str, Any] | None]"
) -> "tuple[list[dict], list[list[dict]]]":
    """足内推移を「共通の確定プレフィクス」と「時点ごとの末尾差分」へ分ける（唯一の定義）。

    ``apply_forming`` は**末尾しか変えない**（先頭側を触らない）。したがって各時点の窓は
    ``prefix + tail`` で復元でき、計算側は窓の変換を 1 回に畳める。値は
    ``apply_forming(bars, forming)`` 全体を渡すのと同値である。

    Args:
        bars: 確定バーの昇順列（末尾 1 本が差し替え対象）。
        formings: 時点ごとの形成中バー（昇順）。

    Returns:
        ``(prefix, tails)``。``prefix`` は ``bars[:-1]`` の複製、``tails[i]`` は i 番目の
        時点における末尾ぶん（``apply_forming(bars[-1:], formings[i])``）。``bars`` が空なら
        ``([], [])``。
    """
    if len(bars) == 0:
        return [], []
    prefix = [dict(b) for b in bars[:-1]]
    last = bars[-1:]
    tails = [apply_forming(last, f) for f in formings]
    return prefix, tails
