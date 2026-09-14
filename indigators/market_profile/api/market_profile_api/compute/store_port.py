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

ISSUE-183（DIP 是正・遅延 import 循環の解消）: 従来は未注入時に本モジュールが
``from market_profile_api.gateway.composition import default_zp_store`` を関数スコープで実行していた。
これは compute（内側）→ gateway（外側）の逆流であり、かつ composition が
:mod:`market_profile_api.compute.market_profile_zp` を import し返すため
``store_port → composition → market_profile_zp → store_port`` の循環（Service Locator 化）を成していた。
本モジュールは **具象も composition root も名指ししない**。既定合成は composition root が
:func:`set_default_zp_store_factory` / :func:`set_default_dwell_store_factory` で **押し込む**（push）
形へ反転し、依存方向を「外側 → 内側」の一方向に揃える（循環は 1 辺の除去で断たれる）。

登録はパッケージの Composition Root（:mod:`market_profile_api` の ``__init__``）が
``install_default_stores()`` として 1 回行う。Python は submodule の import 前に必ず親パッケージの
``__init__`` を実行するため、本ポートが呼ばれる時点で登録済みであることが構造的に保証される
（＝エントリポイントの列挙漏れが起こり得ない）。未登録での呼出は結線漏れとして
:class:`RuntimeError` を送出する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, ClassVar, Optional, Protocol, Tuple, runtime_checkable

# ISSUE-178: 層間 DTO（不変）。Port の型契約は生 dict でなく frozen dataclass を指す。
from market_profile_api.compute.rollup_dto import DayRollup, ZpRollup


@runtime_checkable
class ZpCacheRootPort(Protocol):
    """z(p) キャッシュ基点だけを要するクライアント向けの狭いポート（ISP・ISSUE-182 item3）。"""

    def cache_root(self) -> Path:
        """ディスクキャッシュの基点を返す。"""
        ...


@runtime_checkable
class ZpDayInvalidationPort(Protocol):
    """完了日キャッシュの無効化契約（番兵 ＋ ソースティック署名）。

    ISSUE-182 item3: 帰属は実測で決めた。``CACHE_MISS`` と ``day_source_signature`` は
    mgrid 経路（:func:`~market_profile_api.compute.market_profile_zp._mgrid_of_day`）と znull 経路
    （:func:`~market_profile_api.compute.market_profile_zp._zp_day_rollup`）の **双方** が呼ぶため、
    どちらか一方の役割へは帰属できない。両役割 Port の共通基底として括る。
    """

    #: ディスク未ヒット/破損/不整合の番兵（``None``＝「実データ無しの完了日」と区別する）。
    CACHE_MISS: ClassVar[Any]

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        """完了日 ``day_start`` のソースティック署名（無効化用）。"""
        ...


