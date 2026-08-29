"""§5.3.3 積み上がる量を「同じ経過割合の分布」へ当てるための比較集合（T-8 裁定の実装）。

問題（§5.3.3・実測）: tick 数は足の中で積み上がるため、形成途中の足を**確定足の分布**へ
当てると必ず極小に出る（1h の f=0.1 で p の中央値 0.000。バイアスは 8 時間足すべてに在る）。
根本原因は「部分和を完全和の分布へ当てている」＝**比較集合の取り違え**である。

是正（本モジュール）: 部分足は「**同じ経過まで進んだ過去の足**」の分布へ当てる。
これは足内の tick 到来プロファイルを**仮定しない**（比較集合を揃えるだけ）。

T-8（丸め禁止）: 経過割合を 0.05 / 0.10 刻みで丸める案は不採用（実測 p90 |Δp| 0.10〜0.15 ＝
バイアスの再導入）。素材の最小単位（tf >= 5m なら 1m 足、1m ならティック供給の秒境界）で
**厳密に同経過**を突き合わせる。

保持（§7 の計算量表明）: 素材は prefix cumsum 1 本だけを持ち、**ティック数に比例しない**。
比較集合は経過 `k` ごとにキャッシュし、素材が 1 単位進んだときにだけ作り直す
（＝窓の再評価は最小単位の境界ごとに 1 回）。

参照実装: `tools/measure/issue449/probe_forming_long.py`（比較集合の作り方はその causal_pct_against
に従う）。
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


class ElapsedFractionPool:
    """足を「最小単位（サブ単位）の列」として保持し、経過 `k` 単位時点の部分和を供給する。

    サブ単位は tf >= 5m では 1m 足、1m ではティック供給から導く秒境界である（どちらでも
    規約は同じなので、本クラスは単位の中身を知らない＝時間足ごとに分岐しない）。
    """

    def __init__(self) -> None:
        # 値の prefix cumsum（先頭に 0）。保持はこれ 1 本＋足の開始位置だけ。
        self._cumsum: "list[float]" = [0.0]
        self._starts: "list[int]" = []       # 各足の先頭サブ単位の位置
        self._keys: "list[int]" = []         # 各足のキー（順序の検査に使う）
        self._cache: "dict[int, np.ndarray]" = {}

    # ------------------------------------------------------------------ 構築
    @classmethod
    def from_units(
        cls, bar_keys: Sequence[int], values: Sequence[float]
    ) -> "ElapsedFractionPool":
        """サブ単位の列（足キー・値）から構築する。足キーは時刻順に連続していること。"""
        if len(bar_keys) != len(values):
            raise ValueError(
                f"bar_keys と values は同一長が必要です: {len(bar_keys)} / {len(values)}"
            )
        pool = cls()
        for key, value in zip(bar_keys, values):
            pool.close_unit(int(key), float(value))
        return pool

    # ------------------------------------------------------------------ 更新
    def close_unit(self, bar_key: int, value: float) -> None:
        """サブ単位 1 つの確定を取り込む（**ここでだけ**保持が増える）。

        Raises:
            ValueError: 値が非有限、または既に閉じた足のキーが再び現れたとき。
        """
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"サブ単位の値は有限値が必要です: {value!r}")
        key = int(bar_key)
        if self._keys and key != self._keys[-1]:
            if key in self._keys:
                raise ValueError(
                    f"閉じた足のキーが再び現れました: {key}（素材を時刻で一意化・整列すること）"
                )
            self._starts.append(len(self._cumsum) - 1)
            self._keys.append(key)
        elif not self._keys:
            self._starts.append(0)
            self._keys.append(key)
        self._cumsum.append(self._cumsum[-1] + number)
        self._cache.clear()

    # ------------------------------------------------------------------ 参照
    @property
    def bar_count(self) -> int:
        return len(self._starts)

    @property
    def unit_count(self) -> int:
        """保持しているサブ単位の総数（＝ティック数ではない）。"""
        return len(self._cumsum) - 1

    @property
    def bar_lengths(self) -> "tuple[int, ...]":
        bounds = [*self._starts, self.unit_count]
        return tuple(bounds[i + 1] - bounds[i] for i in range(len(self._starts)))

    def partial_sum(self, bar_index: int, k: int) -> float:
        """足 `bar_index` の先頭 `k` サブ単位の和（prefix cumsum の差で O(1)）。

        Raises:
            IndexError: 足の番号が範囲外のとき。
            ValueError: `k` が 1..その足の長さ の外のとき。
        """
        if not 0 <= bar_index < self.bar_count:
            raise IndexError(f"足の番号が範囲外です: {bar_index}（0..{self.bar_count - 1}）")
        length = self.bar_lengths[bar_index]
        if not 1 <= int(k) <= length:
            raise ValueError(
                f"経過 k は 1..{length} が必要です（足 {bar_index}）: k={k}"
            )
        start = self._starts[bar_index]
        return float(self._cumsum[start + int(k)] - self._cumsum[start])

    def partial_sums_at(self, k: int) -> np.ndarray:
        """経過 `k` サブ単位まで進んだ**すべての足**の部分和（古い順）。

        `k` に到達していない足は比較集合から外す（丸めて混ぜない＝T-8）。
        戻り値は読み取り専用のキャッシュ（同じ `k` を何度聞かれても作り直さない）。
        """
        elapsed = int(k)
        if elapsed < 1:
            raise ValueError(f"経過 k は 1 以上が必要です: {k}")
        cached = self._cache.get(elapsed)
        if cached is not None:
            return cached
        values = [
            self.partial_sum(index, elapsed)
            for index, length in enumerate(self.bar_lengths)
            if length >= elapsed
        ]
        array = np.asarray(values, dtype=np.float64)
        array.flags.writeable = False
        self._cache[elapsed] = array
        return array
