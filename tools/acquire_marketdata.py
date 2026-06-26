"""marketdata 取得パイプライン CLI（散在する取得ツールを単一エントリで順次実行）。

合成点（Composition Root 相当）として既存エントリを **in-process import** で呼ぶ。
既定は増分（最終→最新を追記）。段階選択・ログ・resume を備える。

段階（実行順・既定で全実行）:
  1. bars   : indigators.indicator_ui.tools.export_jp225_m1.main([])（増分・rollups 込み）
  2. daily  : indigators.indicator_ui.tools.export_jp225_csv.main([...])（最新まで）
  3. ticks  : data/marketdata/ticks の y/m/d を走査し最新日の翌日から本日+1日まで取得
  4. ingest : raw parquet を走査し未 ingest 日のみ canonical tick-store へ ingest（state 冪等）

クリーンアーキ:
  - marketdata/・既存ツール・simulator の tick-store 参照先は無改変（合成点が上位）。
  - ベンダ（dukascopy）は直接 import しない。取得は既存ツール経由。
  - 自前ロジックは「ティック増分開始日算出」「ingest 未処理日抽出 + state」のみ。
    残りは既存エントリへ委譲しロジック重複を避ける。

テスト容易性:
  - 既存エントリ呼び出しは _export_jp225_m1_main / _export_jp225_csv_main /
    _fetch_ticks_run / _ingest_raw_parquet のモジュール関数に隔離し monkeypatch 差替可能。
  - 「本日」は --today で注入可能（Date.now 相当をハードコードしない）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Set

LOG = logging.getLogger("acquire_marketdata")

SYMBOL = "JP225"
TICK_PARQUET_NAME = "JP225_ticks.parquet"
TICK_EMPTY_NAME = "JP225_ticks.empty"
INGEST_STATE_NAME = "ingest_state.json"
STAGE_NAMES = ("bars", "daily", "ticks", "ingest")


class PipelineError(RuntimeError):
    """パイプライン固有の明示エラー（増分起点不定・--full の start 欠落等）。"""


# --------------------------------------------------------------------------- #
# パス基点（marketdata.paths.DATA_DIR を単一基点とする・遅延 import）
# --------------------------------------------------------------------------- #
def _data_dir() -> Path:
    from marketdata.paths import DATA_DIR  # 遅延: import 副作用を実行時に限定

    return Path(DATA_DIR)


def _default_ticks_root() -> Path:
    return _data_dir() / "ticks"


def _default_tickstore_root() -> Path:
    return _data_dir() / "tickstore"


def _default_daily_output() -> Path:
    return _data_dir() / "jp225_daily.csv"


# --------------------------------------------------------------------------- #
# 既存エントリの薄いラッパ（monkeypatch 差替点・遅延 import で副作用を限定）
# --------------------------------------------------------------------------- #
def _export_jp225_m1_main(argv: List[str]) -> int:
    from indigators.indicator_ui.tools.export_jp225_m1 import main as _main

    return _main(argv)


def _export_jp225_csv_main(argv: List[str]) -> int:
    from indigators.indicator_ui.tools.export_jp225_csv import main as _main

    return _main(argv)


def _fetch_ticks_run(start: dt.datetime, end: dt.datetime, root: Path) -> int:
    from simulator.tools.fetch_ticks_ymd import run as _run

    return _run(start, end, root)


def _ingest_raw_parquet(raw_path: Path, store_root: Path, symbol: str):
    from simulator.tools.ingest_ticks import ingest_raw_parquet as _ingest

    return _ingest(raw_path, store_root, symbol)


# --------------------------------------------------------------------------- #
# 純粋ロジック: y/m/d tree 走査
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TickDay:
    """ティック tree 上の 1 日（parquet を持つ日）。"""

    day: dt.date
    parquet_path: Path

    @property
    def day_str(self) -> str:
        return self.day.isoformat()


def _iter_tick_days(root: Path, *, kind: str) -> Iterable[TickDay]:
    """root/YYYY/MM/DD/ を走査し、指定種別のファイルを持つ日を yield する。

    kind="parquet" は JP225_ticks.parquet を持つ日のみ。
    kind="any"     は parquet または .empty を持つ日（取得済み判定用）。
    """
    if not root.exists():
        return
    for ydir in sorted(p for p in root.iterdir() if p.is_dir() and p.name.isdigit()):
        for mdir in sorted(p for p in ydir.iterdir() if p.is_dir() and p.name.isdigit()):
            for ddir in sorted(
                p for p in mdir.iterdir() if p.is_dir() and p.name.isdigit()
            ):
                parquet = ddir / TICK_PARQUET_NAME
                empty = ddir / TICK_EMPTY_NAME
                has_parquet = parquet.exists()
                has_any = has_parquet or empty.exists()
                want = has_parquet if kind == "parquet" else has_any
                if not want:
                    continue
                try:
                    day = dt.date(int(ydir.name), int(mdir.name), int(ddir.name))
                except ValueError:
                    continue
                yield TickDay(day=day, parquet_path=parquet)


def next_tick_start_day(root: Path) -> dt.datetime:
    """y/m/d tree 上で取得済み（parquet または .empty）の最新日の翌日を返す（UTC 00:00）。

    取得済み日が 1 つも無い（tree 不在 / 空 tree）場合は PipelineError を送出する。
    増分起点が定まらないため、呼び出し側は --start を明示する必要がある。
    """
    latest: Optional[dt.date] = None
    for td in _iter_tick_days(root, kind="any"):
        if latest is None or td.day > latest:
            latest = td.day
    if latest is None:
        raise PipelineError(
            f"ティック tree に取得済み日がありません（root={root}）。"
            "増分起点が不定のため --start を指定してください。"
        )
    nxt = latest + dt.timedelta(days=1)
    return dt.datetime(nxt.year, nxt.month, nxt.day, tzinfo=dt.timezone.utc)


def pending_ingest_days(root: Path, ingested: Set[str]) -> List[TickDay]:
    """parquet を持つ日のうち、state（ingested 済み YYYY-MM-DD 集合）に無い日を昇順で返す。"""
    return [
        td
        for td in _iter_tick_days(root, kind="parquet")
        if td.day_str not in ingested
    ]


# --------------------------------------------------------------------------- #
# 純粋ロジック: ingest state（冪等性追跡）
# --------------------------------------------------------------------------- #
def _state_path(tickstore_root: Path) -> Path:
    return tickstore_root / INGEST_STATE_NAME


def load_ingest_state(tickstore_root: Path) -> Set[str]:
    """ingest 済み YYYY-MM-DD 集合をロードする。state 不在時は空集合。"""
    path = _state_path(tickstore_root)
    if not path.exists():
        return set()
    data = json.loads(path.read_text())
    return set(data)


def save_ingest_state(tickstore_root: Path, ingested: Set[str]) -> None:
    """ingest 済み集合を sorted JSON 配列で永続化する（既存 store を破壊せず追記更新）。"""
    tickstore_root.mkdir(parents=True, exist_ok=True)
    _state_path(tickstore_root).write_text(
        json.dumps(sorted(ingested), ensure_ascii=False, indent=0)
    )


# --------------------------------------------------------------------------- #
# 段階選択
# --------------------------------------------------------------------------- #
def select_stages(skip: Sequence[str], only: Optional[str]) -> List[str]:
    """実行する段階名を実行順で返す。only 指定時は skip を無視し単一段階のみ。"""
    if only is not None:
        return [only]
    skip_set = set(skip)
    return [s for s in STAGE_NAMES if s not in skip_set]


# --------------------------------------------------------------------------- #
# パイプライン文脈
# --------------------------------------------------------------------------- #
@dataclass
class PipelineContext:
    full: bool = False
    quiet: bool = False
    continue_on_error: bool = False
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    today: dt.date = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).date())
    ticks_root: Path = field(default_factory=_default_ticks_root)
    tickstore_root: Path = field(default_factory=_default_tickstore_root)
    daily_output: Path = field(default_factory=_default_daily_output)


def _to_utc_midnight(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)


# --------------------------------------------------------------------------- #
# 各段階の実体（既存エントリへ委譲）
# --------------------------------------------------------------------------- #
def stage_bars(ctx: PipelineContext) -> int:
    """1 分足 + ロールアップ。増分は引数なし main([])。--full は --start/--end 範囲で再取得。"""
    if ctx.full:
        # --full の契約を全段で一致させる（ticks と同様、全期間再取得には範囲必須）。
        if ctx.start is None or ctx.end is None:
            raise PipelineError("--full 指定時は bars に --start/--end が必須です（全期間再取得）。")
        argv = ["--start", ctx.start.isoformat(), "--end", ctx.end.isoformat()]
    else:
        argv = []
    if ctx.quiet:
        argv = argv + ["--quiet"]
    return _export_jp225_m1_main(argv)


def stage_daily(ctx: PipelineContext) -> int:
    """日足。最新まで取得（小容量のため増分=最新まで）。範囲明示は --start/--end で上書き。"""
    argv: List[str] = ["--output", str(ctx.daily_output)]
    if ctx.start is not None:
        argv += ["--start", ctx.start.isoformat()]
    if ctx.end is not None:
        argv += ["--end", ctx.end.isoformat()]
    if ctx.quiet:
        argv += ["--quiet"]
    return _export_jp225_csv_main(argv)


def stage_ticks(ctx: PipelineContext) -> int:
    """ティック増分。最新取得日の翌日〜本日+1日（UTC・[start,end)）を fetch_ticks に委譲。

    --full は全期間再取得のため --start 必須。空 tree かつ --start 無しも PipelineError。
    """
    if ctx.start is not None:
        start = _to_utc_midnight(ctx.start)
    elif ctx.full:
        raise PipelineError("--full 指定時は ticks に --start が必須です（全期間再取得）。")
    else:
        # 空 tree なら next_tick_start_day が PipelineError を送出（起点不定）。
        start = next_tick_start_day(ctx.ticks_root)
    end = _to_utc_midnight(ctx.today + dt.timedelta(days=1))
    if end <= start:
        LOG.info("ticks: 取得対象期間なし（start=%s end=%s）", start, end)
        return 0
    total = _fetch_ticks_run(start, end, ctx.ticks_root)
    LOG.info("ticks: %s ticks 取得（%s..%s）", total, start.date(), end.date())
    return 0


def stage_ingest(ctx: PipelineContext) -> int:
    """raw parquet を走査し未 ingest 日のみ canonical tick-store へ ingest（state で冪等）。"""
    state = load_ingest_state(ctx.tickstore_root)
    pending = pending_ingest_days(ctx.ticks_root, state)
    if not pending:
        LOG.info("ingest: 未処理日なし（既 ingest %s 日）", len(state))
        return 0
    done = 0
    for td in pending:
        _ingest_raw_parquet(td.parquet_path, ctx.tickstore_root, SYMBOL)
        state.add(td.day_str)
        done += 1
        # 1 日ごとに state を永続化し、途中失敗でも済み日を失わない（resume 性）。
        save_ingest_state(ctx.tickstore_root, state)
        LOG.info("ingest: %s -> tick-store", td.day_str)
    LOG.info("ingest: %s 日 ingest 完了", done)
    return 0


_STAGE_FUNCS = {
    "bars": "stage_bars",
    "daily": "stage_daily",
    "ticks": "stage_ticks",
    "ingest": "stage_ingest",
}


def _dispatch(stage: str, ctx: PipelineContext) -> int:
    """stage 名 → モジュール関数を解決して実行（monkeypatch 差替を尊重し getattr 経由）。"""
    func = getattr(sys.modules[__name__], _STAGE_FUNCS[stage])
    return func(ctx)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="acquire_marketdata",
        description="marketdata 取得を単一エントリで順次実行（既定=増分）。",
    )
    p.add_argument("--full", action="store_true", help="増分でなく全期間/全件の再取得（ticks は --start 必須）")
    p.add_argument("--skip", action="append", default=[], choices=list(STAGE_NAMES),
                   help="実行しない段階（複数指定可）")
    p.add_argument("--only", choices=list(STAGE_NAMES), default=None, help="この段階のみ実行")
    p.add_argument("--start", type=_parse_date, default=None, help="開始日 YYYY-MM-DD")
    p.add_argument("--end", type=_parse_date, default=None, help="終了日 YYYY-MM-DD")
    p.add_argument("--today", type=_parse_date, default=None,
                   help="「本日」を注入 YYYY-MM-DD（未指定=実 UTC 本日）")
    p.add_argument("--tickstore-root", type=Path, default=None,
                   help="canonical tick-store root（既定 data/marketdata/tickstore）")
    p.add_argument("--quiet", action="store_true", help="ログを抑制する")
    p.add_argument("--continue-on-error", action="store_true",
                   help="段階失敗時も後続を継続する（既定は中断）")
    return p


def _build_context(args: argparse.Namespace) -> PipelineContext:
    kwargs = dict(
        full=args.full,
        quiet=args.quiet,
        continue_on_error=args.continue_on_error,
        start=args.start,
        end=args.end,
    )
    if args.today is not None:
        kwargs["today"] = args.today
    if args.tickstore_root is not None:
        kwargs["tickstore_root"] = args.tickstore_root
    return PipelineContext(**kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s"
    )
    ctx = _build_context(args)
    stages = select_stages(args.skip, args.only)

    results: List[tuple] = []  # (stage, rc, elapsed, error)
    overall_ok = True
    for stage in stages:
        LOG.info("=== stage %s 開始 ===", stage)
        t0 = time.monotonic()
        rc = 1
        err = None
        try:
            rc = _dispatch(stage, ctx)
        except Exception as exc:  # noqa: BLE001 — 段階例外を集約しサマリに反映
            err = exc
            rc = 1
            LOG.error("stage %s 例外: %s: %s", stage, type(exc).__name__, exc)
        elapsed = time.monotonic() - t0
        ok = rc == 0 and err is None
        results.append((stage, rc, elapsed, err))
        LOG.info("=== stage %s 終了 rc=%s elapsed=%.2fs %s ===",
                 stage, rc, elapsed, "OK" if ok else "NG")
        if not ok:
            overall_ok = False
            if not ctx.continue_on_error:
                break

    LOG.info("---- サマリ ----")
    for stage, rc, elapsed, err in results:
        status = "OK" if (rc == 0 and err is None) else "NG"
        detail = f" ({type(err).__name__}: {err})" if err is not None else ""
        LOG.info("  %-7s %s rc=%s %.2fs%s", stage, status, rc, elapsed, detail)

    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
