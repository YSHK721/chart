"""Python→JS パリティ golden fixture 生成（ISSUE-087 🔴-3: 二重実装の生成同期）。

規則の権威は Python（marketdata.session_day / market_profile _value_area / marketdata.tf_meta）。
本スクリプトが境界網羅の (input, expected) を JSON へ書き出し、JS テスト
（market_profile/web/tests/py_parity_golden.test.js）が JS 実装（session_day.js /
dwell_accumulator.valueArea / tf_meta.js）との一致を検定する。規則変更時は本スクリプトを
再実行して fixture を更新する（手写しスポット値による弱同期を置換）。

実行: PYTHONPATH=. python3 tools/gen_js_parity_golden.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api"))

import numpy as np  # noqa: E402

from marketdata import session_day as sd  # noqa: E402
from marketdata import tf_meta  # noqa: E402
# ISSUE-091 A7: private 名でなく公開 API（value_area）を参照する。
from market_profile_api.compute.market_profile import value_area  # noqa: E402

OUT = ROOT / "indigators" / "market_profile" / "web" / "tests" / "fixtures" / "py_parity_golden.json"


def _utc(y, m, d, hh=0, mm=0, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


def session_cases() -> list[int]:
    """境界網羅の検定時刻: 米 DST 切替（3月第2日・11月第1日）前後・週跨ぎ・月末・日常点。"""
    pts: list[int] = []
    # 米 DST 切替日（2024-2026）: 春（3月）と秋（11月）の切替日 ±（前日・当日・翌日、各 3 時刻）。
    dst_days = [
        (2024, 3, 10), (2024, 11, 3),
        (2025, 3, 9), (2025, 11, 2),
        (2026, 3, 8), (2026, 11, 1),
    ]
    for (y, m, d) in dst_days:
        base = _utc(y, m, d)
        for off_day in (-1, 0, 1):
            for hh in (0, 12, 21, 22, 23):
                pts.append(base + off_day * 86400 + hh * 3600)
    # 週跨ぎ（金→土→日→月）・月末・年跨ぎ・日常点。
    for (y, m, d) in [(2026, 7, 10), (2026, 7, 11), (2026, 7, 12), (2026, 7, 13),
                      (2026, 1, 31), (2026, 2, 28), (2025, 12, 31), (2026, 1, 1),
                      (2026, 4, 30), (2026, 7, 15)]:
        for hh in (0, 3, 12, 20, 21, 22, 23):
            pts.append(_utc(y, m, d, hh, 30))
    return sorted(set(pts))


def value_area_cases() -> list[dict]:
    cases = [
        # 整数 TPO（count 系）。
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [1, 3, 8, 2, 1], "pct": 0.70},
        {"centers": [10.0, 11.0, 12.0], "tpo": [5, 5, 5], "pct": 0.70},
        {"centers": [10.0], "tpo": [7], "pct": 0.70},
        # float z（zp 系・ISSUE-085 の切り捨てバグ回帰域）。
        {"centers": [10.0, 11.0, 12.0, 13.0], "tpo": [0.9, 0.9, 0.9, 0.9], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [0.1, 0.2, 5.0, 0.3, 0.1], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0], "tpo": [0.0, 0.0, 2.5, 0.5], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "tpo": [2.2, 0.4, 3.1, 3.1, 0.2, 1.9], "pct": 0.70},
    ]
    out = []
    for c in cases:
        lo, hi = value_area(np.asarray(c["tpo"], dtype=float), np.asarray(c["centers"]), c["pct"])
        out.append({**c, "expected": [float(lo), float(hi)]})
    return out


def main() -> None:
    sessions = [
        {
            "t": t,
            "dayStart": sd.session_day_start(t),
            "nextDayStart": sd.next_session_day_start(sd.session_day_start(t)),
            "label": sd.session_date_label(t),
            "barTime": sd.session_bar_time(t),
        }
        for t in session_cases()
    ]
    golden = {
        "generator": "tools/gen_js_parity_golden.py",
        "session_day": sessions,
        "tf_bar_sec": dict(tf_meta.TF_BAR_SEC),
        "value_area": value_area_cases(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (sessions={len(sessions)}, va={len(golden['value_area'])})")


if __name__ == "__main__":
    main()
