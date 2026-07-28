"""期間プリセット換算表の計測スクリプト（基本設計_期間プリセット.md §4・§11）。

換算プリミティブ（設計 §4.1・唯一の定義）:
    bars(tf, P) = 半開ローリング窓 [t - P, t) に含まれる実バー本数の中央値
    （右端 t は実バー時刻のみを走査。窓の左端が実測期間の外に出る t は除外）

用途: 換算表の**版上げ**（v2 の生成）時に、v1 と同一手順で再計測するための唯一の実行手順。
出力をそのまま ``web/js/usecase/period_presets.js`` の表と ``web/tests/period_presets.test.js``
の期待値へ反映する（両者は同じ数値を二重に持ち、テストが乖離を検出する）。

実行:
    /workspaces/app/lightweight-charts-python-main/.venv/bin/python \
        indigators/indicator_ui/tools/period_presets_measure.py

補足 1（再計測の再現性）: 本スクリプトの出力は、同じデータに対しては決定論的だが、**データが
伸びると一部セルが ±1 変動する**。右端サンプルを ``linspace`` で等間隔に取るため、バーが増えると
格子が動き、窓に入る本数分布の中央値が 1 だけずれることがあるためである（実測: 稼働中の
``serve.sh`` データ watch により 40 分で 30m の '1mo' が 971→972）。これは誤りではなく、
「暦期間に入る本数」が本来ゆらぐ量である。だからこそ換算表は実行時に計算せず**静的定数として
版で凍結**する（設計 §4.4）。版上げの判断は ±1 の揺れではなく、検証 1 が示すセッション構造の
**段差**（例: 1h が 22→23 本/日）を根拠に行うこと。

補足 2（実装上の落とし穴）: ロールアップ CSV の ``date`` 列は pandas が ``datetime64[us]`` で
読むため、秒への変換は ``astype("datetime64[s]").astype("int64")`` を使う。
``astype("int64") // 10**9`` はマイクロ秒を秒として扱う誤りで、日切りが全滅する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from marketdata.session_day import session_date_label, session_day_starts  # noqa: E402

ROLL = REPO / "data" / "marketdata" / "rollups" / "jp225_tick"
M1 = REPO / "data" / "marketdata" / "jp225_tick_m1.csv"

TFS = ["1m", "5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"]
TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800,
          "1h": 3600, "4h": 14400, "1D": 86400, "1W": 604800, "1M": 2592000}

# 表の単位（キーは period_presets.js の UNIT_ORDER と一致させる）。
UNITS = [
    ("1h", pd.DateOffset(hours=1), 3600),
    ("4h", pd.DateOffset(hours=4), 14400),
    ("1d", pd.DateOffset(days=1), 86400),
    ("1w", pd.DateOffset(weeks=1), 604800),
    ("1mo", pd.DateOffset(months=1), 2629746),
    ("3mo", pd.DateOffset(months=3), 7889238),
    ("6mo", pd.DateOffset(months=6), 15778476),
    ("1y", pd.DateOffset(years=1), 31556952),
    ("2y", pd.DateOffset(years=2), 63113904),
    ("3y", pd.DateOffset(years=3), 94670856),
    ("5y", pd.DateOffset(years=5), 157784760),
]

# 実測期間（設計 §4.2）。日中足は現行セッション構造が定常な区間に限定する（§4.5 検証 1）。
SINCE_INTRADAY = pd.Timestamp("2021-01-01")
SINCE_DAILY_PLUS = pd.Timestamp("2015-01-01")
MAX_SAMPLES = 4000


def load(tf: str) -> pd.DataFrame:
    path = M1 if tf == "1m" else ROLL / f"jp225_tick_{tf}.csv"
    return pd.read_csv(path, parse_dates=["date"])


def to_epoch_s(series: pd.Series) -> np.ndarray:
    return series.to_numpy().astype("datetime64[s]").astype("int64")


def measure_row(tf: str) -> dict[str, int]:
    """1 時間足分の {単位: 本数} を返す。"""
    df = load(tf)
    t_all = df["date"].to_numpy()
    intraday = TF_SEC[tf] <= 14400
    since = SINCE_INTRADAY if intraday else SINCE_DAILY_PLUS
    i0 = int(np.searchsorted(t_all, np.datetime64(since), side="left"))
    # 窓の左端が「現行構造の区間」より前へ出る右端は除外する（日中足のみ）。
    floor = max(pd.Timestamp(t_all[0]), SINCE_INTRADAY) if intraday else pd.Timestamp(t_all[0])

    row: dict[str, int] = {}
    for unit, off, sec in UNITS:
        if sec / TF_SEC[tf] < 1:
            continue  # 当該時間足で 1 本未満になる組み合わせは表に持たない。
        idx = np.linspace(i0, len(t_all) - 1, min(MAX_SAMPLES, len(t_all) - i0)).astype(int)
        right = pd.DatetimeIndex(t_all[idx])
        left = right - off
        lo = np.searchsorted(t_all, left.to_numpy(), side="left")
        cnt = (idx - lo)[np.asarray(left >= floor)]
        if len(cnt) == 0:
            continue
        row[unit] = int(np.median(cnt))
    return row


def print_table() -> dict[str, dict[str, int]]:
    table = {tf: measure_row(tf) for tf in TFS}
    print("// 換算表（period_presets.js の TABLE_Vn へ貼る形）")
    for tf in TFS:
        cells = ", ".join(f"'{u}': {v}" for u, v in table[tf].items())
        print(f"    '{tf}': Object.freeze({{ {cells} }}),")
    return table


def print_stationarity(years: range = range(2016, 2027)) -> None:
    """設計 §4.5 検証 1: 年別の 1 セッション日あたり本数（実測期間の妥当性根拠）。"""
    print()
    print("## 検証 1: 年別 1 セッション日あたり本数（中央値）")
    print("tf  | " + " | ".join(str(y) for y in years))
    for tf in ("1h", "5m", "1D"):
        df = load(tf)
        days = session_day_starts(to_epoch_s(df["date"]))
        g = (pd.DataFrame({"day": days, "yr": df["date"].dt.year.to_numpy()})
             .groupby(["yr", "day"]).size().reset_index(name="n"))
        cells = []
        for y in years:
            v = g[g["yr"] == y]["n"]
            cells.append(f"{int(np.median(v)) if len(v) else 0:>4}")
        print(f"{tf:<3} | " + " | ".join(cells))


def print_holiday_breakdown(years: range = range(2021, 2026)) -> None:
    """設計 §4.5 検証 3: 慣行値 252 と実測 258 の差の内訳（暦平日 − 実休場日）。"""
    print()
    print("## 検証 3: 年別 実セッション日 / 暦平日数 / 休場平日")
    df = load("1D")
    labels = pd.to_datetime([session_date_label(t) for t in to_epoch_s(df["date"])])
    have_by_year = {y: set(labels[labels.year == y].date) for y in years}
    for y in years:
        cal = set(pd.date_range(f"{y}-01-01", f"{y}-12-31", freq="B").date)
        miss = sorted(cal - have_by_year[y])
        print(f"{y} | 実={len(cal & have_by_year[y]):>3} 暦平日={len(cal):>3} 休場={len(miss)} "
              + ", ".join(d.strftime("%m-%d") for d in miss))


if __name__ == "__main__":
    print_table()
    print_stationarity()
    print_holiday_breakdown()
