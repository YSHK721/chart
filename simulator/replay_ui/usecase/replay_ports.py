"""replay_ui の境界（Port）抽象（CLEAN_ARCH §5・scan_contacts_ports.py 流儀）。

ISP: 既存 ``simulator/usecase/ports.py`` は無改変。replay 固有の Port は本ファイルへ
別出しする。usecase は tick 源・resample・indicator 計算という偶有的技術を知らない。
Protocol として注入対象を表明し、numpy/pandas をここに漏らさない（実装は adapter 側）。
戻り値は plain 値のみ（バーは ``{"time": int, "open"/"high"/"low"/"close": float}`` の list）。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CausalCandlePort(Protocol):
    """/candles 用の足取得（load_tick_candles 相当）。untilTime 切断は行わない。"""

    def load_candles(
        self, ref: str, timeframe: "str | None", limit: "int | None"
    ) -> "list[dict]":
        """``[{time,open,high,low,close}]`` を返す（resample + tail(limit)）。"""
        ...


@runtime_checkable
class CausalComputePort(Protocol):
    """/compute 用の計算源ロード + 指標計算（dataset.load_dataframe + full/latest_compute）。"""

    def load_source(self, ref: str, timeframe: "str | None") -> "list[dict]":
        """計算源のバー列を plain dict の昇順 list で返す。

        未知 ref / 未知 timeframe は ``ValueError``（proto do_compute 忠実・serve が
        validation error へ翻訳する）。
        """
        ...

    def compute(
        self,
        indicator: str,
        variant: str,
        mode: str,
        bars: "list[dict]",
        params: dict,
    ) -> "list[dict]":
        """``mode`` が ``"latest"`` なら latest_compute、それ以外は full_compute を呼ぶ。

        ``bars`` は truncate/tail/forming 適用済の plain バー列。series（plain dict の
        list）を返す。
        """
        ...


@runtime_checkable
class IntrabarWindowPort(Protocol):
    """/intraday 用の足内 m1 行・実ティック mid の取得。"""

    def load_m1_rows(self, ref: str, start: int, end: int) -> "list[list[float]]":
        """区間 ``[start,end)`` の m1 OHLC 行（``[o,h,l,c]``・上位足は cap 済）を返す。"""
        ...

    def load_ticks(self, start: int, end: int) -> "list[tuple[int, float]]":
        """区間 ``[start,end)`` の実ティック ``[(sec, mid), ...]``（cap 無し）を返す。"""
        ...


@runtime_checkable
class ContactScanPort(Protocol):
    """UC-R5: 接点スキャン（既存 ``simulator/usecase/scan_contacts.py`` 再利用）の境界。

    replay の再生時点データに対する接点抽出を、既存 usecase へ委譲するための Port。
    実装は adapter 層で scan_contacts を結線する（次フェーズのフロントが利用）。
    """

    def scan(self, request: Any) -> Any:
        """ScanContactsRequest 相当を受け、events/summary を持つ結果を返す。"""
        ...
