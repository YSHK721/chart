"""tf_period_cache — tf-period 列の per-entry キャッシュ協調（ISSUE-179 項目 B）。

**抽出前（実測）**: ``tf_period_profile_controller`` の 4 メソッド（``_day_columns`` /
``_day_columns_zp`` / ``_bucket_columns`` / ``_bucket_columns_zp``）が同一の協調手順を逐語複製
していた。手順は「完了エントリなら メモリ LRU → ディスク → 計算（＋メモリ・ディスクへ保存）、
未完了エントリはどちらにも触れず都度計算」で、差分は 5 点（エントリキー／ディスク世代 subdir／
ディスク側キー／完了判定式／計算本体）のみである。

**抽出方針**: 手順を :class:`TfPeriodDayCache` へ移し、差分はパラメータと計算戦略（0 引数の
callable）で受ける。**状態（メモリ LRU の辞書と上限）も一緒に移す**——協働子が host の private
フィールドへ代入する「分割不全」（ISSUE-181 が指摘する形）にはしない。ディスク側は
:class:`DayCacheDiskPort`（本モジュール所有の Output Boundary）へ依存し、実装（``_TFP_CACHE_ROOT``
の call-time 解決＋ gateway の純 I/O 委譲）は host 側に残る＝依存は host → 協働子の一方向。

ISSUE-172 との整合: ディスク世代 subdir（``disk_tf``）の組み立ては書込パス所有者である
controller のビルダ（``_disk_tf_count`` / ``_disk_tf_bucket`` / ``_disk_tf_zp``）が唯一の情報源で
あり続ける。本協働子は受け取った ``disk_tf`` を素通しするだけで、世代タグを一切知らない。
"""
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Protocol


class DayCacheDiskPort(Protocol):
    """完了エントリの永続層（Output Boundary）。実装は host 側（root 解決を所有する層）。

    ``@runtime_checkable`` は付けない。注入口は :class:`TfPeriodDayCache` のコンストラクタのみで、
    ``isinstance`` ガードを持つ setter（ISSUE-177 の ``set_zp_store`` 等）が存在しないため、
    実行時チェック機構を導入する現存要求がない（YAGNI）。
    """

    def load(self, symbol: Any, disk_tf: Any, disk_key: Any) -> "tuple[float, list] | None":
        """ヒットなら ``(unit, columns)``、無効・未ヒット・破損は ``None``（＝再計算へ）。"""

    def save(self, symbol: Any, disk_tf: Any, disk_key: Any, unit: float, columns: list) -> None:
        """完了エントリを保存する（失敗は握りつぶす＝次回再計算）。"""


class TfPeriodDayCache:
    """tf-period 列の per-entry キャッシュ協調（メモリ LRU ＋ ディスクの 2 層）。

    メモリ層は完了エントリのみを持つ有界 LRU（``max_entries``）。過去日ティックは不変
    （``.doc/TICK_IMMUTABILITY_VERIFICATION.md``）ゆえ完了エントリは無効化不要で、
    未完了エントリ（当日・当週・当月）はティック成長のため一切キャッシュしない。
    """

    def __init__(self, disk: DayCacheDiskPort, *, max_entries: int = 1024) -> None:
        self._disk = disk
        self._max_entries = int(max_entries)
        self._mem: "OrderedDict[Any, tuple[float, list]]" = OrderedDict()

    # ------------------------------------------------------------------ #
    # 協調本体
    # ------------------------------------------------------------------ #
    def resolve(
        self,
        *,
        key: Any,
        symbol: Any,
        disk_tf: Any,
        disk_key: Any,
        completed: bool,
        compute: "Callable[[], tuple[float, list]]",
    ) -> "tuple[float, list]":
        """``(unit, columns)`` を返す。完了エントリは メモリ → ディスク → ``compute()``（＋保存）。

        Args:
            key: メモリ層のエントリキー（呼出側が src / tf / 日 or バケットで分離する）。
            symbol / disk_tf / disk_key: ディスク層の座標（``disk_tf`` は世代 subdir 込み）。
            completed: エントリが不変確定か（未完了ならキャッシュを読まず・書かない）。
            compute: キャッシュ不使用時に列を作る戦略（0 引数）。
        """
        if completed:
            hit = self._mem.get(key)
            if hit is not None:
                self._mem.move_to_end(key)
                return hit
            disk = self._disk.load(symbol, disk_tf, disk_key)
            if disk is not None:
                self._remember(key, disk)
                return disk
        result = compute()
        if completed:
            self._remember(key, result)
            self._disk.save(symbol, disk_tf, disk_key, result[0], result[1])
        return result

    # ------------------------------------------------------------------ #
    # 状態（協働子が所有する）
    # ------------------------------------------------------------------ #
    def clear(self) -> None:
        """メモリ層を全消去する（テスト隔離用・ディスクは触らない）。"""
        self._mem.clear()

    def memory_size(self) -> int:
        """メモリ層のエントリ数（観測用）。"""
        return len(self._mem)

    def memory_keys(self) -> tuple:
        """メモリ層のキーを LRU 昇順（古い順）で返す（観測用）。"""
        return tuple(self._mem)

    def _remember(self, key: Any, entry: "tuple[float, list]") -> None:
        self._mem[key] = entry
        self._mem.move_to_end(key)
        while len(self._mem) > self._max_entries:
            self._mem.popitem(last=False)