@runtime_checkable
class ZpMgridStorePort(ZpDayInvalidationPort, Protocol):
    """完了日 mgrid の永続化だけを要するクライアント向けの狭いポート（ISP・ISSUE-182 item3）。"""

    def mgrid_path(self, symbol: str, day_start: int) -> Path:
        """完了日 mgrid の保存パス。"""
        ...

    def save_mgrid(self, path: Path, grid: "tuple[Any, float] | None", sig: str = "") -> None:
        """mgrid（``None``=実データ無し完了日を含む）を原子的に保存する。"""
        ...

    def load_mgrid(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / (closes, open)。"""
        ...


@runtime_checkable
class ZpNullStorePort(ZpDayInvalidationPort, Protocol):
    """完了日 znull（z 成果物）の永続化だけを要するクライアント向けの狭いポート（ISP・ISSUE-182 item3）。"""

    def null_path(self, symbol: str, day_start: int) -> Path:
        """完了日 znull（z 成果物）の保存パス（帰無パラメータ L/M タグを含む）。"""
        ...

    def save_null(self, path: Path, roll: "ZpRollup | None", sig: str = "") -> None:
        """znull（``None``=z 未定義/実データ無しの完了日を含む）を原子的に保存する。"""
        ...

    def load_null(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / :class:`ZpRollup`（不変 DTO・ISSUE-178）。"""
        ...


@runtime_checkable
class ZpStorePort(ZpCacheRootPort, ZpMgridStorePort, ZpNullStorePort, Protocol):
    """z(p) 日別成果物のディスク永続キャッシュ Repository の抽象（後方互換の合成ポート）。

    ISSUE-182 item3（ISP）: 旧 ``ZpStorePort`` は「キャッシュ基点」「mgrid 永続化」「znull 永続化」を
    1 つの太い抽象に混載していた（7 メソッド ＋ 番兵）。実測ではどのクライアントも全面は使わない
    （``_mgrid_of_day`` は mgrid 系 ＋ 無効化契約、``_zp_day_rollup`` と ``market_profile_zp_warmer``
    は znull 系 ＋ 無効化契約）。``tick_store_port`` が ISSUE-136 で自ら実施した規律に揃え、役割別
    :class:`ZpCacheRootPort` / :class:`ZpMgridStorePort` / :class:`ZpNullStorePort` へ分割し、
    クライアントは狭い getter（:func:`zp_mgrid_store` / :func:`zp_null_store`）にのみ依存する。

    本合成 Port の **メンバ集合は分割前と厳密に一致**する（``tests/test_store_port_role_split.py``
    が literal な期待集合で固定）。既存の注入面（:func:`set_zp_store`）と ``isinstance`` ガード
    （ISSUE-177）が受理/拒否する対象は分割で変わらない。``layout()`` は Port の契約外（ISSUE-172）。

    ``cache_root`` の実測: 本 Port 経由の外部クライアントは 0 件（既定具象 :class:`ZpStore` が
    ``mgrid_path`` / znull 基点を組み立てる自クラス内部の呼出のみ）。メンバ集合不変の要請から
    合成 Port には残すが、クライアント不在のため狭い getter は設けない（YAGNI）。
    """


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

    def save_day_rollup(self, path: Path, roll: "DayRollup | None", sig: str = "") -> None:
        """ロールアップ（``None``=実データ無し完了日を含む）を原子的に保存する。"""
        ...

    def load_day_rollup(self, path: Path) -> "Tuple[Any, str]":
        """(status, sig)。status は CACHE_MISS / None / :class:`DayRollup`（不変 DTO・ISSUE-178）。"""
        ...

    def day_source_signature(self, symbol: str, day_start: int) -> str:
        """完了日 ``day_start`` のソースティック署名（無効化用）。"""
        ...


_ZP_STORE: "Optional[ZpStorePort]" = None
_DWELL_STORE: "Optional[DwellStorePort]" = None

# ISSUE-183: 既定 Store の合成関数（composition root が push する）。本モジュールは gateway を import しない。
_ZP_FACTORY: "Optional[Callable[[], ZpStorePort]]" = None
_DWELL_FACTORY: "Optional[Callable[[], DwellStorePort]]" = None


def set_default_zp_store_factory(factory: "Optional[Callable[[], ZpStorePort]]") -> None:
    """既定 zp Store の合成関数を composition root から登録する（``None`` で登録解除）。"""
    global _ZP_FACTORY
    _ZP_FACTORY = factory


def set_default_dwell_store_factory(factory: "Optional[Callable[[], DwellStorePort]]") -> None:
    """既定 dwell Store の合成関数を composition root から登録する（``None`` で登録解除）。"""
    global _DWELL_FACTORY
    _DWELL_FACTORY = factory


def _unwired(kind: str) -> RuntimeError:
    """既定 factory 未登録（composition root の結線漏れ）を表す :class:`RuntimeError` を組み立てる。"""
    return RuntimeError(
        f"{kind} Store が未結線です。market_profile_api.gateway.composition."
        f"install_default_stores() を呼ぶか、set_{kind}_store(...) で注入してください。"
    )


def _port_violation(kind: str, port: type, store: Any) -> TypeError:
    """Port 非準拠の注入に対する :class:`TypeError` を組み立てる（ISSUE-177）。

    欠落属性の列挙は診断のための付加情報。``__protocol_attrs__`` は CPython の非公開属性のため
    :func:`getattr` で存在を確認し、無い環境では Port 名と受領型のみを報告する（本ガードの契約は
    「Port 非準拠なら TypeError」であり、メッセージ内訳に依存させない）。
    """
    detail = f"got {type(store).__name__}"
    attrs = getattr(port, "__protocol_attrs__", None)
    if attrs:
        missing = sorted(set(attrs) - set(dir(store)))
        if missing:
            detail = f"missing: {missing}"
    return TypeError(f"{kind} store must satisfy {port.__name__} ({detail})")


def set_zp_store(store: "Optional[ZpStorePort]") -> None:
    """zp 永続化 Store 実装を注入する（None で既定へ戻す）。合成は composition root の責務。

    ISSUE-177: Protocol を「宣言」でなく「強制」にする。Port 非準拠の実装は注入時点で
    :class:`TypeError` にする（欠落メソッドが実際に呼ばれる serving 中まで破綻を遅延させない）。
    検査は構造的部分型（``@runtime_checkable``）で行うため、既定具象 :class:`ZpStore` の派生である
    必要はない（代替実装の置換可能性＝LSP は殺さない）。``layout()`` は Port の契約外（ISSUE-172）。
    """
    global _ZP_STORE
    if store is not None and not isinstance(store, ZpStorePort):
        raise _port_violation("zp", ZpStorePort, store)
    _ZP_STORE = store


def set_dwell_store(store: "Optional[DwellStorePort]") -> None:
    """dwell 永続化 Store 実装を注入する（None で既定へ戻す）。合成は composition root の責務。

    ISSUE-177: :func:`set_zp_store` と同一規律（Port 非準拠は注入時点で :class:`TypeError`）。
    """
    global _DWELL_STORE
    if store is not None and not isinstance(store, DwellStorePort):
        raise _port_violation("dwell", DwellStorePort, store)
    _DWELL_STORE = store


def zp_store() -> ZpStorePort:
    """現在の zp Store を返す。未注入なら登録済み既定 factory で合成する（自己完結起動の温存）。

    既定 Store は設定（cache root / 帰無パラメータ / 版数 / 正準ティック列挙）を call-time の
    provider で読む純設定ホルダ（無状態）ため、注入なし時は都度合成してよい（プロセス跨ぎの
    状態を持たない＝テスト間の設定リークが構造的に生じない）。注入時はその実体をそのまま返す。
    """
    if _ZP_STORE is not None:
        return _ZP_STORE
    if _ZP_FACTORY is None:
        raise _unwired("zp")
    return _ZP_FACTORY()


def zp_mgrid_store() -> ZpMgridStorePort:
    """mgrid 永続化ポート（mgrid 系 ＋ 無効化契約）を返す（ISSUE-182 item3・ISP）。

    単一の注入シーム（:func:`zp_store`）へ委譲し、mgrid のみを要するクライアント
    （``market_profile_zp._mgrid_of_day``）が znull 系を含まない狭いポート型に依存できるようにする
    （参照実装 :func:`~market_profile_api.compute.tick_store_port.tick_reader` と同規律）。
    """
    return zp_store()


def zp_null_store() -> ZpNullStorePort:
    """znull 永続化ポート（znull 系 ＋ 無効化契約）を返す（ISSUE-182 item3・ISP）。

    単一の注入シーム（:func:`zp_store`）へ委譲し、znull のみを要するクライアント
    （``market_profile_zp._zp_day_rollup`` / ``market_profile_zp_warmer``）が mgrid 系を含まない
    狭いポート型に依存できるようにする。
    """
    return zp_store()


def dwell_store() -> DwellStorePort:
    """現在の dwell Store を返す。未注入なら登録済み既定 factory で合成する（自己完結起動の温存）。"""
    if _DWELL_STORE is not None:
        return _DWELL_STORE
    if _DWELL_FACTORY is None:
        raise _unwired("dwell")
    return _DWELL_FACTORY()


def zp_cache_miss() -> Any:
    """zp Store の CACHE_MISS 番兵（gateway 具象のクラス属性・identity 一致）を遅延取得する。"""
    return zp_store().CACHE_MISS


def dwell_cache_miss() -> Any:
    """dwell Store の CACHE_MISS 番兵（gateway 具象のクラス属性・identity 一致）を遅延取得する。"""
    return dwell_store().CACHE_MISS
