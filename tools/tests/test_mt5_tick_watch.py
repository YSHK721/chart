"""常駐供給ループの検定（ISSUE-447 段階 1 / 設計 §4 CLI 面・E-10・1 周期の合成）。

``tools/mt5_tick_watch.py`` は Composition Root である。ここが持ってよいのは
「部品を組み立てる順序」と「運用の面（CLI・周期・バックオフ）」だけで、規則は 1 つも持たない
（規則を tools に置いたら、それは合成点ではなくライブラリの仕事である）。

よって本検定が固定するのも 3 つに絞る:
    1. 運用の面（オプション・既定値・拒否条件）が勝手に増減しないこと
    2. 1 周期の**合成の順序**（取得 → 取込 → 表示系列 → 日次確定 → 再構築）
    3. 失敗の扱い（待てば直る／直らないの分岐とバックオフ）

実ネットワークにも MetaTrader5 にも依存しない（供給元は注入する）。データは tmp_path に閉じる。
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pandas as pd
import pytest

from marketdata import tick_m1
from marketdata.mt5_ticks import fakes, ingest, journal, rebuild, server_clock
from marketdata.mt5_ticks.port import Mt5SupplyError, SupplyUnavailable
from tools import mt5_tick_watch as watch

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE = _REPO_ROOT / "tools" / "mt5_tick_watch.py"
_SECRET = "watch-test-secret-value"
_SUMMER_OFFSET_MS = 3 * 3600 * 1000


def _label_ms(utc: dt.datetime) -> int:
    """UTC 時刻に対応するサーバラベル ms（2026-08 は夏＝UTC+3）。"""
    return int(utc.replace(tzinfo=dt.timezone.utc).timestamp() * 1000) + _SUMMER_OFFSET_MS


def _tape(start: dt.datetime, *, minutes: int, per_minute: int = 4):
    return [
        (_label_ms(start + dt.timedelta(minutes=m, seconds=i * (60 // per_minute))),
         66000.0 + m + i * 0.1, 66010.0 + m + i * 0.1)
        for m in range(minutes) for i in range(per_minute)
    ]


@pytest.fixture()
def secret(monkeypatch):
    monkeypatch.setenv(watch.SECRET_ENV, _SECRET)
    return _SECRET


def _argv(tmp_path, *extra):
    return ["--data-dir", str(tmp_path), "--once", *extra]


def _wrote_anything(tmp_path: Path) -> bool:
    return any(p.is_file() for p in tmp_path.rglob("*"))


class _Restart(NamedTuple):
    """再起動 1 回の観測（確定された日・ジャーナル探索の発行回数）。"""

    finalized: "list[dt.date]"
    journal_probes: int


def _restart_on_the_next_day(root: Path, *, stored_days: int, monkeypatch) -> _Restart:
    """``stored_days`` 日ぶんの受信済みジャーナルを置き、翌日に 1 周期だけ再起動する。

    測るのは「確定された日」と「ジャーナルのパス探索を何回発行したか」の 2 つである。後者は
    起動時の種付けが台帳の大きさに比例していないことを表す（回数そのものは期待値に焼き込まず、
    蓄積 2 点で**増えないこと**だけを固定する）。
    """
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    root.mkdir(parents=True, exist_ok=True)
    days = [dt.date(2026, 8, 25) - dt.timedelta(days=offset) for offset in range(stored_days)]
    for day in days:
        journal.append(
            day, _tape(dt.datetime(day.year, day.month, day.day, 9, 0), minutes=1),
            symbol=token, data_dir=root,
        )
    tape = (
        _tape(dt.datetime(2026, 8, 25, 9, 0), minutes=1)
        + _tape(dt.datetime(2026, 8, 26, 9, 0), minutes=2)
    )
    probes = fakes.CallSpy(journal.journal_path)
    monkeypatch.setattr(journal, "journal_path", probes)

    watch.main(
        ["--data-dir", str(root), "--once", "--no-publish"],
        source=fakes.FakeTickSource(tape),
        clock=fakes.FixedClock(dt.datetime(2026, 8, 26, 9, 2, tzinfo=dt.timezone.utc)),
    )

    finalized = [
        day for day in sorted(days)
        if tick_m1.day_parquet_path(day, symbol=token, data_dir=root).is_file()
    ]
    return _Restart(finalized=finalized, journal_probes=probes.count)


# =====================================================================
# 運用の面（設計 §4）— 増やすときは設計と一緒に変える
# =====================================================================

def test_the_option_surface_is_exactly_the_designed_one():
    """認知負荷を上げるフラグを勝手に増やさない（AST-7 相当の面固定）。"""
    opts = {a for act in watch.build_parser()._actions for a in act.option_strings}
    assert opts == {
        "-h", "--help", "--symbol", "--endpoint", "--key-id", "--interval",
        "--data-dir", "--ref", "--from", "--once", "--no-publish", "--quiet",
    }


def test_the_defaults_are_the_designed_ones():
    """既定値は設計 §4 の値そのもの（黙って別の相手・別の周期を向かない）。"""
    args = watch.build_parser().parse_args([])
    assert (args.symbol, args.endpoint, args.interval, args.ref) == (
        "JP225", "http://172.16.162.129:8771", 5.0, "jp225_mt5"
    )


def test_the_secret_comes_only_from_the_environment():
    """秘密は環境変数 1 つだけ（引数にもファイルにも置かない）。"""
    opts = {a for act in watch.build_parser()._actions for a in act.option_strings}
    assert watch.SECRET_ENV == "MT5_BRIDGE_SECRET"
    assert not {o for o in opts if "secret" in o or "key" == o.strip("-")}


def test_a_missing_secret_stops_before_anything_is_written(tmp_path, monkeypatch):
    """秘密が無ければ何もせずに落ちる（無認証で外へ出る経路を作らない）。"""
    monkeypatch.delenv(watch.SECRET_ENV, raising=False)

    code = watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"))

    assert code != 0
    assert not _wrote_anything(tmp_path)


@pytest.mark.parametrize("interval", ["1.9", "0", "-1"])
def test_an_interval_below_the_floor_is_refused(tmp_path, secret, interval):
    """周期の下限（2.0 秒）を割る指定は拒む（端末を叩き過ぎない）。"""
    code = watch.main(_argv(tmp_path, "--interval", interval, "--from", "2026-08-25 12:00:00"))

    assert code != 0
    assert not _wrote_anything(tmp_path)


# =====================================================================
# E-10 コールドスタートは --from 必須
# =====================================================================

def test_a_cold_start_without_an_explicit_from_is_refused(tmp_path, secret):
    """E-10: 再開点が無いのに既定（now−30 分等）を作らない → 非 0 終了・書込 0。"""
    source = fakes.FakeTickSource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2))

    code = watch.main(_argv(tmp_path), source=source)

    assert code != 0
    assert not _wrote_anything(tmp_path)


def test_a_cold_start_with_an_explicit_from_supplies_one_cycle(tmp_path, secret):
    """``--from`` を与えれば 1 周期が通る（ジャーナルへ追記される）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    source = fakes.FakeTickSource(_tape(start, minutes=2))

    code = watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source)

    written = journal.read_rows(
        dt.date(2026, 8, 25),
        symbol=ingest.token_for("JP225", fakes.DEFAULT_SERVER),
        data_dir=tmp_path,
    )
    assert code == 0
    assert len(written) == 8


