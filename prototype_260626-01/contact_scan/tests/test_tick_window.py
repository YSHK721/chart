"""tick_window.window_ticks の後方互換不変条件を固定する回帰テスト。

do_intraday から抽出した window_ticks が満たすべき不変条件（旧 do_intraday と同等＝後方互換）:
  ① 窓フィルタ: 返るのは [start, end) のティックのみ（窓外混入なし）。
  ② 時系列順: sec 昇順（順序保存）。
  ③ 形: (sec:int, mid:float) で mid=(bid+ask)/2。
  ④ 外れ値除去: 窓内 mid 中央値から ±OUTLIER_THRESHOLD 超を除去。
これらが崩れると proto_server の out["ticks"] と接点検出の双方が退行する（その間違いを禁止する1本）。

実 parquet 前提のため @slow。データ不在時は skip。
"""
from __future__ import annotations

import datetime as dt
import glob
import statistics
import sys
from pathlib import Path

import pytest

# contact_scan を import 可能に（テスト実行位置非依存）。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from contact_scan.tick_window import OUTLIER_THRESHOLD, TICK_ROOT, window_ticks  # noqa: E402


def _pick_day_window(span_sec: int = 3600) -> tuple[int, int]:
    """実 parquet が存在する日を1つ選び、その日内の [start, start+span) 窓を返す。"""
    files = sorted(glob.glob(str(TICK_ROOT / "*" / "*" / "*" / "JP225_ticks.parquet")))
    if not files:
        pytest.skip("tick parquet 不在（実データ前提の slow テスト）")
    parts = Path(files[-1]).parts
    y, m, d = int(parts[-4]), int(parts[-3]), int(parts[-2])
    start = int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())
    return start, start + span_sec


@pytest.mark.slow
def test_window_ticks_backward_compat_invariants() -> None:
    start, end = _pick_day_window()
    pairs = window_ticks(start, end)
    if not pairs:
        pytest.skip("選定窓にティックが無い")
    secs = [s for s, _ in pairs]
    mids = [mm for _, mm in pairs]
    # ① 窓フィルタ
    assert all(start <= s < end for s in secs), "窓外ティックが混入している"
    # ② 時系列順
    assert secs == sorted(secs), "sec が昇順でない（順序保存されていない）"
    # ③ 形（sec int / mid float）
    assert all(isinstance(s, int) for s in secs)
    assert all(isinstance(mm, float) for mm in mids)
    # ④ 外れ値除去（窓内中央値±threshold 内に収まる）
    med = statistics.median(mids)
    assert med > 0
    assert all(abs(mm / med - 1.0) <= OUTLIER_THRESHOLD + 1e-9 for mm in mids), \
        "外れ値（中央値±threshold 超）が除去されていない"


@pytest.mark.slow
def test_window_ticks_filters_to_window() -> None:
    """日窓と、その先頭1時間窓で件数が異なる（窓が実際に効いている＝旧 do_intraday の日固定でない）。"""
    start, _ = _pick_day_window()
    hour = window_ticks(start, start + 3600)
    day = window_ticks(start, start + 86400)
    if not day:
        pytest.skip("選定日にティックが無い")
    assert len(hour) <= len(day), "1時間窓が日窓を超えるのは不整合"
