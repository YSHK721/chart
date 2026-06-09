#!/usr/bin/env python3
"""out/prototype.html の埋め込み時系列（SAMPLE_DATA.candles）を marketdata の JP225 へ差し替える。

A方式の自己完結 HTML（``out/prototype.html``）は ``SAMPLE_DATA = {candles, precomputed, meta}``
を inline 保持する。本ツールはそのうち **candles（時系列 OHLC）のみ**を Dukascopy 実データ
（``marketdata``）へ外科的に置換し、``meta`` を実データへ更新する。``precomputed``（埋め込み
指標系列）と他の生成物（``sample_data.js`` 等）には一切触れない。

注意:
    precomputed は旧サンプル（価格帯 ~300）のまま据え置くため、UI で指標を追加した場合のみ
    新しい JP225 の価格帯（~数万）とズレる。既定の初期表示（candles のみ）は正しく JP225 になる。

使用例:
    python tools/prototype_swap_data.py                       # 既定: 2022-01-01〜今日, 日足
    python tools/prototype_swap_data.py --start 2024-01-01 --interval day_1
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

import dukascopy_python

_UI_ROOT = Path(__file__).resolve().parent.parent
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TARGET = _UI_ROOT / "out" / "prototype.html"

if str(_WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_ROOT))

from marketdata import (  # noqa: E402  （sys.path 設定後に import）
    INTERVALS,
    DukascopyCandleSource,
    repair_ohlc_outliers,
)

logger = logging.getLogger("prototype_swap_data")


def _json_value_span(text: str, key: str, open_ch: str, close_ch: str) -> Tuple[int, int]:
    """``"key":`` 直後の ``open_ch`` … 対応する ``close_ch`` の半開区間 [start, end) を返す。

    文字列リテラル内の括弧を無視する（string-aware）ことで、ネストした JSON を安全に切り出す。
    """
    ki = text.index(f'"{key}":')
    start = text.index(open_ch, ki)
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    raise ValueError(f"{key} の {close_ch} が見つかりません")


def swap_candles(html: str, candles: List[dict], *, symbol: str, interval_label: str) -> str:
    """prototype.html の ``SAMPLE_DATA.candles`` を ``candles`` へ、``meta`` を実データへ置換する。

    ``precomputed`` を含む他の領域はバイト単位で保持する（candles 配列と meta オブジェクトの
    文字列スパンのみを差し替える）。
    """
    # candles 配列を置換（後続オフセットがずれるため meta より先に行い、meta を再探索する）。
    c_start, c_end = _json_value_span(html, "candles", "[", "]")
    html = html[:c_start] + json.dumps(candles) + html[c_end:]

    # meta オブジェクトを実データへ更新（symbol / interval / bars）。
    m_start, m_end = _json_value_span(html, "meta", "{", "}")
    meta = json.loads(html[m_start:m_end])
    meta["symbol"] = symbol
    meta["interval"] = interval_label
    meta["bars"] = len(candles)
    html = html[:m_start] + json.dumps(meta) + html[m_end:]
    return html


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="prototype.html の埋め込み時系列を marketdata の JP225 へ差し替える",
    )
    parser.add_argument("--start", type=_parse_date, default=_parse_date("2022-01-01"),
                        help="取得開始日 YYYY-MM-DD（含む・既定 2022-01-01）")
    parser.add_argument("--end", type=_parse_date, default=None,
                        help="取得終了日 YYYY-MM-DD（含む・既定 今日）")
    parser.add_argument("--interval", choices=list(INTERVALS), default="day_1",
                        help="足種（既定 day_1）")
    parser.add_argument("--offer-side", choices=["bid", "ask"], default="bid",
                        help="気配側（既定 bid）")
    parser.add_argument("--target", type=Path, default=_DEFAULT_TARGET,
                        help=f"対象 HTML（既定 {_DEFAULT_TARGET}）")
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True,
                        help="足内 OHLC の外れ値を補正する（既定: 有効）")
    parser.add_argument("--repair-threshold", type=float, default=0.3,
                        help="外れ値判定の足内中央値からの許容相対乖離（既定 0.3）")
    parser.add_argument("--quiet", action="store_true", help="進捗ログを抑制する")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO,
                        format="%(message)s")
    logging.getLogger("DUKASCRIPT").setLevel(logging.WARNING)

    end = args.end or datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0, tzinfo=None
    )
    fetch_end = end + timedelta(days=1)
    offer_side = (dukascopy_python.OFFER_SIDE_BID if args.offer_side == "bid"
                  else dukascopy_python.OFFER_SIDE_ASK)

    source = DukascopyCandleSource(interval=INTERVALS[args.interval], offer_side=offer_side)
    logger.info("fetching JP225 %s  %s 〜 %s", args.interval, args.start.date(), end.date())
    candles = source.fetch_candles(args.start, fetch_end)
    if not candles:
        logger.warning("取得結果が空でした（期間・休場日を確認してください）")
        return 1
    if args.repair:
        candles, fixes = repair_ohlc_outliers(candles, threshold=args.repair_threshold)
        for line in fixes:
            logger.info("外れ値補正:%s", line)

    html = args.target.read_text(encoding="utf-8")
    interval_label = {"day_1": "1D", "hour_4": "4H", "hour_1": "1H",
                      "min_30": "30m", "min_15": "15m", "min_5": "5m",
                      "min_1": "1m"}.get(args.interval, args.interval)
    new_html = swap_candles(html, candles,
                            symbol="JP225 (Dukascopy E_N225Jap)",
                            interval_label=interval_label)

    args.target.write_text(new_html, encoding="utf-8")
    logger.info("差し替え完了: candles %d 本 -> %s", len(candles), args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
