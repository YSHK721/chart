"""地平（horizon）ごとの「次のターゲット」の持続と距離を測る（ISSUE-449 §4.3）。

設計書 `.doc/PRICE_LEVEL_REACH_SHEET_BASIC_DESIGN.md` §4.3 の表を出す。
**束ねない**（水準 1 本 = 1 行）。帯に束ねる案は §4.4 のとおり採らない。

入力: ``probe_levels.py`` が作る long.json（または同形の levels_clean.json）。
使い方:
    ISSUE449_DIR=/path/to/work python3 analyze_horizon.py
    N=390 で標本数（末尾から何本の 1m 足を使うか）を変えられる。
"""

# --- 実行時パス（唯一の設定） -------------------------------------------------
#   ISSUE449_DIR : 作業ディレクトリ。probe_levels.py の生成物を読む。
#   N            : 末尾から使う 1m 足の本数（既定 390＝6.5 時間）。
import os as _os
import json
import bisect
import statistics

D = _os.environ.get("ISSUE449_DIR", _os.path.dirname(_os.path.abspath(__file__))) + "/"
N_SAMPLES = int(_os.environ.get("N", "390"))
SRC = _os.environ.get("ISSUE449_SRC", "levels_clean.json")

TFO = ["1m", "5m", "15m", "1h", "4h", "1D", "1W", "1M"]

h = json.load(open(D + SRC))
series, candles = h["series"], h["candles"]
for i, s in enumerate(series):
    s["ts"] = [p[0] for p in s["pts"]] if "pts" in s else s["t"]
    s["vs"] = [p[1] for p in s["pts"]] if "pts" in s else s["v"]
    s["id"] = i
    s["rank"] = TFO.index(s["tf"])
byid = {s["id"]: s for s in series}


def val(s, t):
    i = bisect.bisect_right(s["ts"], t) - 1
    return s["vs"][i] if i >= 0 else None


samples = candles[-N_SAMPLES:]
snaps = [(float(c["close"]),
          sorted((v, s["id"]) for s in series if (v := val(s, c["time"])) is not None))
         for c in samples]

print(f"標本 {len(samples)} 本（1m）/ 価格水準 {len(series)} 本\n")
print(f"{'含める水準':<20}{'上 持続':>9}{'上 距離':>9}{'下 持続':>9}{'下 距離':>9}{'水準数':>8}")
for name, r in [("すべて(1m 以上)", 0), ("5m 以上", 1), ("15m 以上", 2), ("1h 以上", 3),
                ("4h 以上", 4), ("1D 以上", 5), ("1W 以上", 6)]:
    us, ds, ud, dd, nn = [], [], [], [], []
    for p, cur in snaps:
        sub = [(v, i) for v, i in cur if byid[i]["rank"] >= r]
        nn.append(len(sub))
        up = [x for x in sub if x[0] > p]
        dn = [x for x in sub if x[0] <= p]
        us.append(up[0][1] if up else None)
        ds.append(dn[-1][1] if dn else None)
        if up:
            ud.append(up[0][0] - p)
        if dn:
            dd.append(p - dn[-1][0])
    uc = sum(1 for a, b in zip(us, us[1:]) if a != b)
    dc = sum(1 for a, b in zip(ds, ds[1:]) if a != b)
    print(f"{name:<20}{(len(us)-1)/max(uc,1):>7.1f} 分{statistics.median(ud):>7.0f} 点"
          f"{(len(ds)-1)/max(dc,1):>7.1f} 分{statistics.median(dd):>7.0f} 点"
          f"{statistics.median(nn):>8.0f}")

print(f"\n※ 持続 = その指名が変わらずに続いた平均の分数（標本 {len(samples)} 本）。"
      f"\n※ 束ねない（水準 1 本 = 1 行）。帯に束ねる案は設計書 §4.4 のとおり採らない。")