def test_a_warm_start_resumes_from_the_journal_without_from(tmp_path, secret):
    """ジャーナルが在れば ``--from`` は要らない（復元の唯一経路はジャーナル）。"""
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    start = dt.datetime(2026, 8, 25, 9, 0)
    tape = _tape(start, minutes=2)
    journal.append(dt.date(2026, 8, 25), tape[:4], symbol=token, data_dir=tmp_path)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc))

    code = watch.main(
        _argv(tmp_path), source=fakes.FakeTickSource(tape), clock=clock
    )

    assert code == 0
    assert len(journal.read_rows(dt.date(2026, 8, 25), symbol=token, data_dir=tmp_path)) == 8


def test_a_warm_start_probes_near_now_instead_of_the_epoch(tmp_path, secret):
    """``--from`` 無しの起動で、トークン解決の探りを 1970 年へ撃たない（ISSUE-447 実測）。

    実端末では ``from_msc=0`` の探りが VM 側の epoch→datetime 変換で 500 になり、常駐が
    起動できなかった（``--once`` ＋ ``--from`` の経路では顕在化せず、既存検定が緑のまま通した）。

    探りの位置は「これから読む場所」であるべきだが、再開点はジャーナルにあり、ジャーナルの
    パスはトークンから決まり、トークンは探りの応答ヘッダからしか得られない（循環）。よって
    ``--from`` が無いときは**現在時刻のラベル近似**を撃つ。近似で足りるのは、探りが 1 行窓で
    あり目的がサーバ名ヘッダだけだからである（位置の厳密さは要らない）。

    固定するのは 3 つ:
        1. 1970 年を撃たない（VM 側の変換が落ちる位置を選ばない）
        2. 近似が「今」の実在しうるラベル帯（now＋冬/夏オフセット）に収まる
        3. 保守側であること（夏は真のラベルより過去へ落ち、未来へは出ない）
    """
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2)
    journal.append(dt.date(2026, 8, 25), tape[:4], symbol=token, data_dir=tmp_path)
    now = dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc)
    source = fakes.CountingTickSource(tape)

    code = watch.main(_argv(tmp_path), source=source, clock=fakes.FixedClock(now))

    now_ms = int(now.timestamp()) * 1000
    probe = source.calls[0]["from_msc"]
    assert code == 0
    assert probe != 0, "探りが 1970 年（epoch）を指しています。実端末では 500 になります。"
    assert now_ms <= probe <= now_ms + server_clock.SUMMER_OFFSET_SECONDS * 1000
    assert probe <= now_ms + server_clock.WINTER_OFFSET_SECONDS * 1000
    # 探りの位置を選び直しても、発行回数は 1 起動あたり 1 回のまま（往復を増やさない）。
    assert source.token_probes == 1


