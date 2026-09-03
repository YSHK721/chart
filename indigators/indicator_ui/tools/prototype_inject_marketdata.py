#!/usr/bin/env python3
"""out/prototype.html へ marketdata（JP225）を注入し、指標も JP225 で再計算して整合させる。

A方式の自己完結 HTML（``out/prototype.html``）が持つ ``SAMPLE_DATA = {candles, precomputed,
meta}`` を、Dukascopy 実データ（``marketdata``）で全面再構築する:

- ``candles``     : JP225 OHLC（marketdata・外れ値補正済み）。
- ``precomputed`` : 同じ JP225 DataFrame で **既存の計算機構を read-only 再利用**して再計算
                    （``IndicatorComputeAdapter.compute`` を dataset whitelist 非経由で直接呼ぶ）。
                    これにより UI で指標を追加しても candles と価格帯・時間軸が整合する。
- ``meta``        : 実データのシンボル/足種/本数。

既存資産（``sample_data.js`` / ``dataset.py`` / 指標 src / build / サーバ）は **無改変**。
変更対象は ``out/prototype.html`` のみ。指標の既定 params は UI catalog（``web/js/usecase/
catalog.js``）の既定値に一致させる。

実行時 sys.path 書き換えを**維持する理由**（ISSUE-479 Wave2 2-7 / ISSUE-482・重要）:
    同ディレクトリの他の CLI は repo 根の insert を撤去し、解決を台帳（tools/dev_paths.txt）
    ＋ venv の .pth へ一本化した。本ファイルだけは撤去できない——``_API_DIR``
    （``indicator_ui/api``）が挿すのは adapter / framework / domain という
    **汎用名**であり、スライス間で衝突するため台帳へ載せられない（台帳の規律：載せるのは
    衝突しない固有名のトップパッケージだけ）。同じ理由で
    replay_ui/adapter/_indicator_ui_bridge の _ensure_paths も維持されている。
    つまりこれは撤去漏れではなく、bridge と同一の規律による意図的な例外である。
    例外が 1 件だけであることは
    ``tools/tests/test_cli_entrypoints_resolve_without_pythonpath.py`` が固定する。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

import dukascopy_python

_UI_ROOT = Path(__file__).resolve().parent.parent
_API_DIR = _UI_ROOT / "api"
_WORKSPACE_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_TARGET = _UI_ROOT / "out" / "prototype.html"

# marketdata（ワークスペース根）と api（adapter/domain 絶対 import）を解決可能にする。
for _p in (str(_WORKSPACE_ROOT), str(_API_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from marketdata import (  # noqa: E402
    INTERVALS,
    DukascopyCandleSource,
    repair_ohlc_outliers,
)
from adapter.compute.indicator_compute_adapter import (  # noqa: E402
    IndicatorComputeAdapter,
)

logger = logging.getLogger("prototype_inject_marketdata")

# 再計算する (key, compute_id, variant, params)。params は catalog.js の既定に一致させ、
# 各 add_* が実際に受け取る引数のみへ限定する（add_profit_band は require_full を取り、
# add_robust_profit_band は require_full を取らない 等・src シグネチャ準拠）。
_PROBABILITIES = [0.51, 0.8, 0.85, 0.9, 0.95, 0.98, 0.99]
_BUCKETS = ["nOH", "pOL", "pOH", "nOL"]

_RECOMPUTE: List[tuple[str, str, str, Dict[str, Any]]] = [
    (
        "tgp_btlm:default", "tgp_btlm", "default",
        {"fitter": "ols", "maxbars": 100, "q_low": 0.05, "q_high": 0.95},
    ),
    (
        "profit_band:global", "profit_band", "global",
        {"probabilities": _PROBABILITIES, "buckets": _BUCKETS,
         "require_full": True, "legend": False},
    ),
    (
        "profit_band:robust", "profit_band", "robust",
        {"probabilities": _PROBABILITIES, "buckets": _BUCKETS,
         "normalize": "return", "window": "expanding", "atr_period": 14,
         "min_obs": 30, "legend": False},
    ),
    (
        "price_range_power:default", "price_range_power", "default",
        # interval は実行時に価格規模へ適応させる（catalog 既定 0.1 は ~300 価格の
        # サンプル用で、JP225 の価格帯では約 44 万バンドを生み破綻するため・下記 _prp_interval）。
        {"interval": None, "range_from": None, "range_to": None, "top_n": 5,
         "bull_color": "rgba(46, 158, 91, 0.9)", "bear_color": "rgba(210, 67, 58, 0.9)",
         "width": 2},
    ),
]

# price_range_power のバンド数目標（サンプルの解像度に合わせる）。
_PRP_TARGET_BANDS = 3000


def _prp_interval(df: pd.DataFrame, override: float | None) -> float:
    """price_range_power の級刻み幅を価格規模へ適応させる（バンド数を ~_PRP_TARGET_BANDS に保つ）。

    interval は絶対価格刻み（core.py:114 ``bands += interval``）。JP225 のような高価格帯では
    catalog 既定 0.1 がバンド爆発を起こすため、価格レンジ /目標バンド数 で動的に決める。
    """
    if override is not None:
        return override
    price_range = float(df["high"].max() - df["low"].min())
    step = price_range / _PRP_TARGET_BANDS
    # 1,2,5×10^n の見やすい刻みへ丸める（最低 0.1）。
    import math

    if step <= 0:
        return 0.1
    exp = math.floor(math.log10(step))
    base = step / (10 ** exp)
    nice = 1.0 if base <= 1 else 2.0 if base <= 2 else 5.0 if base <= 5 else 10.0
    return max(round(nice * (10 ** exp), 4), 0.1)


def _candles_to_df(candles: List[dict]) -> pd.DataFrame:
    """candles を dataset.load_dataframe と同形（DatetimeIndex + OHLC）へ変換する。

    指標の ``_resolve_times`` は time_column=None のとき DatetimeIndex から時刻解決するため、
    B方式（dataset 経由）と同一の時刻・系列が得られる。
    """
    df = pd.DataFrame(candles)
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    return df[["open", "high", "low", "close"]]


def recompute_precomputed(
    df: pd.DataFrame, *, prp_interval: float
) -> Dict[str, List[dict]]:
    """JP225 df で 4 キーを再計算し ``{"<id>:<variant>": SeriesPayload[]}`` を返す。"""
    adapter = IndicatorComputeAdapter()
    precomputed: Dict[str, List[dict]] = {}
    for key, compute_id, variant, params in _RECOMPUTE:
        params = dict(params)
        if compute_id == "price_range_power":
            params["interval"] = prp_interval  # 価格規模へ適応した刻み（爆発回避）。
        payloads = adapter.compute(compute_id, variant, df, params)
        precomputed[key] = payloads
        logger.info("recompute %-26s -> %d 系列", key, len(payloads))
    return precomputed


def _sample_data_span(html: str) -> tuple[int, int]:
    """``SAMPLE_DATA = {...}`` のオブジェクト ``{`` … ``}`` の半開区間 [start, end) を返す。"""
    anchor = html.index("SAMPLE_DATA = {")
    start = html.index("{", anchor)
    depth = 0
    in_str = False
    esc = False
    i = start
    while i < len(html):
        ch = html[i]
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
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return start, i + 1
        i += 1
    raise ValueError("SAMPLE_DATA オブジェクトの閉じ } が見つかりません")


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="prototype.html へ JP225（marketdata）を注入し指標も再計算して整合させる",
    )
    parser.add_argument("--start", type=_parse_date, default=_parse_date("2022-01-01"))
    parser.add_argument("--end", type=_parse_date, default=None)
    parser.add_argument("--interval", choices=list(INTERVALS), default="day_1")
    parser.add_argument("--offer-side", choices=["bid", "ask"], default="bid")
    parser.add_argument("--target", type=Path, default=_DEFAULT_TARGET)
    parser.add_argument("--repair", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--repair-threshold", type=float, default=0.3)
    parser.add_argument("--prp-interval", type=float, default=None,
                        help="price_range_power の級刻み幅（既定: 価格規模から自動算出）")
    parser.add_argument("--quiet", action="store_true")
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

    df = _candles_to_df(candles)
    prp_interval = _prp_interval(df, args.prp_interval)
    logger.info("price_range_power interval = %s（価格規模適応）", prp_interval)
    precomputed = recompute_precomputed(df, prp_interval=prp_interval)

    interval_label = {"day_1": "1D", "hour_4": "4H", "hour_1": "1H", "min_30": "30m",
                      "min_15": "15m", "min_5": "5m", "min_1": "1m"}.get(
        args.interval, args.interval)
    sample_data = {
        "candles": candles,
        "precomputed": precomputed,
        "meta": {"symbol": "JP225 (Dukascopy E_N225Jap)",
                 "interval": interval_label, "bars": len(candles)},
    }

    html = args.target.read_text(encoding="utf-8")
    s, e = _sample_data_span(html)
    new_html = html[:s] + json.dumps(sample_data) + html[e:]
    args.target.write_text(new_html, encoding="utf-8")
    logger.info("注入完了: candles %d 本 + precomputed %d キー -> %s",
                len(candles), len(precomputed), args.target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
