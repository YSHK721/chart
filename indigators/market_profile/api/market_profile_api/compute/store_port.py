"""永続キャッシュ Store の Output Boundary（ISSUE-137: DIP 逆転・TickStorePort と同規律）。

compute（方針側＝Application Business Rules）が所有する境界ポート。zp/dwell の集計数学は
z(p)・dwell 日別成果物の物理格納（npz レイアウト・原子的保存・署名無効化＝偶有的性質）を知らず、
本ポート（:class:`ZpStorePort` / :class:`DwellStorePort`）にのみ依存する。具象実装は
gateway 層（:class:`market_profile_api.gateway.zp_store.ZpStore` /
:class:`market_profile_api.gateway.dwell_rollup_store.DwellRollupStore`）が担い、既定結線は
composition root（:mod:`market_profile_api.gateway.composition`）が単独で名指し合成する。

ISSUE-091/092 で tick I/O は :class:`tick_store_port.TickStorePort` ＋ :func:`set_tick_store`
で逆転済みだった一方、永続化 Store のみ compute が module-level で gateway 具象を直接 new して
composition root を迂回していた（非対称）。本モジュールで永続化 Store も同一規律
（Output Boundary ＋ set_*() 注入 ＋ 未注入時は composition root へ遅延委譲）へ揃える。

未注入時は既定実装を composition root から遅延合成する。これは
:mod:`tick_store_port` と同じ「自己完結起動の温存」であり、compute からの module-level
gateway 具象依存は排除される（型契約は本ポートが唯一）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class ZpStorePort(Protocol):
    """z(p) 日別成果物（mgrid / znull）のディスク永続キャッシュ Repository の抽象。"""

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS: ClassVar[Any]

    def cache_root(self) -> Path:
        """ディスクキャッシュの基点を返す。"""
        ...

    def mgrid_path(self, symbol: str, day_start: int) -> Path:
        """完了日 mgrid の保存パス。"""
        ...

    def null_path(self, symbol: str, day_start: int) -> Path:
        """完了日 znull（z 成果物）の保存パス（帰無パラメータ L/M タグを含む）。"""
        ...

    def save_mgrid(self, path: Path, grid: "tuple[Any, float] | None", sig: str = "") -> None:
        """mgrid（``None``=実データ無し完了日を含む）を原子的に保存する。"""
        ...

    def load_mgrid(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / (closes, open)。"""
        ...

    def save_null(self, path: Path, roll: "dict | None", sig: str = "") -> None:
        """znull（``None``=z 未定義/実データ無しの完了日を含む）を原子的に保存する。"""
        ...

    def load_null(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / dict{kmin,obs,mean,var}。"""
        ...

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        """完了日 ``day_start`` のソースティック署名（無効化用）。"""
        ...


@runtime_checkable
class DwellStorePort(Protocol):
    """dwell 日別ロールアップのディスク永続キャッシュ Repository の抽象。"""

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS: ClassVar[Any]

    def cache_root(self) -> Path:
        """ディスクキャッシュの基点を返す。"""
        ...

    def cache_path(self, symbol: str, day_start: int) -> Path:
        """日別ロールアップの保存パス（version / grid_w タグを含む）。"""
        ...

    def save_day_rollup(self, path: Path, roll: "dict | None", sig: str = "") -> None:
        """ロールアップ（``None``=実データ無し完了日を含む）を原子的に保存する。"""
        ...

    def load_day_rollup(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / dict{kmin,dwell,cnt}。"""
        ...

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        """完了日 ``day_start`` のソースティック署名（無効化用）。"""
        ...


_ZP_STORE: "Optional[ZpStorePort]" = None
_DWELL_STORE: "Optional[DwellStorePort]" = None


def set_zp_store(store: "Optional[ZpStorePort]") -> None:
    """zp 永続化 Store 実装を注入する（None で既定へ戻す）。合成は composition root の責務。"""
    global _ZP_STORE
    _ZP_STORE = store


def set_dwell_store(store: "Optional[DwellStorePort]") -> None:
    """dwell 永続化 Store 実装を注入する（None で既定へ戻す）。合成は composition root の責務。"""
    global _DWELL_STORE
    _DWELL_STORE = store


def zp_store() -> ZpStorePort:
    """現在の zp Store を返す。未注入なら composition root の既定を遅延合成する（自己完結起動）。

    既定 Store は設定（cache root / 帰無パラメータ / 版数 / 正準ティック列挙）を call-time の
    provider で読む純設定ホルダ（無状態）ため、注入なし時は都度合成してよい（プロセス跨ぎの
    状態を持たない＝テスト間の設定リークが構造的に生じない）。注入時はその実体をそのまま返す。
    """
    if _ZP_STORE is not None:
        return _ZP_STORE
    from market_profile_api.gateway.composition import default_zp_store

    return default_zp_store()


def dwell_store() -> DwellStorePort:
    """現在の dwell Store を返す。未注入なら composition root の既定を遅延合成する（自己完結起動）。"""
    if _DWELL_STORE is not None:
        return _DWELL_STORE
    from market_profile_api.gateway.composition import default_dwell_store

    return default_dwell_store()


def zp_cache_miss() -> Any:
    """zp Store の CACHE_MISS 番兵（gateway 具象のクラス属性・identity 一致）を遅延取得する。"""
    return zp_store().CACHE_MISS


def dwell_cache_miss() -> Any:
    """dwell Store の CACHE_MISS 番兵（gateway 具象のクラス属性・identity 一致）を遅延取得する。"""
    return dwell_store().CACHE_MISS
