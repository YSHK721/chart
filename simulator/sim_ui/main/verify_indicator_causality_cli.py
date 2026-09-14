"""因果性検定 CLI（main 層・Phase 3 F-5）。

基本設計書 §3.5.4 の通過条件を実測する。系列ごとに、案 ii（供給窓を 1 回 full 計算）の
系列と案 i（バーごとに until_time で truncate して full 計算）の系列が**全バーで一致する**
ことを確かめ、結果を台帳（`indicator_causality.json`）へ**機械生成**する。台帳を手で書かない。

三段実行（裁定 C・cost-gating）:

    段 0  供給コスト測定: series_full(until=供給窓末尾) を 1 回計測（既定 10,000 本）
          予算（既定 1.0 秒）超過 → selectable:false / reason=supply_cost_exceeded
          → **案 i は走らせない**（供給に使えないものの照合に案 i の実費を払わない）
    段 1  検定: データセット先頭から --verify-bars 本（既定 1000）で全バー突合
          予算（--verify-timeout）超過 → selectable:false / reason=verification_incomplete
    段 2  段 1 で一致した系列を、供給窓（10,000 本）で再検定して coverage=1.0 を確定

採らない応急処置（裁定 C）: 検定せず selectable:true にする／バーを間引いて「全バー一致」と
称する／tolerance を緩める。いずれも「一致した」という主張の意味を変えてしまう。

検定窓を「データセット先頭から N 本」に取るのは、窓の左端を動かさないため（案 i の定義を
変えずに prefix 関係を保つ唯一の取り方）。費用は N の 2 乗で減るため、段 1（N=1000）は
段 2（N=10000）の約 1/100 で済む。

測定条件の固定（Phase 3 構造設計 §絶対制約）:
    * ``limit=None``（tail で窓長を変えない）
    * ``tolerance`` 既定 0.0（厳密一致・緩めない）
    * ``probe_mode`` 既定 "full"（案 i も full 計算。latest は同値性未検証）

1 系列の異常（供給できない・比較不能）で検定全体を止めない。その系列を理由つきで
選択不可として記録する（無音で消さない・他の系列の測定を捨てない）。

使い方:
    python -m simulator.sim_ui.main.verify_indicator_causality_cli \
        --ref jp225_tick --timeframe 5m [--supply-bars 10000] [--verify-bars 1000] \
        [--verify-timeout 600] [--supply-budget 1.0] [--tolerance 0.0] \
        [--data-root <path>] [--indicator NAME ...]
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from simulator.sim_ui.usecase.align_series_to_bars import align_series_to_bars
from simulator.sim_ui.usecase.indicator_models import (
    REASON_SUPPLY_COST_EXCEEDED,
    REASON_VERIFICATION_INCOMPLETE,
    CausalityComparisonError,
    CausalityFinding,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
    SeriesAlignmentError,
    SeriesBundle,
)
from simulator.sim_ui.usecase.verify_indicator_causality import (
    measure_supply_cost,
    verify_indicator_causality,
)

#: 台帳 schema 版（`file_indicator_causality_ledger.LEDGER_SCHEMA` と同値）。
_SCHEMA = 1
#: 系列名が判らない段階（束の計算そのものが失敗）で使う記録上の系列名。
_WHOLE_INDICATOR = "*"


@dataclass(frozen=True)
class CliOptions:
    """検定の測定条件（台帳へそのまま残る）。"""

    ref: str
    timeframe: "str | None" = None
    supply_bars: int = 10_000
    verify_bars: int = 1_000
    verify_timeout: "float | None" = None
    supply_budget: float = 1.0
    tolerance: float = 0.0
    data_root: "str | None" = None
    indicators: "tuple[str, ...]" = ()
    probe_mode: str = "full"
    limit: "int | None" = None


def parse_args(argv: "Sequence[str] | None" = None) -> CliOptions:
    """引数を測定条件へ変換する（``--ref`` は必須＝条件を欠いた台帳を作らせない）。"""
    parser = argparse.ArgumentParser(
        prog="verify_indicator_causality_cli",
        description="系列ごとに案 i（逐次 truncate）と案 ii（一括計算）の一致を実測する",
    )
    parser.add_argument("--ref", required=True, help="データセット参照（例: jp225_tick）")
    parser.add_argument("--timeframe", default=None, help="足（例: 5m）")
    parser.add_argument("--supply-bars", type=int, default=10_000,
                        help="供給窓のバー本数（段 0 の計測窓・段 2 の検定窓）")
    parser.add_argument("--verify-bars", type=int, default=1_000,
                        help="段 1（スクリーニング）のバー本数")
    parser.add_argument("--verify-timeout", type=float, default=None,
                        help="1 指標 1 段あたりの検定予算（秒・既定は無制限）")
    parser.add_argument("--supply-budget", type=float, default=1.0,
                        help="供給コストの上限（秒・通過条件 3 の閾値）")
    parser.add_argument("--tolerance", type=float, default=0.0,
                        help="一致とみなす絶対差（既定 0.0＝厳密一致）")
    parser.add_argument("--data-root", default=None, help="台帳を置く根")
    parser.add_argument("--indicator", action="append", default=None,
                        help="対象指標を絞る（複数指定可・既定は全指標）")
    args = parser.parse_args(list(argv) if argv is not None else None)
    return CliOptions(
        ref=args.ref,
        timeframe=args.timeframe,
        supply_bars=args.supply_bars,
        verify_bars=args.verify_bars,
        verify_timeout=args.verify_timeout,
        supply_budget=args.supply_budget,
        tolerance=args.tolerance,
        data_root=args.data_root,
        indicators=tuple(args.indicator or ()),
        probe_mode="full",   # 絶対制約: 案 i は full 計算
        limit=None,          # 絶対制約: tail で窓長を変えない
    )


def run(
    *,
    options: CliOptions,
    probe: Any,
    catalog: Any,
    ledger: Any,
    report: "Callable[[str], None]" = lambda _line: None,
) -> LedgerSnapshot:
    """三段の検定を実行し、台帳を書いて snapshot を返す。

    ``report``: 段ごとの実測（所要秒・判定）を流す先。既定は捨てる（検定では使わない）。
    """
    specs = [
        spec for spec in catalog.specs()
        if not options.indicators or spec.indicator in options.indicators
    ]
    supply_window = probe.bar_times(
        ref=options.ref, timeframe=options.timeframe, count=options.supply_bars
    )
    verify_window = probe.bar_times(
        ref=options.ref, timeframe=options.timeframe, count=options.verify_bars
    )

    findings: "list[CausalityFinding]" = []
    for spec in specs:
        findings.extend(_verify_spec(
            spec=spec, options=options, probe=probe,
            supply_window=supply_window, verify_window=verify_window, report=report,
        ))
    findings.sort(key=lambda f: (f.spec.indicator, f.spec.variant, f.series_name))

    snapshot = LedgerSnapshot(
        schema=_SCHEMA,
        measured_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        conditions=LedgerConditions(
            ref=options.ref,
            timeframe=options.timeframe,
            supply_bars=len(supply_window),
            verify_bars=len(verify_window),
            # 段 2 の窓は供給窓そのもの。selectable=true になれるのは段 2 を最後まで
            # 通った系列だけなので、選択可能な系列の検定範囲は供給窓の全域＝1.0 である
            # （途中で打ち切られた系列は verification_incomplete として残る）。
            verify_coverage=1.0 if supply_window else 0.0,
            timeout=options.verify_timeout,
            supply_budget=options.supply_budget,
            limit=options.limit,
            tolerance=options.tolerance,
            probe_mode=options.probe_mode,
        ),
        findings=tuple(findings),
    )
    ledger.write(snapshot)
    return snapshot


def main(
    argv: "Sequence[str] | None" = None,
    *,
    probe: Any = None,
    catalog: Any = None,
    ledger: Any = None,
) -> int:
    """CLI エントリポイント（Port 実装は既定で実物・検定ではフェイクを渡す）。"""
    options = parse_args(argv)
    probe = probe if probe is not None else _default_probe()
    catalog = catalog if catalog is not None else _default_catalog()
    ledger = ledger if ledger is not None else _default_ledger(options)
    started = time.perf_counter()
    snapshot = run(
        options=options, probe=probe, catalog=catalog, ledger=ledger, report=print,
    )
    for line in summarize(snapshot, seconds=time.perf_counter() - started):
        print(line)
    return 0


def summarize(snapshot: LedgerSnapshot, *, seconds: float) -> "list[str]":
    """台帳の内訳（選択可能 / 理由別）を人が読める行にする。"""
    total = len(snapshot.findings)
    ok = sum(1 for f in snapshot.findings if f.selectable)
    by_reason: "dict[str, int]" = {}
    for finding in snapshot.findings:
        if finding.reason is not None:
            by_reason[finding.reason] = by_reason.get(finding.reason, 0) + 1
    lines = [
        f"検定完了: 選択可能 {ok} / 全 {total} 系列"
        f"（供給窓 {snapshot.conditions.supply_bars} 本・"
        f"段 1 窓 {snapshot.conditions.verify_bars} 本・"
        f"tolerance={snapshot.conditions.tolerance}・"
        f"所要 {seconds:.1f} 秒）"
    ]
    lines += [f"  {reason}: {count} 系列" for reason, count in sorted(by_reason.items())]
    return lines


# --- 1 指標ぶんの三段検定 ---------------------------------------------------


def _verify_spec(
    *,
    spec: IndicatorSpec,
    options: CliOptions,
    probe: Any,
    supply_window: "Sequence[int]",
    verify_window: "Sequence[int]",
    report: "Callable[[str], None]",
) -> "list[CausalityFinding]":
    """段 0 → 段 1 → 段 2 を 1 指標に適用し、系列ごとの結果を返す。"""
    # --- 段 0: 供給コスト（通過条件 3 の実測）
    try:
        cost = measure_supply_cost(
            spec=spec, ref=options.ref, timeframe=options.timeframe,
            until_time=int(supply_window[-1]) if supply_window else None, probe=probe,
        )
    except Exception as exc:  # noqa: BLE001 — 1 指標の異常で全体を落とさない
        report(f"段0 {spec.key} 供給できません: {exc}")
        return [_incomplete(spec, _WHOLE_INDICATOR, f"供給できません: {exc}", None)]
    report(
        f"段0 {spec.key} supply_seconds={cost.seconds:.3f} "
        f"series={sorted(cost.bundle)} excluded={list(cost.bundle.excluded)}"
    )

    excluded = [
        _incomplete(spec, name, "供給対象の kind ではありません（時系列でない）",
                    cost.seconds)
        for name in cost.bundle.excluded
    ]
    if cost.seconds > options.supply_budget:
        # 案 i は走らせない（供給に使えないものの照合に実費を払わない）。
        return excluded + [
            CausalityFinding(
                spec=spec, series_name=name, selectable=False,
                reason=REASON_SUPPLY_COST_EXCEEDED,
                detail=(
                    f"供給 {cost.seconds:.3f} 秒 > 予算 {options.supply_budget} 秒"
                    f"（供給窓 {len(supply_window)} 本）"
                ),
                supply_seconds=cost.seconds,
            )
            for name in sorted(cost.bundle)
        ]

    # --- 段 0b: 整列（供給はレジストリへ載るまでが供給）
    unaligned = _alignment_failures(
        spec=spec, bundle=cost.bundle, bar_times=supply_window,
        supply_seconds=cost.seconds,
    )
    if unaligned:
        report(f"段0 {spec.key} 整列不能: {[f.series_name for f in unaligned]}")
    blocked = {f.series_name for f in unaligned}
    targets = [name for name in sorted(cost.bundle) if name not in blocked]
    if not targets:
        return excluded + unaligned

    # --- 段 1: スクリーニング
    started = time.perf_counter()
    screened = _verify_window(
        spec=spec, options=options, probe=probe, bar_times=verify_window,
        names=targets, supply_seconds=cost.seconds,
    )
    report(
        f"段1 {spec.key} {time.perf_counter() - started:.1f} 秒 "
        f"一致={sorted(n for n in targets if screened[n].selectable)}"
    )
    passing = [name for name in targets if screened[name].selectable]
    if not passing:
        return excluded + unaligned + [screened[name] for name in targets]

    # --- 段 2: 供給窓で確定（coverage=1.0）
    started = time.perf_counter()
    confirmed = _verify_window(
        spec=spec, options=options, probe=probe, bar_times=supply_window,
        names=targets, supply_seconds=cost.seconds,
    )
    report(
        f"段2 {spec.key} {time.perf_counter() - started:.1f} 秒 "
        f"一致={sorted(n for n in targets if confirmed[n].selectable)}"
    )
    return excluded + unaligned + [
        _confirmed_or_screened(
            confirmed=confirmed[name], screened=screened[name],
            passed_screening=name in passing, verify_bars=len(verify_window),
        )
        for name in targets
    ]


def _confirmed_or_screened(
    *,
    confirmed: CausalityFinding,
    screened: CausalityFinding,
    passed_screening: bool,
    verify_bars: int,
) -> CausalityFinding:
    """段 2 の結果を採る（段 1 で落ちた系列は段 1 の結果）。

    段 1 で一致したのに段 2 が確定しなかった系列は、**段 1 で一致した事実を detail に残す**。
    測れたことを台帳から消すと、次の再検定でどこから測り直せばよいかが判らなくなる。
    """
    if not passed_screening:
        return screened
    if confirmed.selectable:
        return confirmed
    return replace(
        confirmed,
        detail=f"{confirmed.detail}（段 1 の {verify_bars} 本では一致）",
    )


def _verify_window(
    *,
    spec: IndicatorSpec,
    options: CliOptions,
    probe: Any,
    bar_times: "Sequence[int]",
    names: "Sequence[str]",
    supply_seconds: float,
) -> "dict[str, CausalityFinding]":
    """1 つの窓で全系列を突合する（比較不能・検定不能は理由へ畳む）。

    対象系列（``names``）は必ず 1 件ずつ結果を持って返る。異常時に系列が消えると
    「検定していないのに一覧から居なくなる」形になる（無音で消さない）。
    """
    try:
        findings = verify_indicator_causality(
            spec=spec, ref=options.ref, timeframe=options.timeframe,
            bar_times=bar_times, probe=probe, tolerance=options.tolerance,
            timeout=options.verify_timeout, supply_seconds=supply_seconds,
        )
    except CausalityComparisonError as exc:
        return {n: _incomplete(spec, n, f"比較不能: {exc}", supply_seconds) for n in names}
    except Exception as exc:  # noqa: BLE001 — 1 指標の異常で全体を落とさない
        return {n: _incomplete(spec, n, f"検定できません: {exc}", supply_seconds)
                for n in names}
    measured = {f.series_name: f for f in findings}
    return {
        name: measured[name] if name in measured else _incomplete(
            spec, name, "検定窓の計算にこの系列が現れませんでした", supply_seconds
        )
        for name in names
    }


def _alignment_failures(
    *,
    spec: IndicatorSpec,
    bundle: SeriesBundle,
    bar_times: "Sequence[int]",
    supply_seconds: float,
) -> "list[CausalityFinding]":
    """供給窓へ整列できない系列を洗い出す（段 0 で測った束をそのまま使う）。

    整列規則の唯一源は usecase の `align_series_to_bars`。ここでは系列ごとに当てて、
    どの系列が落ちたかを個別に記録する（1 系列の失敗で束全体を捨てない）。
    """
    failures: "list[CausalityFinding]" = []
    for name in sorted(bundle):
        try:
            align_series_to_bars(bundle[name], bar_times)
        except SeriesAlignmentError as exc:
            failures.append(_incomplete(spec, name, f"整列できません: {exc}", supply_seconds))
    return failures


def _incomplete(
    spec: IndicatorSpec, series_name: str, detail: str, supply_seconds: "float | None"
) -> CausalityFinding:
    """「検定を完了できなかった」系列の記録（値がずれた＝mismatch とは別事象）。"""
    return CausalityFinding(
        spec=spec, series_name=series_name, selectable=False,
        reason=REASON_VERIFICATION_INCOMPLETE, detail=detail,
        supply_seconds=supply_seconds,
    )


# --- 既定の Port 実装（main 層だけが実装を知る）-----------------------------


def _default_probe() -> Any:
    """案 i / 案 ii の系列取得（源ロードを記憶する `CausalComputePort` を包む）。

    裁定 B: 記憶の寿命は**この短命プロセス**に限る。案 i は 1 バーにつき 1 回計算する
    ため、記憶が無いと毎回同じ CSV を plain dict へ実体化し直す（実測 2026-08-11:
    案 i 0.25 秒/バーのうち load_source が 242ms＝97%）。常駐へは注入しない。
    """
    from simulator.replay_ui.adapter.causal_compute_gateway import CausalComputeGateway
    from simulator.sim_ui.adapter.causal_compute_ports import (
        memoized_causal_compute_ports,
    )
    from simulator.sim_ui.adapter.causal_series_probe import CausalSeriesProbe

    # ISSUE-479 S-5 / Wave2b: ロード面だけを記憶し、時間足グリッド面と指標計算面は素の
    #   実体へ委ねる明示合成を使う（拾い先となる __getattr__ を持たない）。後方互換の
    #   ためだけに残っていた旧名 shim は削除済みで、記憶の入口はこの合成 1 つである。
    return CausalSeriesProbe(
        compute_port=memoized_causal_compute_ports(inner=CausalComputeGateway())
    )


def _default_catalog() -> Any:
    from simulator.sim_ui.adapter.indicator_catalog_source import IndicatorCatalogSource

    return IndicatorCatalogSource()


def _default_ledger(options: CliOptions) -> Any:
    from pathlib import Path

    from simulator.sim_ui.adapter.file_indicator_causality_ledger import (
        FileIndicatorCausalityLedger,
    )

    if options.data_root is not None:
        return FileIndicatorCausalityLedger(data_root=options.data_root)
    # 既定は合成根が解決する根（既定値を二重定義しない）。
    from simulator.sim_ui.main.composition_root_jobs import build_sim_job_app

    app = build_sim_job_app(web_dir=Path(__file__).resolve().parents[1] / "web")
    return FileIndicatorCausalityLedger(data_root=app.ledger.data_root)


if __name__ == "__main__":  # pragma: no cover — 実行経路
    raise SystemExit(main())
