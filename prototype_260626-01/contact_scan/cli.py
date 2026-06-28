"""cli — argparse → scan → JSONL 追記 + .summary.json（IO はここに閉じる）。

sys.path に API 根（indigators/indicator_ui/api）と repo 根を冒頭で追加し、dataset / full_compute /
IndicatorComputeAdapter を read-only import する（proto_server の sys.path 設定を踏襲）。
MA は ``full_compute(..., {ma_type, length, source, offset:0, wait_for_close:False})`` の name=='MA' を使う
（wait_for_close=False 必須＝最終足の MA も出す）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# --- sys.path 設定（proto_server を参考に：API 根は `from adapter...` のパッケージルート） ---
_HERE = Path(__file__).resolve().parent              # contact_scan/
_REPO = _HERE.parents[1]                             # repo 根（/.../app）
_API = _REPO / "indigators" / "indicator_ui" / "api"
for _p in (str(_API), str(_REPO)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contact_scan.bar_window import bar_window          # noqa: E402
from contact_scan.engine import ScanConfig, build_context, make_summary, scan  # noqa: E402
from contact_scan.spec import MovingAverageContact      # noqa: E402
from contact_scan.tick_window import window_ticks        # noqa: E402


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="contact_scan.cli",
                                description="価格×指標の接点（クロス）を全ティック走査で抽出する。")
    p.add_argument("--ref", required=True, help="datasetRef（例 jp225_tick）")
    p.add_argument("--timeframe", required=True, help="時間足（1m..1M）")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--last-n", type=int, default=None, help="末尾 N 足のみ対象")
    p.add_argument("--from", dest="from_ts", type=int, default=None, help="開始 UNIX 秒（含む）")
    p.add_argument("--to", dest="to_ts", type=int, default=None, help="終了 UNIX 秒（含む）")
    p.add_argument("--indicator", default="moving_averages")
    p.add_argument("--variant", default="default")
    p.add_argument("--ma-type", default="ema")
    p.add_argument("--length", type=int, default=9)
    p.add_argument("--source", default="close")
    p.add_argument("--full-scan", dest="full_scan", action=argparse.BooleanOptionalAction,
                   default=True, help="候補足を全ティック走査（既定 ON）。--no-full-scan でプレビュー。")
    p.add_argument("--out", required=True, help="出力ディレクトリ")
    return p


def _select_df(df, args):
    """last-n / from-to で df を絞る（どちらも無ければ全件）。"""
    if args.last_n is not None and args.last_n > 0:
        return df.tail(args.last_n)
    if args.from_ts is not None or args.to_ts is not None:
        secs = df.index.values.astype("datetime64[s]").astype("int64")
        mask = (secs >= (args.from_ts if args.from_ts is not None else secs.min())) & \
               (secs <= (args.to_ts if args.to_ts is not None else secs.max()))
        return df[mask]
    return df


def _ma_series(adapter, args, df):
    from adapter.compute.latest_dispatch import full_compute
    params = {"ma_type": args.ma_type, "length": args.length, "source": args.source,
              "offset": 0, "wait_for_close": False}     # 最終足の MA も出す＝必須
    series = full_compute(adapter, args.indicator, args.variant, df, params)
    for s in series:
        if s.get("name") == "MA":
            return s["data"], params
    raise SystemExit("MA 系列（name=='MA'）が full_compute 出力に見つからない")


def _basename(args, mode: str) -> str:
    return f"{args.ref}_{args.timeframe}_{args.indicator}_{args.ma_type}{args.length}_{mode}"


def run(args) -> dict:
    from adapter.compute import dataset, IndicatorComputeAdapter

    df = dataset.load_dataframe(args.ref, args.timeframe)
    df = _select_df(df, args)
    if len(df) == 0:
        raise SystemExit("対象足が 0 件（ref/timeframe/範囲指定を確認）")

    adapter = IndicatorComputeAdapter()
    ma_data, params = _ma_series(adapter, args, df)
    ctx = build_context(df, ma_data)
    cfg = ScanConfig(ref=args.ref, timeframe=args.timeframe, indicator=args.indicator,
                     variant=args.variant, params=params, full_scan=args.full_scan)
    spec = MovingAverageContact()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mode = "full_scan" if cfg.full_scan else "preview"
    base = _basename(args, mode)
    jsonl_path = out_dir / f"{base}.jsonl"
    summary_path = out_dir / f"{base}.summary.json"

    counts: dict = {}
    n_lines = 0
    with jsonl_path.open("w", encoding="utf-8") as f:
        for ev in scan(ctx, spec, cfg, summary=counts,
                       ticks_fn=window_ticks, bar_window_fn=bar_window):
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
            n_lines += 1

    summary = make_summary(cfg, ctx, counts)
    with summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    print(f"[contact_scan] events={n_lines} -> {jsonl_path}")
    print(f"[contact_scan] summary -> {summary_path}")
    print(f"[contact_scan] candidate_bars={summary['candidate_bars']} "
          f"contacts={summary['contacts']} ticks_scanned={summary['ticks_scanned']} "
          f"full_scan={summary['full_scan']}")
    return summary


def main(argv=None) -> int:
    args = _build_parser().parse_args(argv)
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