def test_a_restart_after_midnight_still_finalizes_the_previous_day(tmp_path, secret):
    """日 D の途中で止め D+1 に再起動しても、D の確定（parquet）が落ちない。

    確定候補の日は「当プロセスが観測した日」だけではない。前回の停止で確定に至らなかった日は
    ジャーナルにだけ残っており、それを起動時に拾わないと ``journal.finalize(D)`` が
    **二度と呼ばれない**（ジャーナル在・parquet 無の日が黙って残り続ける）。
    """
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    previous = _tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2)
    journal.append(dt.date(2026, 8, 25), previous, symbol=token, data_dir=tmp_path)
    tape = previous + _tape(dt.datetime(2026, 8, 26, 9, 0), minutes=2)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 26, 9, 2, tzinfo=dt.timezone.utc))

    code = watch.main(_argv(tmp_path), source=fakes.FakeTickSource(tape), clock=clock)

    assert code == 0
    assert tick_m1.day_parquet_path(
        dt.date(2026, 8, 25), symbol=token, data_dir=tmp_path
    ).is_file()


def test_seeding_the_unfinalized_days_is_bounded_by_the_lookback_window(
    tmp_path, secret, monkeypatch
):
    """CX: 起動時の種付けが**保存済み日数に比例しない**（蓄積 2 点で固定）。

    未確定日を拾うのに台帳全体を舐めると、常駐の起動費が保存日数に比例して伸びる。探索窓は
    再開点の復元と同じ :data:`watch.RESTORE_LOOKBACK_DAYS` 日であり、窓の外の日は
    （ジャーナルが在っても）確定しない — 何日でも黙って遡ると、欠測を埋めたのか飛ばしたのかが
    運用者に見えなくなる。
    """
    small = _restart_on_the_next_day(tmp_path / "stored5", stored_days=5, monkeypatch=monkeypatch)
    large = _restart_on_the_next_day(tmp_path / "stored50", stored_days=50, monkeypatch=monkeypatch)

    assert small.finalized == [dt.date(2026, 8, 25)]
    assert large.finalized == small.finalized
    assert large.journal_probes == small.journal_probes


@pytest.mark.parametrize(
    "text,expected",
    [("2026-08-25 12:00:00", 1_787_659_200_000), ("1787659200000", 1_787_659_200_000)],
)
def test_the_from_argument_is_read_as_a_server_label(text, expected):
    """``--from`` はサーバラベル（端末の壁時計）で読む。

    UTC→ラベルの逆変換は多価であり `marketdata/mt5_ticks/server_clock.py` は**実装しない**。
    よって運用者が渡すのは端末が見せている時刻そのもの（またはその epoch ms）である。
    """
    assert watch.parse_from_label(text) == expected


