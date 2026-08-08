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
        """``[{time,open,high,low,close}]`` を返す（resample + tail(limit)）。

        tick 源（volume 列あり）は各足に optional ``tickvol``（足内実 tick 数・int）を
        additive に付与する（ISSUE-044 real_ticks ETA 用）。非 tick 源は付与しない。
        """
        ...


@runtime_checkable
class WindowedCandlePort(Protocol):
    """/candles の「開始時刻起点」窓取得（リプレイバーのカレンダー選択＝再生開始日）。

    ``load_candles`` が末尾 N 本（tail）なのに対し、本 Port は ``start``（UNIX 秒・含む）以降の
    足を先頭から ``limit`` 本返す。``pre`` は ``start`` の直前に付ける前置き本数（指標のウォーム
    アップ＋開始日より前の相場文脈）。返す足の形は ``load_candles`` と完全同一（``tickvol`` 込み）。
    """

    def load_candles_from(
        self,
        ref: str,
        timeframe: "str | None",
        start: int,
        pre: int,
        limit: "int | None",
    ) -> "list[dict]":
        """``time >= start`` の最初の足の ``pre`` 本手前から ``limit`` 本を返す。"""
        ...


@runtime_checkable
class AvailableDaysPort(Protocol):
    """/available_days 用の「足が存在する日」列挙（カレンダーのグレーアウト判定）。"""

    def load_days(self, ref: str, timeframe: "str | None") -> "list[str]":
        """足が 1 本以上存在する UTC 日を ``"YYYY-MM-DD"`` の昇順 list で返す。"""
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

    def bar_time(self, timeframe: str, unix_sec: int) -> int:
        """その時刻が属するバーの time を返す（ISSUE-290）。

        規則源は ``marketdata.tf_meta.bar_time_unix``（ローソク＝ロールアップと同一のラベル
        規約）。usecase は時間足を一切知らない（周期秒・floor・セッション日・暦周期の分岐を
        持たない）＝ライブ側 `forming_states` の `bar_time_fn` 注入と同一の設計。
        """
        ...

    def period_start(self, timeframe: str, unix_sec: int) -> int:
        """その時刻が属する期間の **UTC 始端** 秒を返す（ISSUE-292）。

        規則源は ``marketdata.tf_meta.period_start_unix``。``bar_time``（ラベル）とは**別物**で、
        セッション足では一致しない（実測: 1D はラベルが暦日の UTC 深夜・始端が前日 21:00 UTC）。
        「どの足がこの期間に属するか」の判定は必ず始端で行う（ラベルで判定すると、期間の
        前半に属する足が 1 本も選ばれず、進行中期間の形成足が作られない）。
        """
        ...

    def project(
        self, series: "list[dict]", chart_times: "list[int]", compute_tf: str,
    ) -> "list[dict]":
        """計算足 H の系列を、チャート足 C のバー時刻列へ投影して返す（ISSUE-287）。

        投影規約（確定済み期間＝その時点で確定していた値／進行中期間＝形成値）の唯一源は
        indicator_ui の ``adapter.compute.mtf_projection``。本 Port は「同じ規約を使う」ことを
        契約として宣言するだけで、規則を再実装しない（リプレイ独自の投影を作らない）。
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

    def compute_latest_seq(
        self,
        indicator: str,
        variant: str,
        prefix_bars: "list[dict]",
        tails: "list[list[dict]]",
        params: dict,
    ) -> "list[list[dict]]":
        """足内推移の各時点の latest series を同順で返す（ISSUE-233・窓の再変換を排す）。

        ``prefix_bars`` は全時点で共通の確定バー列、``tails[i]`` は時点 i の末尾差分
        （``forming_bar.apply`` を末尾へ適用した 1〜2 本）。``compute(..., "latest",
        prefix_bars + tails[i], ...)`` を各 i について呼んだ結果と **同値**である。

        差は「共通の窓を 1 回だけ計算源の表現へ変換し、時点ごとには末尾だけを差し替える」
        点だけ。1 ステップの限界費用を指標計算そのものだけにするために要る（実測: 変換を
        毎回行うと 1 ステップ 2.1ms・指標計算は 0.36ms）。
        """
        ...


@runtime_checkable
class IntrabarWindowPort(Protocol):
    """/intraday 用の足内 m1 行・実ティック mid の取得。"""

    def load_m1_rows(self, ref: str, start: int, end: int) -> "list[list[float]]":
        """区間 ``[start,end)`` の m1 OHLC 行（``[o,h,l,c]``・上位足は cap 済）を返す。"""
        ...

    def load_raw_ticks(self, start: int, end: int) -> "list[tuple[int, float, float]]":
        """区間を跨ぐ**生ティック** ``[(sec, bid, ask), ...]`` を返す（cap 無し・整形しない）。

        ISSUE-031: 以前は ``load_ticks`` が mid 算出・窓フィルタ・外れ値除去（domain E-4）まで
        済ませた ``(sec, mid)`` を返していた。これは**本質ルールの適用を各 adapter に委ねる**契約で、
        tick 源を差し替えるたびに `mid_series` を再結線する必要があり、結線漏れが静かに
        「外れ値除去なしの mid 列」を生む。契約を「素の観測値を運ぶ」ことに限定し、
        本質ルールの適用は usecase（:func:`~usecase.intrabar_window.intrabar_window`）へ寄せる。
        """
        ...


@runtime_checkable
class MarketProfileFormingPort(Protocol):
    """/market_profile_forming 用の MP サブバー tick 逐次成長データ源（indicator_ui bridge 委譲）。

    クライアント DwellAccumulator が初回取得する base（GRID_W 固定グリッド累積・不変）＋ forming 期間の
    tick 列 ＋ active table を束ねた ``(status, body)`` を返す。``now`` は必ずリビール T を渡す
    （因果＝T 以前のみ・未来リーク防止）。実装は adapter 層（bridge 委譲）に閉じる（DIP）。
    """

    def forming(
        self,
        ref: str,
        timeframe: "str | None",
        now: "int | None",
        base: Any,
        since: Any,
        bins: Any,
        va: Any,
        barw: Any,
        frm: Any = None,
    ) -> "tuple[int, dict]":
        """``(status, body)`` を返す（非 tick ref / 非対応 tf は 400 nested error）。

        ``frm``（任意・既定 None）: セッション窓 MP の base 累積下限 time（当日始まり=floor(now,86400)）。
        指定時は base を [frm, formingStart) の当日経過ぶんへ限定する。None は従来全期間 base（後方互換）。
        """
        ...


@runtime_checkable
class MarketProfilePort(Protocol):
    """/market_profile 用の MP データ源（indicator_ui bridge 委譲・normal/sessions/replay モード）。

    足ベース TPO / dwell プロファイルを ``(status, body)`` で返す。``to`` は必ずリビール T を渡す
    （因果＝as-seen-at-t＝T 以前に観測できた足のみで集計・未来リーク防止）。実装は adapter 層
    （bridge 委譲）に閉じる（DIP）。ticklive×{1W,1M} は forming が非対応（本 Port は as-of-cursor で代替）。
    """

    def profile(
        self,
        ref: str,
        timeframe: "str | None",
        limit: Any,
        bins: Any,
        va: Any,
        src: Any,
        barw: Any,
        to: Any,
        frm: Any = None,
        today: Any = None,
        sessions: Any = None,
    ) -> "tuple[int, dict]":
        """``(status, body)`` を返す（未知 ref / 未知 tf は 400 nested error）。

        ``to``（任意）: リプレイ時間カーソル（UNIX 秒・リビール秒粒度＝単一時計・ISSUE-129）。指定時は
        ``time<=to`` の足だけで集計し（as-seen-at-t）、zp は now=to として現在時刻に読む。
        ``frm``/``today``/``sessions`` は増分2/日別分割の任意フラグ（None/省略は現行挙動）。
        """
        ...


@runtime_checkable
class TickvolProfilePort(Protocol):
    """/tickvol_profile 用の取引密度プロファイル源（indicator_ui bridge 委譲）。

    セッション日内の時刻帯別ティック密度と、そこから決まる HIGH 帯を ``(status, body)`` で返す。
    ``until`` は必ずリビール T（単一時計 to）を渡す。``until`` が属するセッション日は集計に含めない
    （当日を覗かない＝因果・未来リーク防止）。実装は adapter 層（bridge 委譲）に閉じる（DIP）。
    """

    def profile(
        self,
        ref: str,
        sessions: Any = None,
        pct: Any = None,
        until: Any = None,
    ) -> "tuple[int, dict]":
        """``(status, body)`` を返す（未知 ref は 400 nested error）。"""
        ...


@runtime_checkable
class CatalogPort(Protocol):
    """``GET /catalog`` 用の指標 param スキーマ源（indicator_ui bridge 委譲）。

    param 既定値と **variant ごとの受理 param（paramScopes）** を ``(status, body)`` で返す。
    単一情報源はライブ側 back（``call_binding._TABLE``）であり、リプレイはそれを read-only
    再利用する（ISSUE-278 #8/#4）。front はこの応答で「表示するコントロール」「送信する params」を
    決めるため、経路が無いと受理しない param を送って ``validation`` エラーになる。
    実装は adapter 層（bridge 委譲）に閉じる（DIP）。
    """

    def catalog(self) -> "tuple[int, dict]":
        """``(status, body)`` を返す。"""
        ...
