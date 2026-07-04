"""run_scan_contacts_cli — 接点スキャンの実行入口（run_is_oos_cli 流儀・Composition Root）。

責務＝結線: argparse → OHLC/ticks/MA 入手 → usecase.scan_contacts → 書込先ガード → JSON 出力。
pandas・simulator.main の import は tools 層では許容（DI-4）。usecase へは numpy/pandas を
渡さない（ticks_fn は [(sec, mid)] の plain tuple 列・ma_values は bar_index→float の dict）。

書込先ガードは run_is_oos_cli.assert_safe_output_dir を流用（禁止プレフィクス
marketdata / simulator/tests/fixtures / simulator/tests/confirmation・repo_root 外拒否）。

出力: out_dir/<base>.json（events 配列）＋ <base>.summary.json（summary）＋ report.md。
<base> = ``{ref}_{timeframe}_{indicator}_{ma_type}{length}_{mode}``（mode=full_scan|preview）。

データ源フラグ（--data-path/--symbol/--tick-store-root）は simulator 側に ref→ファイルの
レジストリが無いため補う結線用（試作 cli の dataset.load_dataframe / 固定 JP225 窓に相当）。
既定 loader/ma/ticks_factory は注入で差し替え可能（単体テストは合成データを注入し実データ非依存）。
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from simulator.tools.run_is_oos_cli import OutputGuardError, assert_safe_output_dir
from simulator.usecase.scan_contacts import (
    ScanContactsRequest,
    ScanContactsResult,
    scan_contacts,
)

__all__ = ["LoadedBars", "main", "OutputGuardError", "assert_safe_output_dir"]


@dataclass
class LoadedBars:
    """OHLC ローダの戻り（bar_times と位置対応・昇順）。source_prices は MA 計算用の価格列。"""
    bar_times: "list[int]"
    highs: "list[float]"
    lows: "list[float]"
    closes: "list[float]"
    source_prices: "list[float]" = field(default_factory=list)


# ---- argparse ----

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_scan_contacts_cli",
        description="価格×指標の接点（クロス）を全ティック走査で抽出する。",
    )
    p.add_argument("--ref", required=True, help="datasetRef（例 jp225_tick）")
    p.add_argument("--timeframe", required=True, help="時間足（1m..1M）")
    # 範囲指定は --last-n（末尾 N 足）と --from/--to（期間）の 2 系統。両指定時は **--last-n を優先**
    #   （_select_df の分岐順・試作 cli 準拠）。単一メンバの相互排他グループは排他を課さないため使わず、
    #   優先順を help に明記して黙示の取り違えを防ぐ。
    p.add_argument("--last-n", dest="last_n", type=int, default=None,
                   help="末尾 N 足のみ対象（--from/--to より優先）")
    p.add_argument("--from", dest="from_ts", type=int, default=None,
                   help="開始 UNIX 秒（含む・--last-n 未指定時に有効）")
    p.add_argument("--to", dest="to_ts", type=int, default=None,
                   help="終了 UNIX 秒（含む・--last-n 未指定時に有効）")
    p.add_argument("--indicator", default="moving_averages")
    p.add_argument("--variant", default="default")
    p.add_argument("--ma-type", dest="ma_type", default="ema")
    p.add_argument("--length", type=int, default=9)
    p.add_argument("--source", default="close")
    p.add_argument(
        "--full-scan", dest="full_scan", action=argparse.BooleanOptionalAction,
        default=True, help="候補足を全ティック走査（既定 ON）。--no-full-scan でプレビュー。",
    )
    p.add_argument("--out", required=True, help="出力ディレクトリ（repo_root 相対）")
    # simulator 結線用データ源（既定 loader/ticks_factory が使用）。
    p.add_argument("--data-path", dest="data_path", default=None, help="OHLC CSV（comma 形式）パス")
    p.add_argument("--symbol", default="JP225", help="tick-store の symbol")
    p.add_argument("--tick-store-root", dest="tick_store_root", default="marketdata/ticks",
                   help="ParquetTickRepository のルート")
    return p


def _basename(args: Any, mode: str) -> str:
    return f"{args.ref}_{args.timeframe}_{args.indicator}_{args.ma_type}{args.length}_{mode}"


# ---- 既定データ源（tools 層・pandas 許容。usecase へは plain 値のみ渡す） ----

def _default_df_loader(args: Any) -> LoadedBars:
    """comma 形式 OHLC CSV（time/open/high/low/close/...）を読み last-n / from-to で絞る。"""
    import pandas as pd

    if not args.data_path:
        raise SystemExit("--data-path が未指定です（既定 OHLC ローダに必要）")
    df = pd.read_csv(args.data_path)
    secs = df["time"].astype("int64")
    if args.last_n is not None and args.last_n > 0:
        df = df.tail(args.last_n)
    elif args.from_ts is not None or args.to_ts is not None:
        lo = args.from_ts if args.from_ts is not None else int(secs.min())
        hi = args.to_ts if args.to_ts is not None else int(secs.max())
        df = df[(secs >= lo) & (secs <= hi)]
    return LoadedBars(
        bar_times=[int(t) for t in df["time"].tolist()],
        highs=[float(v) for v in df["high"].tolist()],
        lows=[float(v) for v in df["low"].tolist()],
        closes=[float(v) for v in df["close"].tolist()],
        source_prices=[float(v) for v in df[args.source].tolist()],
    )


def _default_ma_computer(source_prices: "list[float]", length: int) -> "dict[int, float]":
    """simulator 既存 EMA（MQL 忠実 _ema_series）で bar_index → MA 値の写像を得る。"""
    import pandas as pd

    from simulator.main import _ema_series

    ema = _ema_series(pd.Series([float(v) for v in source_prices], dtype=float), length)
    return {i: float(v) for i, v in enumerate(ema.tolist())}


def _default_ticks_factory(args: Any) -> Callable[[int, int], "list[tuple[int, float]]"]:
    """ParquetTickRepository から足内窓 [start, end) の実ティックを読み [(sec, mid)] を返す。

    mid=(bid+ask)/2 と sec=timestamp(datetime64→UNIX 秒) の算出は本 adapter/tools 境界に閉じる。
    """
    import pandas as pd

    from simulator.adapter.repository.tick_parquet import ParquetTickRepository

    repo = ParquetTickRepository(args.tick_store_root)
    symbol = args.symbol

    def ticks_fn(start: int, end: int) -> "list[tuple[int, float]]":
        start_ts = pd.Timestamp(int(start), unit="s")
        end_ts = pd.Timestamp(int(end), unit="s")
        df = repo.load_ticks(symbol, start_ts, end_ts, columns=["timestamp", "bid", "ask"])
        if len(df) == 0:
            return []
        secs = pd.to_datetime(df["timestamp"]).astype("int64") // 1_000_000_000
        mid = (df["bid"].astype(float) + df["ask"].astype(float)) / 2.0
        return [(int(s), float(m)) for s, m in zip(secs.tolist(), mid.tolist())]

    return ticks_fn


# ---- 出力 ----

def _write_outputs(out_dir: Path, base: str, result: ScanContactsResult) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{base}.json").write_text(
        json.dumps(result.events, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / f"{base}.summary.json").write_text(
        json.dumps(result.summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "report.md").write_text(_to_markdown(result), encoding="utf-8")


def _to_markdown(result: ScanContactsResult) -> str:
    s = result.summary
    rng = s.get("range", {})
    lines = [
        "# Contact Scan Report",
        "",
        f"- schema: {s.get('schema')}",
        f"- range: from={rng.get('from')} to={rng.get('to')} n_bars={rng.get('n_bars')}",
        f"- full_scan: {s.get('full_scan')}",
        "",
        "## Summary",
        "| metric | value |",
        "|---|---|",
        f"| bars_total | {s.get('bars_total')} |",
        f"| bars_warmup_skipped | {s.get('bars_warmup_skipped')} |",
        f"| candidate_bars | {s.get('candidate_bars')} |",
        f"| skipped_bars | {s.get('skipped_bars')} |",
        f"| scanned_bars | {s.get('scanned_bars')} |",
        f"| contacts | {s.get('contacts')} |",
        f"| ticks_scanned | {s.get('ticks_scanned')} |",
    ]
    return "\n".join(lines) + "\n"


# ---- entry ----

def main(
    argv: "list[str] | None" = None,
    *,
    repo_root: Any = None,
    df_loader: "Callable[[Any], LoadedBars] | None" = None,
    ma_computer: "Callable[[list, int], dict] | None" = None,
    ticks_factory: "Callable[[Any], Callable[[int, int], list]] | None" = None,
) -> int:
    args = _build_parser().parse_args(argv)
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[2]

    # 書込先ガード（データ入手より先＝不正 out で無駄な IO を行わない）。
    out_dir = assert_safe_output_dir(args.out, repo_root)

    df_loader = df_loader or _default_df_loader
    ma_computer = ma_computer or _default_ma_computer
    ticks_factory = ticks_factory or _default_ticks_factory

    bars = df_loader(args)
    ma_values = ma_computer(bars.source_prices, args.length)
    ticks_fn = ticks_factory(args)

    request = ScanContactsRequest(
        ref=args.ref,
        timeframe=args.timeframe,
        indicator=args.indicator,
        variant=args.variant,
        params={"ma_type": args.ma_type, "length": args.length, "source": args.source},
        bar_times=bars.bar_times,
        highs=bars.highs,
        lows=bars.lows,
        closes=bars.closes,
        full_scan=args.full_scan,
    )
    result = scan_contacts(request=request, ticks_fn=ticks_fn, ma_values=ma_values)

    mode = "full_scan" if args.full_scan else "preview"
    _write_outputs(out_dir, _basename(args, mode), result)
    print(
        f"[contact_scan] events={len(result.events)} "
        f"candidate_bars={result.summary.get('candidate_bars')} "
        f"contacts={result.summary.get('contacts')} "
        f"ticks_scanned={result.summary.get('ticks_scanned')} full_scan={args.full_scan}"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
