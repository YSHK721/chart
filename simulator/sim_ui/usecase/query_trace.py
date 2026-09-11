"""何を返すか（usecase 層・RUN_TRACE_BASIC_DESIGN §9.2/§9.3）。

アクター（改訂の動機）: **窓・列の解釈と、返す量の決定**。読み方
（`simulator/adapter/trace/parquet_trace_store.py`）とも、事象の意味
（`simulator/sim_ui/usecase/derive_trace_events.py`）とも別の理由で変わる。

**返す量（実測に基づく設計・2026-09-10。憶測ではない）**:
    実ティック 1 ヶ月 run（JP225 2026-01）の trace_points.parquet は
    **1,036,394 行 × 18 列 / 9.7MB**。画面が描くのは数千点である。全量を渡して front で
    捨てる形は絶対命令（作ってから捨てるの禁止）に反する。よってサーバが窓を受けて
    必要ぶんだけ返す。先例は `dashboard_ui/web/js/domain/ladder_window.js`
    （「窓の外は**建てない**——建ててから隠すと捨てる色計算が毎描画発生する」）。

**間引きを採らない理由（対症療法の禁止）**:
    「N 点ごとに拾う」「同一秒を代表値へ潰す」は §9.0 が是正した欠陥と同型であり、
    equity / margin_level の谷が消えて DD 分析が壊れる（既存 front の `chart.js`
    `dedupeCurve` がまさにその形）。絞り方は**窓を狭めること**ただ 1 つとし、窓が
    広すぎるときは黙って間引かず `TraceWindowTooWideError` で断る。
    俯瞰用の集約（バケットごとの min/max 包絡）は §9.6 の「読んだ行数 − 返した行数 = 0」
    と両立しないため、別の裁定を要する（本段階の範囲外・申し送り）。

**上限判定に行を読まない**（絶対命令・§9.6）:
    読んでから断るのは「作ってから捨てる」形そのものである。件数は Port の `count`
    （parquet の述語評価だけで答える）に問い、読みは通ったときにしか発行しない。

**DD の式は借りる（新規実装を作らない・§9.4）**: `simulator/usecase/mt5_parity.py` の
    `equity_dd_absolute` / `equity_dd_maximal` / `equity_dd_maximal_percent`。
    `sim_ui/usecase` と `simulator/usecase` は**同じ usecase 層**であり相互 import は
    層ゲート上合法である。第 2 の DD 式を書けば report と分析タブが静かに食い違う。

依存規律: adapter を掴まない（読み取りは `TracePointsPort` 経由）。pandas / pyarrow を
import しない。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from simulator.sim_ui.usecase.derive_trace_events import (
    REQUIRED_COLUMNS as _EVENT_COLUMNS,
    DeriveTraceEventsInteractor,
    TraceEvent,
)
from simulator.domain.exceptions import ConfigError
from simulator.sim_ui.usecase.trace_query_ports import TraceExtent, TracePointsPort
from simulator.usecase.mt5_parity import (
    equity_dd_absolute,
    equity_dd_maximal,
    equity_dd_maximal_percent,
)

#: 分析タブが描く列の**宣言**（§9.1「既存 3 窓の隣に別の量を並べる」）。
#:
#: 既存の「Balance」「Drawdown」窓は在るが別物である——中身は agg.balance_curve
#: （確定トレードの `exit_time` × 走行残高）であり、窓のラベル自身が「**残高ベース**DD」と
#: 名乗っている。equity（含み損益込み）・証拠金・維持率・保有玉数は
#: report.json にも stats.json にも無い。ここが唯一の供給源である。
#:
#: 事象導出が要る列（`derive_trace_events.REQUIRED_COLUMNS`）を**必ず含む**ことは
#: `simulator/sim_ui/tests/unit/test_query_trace.py` が固定する（取り落とすと事象が
#: 黙って 0 件になる）。
ANALYSIS_COLUMNS: "tuple[str, ...]" = (
    "time", "balance", "equity", "margin", "margin_level", "open_count", "halted",
)

#: 1 回の問い合わせで返す行数の上限。
#:
#: 値の根拠（実測）: 実 1 ヶ月 run は 1,036,394 行あり、lightweight-charts が 1 系列
#: あたり快適に扱う点数は数千である。上限は「窓を狭めよ」と伝えるための関門であって
#: 間引きの発動点ではない（間引きは行わない）。
MAX_RETURNED_ROWS = 20_000

#: 「どの窓なら収まるか」を探すときに許す件数問い合わせの上限（二分探索の予算）。
#:
#: 探索は行を 1 つも materialise しない（`TracePointsPort.count` の契約）。予算を置くのは
#: 病的な入力で探索が止まらないことを構造的に保証するためであり、回数そのものを仕様に
#: するためではない（実測 1,036,394 行 / 上限 20,000 行では 10 回・253 ms で収束した）。
MAX_WINDOW_PROBES = 24

#: 提案窓が使う上限の下限割合。収まりさえすればよいのではない——1 行の窓を返せば
#: 「収まる」は恒真になる。画面が読める量（上限の半分以上）まで広げる。
_WINDOW_FILL_FLOOR = 0.5


class TraceWindowTooWideError(Exception):
    """窓に入る行数が上限を超えた。黙って間引かず、狭めるべき量を添えて断る。"""


@dataclass(frozen=True)
class TraceAnalysis:
    """分析タブ 1 枚ぶんの答え。

    ``window``: 実際に使った窓 `(start, end)`（epoch ミリ秒。無指定側は `None`）。
    ``rows``: 返した行数。
    ``columns``: `ANALYSIS_COLUMNS` の列（列名 → 素の並び）。
    ``events``: 導出した事象（時刻昇順）。
    ``drawdown``: equity ベースの DD（式は `usecase/mt5_parity` から借りる）。
    """

    window: "tuple[Any, Any]"
    rows: int
    columns: "Mapping[str, list]"
    events: "tuple[TraceEvent, ...]"
    drawdown: "Mapping[str, float]"


class QueryTraceInteractor:
    """窓・列を解釈して分析タブ 1 枚ぶんを返す。"""

    def __init__(
        self, *, source: TracePointsPort, events: DeriveTraceEventsInteractor
    ) -> None:
        self._source = source
        self._events = events

    def extent(self, job_id: str) -> TraceExtent:
        """記録の範囲・run の設定値・**最初に問うべき窓**（行を 1 つも読まない）。

        提案窓を front に計算させない理由（実測・2026-09-10）:
            front が「上限 ÷ 全行数」の比で時間幅を決める形にしていたが、ティック密度は
            一様でない（週末・立会時間）。実ティック 1 ヶ月 run（1,036,394 行）でその比
            から出した窓には **59,030 行**が入り、初回表示が 413 になった。比例配分は
            「密度が一様」という**偽の前提**の上に立った推測である。

            安全率を上げるのは対症療法である（偏りは残り、別の run でまた外れる）。
            行の分布を持つのはサーバなので、**測って答える**。
        """
        extent = self._source.extent(job_id)
        return replace(
            extent, suggested_window=self._window_that_fits(job_id, extent)
        )

    def _window_that_fits(
        self, job_id: str, extent: TraceExtent
    ) -> "tuple[Any, Any]":
        """上限に収まり、かつ上限の半分以上を使う窓を返す（末尾側）。

        末尾を採るのは分析の起点が直近だからである（halt・維持率割れは run の終盤に
        集まる）。探索は件数の問い合わせだけで、行は 1 つも materialise しない。
        """
        first, last = extent.first_time, extent.last_time
        if first is None or last is None or extent.rows == 0:
            return None, None            # 記録が無いのに窓を発明しない。
        end = last + 1                   # 半開なので最後の点を含める。
        if extent.rows <= MAX_RETURNED_ROWS:
            return first, end            # 絞る理由が無い。

        # 行数が多すぎる側（too_wide）と収まる側（fits）を挟み込む。
        too_wide, fits = first, end
        best = end                       # 収まることが判った最も広い開始時刻。
        floor = int(MAX_RETURNED_ROWS * _WINDOW_FILL_FLOOR)
        for _probe in range(MAX_WINDOW_PROBES):
            middle = (too_wide + fits) // 2
            if middle <= too_wide or middle >= fits:
                break
            rows = self._source.count(job_id, start=middle, end=end)
            if rows > MAX_RETURNED_ROWS:
                too_wide = middle
            else:
                best = middle
                if rows >= floor:
                    break                # 十分に広く、かつ収まっている。
                fits = middle
        return best, end

    def analyse(
        self, job_id: str, *, start: "int | None" = None, end: "int | None" = None
    ) -> TraceAnalysis:
        """窓 `[start, end)`（epoch ミリ秒）の分析結果を返す。

        事前条件: ``start`` / ``end`` は epoch ミリ秒か `None`（その側は無制限）。
        事後条件: 窓に入る行を**間引かず全部**返す。事象と DD は同じ 1 回の読みから導く。
        例外:
            `ValueError`                — `start > end`（0 行で成功させない・§7）。
            `TraceWindowTooWideError`   — 窓に入る行数が `MAX_RETURNED_ROWS` を超える。
                このとき**行の読みは 1 回も発行しない**（作ってから捨てない）。
        """
        if start is not None and end is not None and start > end:
            raise ValueError(
                f"分析窓の開始が終了より後です: start={start} end={end}"
            )
        # 上限判定は読みの**前**に置く（読んでから断ると 100MB を捨てることになる）。
        rows = self._source.count(job_id, start=start, end=end)
        if rows > MAX_RETURNED_ROWS:
            raise TraceWindowTooWideError(
                f"窓に入る点が {rows} 行あり上限 {MAX_RETURNED_ROWS} 行を超えます。"
                "窓を狭めてください（間引くと equity / 維持率の谷が消えます）"
            )

        facts = self._source.extent(job_id)
        columns = self._source.read(
            job_id, columns=ANALYSIS_COLUMNS, start=start, end=end
        )
        events = self._events.execute(
            columns, margin_level_floor=facts.margin_level_floor
        )
        return TraceAnalysis(
            window=(start, end),
            rows=len(columns["time"]),
            columns=columns,
            events=events,
            drawdown=_drawdown(columns["equity"], facts.initial_deposit),
        )


def _drawdown(equity: "list[float]", initial_deposit: float) -> "dict[str, float]":
    """equity 列から DD を出す。**式は `usecase/mt5_parity` の実体をそのまま呼ぶ**。

    0 行の窓は 0.0 で返す（`mt5_parity` の式は B_0 だけの系列に対して 0 を返すが、
    ここで空の意味を明示しておくほうが読み手に親切である）。
    """
    if not equity:
        return {
            "equity_dd_absolute": 0.0,
            "equity_dd_maximal": 0.0,
            "equity_dd_maximal_percent": 0.0,
        }
    return {
        "equity_dd_absolute": equity_dd_absolute(equity, initial_deposit),
        "equity_dd_maximal": equity_dd_maximal(equity, initial_deposit),
        "equity_dd_maximal_percent": equity_dd_maximal_percent(
            equity, initial_deposit
        ),
    }


def verify_column_declarations(
    *, returned: "tuple[str, ...]", required: "tuple[str, ...]"
) -> None:
    """返す列の宣言が、事象導出が要る列を**過不足なく含む**ことを表明する。

    なぜ `assert` ではなく例外か（工程 5 レビュー 🟡-1・実測 2026-09-11）:
        module 直下の `assert` は `python -O` で **bytecode から消える**（失敗する
        assert を持つモジュールを `-O` で import すると何も起きないことを実測）。
        この表明が消えると、宣言が食い違ったまま起動し、実行時に
        `derive_trace_events` が `KeyError` を出す——それは
        TraceApiController._guarded の翻訳表に無いので **500 になり、front は理由を
        受け取れない**（事象が 0 件なのか列を取り落としたのか区別できない）。

        同じ「宣言の食い違い」に対して `simulator/domain/bar_time.py` は例外送出
        （`ConfigError`）を選んでいる。同一変更集合の中で判断を割らない。

    例外: 要る列が返す列に含まれていなければ `ConfigError`（起動時に落ちる）。
    """
    missing = tuple(name for name in required if name not in returned)
    if missing:
        raise ConfigError(
            "事象導出が要る列が、分析が返す列の宣言に含まれていません: "
            f"{missing}（返す列={returned}）",
            context={"missing": list(missing), "returned": list(returned)},
        )


# 起動時に 1 回走らせる（import 順に依らず、宣言の食い違いをその場で落とす）。
verify_column_declarations(returned=ANALYSIS_COLUMNS, required=_EVENT_COLUMNS)
