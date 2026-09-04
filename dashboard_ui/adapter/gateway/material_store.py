"""確定素材を epoch 単位で要求をまたいで共有するストア（ISSUE-457）。

なぜ要るか（実測）:
    段 2（ティック）で確定足の素材は定義上変わらない。にもかかわらず要求ごとに口
    （gateway）を組み直すと、同じ確定素材の full 系列を毎要求作り直す。§9-4 の実測では
    要求 9,452 ms のうち P-1 系列供給が 7,440 ms（78%）を占めていた。**出力は正しいまま
    なので状態検証では原理的に落ちない**——ISSUE-450 / ISSUE-257 と同型の欠陥である。

何を共有するか:
    「その時間足の周期（epoch）の中で不変な量」だけである。確定足ぶんの full 系列がこれに
    当たる。形成中足の 1 点と素材（DataFrame）そのものは共有しない（毎要求読み直す）ので、
    現在値・走行 H/L の鮮度は共有と引き換えにならない。これは controller 側の持ち越し状態
    （SheetState）が当てはめの epoch を持ち越すのと同じ構造であり、置き場所（adapter）も
    同じ規律に従う。

版（epoch）の扱い:
    版はキーごとに 1 つだけ持つ。新しい版が来たら**そのキーの旧版は丸ごと捨てる**
    （古い素材を配らない・無限に溜めない）。キーを `(dataset_ref, timeframe)` に取るので、
    ある時間足の周期が進んでも他の時間足の素材は生き残る（§7 の「作り直すのは確定した足だけ」）。

並行性:
    ThreadingHTTPServer 下では要求が同時に走りうる。錠はキーごとに 1 つ持ち、素材を作る間
    だけ握る。こうすると (a) 同じキーの二重計算が起きず、(b) 別の時間足は待たされない
    （キーをまたいだ直列化は「仕事の量」を減らさないので採らない・ISSUE-257 の裁定と同旨）。
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Hashable


class MaterialStore:
    """`(key, epoch, name) -> 素材`。プロセス寿命で持ち、版が変わったらキー単位で捨てる。"""

    def __init__(self) -> None:
        self._registry = threading.Lock()
        self._locks: "dict[Hashable, threading.Lock]" = {}
        self._epochs: "dict[Hashable, Hashable]" = {}
        self._materials: "dict[Hashable, dict[Hashable, Any]]" = {}

    def material(
        self, *, key: Hashable, epoch: Hashable, name: Hashable,
        factory: Callable[[], Any],
    ) -> Any:
        """`key` の版が `epoch` である前提で `name` の素材を返す（無ければ作る）。

        Args:
            key: 版を共有する単位（`(dataset_ref, timeframe)`）。
            epoch: その単位の版（周期の始端）。変わったらこの単位の素材を全部捨てる。
            name: 単位の中での素材の名前（`(indicator_id, variant, params_key)`）。
            factory: 素材を作る手順。**この錠の中で 1 回だけ呼ばれる**。
        """
        with self._lock_for(key):
            if self._epochs.get(key) != epoch:
                self._epochs[key] = epoch
                self._materials[key] = {}
            materials = self._materials[key]
            if name not in materials:
                materials[name] = factory()
            return materials[name]

    def _lock_for(self, key: Hashable) -> threading.Lock:
        with self._registry:
            lock = self._locks.get(key)
            if lock is None:
                lock = self._locks[key] = threading.Lock()
            return lock
