"""因果 MTF 系列のバー単位記憶（ISSUE-297）。

:mod:`adapter.compute.mtf_causal` の規約は「``value(τ)`` は τ より後のデータに依存しない」
（ISSUE-294 / 295）。よって **同じ入力に対する同じ τ の点は何度計算しても同じ値**であり、
窓が重なるたびに計算し直すのは捨てられる計算である。実測（jp225_tick・チャート足 1h・
計算足 1D・1500 バー）では 1 指標 1 回の全長計算に 1,278〜1,862ms を要し、その大半は
**C バー 1 本ごとの latest 計算 1,500 回**だった。本モジュールはその 1 本ぶんの結果を τ 単位で
記録し、次の要求では計算を発行させない。

正しさの担保（指紋）: 記録の鍵は τ だけではなく、``value(τ)`` を決める入力そのもの
（[τ の期間より前の確定 H 足] と [τ の期間の C 足を τ まで畳んだ H 足]）の指紋を併せ持つ。
入力が 1 つでも違えば指紋が変わり、記録は使われない（fail-closed）。これにより

  - 形成中バー（値が伸びる）は畳んだ足が変わるたびに別物として扱われる、
  - リプレイの ``untilTime`` が同じバーの途中を指す各時点も互いに別物として扱われる、
  - 当日の M1 が再取得で訂正された場合も、畳んだ足が変わるため古い値を返さない、

が構造的に保証される（「最後のバーだけ除く」といった条件分岐を置かない）。

隔離: pandas も指標も知らない（``mtf_causal`` と同じく plain な値だけを扱う）。プロセス内の
寿命だけを持つ技術的関心事であり、規約側（``mtf_causal``）へは注入で渡す。
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any

#: 1 つの計算条件（指標・variant・params・C 足・H 足・データセット）あたりの保持バー数。
#: 既定の計算窓（1500 本）を複数回ぶん覆う。超えたら古い τ から捨てる。
DEFAULT_BAR_CAPACITY = 8192

#: 同時に保持する計算条件の数。指標インスタンスを増やしても足りる程度に取り、超えたら LRU で捨てる。
DEFAULT_KEY_CAPACITY = 64


class CausalBarMemo:
    """1 つの計算条件に対する「バー時刻 τ → (指紋, その時点の点)」の記録。"""

    def __init__(self, capacity: int = DEFAULT_BAR_CAPACITY) -> None:
        self._capacity = int(capacity)
        self._by_time: "OrderedDict[int, tuple[int, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, bar_time: int, fingerprint: int) -> "Any | None":
        """指紋まで一致するときだけ記録を返す（不一致・未記録は None）。"""
        with self._lock:
            hit = self._by_time.get(int(bar_time))
            if hit is None or hit[0] != fingerprint:
                return None
            self._by_time.move_to_end(int(bar_time))
            return hit[1]

    def put(self, bar_time: int, fingerprint: int, payload: Any) -> None:
        with self._lock:
            key = int(bar_time)
            self._by_time[key] = (fingerprint, payload)
            self._by_time.move_to_end(key)
            while len(self._by_time) > self._capacity:
                self._by_time.popitem(last=False)

    def __len__(self) -> int:
        return len(self._by_time)


class CausalMtfMemoStore:
    """計算条件ごとの :class:`CausalBarMemo` を配る（プロセス内・LRU）。"""

    def __init__(self, key_capacity: int = DEFAULT_KEY_CAPACITY,
                 bar_capacity: int = DEFAULT_BAR_CAPACITY) -> None:
        self._key_capacity = int(key_capacity)
        self._bar_capacity = int(bar_capacity)
        self._by_key: "OrderedDict[str, CausalBarMemo]" = OrderedDict()
        self._lock = threading.Lock()

    def memo(self, key: str) -> CausalBarMemo:
        with self._lock:
            memo = self._by_key.get(key)
            if memo is None:
                memo = CausalBarMemo(self._bar_capacity)
                self._by_key[key] = memo
            self._by_key.move_to_end(key)
            while len(self._by_key) > self._key_capacity:
                self._by_key.popitem(last=False)
            return memo

    def clear(self) -> None:
        with self._lock:
            self._by_key.clear()


#: プロセス内の唯一の記憶（ライブ core / リプレイ core は別プロセス＝各々が自分のぶんを持つ）。
STORE = CausalMtfMemoStore()


def memo_for(*, compute_tf: Any, indicator: Any, variant: Any, params: "dict | None",
             dataset_ref: Any = None, timeframe: Any = None) -> CausalBarMemo:
    """計算条件から記憶を引く（同じ条件は同じ記憶を共有する）。

    正しさを担保するのは鍵ではなく**指紋**（``value(τ)`` を決める入力そのもの）である。
    鍵は記憶を分けて容量を効かせるための入れ物にすぎないため、``dataset_ref`` /
    ``timeframe`` を渡せない呼び出し（Port の面が持たない場合）は省略してよい
    ——別データ・別チャート足は指紋が変わるため、取り違えは起こらない。
    """
    flat = tuple(sorted((str(k), repr(v)) for k, v in (params or {}).items()))
    key = repr((str(dataset_ref), str(timeframe), str(compute_tf),
                str(indicator), str(variant), flat))
    return STORE.memo(key)
