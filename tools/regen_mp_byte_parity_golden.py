"""MP byte-parity golden の再生成（ISSUE-088 🟡-1）。

golden（tests/fixtures/mp_byte_parity_golden.jsonl）は固定クエリ集合に対する応答スナップショット。
固定 to/now 窓が指す parquet のデータドリフト（レビューで実測確定）や、承認済みの挙動変更
（ISSUE-078 セッション日再設計の dwell 変化）で陳腐化した expected を、現行ハンドラの応答で
更新する（クエリ集合 q/h は不変＝カバレッジ維持）。

実行: PYTHONPATH=.:indigators/market_profile/api:indigators/indicator_ui/api \
      python3 tools/regen_mp_byte_parity_golden.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api"))
sys.path.insert(0, str(ROOT / "indigators" / "indicator_ui" / "api"))

sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api" / "tests"))
import mp_parity_world  # noqa: E402（ISSUE-089: jp225_tick 系は決定論の合成世界で固定）

mp_parity_world.apply()

from market_profile_api.controller.market_profile_controller import handle_market_profile  # noqa: E402
from market_profile_api.controller.market_profile_forming_controller import (  # noqa: E402
    handle_market_profile_forming,
)

GOLDEN = (ROOT / "indigators" / "market_profile" / "api" / "tests" / "fixtures"
          / "mp_byte_parity_golden.jsonl")


def main() -> None:
    lines = [json.loads(x) for x in GOLDEN.read_text().splitlines() if x.strip()]
    changed = 0
    out = []
    for entry in lines:
        q = dict(entry["q"])
        ref = q.pop("ref")
        if entry["h"] == "mp":
            status, body = handle_market_profile(ref, **q)
        elif entry["h"] == "forming":
            timeframe = q.pop("timeframe")
            status, body = handle_market_profile_forming(ref, timeframe, **q)
        else:
            raise AssertionError(f"unknown handler kind: {entry['h']!r}")
        new_body = json.dumps(body, sort_keys=True, default=str)
        if entry.get("status") != status or entry.get("body") != new_body:
            changed += 1
        out.append({**entry, "status": status, "body": new_body})
    GOLDEN.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in out) + "\n",
                      encoding="utf-8")
    print(f"regenerated {GOLDEN.name}: {len(out)} cases ({changed} updated)")


if __name__ == "__main__":
    main()
