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

規則の唯一化（F-9 / ISSUE-479）:
    判定は :func:`forming_patch`（純関数）1 つに集約し、list 版（:func:`apply_forming`）・
    DataFrame 版（``adapter.compute.forming_bar.apply_forming_bar``）・ライブ末尾値
    （``adapter.compute.live_tick_tails.make_tail_at``）はいずれもその**消費者**である。

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

from typing import Any, Iterable, Mapping, NamedTuple, Sequence

_FIELDS = ("open", "high", "low", "close", "volume")


class FormingPatch(NamedTuple):
    """「窓の末尾に対して forming をどうするか」の判定結果（規則の唯一の表現）。

    Attributes:
        mode: ``"skip"``（触らない）/ ``"replace"``（末尾を置換）/ ``"append"``（追加）。
        time: 形成中バーの ``time``（UNIX 秒）。判定できなかった場合は ``0``。
        values: 更新する値（小文字キー・float 正規化済み）。``forming`` に在るキーだけを含み、
            ``mode == "skip"`` のときは空。
    """

    mode: str
    time: int
    values: "dict[str, float]"


def forming_patch(last_time: Any, forming: "Mapping[str, Any] | None") -> FormingPatch:
    """差し替え規則そのもの（純関数・唯一の述語）。

    「形成中バーを窓の末尾へ set/replace する」規則は list 版・DataFrame 版・ライブ末尾値の
    3 経路が必要とする。実装が分かれると食い違った瞬間に「描いたローソクと指標値が別のバー」に
    なるため（ISSUE-232 の失敗モード）、判定はここ 1 箇所だけに置き、各経路は**適用**だけを行う。

    Args:
        last_time: 窓の末尾バーの ``time``（UNIX 秒・``int()`` 可能な値）。末尾が無い窓は
            ``None``（比較対象が無い＝``"append"``）。
        forming: 形成中バー。``Mapping`` でない・``time`` が欠落/非数値なら ``"skip"``。

    Returns:
        :class:`FormingPatch`。判定規則は次のとおり（``apply_forming`` の docstring と同一）:
          - ``time == last_time`` → ``"replace"``
          - ``time  > last_time`` → ``"append"``
          - ``time  < last_time`` → ``"skip"``（異常時の防御）
          - ``forming`` が None/非 Mapping・``time`` が欠落/不正 → ``"skip"``

    Raises:
        ValueError / TypeError: ``last_time`` または値が数値へ変換できない場合（呼び出し側の
            前提違反をここで潰さない＝黙って別の値を描かない）。
    """
    if not isinstance(forming, Mapping):
        return FormingPatch("skip", 0, {})
    try:
        t = int(forming["time"])
    except (KeyError, TypeError, ValueError):
        return FormingPatch("skip", 0, {})
    if last_time is not None and t < int(last_time):  # 末尾より過去 → 触らない（防御）
        return FormingPatch("skip", t, {})

    # forming のキーを大小無視で引くための小文字マップ。
    lower_forming = {str(k).lower(): v for k, v in forming.items()}
    values = {key: float(lower_forming[key]) for key in _FIELDS if key in lower_forming}
    mode = "replace" if last_time is not None and t == int(last_time) else "append"
    return FormingPatch(mode, t, values)


def apply_forming(
    bars: "Sequence[Mapping[str, Any]]", forming: "Mapping[str, Any] | None"
) -> "list[dict]":
    """形成中バー ``forming`` を ``bars`` 末尾へ set/replace した新しい list を返す。"""
    out = [dict(b) for b in bars]
    if len(out) == 0:
        return out  # 空窓は list API 固有の契約（述語ではなくここが持つ）。
    patch = forming_patch(out[-1]["time"], forming)
    if patch.mode == "skip":
        return out
    if patch.mode == "replace":  # 末尾を置換
        target = out[-1]
    else:  # t > 末尾 → 追加
        target = {"time": patch.time}
        out.append(target)
    target.update(patch.values)
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
