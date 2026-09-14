"""ティック外れ値の読み取り時補正の回帰テスト（prototype_260626-01）。

禁止する退行: Dukascopy 区間欠損による外れ値（例 2025-08-26 の ~15100＝当日 ~42600 から
約 -64%）が、ティック由来データ（/candles・/intraday の両経路）にそのまま現れること。
proto_server は生 CSV/parquet を変更せず、読み取り時に日内中央値±30%超を除去する。

契約（2025-08-26 について）:
  - /candles?datasetRef=jp225_tick&timeframe=1D の当日足 OHLC が、当日 close 近傍から
    ±THRESHOLD を超えて乖離しない（＝外れ値が混入していない）。
  - /intraday の ticks / m1(low) も同様に外れ値を含まない。
  - 正常日(2025-08-25)はティックが残存する（過剰除去でない）。

前提: proto_server.py 起動済み。使い方: python3 verify_tick_outlier_cleaning.py [PORT]
終了コード: 0=OK / 1=退行（外れ値が残存 or 過剰除去）。
"""
import datetime
import json
import sys
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8796"
BASE = f"http://127.0.0.1:{PORT}"
THRESHOLD = 0.30
TARGET = datetime.datetime(2025, 8, 26, tzinfo=datetime.timezone.utc)
NORMAL = datetime.datetime(2025, 8, 25, tzinfo=datetime.timezone.utc)


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=60) as r:
        return json.load(r)


def main():
    failures = []
    t = int(TARGET.timestamp())

    # --- /candles 1D: 当日足に外れ値が無い ---
    cs = _get(f"/candles?datasetRef=jp225_tick&timeframe=1D&limit=1500")["candles"]
    day = [c for c in cs if c["time"] == t]
    if not day:
        failures.append("2025-08-26 の日足が存在しない")
    else:
        c = day[0]
        ref = c["close"]
        for k in ("open", "high", "low", "close"):
            if ref > 0 and abs(c[k] / ref - 1.0) > THRESHOLD:
                failures.append(f"日足 {k}={c[k]} が close={ref} から±{int(THRESHOLD*100)}%超（外れ値残存）")
        print(f"  /candles 1D 2025-08-26: O/H/L/C="
              f"{c['open']:.1f}/{c['high']:.1f}/{c['low']:.1f}/{c['close']:.1f}")

    # --- /intraday: ticks と m1(low) に外れ値が無い ---
    intr = _get(f"/intraday?datasetRef=jp225_tick&start={t}&end={t+86400}")
    ticks = intr.get("ticks", [])
    if not ticks:
        failures.append("2025-08-26 の intraday ticks が空")
    else:
        srt = sorted(ticks)
        med = srt[len(srt) // 2]
        lo, hi = min(ticks), max(ticks)
        if med > 0 and (abs(lo / med - 1.0) > THRESHOLD or abs(hi / med - 1.0) > THRESHOLD):
            failures.append(f"intraday ticks に外れ値: min={lo:.1f} max={hi:.1f} median={med:.1f}")
        print(f"  /intraday ticks: n={len(ticks)} min={lo:.1f} max={hi:.1f} median={med:.1f}")
    m1 = intr.get("m1", [])
    if m1:
        lows = [r[2] for r in m1]
        closes = sorted(r[3] for r in m1)
        med = closes[len(closes) // 2]
        if med > 0 and abs(min(lows) / med - 1.0) > THRESHOLD:
            failures.append(f"intraday m1 に外れ低値: min_low={min(lows):.1f} median_close={med:.1f}")

    # --- 正常日は過剰除去でない（ティック残存） ---
    n = int(NORMAL.timestamp())
    nt = _get(f"/intraday?datasetRef=jp225_tick&start={n}&end={n+86400}").get("ticks", [])
    if not nt:
        failures.append("2025-08-25(正常日) の ticks が空＝過剰除去の疑い")
    print(f"  正常日 2025-08-25 ticks n={len(nt)}")

    if failures:
        print("\nREGRESSION DETECTED:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    print("\nOK: 2025-08-26 の外れ値はティック/足の両経路で除去され、正常日は無影響")


main()
