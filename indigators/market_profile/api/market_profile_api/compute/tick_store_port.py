"""正準ティックストアの Output Boundary（ISSUE-091 🔴-2: DIP 逆転）。

compute（方針側）が所有する境界ポート。dwell/zp の集計数学はティックの物理格納
（day parquet・DATA_DIR レイアウト＝偶有的性質）を知らず、本ポートにのみ依存する。
具象実装は :mod:`market_profile_api.gateway.marketdata_tick_store`（marketdata 結線）が担い、
エントリポイントは :func:`set_tick_store` で差し替えできる。

ISSUE-183（DIP 是正）: 従来は未注入時に本モジュールが composition root を関数スコープで import
（pull）していた＝compute（内側）→ gateway（外側）の逆流。既定合成は composition root が
:func:`set_default_tick_store_factory` で **押し込む**（push）形へ反転し、依存方向を「外側 → 内側」の
一方向に揃える。登録はパッケージの Composition Root（:mod:`market_profile_api` の ``__init__``）が
``install_default_stores()`` として 1 回行うため、本ポート呼出時点での登録済みは構造的に保証される。
ISSUE-137: 既定具象名（``MarketdataTickStore``）は composition root へ集約し、本ポートには具象
クラス名を持たせない。

ISSUE-136（ISP）: 旧 ``TickStorePort`` は「キャッシュ基点（``data_dir``）」と「tick ファイルアクセス
（``day_files`` / ``read_ticks`` / ``load_window_ticks``）」を 1 つの太い抽象に混載していた。実測では
dwell/zp は tick アクセスのみ・``tf_period_profile_controller`` と composition の既定 root provider は
``data_dir`` のみを使い、どのクライアントも全面は使わない。そこで役割別に :class:`DataRootPort`
（基点）と :class:`TickReaderPort`（tick 読取）へ分割し、クライアントは自分が使う狭いポート
（:func:`data_root` / :func:`tick_reader`）にのみ依存する。既存消費者向けに両者を合成した
``TickStorePort`` 名と単一の注入シーム（:func:`set_tick_store` / :func:`tick_store`）は温存する。

ISSUE-182 item3（ISP の徹底）: ``read_ticks``（日別ファイルを列指定で読む）を Port から削除した。
repo 全体 Grep の実測で外部クライアントは **0 件** であり、唯一の呼出は既定具象
:class:`~market_profile_api.gateway.marketdata_tick_store.MarketdataTickStore` が自身の
``load_window_ticks`` の内部で行う自己呼出だった（ISSUE-133 で窓復号を gateway へ移設した際に
compute 側の呼出が消え、Port 上の宣言だけが取り残されていた）。どのクライアントも要求しない
メソッドを Port に残すと代替実装へ実装を強要するため、gateway の private（``_read_ticks``）へ
降格する（``layout()`` を Port に含めない ISSUE-172 の判断と同一規律）。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional, Protocol, Sequence, Tuple, runtime_checkable

# ISSUE-178: 層間 DTO（不変）。窓ティックの型契約は生タプルでなく frozen dataclass を指す。
from market_profile_api.compute.rollup_dto import TickWindow


@runtime_checkable
class DataRootPort(Protocol):
    """データ基点（キャッシュ既定配置の単一基点）の抽象。tick 読取を要さないクライアント向け（ISP）。"""

    def data_dir(self) -> Path:
        """データ基点（キャッシュ既定配置の単一基点）。"""
        ...


@runtime_checkable
class TickReaderPort(Protocol):
    """保存済み正準ティック（read-only）の列挙・読取・窓復号の抽象。基点を要さないクライアント向け（ISP）。"""

    def day_files(self, lo_day: int, hi_day: int, *, symbol: str) -> "list[Path]":
        """``[lo_day, hi_day]``（両端含む・日次）の実在する日別ティックファイルを昇順で列挙する。

        ISSUE-183（DIP）: 引数は **UNIX 秒（int）** で規定する。旧契約は実引数が ``pd.Timestamp``
        （実測）であり、compute 所有のポート契約がインフラのデータ型（pandas）で貫通していた
        ＝DIP でインフラ依存を切ったつもりが型で漏出していた。``pd.Timestamp`` への変換は
        実装側（:mod:`market_profile_api.gateway.marketdata_tick_store`）へ押し込む。
        値は UTC 日始端（00:00:00 UTC）を指す秒を想定する。
        """
        ...

    def load_window_ticks(
        self,
        symbol: str,
        start: Any,
        end: Any,
        *,
        columns: "Sequence[str]",
        outlier_frac: float,
    ) -> "TickWindow":
        """``[start, end)`` の正準ティックを :class:`TickWindow`（不変 DTO・ISSUE-178）で返す（ISSUE-133 SRP）。

        日別ファイルの列挙・読取・concat・tz 正規化・窓マスク・mid 算出・窓内中央値 ±``outlier_frac``
        の外れ値除去・secs 安定ソートまで（＝ティック格納スキーマの復号＝偶有的性質）を実装側に隔離する。
        窓内ティックゼロは空配列。compute（方針側）は本境界にのみ依存し tick I/O 解析を持たない。
        """
        ...


@runtime_checkable
class TickStorePort(DataRootPort, TickReaderPort, Protocol):
    """後方互換の合成ポート（:class:`DataRootPort` ＋ :class:`TickReaderPort`）。

    既存の ``TickStorePort`` 名消費者（テスト・既定具象 ``MarketdataTickStore``）向けに全メソッドを
    保持する。新規クライアントは役割別の :class:`DataRootPort` / :class:`TickReaderPort` に依存する。
    """


_STORE: "TickStorePort | None" = None

# ISSUE-183: 既定 Store の合成関数（composition root が push する）。本モジュールは gateway を import しない。
_TICK_FACTORY: "Optional[Callable[[], TickStorePort]]" = None


def set_default_tick_store_factory(factory: "Optional[Callable[[], TickStorePort]]") -> None:
    """既定ティックストアの合成関数を composition root から登録する（``None`` で登録解除）。"""
    global _TICK_FACTORY
    _TICK_FACTORY = factory


def _port_violation(kind: str, port: type, store: Any) -> TypeError:
    """Port 非準拠の注入に対する :class:`TypeError` を組み立てる（ISSUE-177・store_port と同一規律）。

    欠落属性の列挙は診断のための付加情報。``__protocol_attrs__`` は CPython の非公開属性のため
    :func:`getattr` で存在を確認し、無い環境では Port 名と受領型のみを報告する。
    """
    detail = f"got {type(store).__name__}"
    attrs = getattr(port, "__protocol_attrs__", None)
    if attrs:
        missing = sorted(set(attrs) - set(dir(store)))
        if missing:
            detail = f"missing: {missing}"
    return TypeError(f"{kind} store must satisfy {port.__name__} ({detail})")


def set_tick_store(store: "TickStorePort | None") -> None:
    """ティックストア実装を注入する（None で既定へ戻す）。合成はエントリポイントの責務。

    ISSUE-177: Protocol を「宣言」でなく「強制」にする（``store_port`` の 2 setter と同一規律）。
    Port 非準拠の実装は注入時点で :class:`TypeError` にする（欠落メソッドが実際に呼ばれる
    serving 中まで破綻を遅延させない）。検査は構造的部分型（``@runtime_checkable``）で行うため、
    既定具象 ``MarketdataTickStore`` の派生である必要はない（LSP は殺さない）。
    """
    global _STORE
    if store is not None and not isinstance(store, TickStorePort):
        raise _port_violation("tick", TickStorePort, store)
    _STORE = store


def tick_store() -> TickStorePort:
    """現在のティックストアを返す。未注入なら登録済み既定 factory で合成する（自己完結起動の温存）。

    Raises:
        RuntimeError: 未注入かつ既定 factory 未登録（composition root の結線漏れ）。
    """
    global _STORE
    if _STORE is None:
        if _TICK_FACTORY is None:
            raise RuntimeError(
                "tick Store が未結線です。market_profile_api.gateway.composition."
                "install_default_stores() を呼ぶか、set_tick_store(...) で注入してください。"
            )
        _STORE = _TICK_FACTORY()
    return _STORE


def data_root() -> DataRootPort:
    """データ基点ポート（キャッシュ基点のみ）を返す（ISSUE-136 ISP）。

    単一の注入シーム（:func:`tick_store`）へ委譲し、``data_dir`` のみを要するクライアント
    （``tf_period`` controller・composition の既定 root provider）が狭いポート型に依存できるようにする。
    """
    return tick_store()


def tick_reader() -> TickReaderPort:
    """ティック読取ポート（列挙・読取・窓復号）を返す（ISSUE-136 ISP）。

    単一の注入シーム（:func:`tick_store`）へ委譲し、tick アクセスのみを要する compute（dwell/zp）が
    ``data_dir`` を含まない狭いポート型に依存できるようにする。
    """
    return tick_store()
