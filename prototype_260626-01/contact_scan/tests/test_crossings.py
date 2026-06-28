"""crossings.detect_crossings の sign 規約を固定する純テスト（AAA）。"""
from contact_scan.crossings import detect_crossings

LEVEL = 100.0


def _s(*vals):
    """値列を (t, val) 系列に（t は 0,1,2,...）。"""
    return [(i, float(v)) for i, v in enumerate(vals)]


def test_no_crossing_all_above_returns_empty():
    # Arrange: すべて level 上
    series = _s(101, 102, 103)
    # Act
    out = detect_crossings(series, LEVEL)
    # Assert
    assert out == []


def test_no_crossing_all_below_returns_empty():
    assert detect_crossings(_s(99, 98, 97), LEVEL) == []


def test_single_up_crossing():
    # 下→上 = up 1 件
    out = detect_crossings(_s(99, 101), LEVEL)
    assert len(out) == 1
    ev = out[0]
    assert ev["direction"] == "up"
    assert ev["index"] == 1
    assert ev["price"] == 101.0
    assert ev["prev_price"] == 99.0
    assert ev["time"] == 1
    assert ev["prev_time"] == 0


def test_single_down_crossing():
    out = detect_crossings(_s(101, 99), LEVEL)
    assert len(out) == 1
    assert out[0]["direction"] == "down"
    assert out[0]["price"] == 99.0
    assert out[0]["prev_price"] == 101.0


def test_multiple_crossings_up_then_down():
    # 下→上→下 = up, down の 2 件
    out = detect_crossings(_s(99, 101, 98), LEVEL)
    assert [e["direction"] for e in out] == ["up", "down"]
    assert [e["index"] for e in out] == [1, 2]


def test_touch_then_return_same_side_no_crossing():
    # 上→タッチ(==level)→上: タッチは符号 0、同じ側へ戻る = 接点なし
    out = detect_crossings(_s(101, 100, 102), LEVEL)
    assert out == []


def test_touch_then_opposite_side_one_crossing():
    # 上→タッチ→下: タッチ後に反対側 = 1 接点（到達点 index2・prev はタッチ点）
    out = detect_crossings(_s(101, 100, 99), LEVEL)
    assert len(out) == 1
    assert out[0]["direction"] == "down"
    assert out[0]["index"] == 2
    assert out[0]["prev_price"] == 100.0      # 直前要素 = タッチ点


def test_consecutive_flats_no_crossing():
    # 連続フラット（複数 ==level）は基準符号を保持し発火しない
    out = detect_crossings(_s(101, 100, 100, 100, 102), LEVEL)
    assert out == []


def test_start_below_establishes_baseline_no_event_on_first_point():
    # 開始時が下: 最初の非ゼロ符号は基準確立のみ（イベントなし）。続けて上抜けで 1 件。
    out = detect_crossings(_s(99, 99, 101), LEVEL)
    assert len(out) == 1
    assert out[0]["direction"] == "up"
    assert out[0]["index"] == 2


def test_start_at_level_then_up_no_event_until_nonzero_baseline():
    # 開始が ==level（符号0）→ 上: 最初の非ゼロ(上)が基準確立 = イベントなし
    out = detect_crossings(_s(100, 101, 102), LEVEL)
    assert out == []
