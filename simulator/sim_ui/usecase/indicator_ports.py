"""指標供給・因果性検定の境界（Port・usecase 層・Phase 3 F-5）。

ISP: クライアントごとに分ける。系列の計算（indicator_ui 経由）・台帳の永続化（FS）・
検定対象母集合の導出（catalog）はそれぞれ別の変更要因を持つため、1 つの Port にまとめない。

DIP: Interactor はこれらの抽象にのみ依存し、indicator_ui / pandas / FS を知らない。

1 ファイルに 3 Port を置くのは既存 `job_ports.py`（4 Port）と同じ規約。

契約改訂（2026-08-11 裁定 A・実測起因）: 系列取得は**束（bundle）単位**にする。
1 指標の計算は 1 回で全系列を返すため、系列ごとに問い合わせる契約だと同じ計算を
系列数ぶん重複して払う（実測: 母集合 26 組が 122 系列に多重化し 1 パス 138.5 秒）。
"""
from __future__ import annotations

import abc

from simulator.sim_ui.usecase.indicator_models import (
    IndicatorSpec,
    LedgerSnapshot,
    SeriesBundle,
    TailBundle,
)


class CausalSeriesProbePort(abc.ABC):
    """指標系列を 2 通りの計算方式で取り出す抽象（基本設計書 §3.5.4 の案 i / 案 ii）。

    両メソッドとも ``limit=None``・``mode="full"`` の窓（データセット先頭 →
    ``until_time``）で計算する。窓の左端を動かさないため
    ``series_full(until_time=t_N)`` の各系列の末尾点は ``series_upto(until_time=t_N)``
    と同じ入力から作られる（prefix 関係）。この関係が崩れると、案 i と案 ii の差が
    「実装差」なのか「窓の取り方の差」なのか区別できなくなる。
    """

    @abc.abstractmethod
    def series_full(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: "int | None",
    ) -> SeriesBundle:
        """案 ii: ``until_time`` までを 1 回計算した**全系列**の全点を返す。

        ``until_time=None`` は全期間（供給の既定）。検定では案 i の最終窓と同じ時刻を渡す。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def series_upto(
        self,
        spec: IndicatorSpec,
        *,
        ref: str,
        timeframe: "str | None",
        until_time: int,
    ) -> TailBundle:
        """案 i: ``until_time`` まで truncate して 1 回計算した**全系列の末尾点**を返す。

        その時刻に点が無い系列は ``None``。末尾点の時刻が ``until_time`` と異なる場合も
        ``None``（＝その時刻の値は無い）とし、時刻をずらして返さない。

        キー集合は同条件の :meth:`series_full` の**部分集合**になり得る（窓が短いうちは
        compute が系列そのものを返さない。2026-08-11 実測: jp225_tick/5m の先頭 20 本で
        moving_averages の応答は空 list）。現れない系列は「その時刻に点が無い」を意味する。
        逆に :meth:`series_full` に無い系列を返してはならない（比較の前提が壊れる）。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def bar_times(
        self, *, ref: str, timeframe: "str | None", count: int
    ) -> "list[int]":
        """**データセット先頭から** ``count`` 本のバー時刻列を返す（``count<=0`` は全件）。

        末尾 N 本ではなく先頭 N 本にするのは、窓の左端を動かさないため。左端が動くと
        EMA 系の seed 位置が変わり、実装差ではない不一致が生まれる。窓を短くする効果は
        「先頭からの本数を減らす」ことで得る（案 i の総費用は本数の 2 乗で減る）。
        """
        raise NotImplementedError


class IndicatorCausalityLedgerPort(abc.ABC):
    """因果性検定の結果台帳（機械生成のみ・手書き禁止）の抽象。"""

    @abc.abstractmethod
    def read(self) -> LedgerSnapshot:
        """台帳を読む。不在・schema 不一致は
        :class:`~simulator.sim_ui.usecase.indicator_models.CausalityLedgerUnavailableError`。
        """
        raise NotImplementedError

    @abc.abstractmethod
    def write(self, snapshot: LedgerSnapshot) -> None:
        """台帳を書く（検定 CLI だけが呼ぶ）。"""
        raise NotImplementedError


class IndicatorCatalogSourcePort(abc.ABC):
    """検定対象の母集合（indicator × variant × 既定 params）の抽象。"""

    @abc.abstractmethod
    def specs(self) -> "list[IndicatorSpec]":
        """検定対象の申告一覧を返す。"""
        raise NotImplementedError
