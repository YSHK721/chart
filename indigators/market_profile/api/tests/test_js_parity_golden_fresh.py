"""ISSUE-087 🔴-3: JS パリティ golden fixture の鮮度検定。

Python 側の規則（session_day / _value_area / tf_meta）を変更したのに
tools/gen_js_parity_golden.py を再実行していない場合、本テストが RED になり
「規則変更 PR は fixture を必ず同時更新」を CI で強制する。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[4]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_js_parity_golden", _ROOT / "tools" / "gen_js_parity_golden.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_committed_fixture_matches_current_rules():
    gen = _load_generator()
    from marketdata import session_day as sd
    from marketdata import tf_meta

    committed = json.loads(gen.OUT.read_text(encoding="utf-8"))
    # session_day: 生成器と同じケース集合で現行実装の値を再計算し、コミット済み fixture と比較。
    current_sessions = [
        {
            "t": t,
            "dayStart": sd.session_day_start(t),
            "nextDayStart": sd.next_session_day_start(sd.session_day_start(t)),
            "label": sd.session_date_label(t),
            "barTime": sd.session_bar_time(t),
        }
        for t in gen.session_cases()
    ]
    assert committed["session_day"] == current_sessions, (
        "session_day 規則が変更されています。tools/gen_js_parity_golden.py を再実行して"
        " fixture を更新してください")
    assert committed["tf_bar_sec"] == dict(tf_meta.TF_BAR_SEC)
    assert committed["value_area"] == gen.value_area_cases()
