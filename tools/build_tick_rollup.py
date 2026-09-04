"""ティック取得〜上位足ロールアップの単一パイプライン CLI（既存アクターの合成のみ）。

ティック（mid・UTC）を唯一のソースに、チャートの足も足内更新も同一由来へ統一する系の
「取得 → M1 → 上位足」を 1 エントリで順次実行する。新しいコアロジックは持たず、既存アクターを
**in-process import で合成**するだけの薄い Composition Root である。

段階（実行順・既定で全実行）:
  1. acquire : 日別ティック parquet を **追加取得**（既存日は上書きせず resume）。
               既存 tick tree があれば最新取得日の翌日〜本日+1日（増分追記）。
               tree が空（データ無し）なら full-start〜本日+1日を**全期間取得**する。
               取得は :func:`simulator.tools.fetch_ticks_ymd.run`（DukascopyTickSource）へ委譲。
  2. m1      : 取得済みティックから tick 由来 M1（``jp225_tick_m1.csv``）を生成する。
               既定は**増分追記**（既存 M1 の最終日より後の新しい日だけ集計し末尾へ追記＝
               全 parquet を再走査しない・:func:`marketdata.tick_m1.append_m1_from_ticks`）。
               初回（M1 不在）は自動で全構築へフォールバック。``--full`` で全再構築。
  3. rollup  : M1 を上位足（5m..1M）へロールアップする。既定は**差分更新**（state 以降の追記
               tail のみ・:func:`marketdata.rollup.incremental_update`）。初回（state 不在）は
               自動で ``stream_build`` へフォールバック。``--full`` で全再構築。出力は
               ``ref_prefix="jp225_tick"``・``DATA_DIR/rollups/jp225_tick/jp225_tick_<tf>.csv``。

データ保全（重要）:
  - ティック parquet は**上書きしない**（fetch_ticks_ymd が既存日/空日マーカーを skip）。
  - 派生物（M1・ロールアップ）は新 ref ``jp225_tick`` 専用ファイルとして生成し、既存
    ``jp225_m1.csv`` / ``jp225_m1_<tf>.csv`` には触れない（読取＋新規追加のみ）。

クリーンアーキ / 依存方向:
  - 本モジュールは最上位の合成点（tools 層）であり marketdata / simulator.tools /
    tools.acquire_marketdata に依存してよい（逆は無い）。ベンダ（dukascopy）は直接 import せず
    既存アクター経由。外部・重い呼び出しは monkeypatch 可能なモジュール関数
    （``_next_tick_start_day`` / ``_fetch_ticks_run`` / ``_build_tick_m1`` / ``_rollup_build``）へ
    隔離し、遅延 import で副作用を実行時に限定する。
  - 「本日」は ``--today`` で注入可能（Date.now 相当をハードコードしない・テスト決定性）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

LOG = logging.getLogger("build_tick_rollup")

# 出力 ref（tick 由来）と既定の全期間起点（既存 tick tree の最古日・--full-start 上書き可）。
# 起点の値は datasetRef 記述子レジストリ（唯一源）の ``data_start`` から**導出**する
# （ISSUE-479 M-4 段階 A）。「素材がいつから在るか」はデータセットの属性であって、
# 本パイプラインの設定ではない。値・CLI 既定・help 文言は従来と不変。
REF = "jp225_tick"
STAGE_NAMES = ("acquire", "m1", "rollup")


class PipelineError(RuntimeError):
    """本パイプライン固有の明示エラー。"""


def _registry_data_start(ref: str) -> dt.date:
    """datasetRef の素材開始日を記述子レジストリ（唯一源）から引く。"""
    from marketdata.dataset_registry import REGISTRY  # 遅延: import 副作用を実行時に限定

    start = REGISTRY[ref].data_start
    if start is None:
        raise PipelineError(f"datasetRef {ref!r} に data_start が登録されていません。")
    return start


_DEFAULT_FULL_START = _registry_data_start(REF)


# --------------------------------------------------------------------------- #
# パス基点（marketdata.paths.DATA_DIR を単一基点とする・遅延 import）
# --------------------------------------------------------------------------- #
def _data_dir() -> Path:
    from marketdata.paths import DATA_DIR  # 遅延: import 副作用を実行時に限定

    return Path(DATA_DIR)


def _rollup_timeframes() -> Tuple[str, ...]:
    """ロールアップ対象の上位足。規則は marketdata.rollup へ一本化（ISSUE-262）。"""
    from marketdata.rollup import rollup_timeframes

    return rollup_timeframes()


# --------------------------------------------------------------------------- #
# 既存アクターの薄いラッパ（monkeypatch 差替点・遅延 import で副作用を限定）
# --------------------------------------------------------------------------- #
def _next_tick_start_day(ticks_root: Path) -> dt.datetime:
    """取得済み最新日の翌日（UTC 00:00）。空 tree は本モジュールの :class:`PipelineError` へ翻訳。"""
    from tools.acquire_marketdata import PipelineError as _AcqError
    from tools.acquire_marketdata import next_tick_start_day

    try:
        return next_tick_start_day(ticks_root)
    except _AcqError as exc:  # 空 tree（起点不定）→ 呼出側で full-start へ倒すため自前例外へ翻訳。
        raise PipelineError(str(exc)) from exc


def _fetch_ticks_run(start: dt.datetime, end: dt.datetime, root: Path) -> int:
    from simulator.tools.fetch_ticks_ymd import run as _run

    return _run(start, end, root)


def _build_tick_m1(
    start: dt.date, end: dt.date, *, ref: str, data_dir: Path, full_rebuild: bool
) -> Path:
    """M1 を生成する。既定は増分追記（新しい日だけ集計）、``full_rebuild`` で全再構築。

    増分は初回（M1 不在）に自動で全構築へフォールバックする（append_m1_from_ticks 内）。
    """
    from marketdata.tick_m1 import append_m1_from_ticks, build_m1_from_ticks

    fn = build_m1_from_ticks if full_rebuild else append_m1_from_ticks
    return fn(start.isoformat(), end.isoformat(), ref=ref, data_dir=data_dir)


def _rollup_build(
    m1_path: Path, tf_list: Sequence[str], out_dir: Path, ref_prefix: str, *, full_rebuild: bool
):
    """ロールアップを生成する。既定は増分（state 以降の tail のみ）、``full_rebuild`` で全構築。

    増分は state 不在（初回）に自動で stream_build へフォールバックする（incremental_update 内）。
    いずれも out_dir へ rollup_state.json を保存する（関数内で save 済み）。
    """
    from marketdata.rollup import RollupState, incremental_update, stream_build

    if full_rebuild:
        return stream_build(m1_path, list(tf_list), out_dir, ref_prefix=ref_prefix)
    state = RollupState.load(out_dir)
    return incremental_update(m1_path, state, list(tf_list), out_dir, ref_prefix=ref_prefix)


# --------------------------------------------------------------------------- #
# 取得範囲の算出（append / 空なら全期間・最新日 probe のみ I/O を seam 経由で行う）
# --------------------------------------------------------------------------- #
def _utc_midnight(d: dt.date) -> dt.datetime:
    return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)


def compute_acquire_range(
    ticks_root: Path,
    today: dt.date,
    *,
    full_start: dt.date,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> Optional[Tuple[dt.datetime, dt.datetime]]:
    """取得すべき ``[start, end)``（UTC・半開）を算出する。取得対象が無ければ ``None``。

    起点規則（データ保全の核）:
      - ``start`` 明示時はそれを採用（全期間/任意窓の手動指定）。
      - 未指定かつ既存 tick tree あり → 最新取得日の翌日（**増分追記**）。
      - 未指定かつ tree 空（取得済み 0 日）→ ``full_start``（**全期間取得**）。
    終端は ``end``（未指定は ``today``）の翌日（当該日を含む半開区間）。``end <= start`` は
    取得対象なし（``None``）。
    """
    if start is not None:
        s = _utc_midnight(start)
    else:
        try:
            s = _next_tick_start_day(ticks_root)
        except PipelineError:  # 空 tree（取得済み 0 日）→ 全期間取得。
            s = _utc_midnight(full_start)
    end_date = end if end is not None else today
    e = _utc_midnight(end_date + dt.timedelta(days=1))
    if e <= s:
        return None
    return s, e


# --------------------------------------------------------------------------- #
# パイプライン文脈
# --------------------------------------------------------------------------- #
@dataclass
class PipelineContext:
    today: dt.date = field(
        default_factory=lambda: dt.datetime.now(dt.timezone.utc).date()
    )
    full_start: dt.date = _DEFAULT_FULL_START
    start: Optional[dt.date] = None
    end: Optional[dt.date] = None
    ref: str = REF
    full_rebuild: bool = False  # True で M1・rollup を全再構築（既定は増分追記/差分更新）。
    continue_on_error: bool = False
    # ティック格納先・派生物出力先の**単一真実源**は data_dir 一本（acquire の書込先と m1 の
    # 読込先が乖離しないよう ticks_root/rollups_dir は data_dir 由来の派生に固定する）。
    data_dir: Path = field(default_factory=_data_dir)

    @property
    def ticks_root(self) -> Path:
        """ティック tree（acquire の書込先＝m1 の読込先・常に一致）。

        レイアウトの単一権威（marketdata.tick_m1.tick_root）へ委譲する（ISSUE-262）。
        """
        from marketdata.tick_m1 import tick_root  # 遅延: import 副作用を実行時に限定

        return tick_root(self.data_dir)

    @property
    def rollups_dir(self) -> Path:
        """tick 派生ロールアップ専用 dir（``DATA_DIR/rollups/<ref>``）。

        データ保全（重要）: ``stream_build`` は ref 非依存の固定名 ``rollup_state.json`` を
        ``out_dir`` へ無条件保存するため、既存 jp225_m1 ロールアップ（``DATA_DIR/rollups`` 直下・
        共有 state）と**同一 dir に書くと既存 state を上書き破壊**する。ref でサブ dir を切り、
        CSV だけでなく state まで物理分離して既存系へ波及させない。
        """
        return self.data_dir / "rollups" / self.ref


# --------------------------------------------------------------------------- #
# 段階の実体（既存アクターへ委譲）
# --------------------------------------------------------------------------- #
def stage_acquire(ctx: PipelineContext) -> int:
    """ティックを追加取得する（既存日は上書きせず resume・空なら全期間）。"""
    rng = compute_acquire_range(
        ctx.ticks_root, ctx.today, full_start=ctx.full_start, start=ctx.start, end=ctx.end
    )
    if rng is None:
        LOG.info("acquire: 取得対象期間なし（最新まで取得済み）")
        return 0
    start, end = rng
    last_day = (end - dt.timedelta(days=1)).date()  # 半開 [start, end) の取得最終日（表示用）。
    LOG.info("acquire: %s..%s 取得開始（既存日は skip）", start.date(), last_day)
    total = _fetch_ticks_run(start, end, ctx.ticks_root)
    LOG.info("acquire: %s ticks 取得", total)
    return 0


def stage_m1(ctx: PipelineContext) -> int:
    """取得済みティックから tick 由来 M1 を生成する（既定は増分追記・--full で全再構築）。"""
    m1_end = ctx.end if ctx.end is not None else ctx.today
    m1_path = _build_tick_m1(
        ctx.full_start, m1_end, ref=ctx.ref, data_dir=ctx.data_dir, full_rebuild=ctx.full_rebuild
    )
    LOG.info("m1: %s を生成（%s）", m1_path, "全再構築" if ctx.full_rebuild else "増分追記")
    return 0


def stage_rollup(ctx: PipelineContext) -> int:
    """tick 由来 M1 を上位足へロールアップする（ref_prefix=jp225_tick）。"""
    from marketdata.tick_m1 import m1_csv_path

    m1_path = m1_csv_path(ref=ctx.ref, data_dir=ctx.data_dir)
    if not Path(m1_path).is_file():
        raise PipelineError(
            f"rollup: M1 が存在しません（{m1_path}）。先に m1 段を実行してください。"
        )
    ctx.rollups_dir.mkdir(parents=True, exist_ok=True)
    tfs = _rollup_timeframes()
    _rollup_build(m1_path, tfs, ctx.rollups_dir, ctx.ref, full_rebuild=ctx.full_rebuild)
    LOG.info(
        "rollup: %s -> %s/%s_<tf>.csv (%s・%s)",
        m1_path, ctx.rollups_dir, ctx.ref, ",".join(tfs),
        "全再構築" if ctx.full_rebuild else "差分更新",
    )
    return 0


_STAGE_FUNCS = {"acquire": "stage_acquire", "m1": "stage_m1", "rollup": "stage_rollup"}


def _dispatch(stage: str, ctx: PipelineContext) -> int:
    """stage 名 → モジュール関数を解決して実行（monkeypatch 差替を尊重し getattr 経由）。"""
    func = getattr(sys.modules[__name__], _STAGE_FUNCS[stage])
    return func(ctx)


def select_stages(skip: Sequence[str], only: Optional[str]) -> List[str]:
    """実行する段階名を実行順で返す。only 指定時は skip を無視し単一段階のみ。"""
    if only is not None:
        return [only]
    skip_set = set(skip)
    return [s for s in STAGE_NAMES if s not in skip_set]


def run_pipeline(ctx: PipelineContext, stages: Sequence[str]) -> int:
    """段階を順次実行し、全段成功なら 0・いずれか失敗なら 1 を返す。"""
    results: List[tuple] = []
    overall_ok = True
    for stage in stages:
        LOG.info("=== stage %s 開始 ===", stage)
        t0 = time.monotonic()
        rc, err = 1, None
        try:
            rc = _dispatch(stage, ctx)
        except Exception as exc:  # noqa: BLE001 — 段階例外を集約しサマリに反映
            err = exc
            rc = 1
            LOG.error("stage %s 例外: %s: %s", stage, type(exc).__name__, exc)
        elapsed = time.monotonic() - t0
        ok = rc == 0 and err is None
        results.append((stage, rc, elapsed, err))
        LOG.info("=== stage %s 終了 rc=%s elapsed=%.2fs %s ===", stage, rc, elapsed, "OK" if ok else "NG")
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


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_date(s: str) -> dt.date:
    return dt.datetime.strptime(s, "%Y-%m-%d").date()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="build_tick_rollup",
        description="ティック取得〜上位足ロールアップを単一エントリで順次実行（取得は追加・空なら全期間）。",
    )
    p.add_argument("--skip", action="append", default=[], choices=list(STAGE_NAMES),
                   help="実行しない段階（複数指定可）")
    p.add_argument("--only", choices=list(STAGE_NAMES), default=None, help="この段階のみ実行")
    p.add_argument("--start", type=_parse_date, default=None,
                   help="取得開始日 YYYY-MM-DD（未指定=増分/空なら full-start）")
    p.add_argument("--end", type=_parse_date, default=None, help="終了日 YYYY-MM-DD（含む）")
    p.add_argument("--full-start", type=_parse_date, default=None,
                   help=f"全期間取得の起点 YYYY-MM-DD（既定 {_DEFAULT_FULL_START.isoformat()}）")
    p.add_argument("--full", action="store_true",
                   help="M1・ロールアップを増分でなく全再構築する（既定は増分追記/差分更新）")
    p.add_argument("--today", type=_parse_date, default=None,
                   help="「本日」を注入 YYYY-MM-DD（未指定=実 UTC 本日）")
    p.add_argument("--data-dir", type=Path, default=None,
                   help="データ基点（既定 marketdata.paths.DATA_DIR）。ティック読書き・ロールアップ"
                        "出力はすべてこの dir 由来（ticks=<dir>/ticks・rollup=<dir>/rollups/<ref>）")
    p.add_argument("--quiet", action="store_true", help="ログを抑制する")
    p.add_argument("--continue-on-error", action="store_true", help="段階失敗時も後続を継続する（既定は中断）")
    return p


def _build_context(args: argparse.Namespace) -> PipelineContext:
    kwargs = dict(
        start=args.start,
        end=args.end,
        full_rebuild=args.full,
        continue_on_error=args.continue_on_error,
    )
    if args.today is not None:
        kwargs["today"] = args.today
    if args.full_start is not None:
        kwargs["full_start"] = args.full_start
    if args.data_dir is not None:
        kwargs["data_dir"] = args.data_dir
    return PipelineContext(**kwargs)


def main(argv: Optional[List[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")
    ctx = _build_context(args)
    stages = select_stages(args.skip, args.only)
    return run_pipeline(ctx, stages)


if __name__ == "__main__":
    raise SystemExit(main())
