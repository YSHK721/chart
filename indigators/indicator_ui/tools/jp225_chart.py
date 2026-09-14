#!/usr/bin/env python3
"""JP225（日経225）の OHLC を取得し、単体 HTML チャートを生成するツール。

データ取得（Dukascopy）は ``marketdata`` パッケージ（:class:`CandleSource` ポート＋
``DukascopyCandleSource`` adapter）へ分離済み。本ツールは「取得ポートの組み立て（合成）・
クリーニングの合成・描画（HTML 生成）・CLI」という配信側の関心事のみを持つ。

出力 ``out/jp225_chart.html`` は vendor の lightweight-charts v4.1.3 を inline した
自己完結 HTML で、ブラウザで直接開ける（サーバ不要）。既存の indicator_ui フロント
（``datasetRef='sample'`` ハードコード）・既存データには一切触れない。

使用例:
    python tools/jp225_chart.py                              # 既定: 2022-01-01〜今日, 日足
    python tools/jp225_chart.py --interval hour_1 --start 2025-01-01 --end 2025-06-01

起動前提（ISSUE-479 Wave2 2-7 / ISSUE-482）: **venv の python で起動する**。トップレベル
``marketdata`` を含む import パスの解決は台帳（tools/dev_paths.txt）が唯一源であり、venv へは
`tools/install_dev_paths.py` が書く .pth が届ける。本ファイルは実行時に sys.path を書き換え
ない（解決先が起動位置に依存しなくなる・ISSUE-279）。起動できることは
`tools/tests/test_cli_entrypoints_resolve_without_pythonpath.py` が実測で固定する。
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import dukascopy_python

from marketdata import (
    INTERVALS,
    DukascopyCandleSource,
    repair_ohlc_outliers,
)

# このファイル: indicator_ui/tools/ → 親が indicator_ui/。
_UI_ROOT = Path(__file__).resolve().parent.parent
_VENDOR_JS = _UI_ROOT / "web" / "vendor" / "lightweight-charts.js"
_DEFAULT_OUTPUT = _UI_ROOT / "out" / "jp225_chart.html"

logger = logging.getLogger("jp225_chart")


def render_html(candles: List[dict], *, title: str) -> str:
    """candles を inline した自己完結 HTML（vendor lightweight-charts v4.1.3）を生成する。

    フロント（composition_root_front.js）と同じダーク配色・ローソク色を踏襲する。
    vendor JS を inline するため file:// で直接開ける（ネット・サーバ不要）。
    """
    vendor_js = _VENDOR_JS.read_text(encoding="utf-8")
    candles_json = json.dumps(candles, separators=(",", ":"))
    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  html, body {{ margin: 0; height: 100%; background: #131722; color: #d1d4dc;
    font-family: -apple-system, "Segoe UI", sans-serif; }}
  #header {{ padding: 8px 12px; font-size: 13px; border-bottom: 1px solid #2a2e39; }}
  #chart {{ position: absolute; top: 37px; left: 0; right: 0; bottom: 0; }}
</style>
</head>
<body>
<div id="header">{title} — {len(candles)} 本（Dukascopy / E_N225Jap）</div>
<div id="chart"></div>
<script>{vendor_js}</script>
<script>
  const candles = {candles_json};
  const chart = LightweightCharts.createChart(document.getElementById('chart'), {{
    layout: {{ background: {{ color: '#131722' }}, textColor: '#d1d4dc' }},
    grid: {{ vertLines: {{ color: '#1f2530' }}, horzLines: {{ color: '#1f2530' }} }},
    rightPriceScale: {{ borderColor: '#2a2e39' }},
    timeScale: {{ borderColor: '#2a2e39', timeVisible: false }},
    autoSize: true,
  }});
  const series = chart.addCandlestickSeries({{
    upColor: '#26a69a', downColor: '#ef5350',
    borderUpColor: '#26a69a', borderDownColor: '#ef5350',
    wickUpColor: '#26a69a', wickDownColor: '#ef5350',
  }});
  series.setData(candles);
  chart.timeScale().fitContent();
</script>
</body>
</html>
"""


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="JP225 の OHLC を取得し単体 HTML チャートを生成する（取得は marketdata に委譲）",
    )
    parser.add_argument("--start", type=_parse_date, default=_parse_date("2022-01-01"),
                        help="取得開始日 YYYY-MM-DD（含む・既定 2022-01-01）")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="取得終了日 YYYY-MM-DD（含む・既定 今日）")
    parser.add_argument("--interval", choices=list(INTERVALS), default="day_1",
                        help="足種（既定 day_1）")
    parser.add_argument("--offer-side", choices=["bid", "ask"], default="bid",
                        help="気配側（既定 bid）")
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT,
                        help=f"出力 HTML パス（既定 {_DEFAULT_OUTPUT}）")
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True,
                        help="足内 OHLC の外れ値を補正する（既定: 有効。--no-repair で無効化）")
    parser.add_argument("--repair-threshold", type=float, default=0.3,
                        help="外れ値判定の足内中央値からの許容相対乖離（既定 0.3=±30%%）")
    parser.add_argument("--quiet", action="store_true", help="進捗ログを抑制する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    end = args.end or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    # fetch は end 未満を返すため「その日を含む」よう 1 日加算。
    fetch_end = end + timedelta(days=1)
    offer_side = (dukascopy_python.OFFER_SIDE_BID if args.offer_side == "bid"
                  else dukascopy_python.OFFER_SIDE_ASK)

    # 合成点: 取得ポートの具象を組み立てる（利用面は fetch_candles(start, end) のみ）。
    source = DukascopyCandleSource(
        interval=INTERVALS[args.interval], offer_side=offer_side
    )

    logger.info("fetching JP225 %s  %s 〜 %s",
                args.interval, args.start.date(), end.date())
    candles = source.fetch_candles(args.start, fetch_end)
    if not candles:
        logger.warning("取得結果が空でした（期間・休場日を確認してください）")
        return 1

    if args.repair:
        candles, fixes = repair_ohlc_outliers(candles, threshold=args.repair_threshold)
        if fixes:
            logger.info("外れ値を補正しました（%d 本）:", len(fixes))
            for line in fixes:
                logger.info("%s", line)

    title = f"JP225 ({args.interval})"
    html = render_html(candles, title=title)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    logger.info("完了: %d 本 -> %s", len(candles), args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