# =====================================================================
# 1 周期の合成（設計 §4: fetch → absorb → journal → M1/rollup → finalize → 再構築）
# =====================================================================

def test_a_cycle_publishes_the_closed_minutes_to_the_display_series(tmp_path, secret):
    """閉じた分が M1 CSV へ出る（表示系列まで結線されている）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    source = fakes.FakeTickSource(_tape(start, minutes=3))
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 3, tzinfo=dt.timezone.utc))

    watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source, clock=clock)

    m1 = pd.read_csv(tick_m1.m1_csv_path(ref="jp225_mt5", data_dir=tmp_path))
    assert list(m1["date"]) == [
        "2026-08-25 09:00:00", "2026-08-25 09:01:00", "2026-08-25 09:02:00"
    ]


def test_the_forming_minute_is_not_published_as_a_settled_bar(tmp_path, secret):
    """形成中の分は確定値として書かない（次の周期へ持ち越す）。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    source = fakes.FakeTickSource(_tape(start, minutes=3))
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 2, 30, tzinfo=dt.timezone.utc))

    watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source, clock=clock)

    m1 = pd.read_csv(tick_m1.m1_csv_path(ref="jp225_mt5", data_dir=tmp_path))
    assert "2026-08-25 09:02:00" not in list(m1["date"])


def test_no_publish_keeps_the_display_series_untouched(tmp_path, secret):
    """``--no-publish`` は表示系列へ 1 バイトも書かない（取り込みだけ回す運用）。"""
    source = fakes.FakeTickSource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=3))

    watch.main(
        _argv(tmp_path, "--from", "2026-08-25 12:00:00", "--no-publish"), source=source
    )

    assert not tick_m1.m1_csv_path(ref="jp225_mt5", data_dir=tmp_path).exists()


def test_no_publish_does_not_accumulate_rows_forever(tmp_path, secret):
    """``--no-publish`` で持ち越しを溜め込まない（常駐が受信量に比例して太らない）。

    持ち越し（``pending``）は「形成中の分を次の周期の畳みに混ぜる」ためだけに在る。畳まない
    運用でそれを貯め続けると、受信したティックが 1 つ残らずメモリに積み上がる。出力に使わない
    ものを持ち続けるのは、作ってから捨てる計算と同じ欠陥である。
    """
    settings = watch.settings_from(watch.build_parser().parse_args(
        ["--data-dir", str(tmp_path), "--from", "2026-08-25 12:00:00", "--no-publish"]
    ))
    source = fakes.FakeTickSource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=6))
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 6, tzinfo=dt.timezone.utc))
    cycle = watch.build_cycle(
        settings, source=source, token=ingest.token_for("JP225", fakes.DEFAULT_SERVER),
        clock=clock,
    )
    state = watch.WatchState(
        cursor=watch.cursor_rules.Cursor(cursor_ms=settings.from_label, boundary_rows=()),
        pending=[], days=set(), latest_day=None,
    )

    carried = []
    for _ in range(3):
        state, _result = cycle(state)
        carried.append(len(state.pending))

    assert carried == [0, 0, 0]


def test_crossing_a_utc_day_finalizes_and_rebuilds_the_closed_day(tmp_path, secret, monkeypatch):
    """日を跨いだら確定し、そのうえで**権威経路で再構築**する（設計 §10 の裁定）。"""
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    tape = _tape(dt.datetime(2026, 8, 25, 23, 58), minutes=4)   # 23:58 → 翌 00:01
    source = fakes.FakeTickSource(tape)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 26, 0, 10, tzinfo=dt.timezone.utc))
    rebuilt: "list[object]" = []
    monkeypatch.setattr(
        rebuild, "rebuild_day",
        lambda day, **kw: rebuilt.append(day) or rebuild.UNCHANGED,
    )

    watch.main(_argv(tmp_path, "--from", "2026-08-26 02:58:00"), source=source, clock=clock)

    assert tick_m1.day_parquet_path(
        dt.date(2026, 8, 25), symbol=token, data_dir=tmp_path
    ).is_file()
    assert rebuilt == [dt.date(2026, 8, 25)]


def test_a_day_that_is_still_open_is_not_finalized(tmp_path, secret):
    """走査中の当日を確定しない（1 周期ごとに当日全量を parquet 化しない）。"""
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    source = fakes.FakeTickSource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2))
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc))

    watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source, clock=clock)

    assert not tick_m1.day_parquet_path(
        dt.date(2026, 8, 25), symbol=token, data_dir=tmp_path
    ).is_file()


