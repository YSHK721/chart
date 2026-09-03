"""/live_ticks の末尾値同梱（ISSUE-250 Phase 1）— 薄殻。

責務: クエリ検証 → 窓ロード → 形成中バーの材料（seed・端数 tick）解決 → 純ロジック
（:mod:`usecase.serve_live_tick_tails`）と計算アダプタ（:mod:`adapter.compute.live_tick_tails`）の
結線のみ。計算規則・畳み方は持たない。

申告が無い／不正／材料不足のときは ``None`` を返し、``/live_ticks`` は従来応答のままにする
（後方互換・byte 不変）。

ISSUE-251: ``/live_ticks`` が持つのは ``since`` 以降の**増分**だけであり、これをそのまま畳むと
周期の累積（open/high/low/volume）が poll ごとに消える。本殻が周期の累積を
:func:`adapter.compute.forming_bar.rollup_forming_bar`（``/forming_bar`` と同一実体）から復元し、
純ロジックへ seed として渡す。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from adapter.compute import forming_bar as forming_bar_mod
from marketdata.resample import is_known_timeframe
from marketdata.tf_meta import bar_time_unix, period_start_unix
from adapter.compute.indicator_compute_adapter import IndicatorComputeAdapter
from adapter.compute.latest_dispatch import latest_compute
from adapter.compute.forming_bar import closed_gap_bars, inject_forming_bars
from adapter.compute.live_tick_tails import (
    forming_bar_of_state,
    make_tail_at,
    window_with_forming,
)
from usecase.dataset_port import dataset_port as _dataset_port
from usecase.serve_live_tick_tails import (
    merge_tail_batches,
    parse_specs,
    states_for_batch,
    tails_for_ticks,
)

logger = logging.getLogger(__name__)


def _bar_time_fn(tf: str):
    """``(tick ms) -> バー time`` を tf に束縛して返す。

    規則は :func:`marketdata.tf_meta.bar_time_unix`（全時間足で唯一の入口）に閉じる。ここで
    tf ごとに規則を分岐させない＝日中足も 1D も 1W/1M も同じ経路・同じ更新粒度になる。
    """
    return lambda ms: int(bar_time_unix(tf, int(ms) // 1000))


def _bar_seed(ref: str, tf: str, first_ms: int, bar_time: int, *, buffer: Any, forming: Any):
    """増分先頭 tick の直前までの「バーの累積」を ``(seed, prior_ticks)`` で返す（ISSUE-251）。

    材料は 2 系統あり、**バーが一致するほうを使う**（tf による分岐は無い）:

    1. ロールアップ方式 forming（``rollup_forming_bar``＝確定畳み込み base ＋ バッファ tail の O(1)
       合成・``/forming_bar`` と同一実体＝フロントのシード源・全 tf 対応）を増分先頭 tick の秒で
       評価する。これは ``[バー始端, floor秒(first_ms))`` を被覆するので、残る端数
       ``[floor秒(first_ms), first_ms)`` だけをバッファから補う（同一秒に複数 tick が来るため
       端数を捨てると volume を取りこぼす）。
    2. 1 のバーが現在のバーと違うとき（バー境界直後は M1/ロールアップの焼き込みが最大 1 分遅れ、
       base が前バーのままになる）は seed を捨て、**バー始端以降のバッファ tick を全部畳む**。
       前バーの値を持ち込まないまま、累積を復元できる（バッファ保持は直近 30 分）。

    buffer 未注入（テスト既定・非 served）は ``(None, [])``＝増分だけで畳む。
    """
    if buffer is None:
        return None, []
    first_ms = int(first_ms)
    cut_sec = first_ms // 1000
    try:
        seed = forming.rollup_forming_bar(ref, tf, cut_sec, buffer=buffer)
    except Exception:  # noqa: BLE001 — seed 取得の失敗で tails 全体を落とさない。
        seed = None
    try:
        if seed is not None and int(seed.get("time", -1)) == int(bar_time):
            lo_ms = cut_sec * 1000 - 1                      # seed は秒境界まで＝端数のみ補う
        else:
            seed = None
            lo_ms = int(period_start_unix(cut_sec, tf)) * 1000 - 1
        prior = [t for t in buffer.ticks_since(lo_ms) if int(t[0]) < first_ms]
    except Exception:  # noqa: BLE001 — 材料が揃わなければ増分のみへ縮退（tails は落とさない）。
        return None, []
    return seed, prior


def _tails_horizon_ms(query: "dict[str, list[str]]", now_ms: "int | None") -> "int | None":
    """末尾値を計算する下限時刻（これ **より新しい** tick だけ計算）を返す。無指定は ``None``。

    フロントは ``tailsWithinMs`` として「個別に描く区間の長さ」を申告する（ISSUE-257）。
    区間の実体は再生遅延（``LiveTickPlayer.DELAY_MS``）＋ poll 間隔であり、**その定義を持つのは
    フロントただ 1 つ**。サーバは値を写さず、申告された長さを自分の時計（``now_ms``＝応答の
    ``serverNowMs`` と同一値）から差し引くだけにする。こうすると定義は 1 箇所に留まり、
    時計の権威はサーバのまま（ISSUE-254 と同じ「値の二重定義を作らない」方針）。

    未申告（旧フロント）は ``None``＝全 tick で計算＝従来挙動（後方互換）。
    """
    raw = (query.get("tailsWithinMs") or [None])[0]
    if now_ms is None or raw is None or not str(raw).isdigit():
        return None
    within = int(raw)
    if within <= 0:
        return None
    return int(now_ms) - within


def _spec_timeframe(spec: Any, chart_tf: str) -> str:
    """その spec を計算する足（ISSUE-274）。``params.timeframe`` の override を解決する。

    規則は front の ``TimeframeController.effectiveTimeframe`` / ``/compute`` の
    ``computeTimeframe`` と同一（``'chart'``・未指定・未知値はチャート足に追従）。
    """
    raw = (spec.params or {}).get("timeframe")
    if not isinstance(raw, str) or raw == "chart" or not is_known_timeframe(raw):
        return chart_tf
    return raw


def _load_window(port: Any, ref: str, tf: str, limit_raw: Any) -> Any:
    """計算足 ``tf`` の窓（直近 N 本）を返す。材料が無ければ ``None``。

    窓を絞らないと 1 ステップが全件に比例する（実測 50,000 本で 29.4ms/tick →
    1,386 本で 3.8ms/tick）。本数の数え方は ``/compute`` の投影経路と同一（計算足の本数）。
    """
    try:
        df = port.load_dataframe(ref, tf)
    except Exception:  # noqa: BLE001 — 1 つの計算足の失敗で他の指標の末尾値を落とさない。
        return None
    if df is None or len(df) == 0:
        return None
    if limit_raw is not None and str(limit_raw).lstrip("-").isdigit():
        limit = int(limit_raw)
        if limit > 0:
            df = df.tail(limit)
    return df


def _set_last_bar(window: Any, values: "dict[str, float]") -> None:
    """窓の末尾行だけを形成中バーで上書きする（pandas 依存はここに閉じる）。"""
    last = len(window) - 1
    for key, val in values.items():
        if key in window.columns:
            window.iat[last, window.columns.get_loc(key)] = val


def handle_live_tick_tails(
    query: "dict[str, list[str]]",
    ticks: "list",
    *,
    adapter: Any = None,
    buffer: Any = None,
    forming: Any = None,
    now_ms: "int | None" = None,
) -> "list[dict] | None":
    """``/live_ticks`` へ同梱する ``tails`` を組む。組めないときは ``None``。

    ``now_ms`` は応答の ``serverNowMs``。``tailsWithinMs`` の申告と併せて「個別に描かれる
    tick だけ計算する」下限を決める（ISSUE-257）。未注入・未申告は全 tick 計算＝従来挙動。
    """
    raw = (query.get("specs") or [None])[0]
    ref = (query.get("datasetRef") or [None])[0]
    tf = (query.get("timeframe") or [None])[0]
    limit_raw = (query.get("limit") or [None])[0]
    if not raw or not ref or not is_known_timeframe(tf) or not ticks:
        return None
    try:
        specs = parse_specs(json.loads(raw))
    except (TypeError, ValueError):
        return None
    if not specs:
        return None

    port = _dataset_port()
    if not port.is_known(ref) or not port.is_known_timeframe(tf):
        return None

    # ISSUE-274: 計算足（params.timeframe）ごとに束ねる。上位足指標は「その tick 時点の
    #   **上位足の**形成中バー」で計算しなければならず、畳み方（どのバーに属するか）も窓も
    #   計算足ごとに別になる。チャート足で畳んだ形成中バーを上位足の窓へ差し込むと、
    #   上位足指標へチャート足の値が入る（黙って別足の値を描く）。
    #   グループ分けだけが増え、1 グループぶんの手順は従来と同一。
    groups: "dict[str, list]" = {}
    for spec in specs:
        groups.setdefault(_spec_timeframe(spec, tf), []).append(spec)

    # 形成中バーの累積（states）は全 tick で畳む（open/high/low/volume は 1 本も飛ばせない）。
    #   費用が tick 数に比例するのは末尾値の計算だけなので、そちらだけを地平で絞る。
    horizon = _tails_horizon_ms(query, now_ms)
    wanted = None if horizon is None else (lambda st: int(st.tick_ms) > horizon)
    first_ms = int(ticks[0][0])
    compute_adapter = adapter or IndicatorComputeAdapter()
    forming_mod = forming or forming_bar_mod

    batches: "list[list[dict]]" = []
    for group_tf, group_specs in groups.items():
        df = _load_window(port, ref, group_tf, limit_raw)
        if df is None:
            continue        # 当該計算足の材料が無い＝そのグループだけ落とす（他は出す）。
        # 「この tick はどのバーに属するか」は tf_meta.bar_time_unix ただ 1 つ（全 tf 同一経路）。
        #   形成中バーは「バーの累積」でなければならない（ISSUE-251）。増分だけを畳むと poll ごとに
        #   open/high/low/volume がリセットされ、フロントが描いたローソクと値が食い違う。
        bar_time_fn = _bar_time_fn(group_tf)
        seed, prior = _bar_seed(
            ref, group_tf, first_ms, bar_time_fn(first_ms),
            buffer=buffer, forming=forming_mod,
        )
        states = states_for_batch(prior, ticks, bar_time_fn, seed=seed)
        # 🔴-1: 窓は確定分（1m なら M1 CSV の排他 floor で M-1 まで）しか無い。末尾行への代入が
        #   正しいのは「窓の末尾＝形成中バー」のときだけなので、供給側でここを揃える。
        #   ISSUE-481: 揃えるのは 2 つある。(a) 形成中バー（states[0]＝この増分の先頭 tick 時点の
        #   累積）の周期、(b) M1 焼き込み猶予（live_tick_watch の grace 約 12-17 秒）の間に
        #   確定窓が欠いている**閉じた**周期。(b) を埋めないと /compute（閉周期合成 ISSUE-162）と
        #   別の窓で計算することになる。合成規則は closed_gap_bars ただ 1 つで、ここは
        #   「どの ref/tf の・どこからどこまでの穴か」を束ねるだけである（上限も列挙も知らない）。
        #   バッチが周期をまたいだ以降の tick は make_tail_at 側が窓へ行を足す。states は
        #   ticks と同数・同順で、ticks が空なら本関数は既に None を返しているため必ず 1 件以上ある。
        # ISSUE-278 #3: 増分器の実装バグは adapter が握らず、この境界まで伝播させる。ここで
        #   **1 度だけ記録**して当該グループを落とす（他の計算足の末尾値は出す）。以前は adapter が
        #   無言で None を返しており、指標が痕跡なくティック更新から消えて原因が追えなかった。
        #   窓供給（window_with_forming）も同じ契約に入れる（2 巡目レビュー 🟡-4）。注入は
        #   事前条件を緩めない設計（欠けた OHLCV キーで KeyError）なので、契約の外に置くと
        #   1 つの計算足の材料破損で /live_ticks の応答全体が落ちた。_load_window・_bar_seed と
        #   同じ「そのグループだけ落とす」規律へ揃える。窓は失敗時に作られない（try の中で
        #   供給してから使うため、使わない窓を作って捨てる経路が構造上できない）。
        try:
            first_bar = forming_bar_of_state(states[0])
            df = window_with_forming(
                df, first_bar, inject=inject_forming_bars,
                gap_bars=lambda last: closed_gap_bars(
                    ref, group_tf, last, int(first_bar["time"])
                ),
            )
            tail_at = make_tail_at(
                df=df, adapter=compute_adapter,
                latest_compute=latest_compute, set_last_bar=_set_last_bar,
                inject=inject_forming_bars,
            )
            batches.append(tails_for_ticks(states, group_specs, tail_at, wanted=wanted))
        except Exception:  # noqa: BLE001 — 記録したうえで当該計算足だけ落とす（無言にしない）。
            logger.exception(
                "live_ticks: 末尾値の窓供給または計算に失敗（計算足=%s・指標=%s）"
                "＝当該グループを落とす",
                group_tf, [s.indicator_id for s in group_specs],
            )

    if not batches:
        return None
    return merge_tail_batches(batches)
