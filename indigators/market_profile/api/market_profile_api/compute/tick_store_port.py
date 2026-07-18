"""正準ティックストアの Output Boundary（ISSUE-091 🔴-2: DIP 逆転）。

compute（方針側）が所有する境界ポート。dwell/zp の集計数学はティックの物理格納
（day parquet・DATA_DIR レイアウト＝偶有的性質）を知らず、本ポートにのみ依存する。
具象実装は :mod:`market_profile_api.gateway.marketdata_tick_store`（marketdata 結線）が担い、
エントリポイントは :func:`set_tick_store` で差し替えできる。

未注入時は既定実装を composition root（:mod:`market_profile_api.gateway.composition`）から遅延
合成する。これは server.py の sys.path フォールバックと同じ「自己完結起動の温存」であり、compute
からの module-level marketdata 依存は排除される（型契約は本ポートが唯一）。ISSUE-137: 既定具象名
（``MarketdataTickStore``）は composition root へ集約し、本ポートには具象クラス名を持たせない。

ISSUE-136（ISP）: 旧 ``TickStorePort`` は「キャッシュ基点（``data_dir``）」と「tick ファイルアクセス
（``day_files`` / ``read_ticks`` / ``load_window_ticks``）」を 1 つの太い抽象に混載していた。実測では
dwell/zp は tick アクセスのみ・``tf_period_profile_controller`` と composition の既定 root provider は
``data_dir`` のみを使い、どのクライアントも全面は使わない。そこで役割別に :class:`DataRootPort`
（基点）と :class:`TickReaderPort`（tick 読取）へ分割し、クライアントは自分が使う狭いポート
（:func:`data_root` / :func:`tick_reader`）にのみ依存する。既存消費者向けに両者を合成した
``TickStorePort`` 名と単一の注入シーム（:func:`set_tick_store` / :func:`tick_store`）は温存する。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, Sequence, Tuple, runtime_checkable


@runtime_checkable
class DataRootPort(Protocol):
    """データ基点（キャッシュ既定配置の単一基点）の抽象。tick 読取を要さないクライアント向け（ISP）。"""

    def data_dir(self) -> Path:
        """データ基点（キャッシュ既定配置の単一基点）。"""
        ...


@runtime_checkable
class TickReaderPort(Protocol):
    """保存済み正準ティック（read-only）の列挙・読取・窓復号の抽象。基点を要さないクライアント向け（ISP）。"""

    def day_files(self, lo_day: Any, hi_day: Any, *, symbol: str) -> "list[Path]":
        """``[lo_day, hi_day]`` の実在する日別ティックファイルを昇順で列挙する。"""
        ...

    def read_ticks(self, path: Path, columns: "Sequence[str]") -> Any:
        """日別ティックファイルを指定列で読み、DataFrame 互換で返す。"""
        ...

    def load_window_ticks(
        self,
        symbol: str,
        start: Any,
        end: Any,
        *,
        columns: "Sequence[str]",
        outlier_frac: float,
    ) -> "Tuple[Any, Any]":
        """``[start, end)`` の正準ティックを ``(secs:int64, mids:float64)`` で返す（ISSUE-133 SRP）。

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


def set_tick_store(store: "TickStorePort | None") -> None:
    """ティックストア実装を注入する（None で既定へ戻す）。合成はエントリポイントの責務。"""
    global _STORE
    _STORE = store


def tick_store() -> TickStorePort:
    """現在のティックストアを返す。未注入なら composition root の既定を遅延合成する（自己完結起動）。"""
    global _STORE
    if _STORE is None:
        from market_profile_api.gateway.composition import default_tick_store

        _STORE = default_tick_store()
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
