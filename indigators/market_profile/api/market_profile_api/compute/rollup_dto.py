"""層間 DTO（compute↔gateway 境界の不変値・ISSUE-178）。

`indigators/PORTING_GUIDE.md` §2「DTO は不変」: 層間で渡す値は ``@dataclass(frozen=True)``、
numpy 配列は ``__post_init__`` で ``writeable=False`` にする。本モジュールはその規約を
market_profile の compute↔gateway 境界へ適用する。

背景（ISSUE-178・実測）: 境界を跨ぐのは生 dict（``{kmin,dwell,cnt}`` / ``{kmin,obs,mean,var}``）
だった。``market_profile_dwell._DAY_CACHE`` / ``market_profile_zp._NULL_CACHE`` はプロセス内
キャッシュへ**参照**を格納し、同じ配列をそのまま呼出元へ返す。つまり可変配列がプロセス全体で
共有されており、in-place 更新が 1 箇所でも混入すればキャッシュが汚染される（現時点で in-place
更新箇所は実在しない＝構造的リスクの封じ込め）。読み出し側の累算（``obs_sum[...] += r.obs``）は
**呼出元所有の配列**が書込先で、DTO 配列は右辺＝読み取りのみのため read-only 化と両立する。

実装形は repo 内の既存実績（``price_range_power/src/core.py`` の複数配列ループ不変化 /
``profit_stc/src/core.py`` の単一配列）に揃える。

依存方向: numpy のみ（compute＝方針層の最内側に置き、gateway 具象はこれを import する）。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def _freeze(obj: object, names: "tuple[str, ...]", dtype: object = np.float64) -> None:
    """``names`` の各フィールドを ``dtype`` の read-only 配列へ正規化して固定する。

    frozen dataclass のため代入は :func:`object.__setattr__` 経由で行う（参照実装と同形）。
    ``np.asarray`` は既に float64 の入力を**コピーせず**返すため、呼出元が渡した配列そのものが
    read-only 化される点に注意する（DTO 化した時点で所有権は DTO 側へ移る、という規約）。
    """
    for name in names:
        arr = np.asarray(getattr(obj, name), dtype=dtype)
        arr.setflags(write=False)  # DTO は不変（PORTING_GUIDE §2）
        object.__setattr__(obj, name, arr)


@dataclass(frozen=True)
class DayRollup:
    """dwell 日別ロールアップ（固定 GRID_W 格子・``k = floor(mid / GRID_W)``）。

    Attributes:
        kmin: 格子セル index の下限（配列の先頭要素が指す k）。
        dwell: セッション認識の実ティック滞在秒（休場帯は 0）。``metric='dwell'`` が使う。
        cnt: 生ティック数（セッションマスク非適用）。``metric='count'``（src=m1）が使う。
    """

    kmin: int
    dwell: np.ndarray
    cnt: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "kmin", int(self.kmin))
        _freeze(self, ("dwell", "cnt"))


@dataclass(frozen=True)
class ZpRollup:
    """zp 日別ロールアップ（絶対 log 格子・``k = floor(ln(price) / W_LOG)``）。

    z は加算不可のため、日別に観測占有と帰無モーメントを保持し、窓合算時に
    ``z = (Σobs − Σmean) / √(Σvar)`` を再計算する（日間・セル間とも独立近似）。

    Attributes:
        kmin: 格子セル index の下限（配列の先頭要素が指す k）。
        obs: 観測占有（分数カウント）。
        mean: Null B 帰無の期待値 Ê[N(p)]。
        var: Null B 帰無の分散 Var[N(p)]。
    """

    kmin: int
    obs: np.ndarray
    mean: np.ndarray
    var: np.ndarray

    def __post_init__(self) -> None:
        object.__setattr__(self, "kmin", int(self.kmin))
        _freeze(self, ("obs", "mean", "var"))


@dataclass(frozen=True)
class TickWindow:
    """窓 ``[start, end)`` の正準ティック（gateway→compute 境界を跨ぐ read-only の 2 配列）。

    ``secs`` は int64（UNIX 秒・昇順安定ソート済み）、``mids`` は float64（(bid+ask)/2）。
    dtype は既存挙動をそのまま保つ（``secs`` を float 化しない＝数値・下流の量子化に影響させない）。

    Attributes:
        secs: ティックの UNIX 秒（int64・昇順）。
        mids: ティックの mid 価格（float64・``secs`` と同順・同長）。
    """

    secs: np.ndarray
    mids: np.ndarray

    def __post_init__(self) -> None:
        _freeze(self, ("secs",), dtype=np.int64)
        _freeze(self, ("mids",), dtype=np.float64)