# =====================================================================
# 失敗の扱い（待てば直る／直らない）
# =====================================================================

@pytest.mark.parametrize(
    "failures,expected",
    [(0, 5.0), (1, 10.0), (2, 20.0), (3, 40.0), (4, 60.0), (7, 60.0), (8, 600.0), (20, 600.0)],
)
def test_the_backoff_doubles_then_caps_then_opens_the_breaker(failures, expected):
    """設計 §4: ×2 → 上限 60 秒 → 連続 8 回で 600 秒のブレーカ。"""
    assert watch.next_delay(failures, interval=5.0) == expected


def test_the_first_delay_is_the_configured_interval():
    """成功している間は指定した周期のまま（バックオフが既定にならない）。"""
    assert watch.next_delay(0, interval=2.0) == 2.0


def test_a_retryable_failure_is_retried_after_a_delay(tmp_path, secret):
    """待てば直る障害（401/429/502・到達不能）は落とさずに待って続ける。"""
    start = dt.datetime(2026, 8, 25, 9, 0)
    source = _FlakySource(_tape(start, minutes=2), fail_times=2)
    slept: "list[float]" = []

    code = watch.main(
        ["--data-dir", str(tmp_path), "--from", "2026-08-25 12:00:00", "--once"],
        source=source, sleep=slept.append,
    )

    assert code == 0
    assert slept == [10.0, 20.0]


def test_a_fail_stop_failure_ends_the_run_without_retrying(tmp_path, secret):
    """待っても直らない障害（引数不正・契約違反）は投げ続けずに終了する。"""
    source = fakes.FailingTickSource(Mt5SupplyError("max_rows が不正です"))
    slept: "list[float]" = []

    code = watch.main(
        _argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source, sleep=slept.append
    )

    assert code != 0
    assert slept == []
    assert not _wrote_anything(tmp_path)


def test_a_run_with_a_cycle_budget_gives_up_instead_of_retrying_forever(tmp_path, secret):
    """``--once`` は「1 周期だけ実行して終わる」（``--help`` の文言）。

    周期は**成功した**ものだけを数えるので、待てば直る障害（429 等）が続くかぎり 1 周期も
    消化されず、``--once`` が終わらない（実測: 51 回継続）。周期の予算を渡された実行は、
    ブレーカが開く連続失敗回数に達した時点で打ち切る。常駐（予算なし）の側は変えない
    — そちらは「直るまで待ち続ける」ことが仕事だからである。
    """
    source = _FlakySource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2), fail_times=10_000)
    slept = _BoundedSleep(limit=50)

    code = watch.main(
        _argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source, sleep=slept
    )

    assert code == watch.EXIT_FAIL_STOP
    assert len(slept.delays) == watch.BREAKER_AFTER_FAILURES - 1


def test_a_broken_cursor_contract_is_a_fail_stop_without_a_traceback(tmp_path, secret, capsys):
    """カーソル規約の破れ（境界 ms の不一致）も**運用者に読める形で**止まる。

    カーソル規約の破れは「待っても直らない」側の障害であり、扱いは供給契約の破れ
    （:class:`Mt5SupplyError`）と同じである。Fail-Stop ハンドラの型集合から漏れると、常駐は
    Python のトレースバックを吐いて exit 1 で落ちる。終了コードは「何が起きたか」を運用者へ
    伝える唯一の手段であり、規約の破れが「未知のクラッシュ」に化ける。
    """
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2)
    journal.append(dt.date(2026, 8, 25), tape[:4], symbol=token, data_dir=tmp_path)
    drifted = list(tape)
    drifted[3] = (tape[3][0], tape[3][1] + 5.0, tape[3][2] + 5.0)
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 2, tzinfo=dt.timezone.utc))

    code = watch.main(_argv(tmp_path), source=fakes.FakeTickSource(drifted), clock=clock)

    assert code == watch.EXIT_FAIL_STOP
    assert "Traceback" not in capsys.readouterr().err


def test_a_fail_stop_failure_writes_nothing_at_all(tmp_path, secret):
    """CX-e: Fail-Stop 経路で全 writer 呼出 0（部分的な台帳を残さない）。"""
    source = fakes.FailingTickSource(Mt5SupplyError("契約違反"))

    watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source)

    assert not _wrote_anything(tmp_path)


# =====================================================================
# 計算量（発行 − 使用 = 0）
# =====================================================================

@pytest.mark.parametrize("cycles", [1, 4])
def test_the_token_is_resolved_once_per_process_not_once_per_cycle(tmp_path, secret, cycles):
    """トークン解決は起動時 1 回。周期数（2 点）を変えても発行が増えない。

    周期数は本番も検定も同じ引数（``run(..., cycles=...)``）で決まる。``--once`` は
    ``cycles=1`` を渡すだけの CLI 面であり、検定専用の入口を作らない。
    """
    start = dt.datetime(2026, 8, 25, 9, 0)
    source = _CountingSource(_tape(start, minutes=5))
    settings = watch.settings_from(watch.build_parser().parse_args(
        ["--data-dir", str(tmp_path), "--from", "2026-08-25 12:00:00"]
    ))

    watch.run(settings, source=source, cycles=cycles, sleep=lambda _: None)

    assert source.token_probes == 1
    assert source.cycle_fetches == cycles


@pytest.mark.parametrize("stored_days", [1, 3])
def test_one_cycle_issues_one_fetch_regardless_of_stored_days(tmp_path, secret, stored_days):
    """CX-c: 1 周期の取得発行は保存済み日数に依存しない（2 点で「増えない」を固定）。"""
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    for offset in range(stored_days):
        day = dt.date(2026, 8, 25) - dt.timedelta(days=stored_days - offset)
        journal.append(
            day, _tape(dt.datetime(day.year, day.month, day.day, 9, 0), minutes=1),
            symbol=token, data_dir=tmp_path,
        )
    source = _CountingSource(_tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2))

    watch.main(_argv(tmp_path, "--from", "2026-08-25 12:00:00"), source=source)

    assert source.cycle_fetches == 1


def test_a_cycle_without_new_rows_writes_nothing(tmp_path, secret):
    """CX-b: 新着 0 の周期は journal も M1 も rollup も書かない。"""
    token = ingest.token_for("JP225", fakes.DEFAULT_SERVER)
    tape = _tape(dt.datetime(2026, 8, 25, 9, 0), minutes=2)
    journal.append(dt.date(2026, 8, 25), tape, symbol=token, data_dir=tmp_path)
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    clock = fakes.FixedClock(dt.datetime(2026, 8, 25, 9, 5, tzinfo=dt.timezone.utc))

    watch.main(_argv(tmp_path), source=fakes.FakeTickSource(tape), clock=clock)

    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before


# =====================================================================
# コンテナで動くこと（A-6 相当）
# =====================================================================

def test_cli_help_succeeds_in_the_container():
    """``--help`` が MetaTrader5 不在のコンテナで成功する。"""
    proc = subprocess.run(
        [sys.executable, str(_SOURCE), "--help"],
        capture_output=True, text=True, cwd=str(_REPO_ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    assert "--endpoint" in proc.stdout


# ---------------------------------------------------------------------
# 検定用の供給元（fakes の上に「数える」「たまに失敗する」を足すだけ）
# ---------------------------------------------------------------------

#: 数える供給元は fakes が唯一の実装を持つ（数え方の定義を検定ごとに書き写さない）。
_CountingSource = fakes.CountingTickSource


class _BoundedSleep:
    """待ちを数え、上限を超えたら止める。

    「終わらない」ことを検定するのに、本当に終わらないまま待つわけにはいかない。上限は
    検定が固まらないための安全弁であり、期待値ではない（期待値は本体の assert が持つ）。
    """

    def __init__(self, *, limit: int):
        self.delays: "list[float]" = []
        self._limit = limit

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if len(self.delays) > self._limit:
            raise AssertionError(
                f"周期の予算を渡した実行が {self._limit} 回の待ちを超えても終わりません。"
            )


class _FlakySource(fakes.FakeTickSource):
    """最初の ``fail_times`` 回だけ「待てば直る」障害を返す。"""

    def __init__(self, tape, *, fail_times: int):
        super().__init__(tape)
        self._left = fail_times

    def fetch(self, **kwargs):
        if kwargs.get("max_rows") != 1 and self._left > 0:
            self._left -= 1
            raise SupplyUnavailable("端末が一時的に応答しません")
        return super().fetch(**kwargs)
