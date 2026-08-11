"""口座状態エンジンの挙動単体テスト（ISSUE-369 Phase 2）。

設計入力（唯一の仕様源）: docs/oanda_indices_cfd_about.md（OANDA 証券公式ページの再構成）。
    §3(2) 必要証拠金＝約定代金×証拠金率（建値固定）
    §1-2  ロスカット＝維持率 100% 以下・損失の大きい建玉から順に・回復まで継続
    §2(9)③ ロスカット取引は逆指値（損切り）より優先
このテストが固定する回帰: 上記条文に対応するエンジンの分岐（判定順・決済順・証拠金基準・
    指値約定・境界 ≤）が合成 tick で仕様どおりであること。

合成 tick は (ts_ms, bid, ask) の素 tuple（実データ非依存・決定論）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 移設ゲートの取り決めは test_account_engine_regression.py と同じ（移設コミットで retarget）。
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "prototype_260811-01"))
from account_engine import (  # noqa: E402
    AccountConfig, AccountEngine, EntryOrder, OrderPlan,
    official_losscut_price, superseded_mark_based_losscut_price,
)


def _run(plan, cfg, ticks):
    return AccountEngine(plan, cfg).run(iter(ticks))


def _mk(ts, mid, spread=1.0):
    return (ts, mid - spread / 2, mid + spread / 2)


def test_required_margin_is_entry_fixed():
    """§3(2): 必要証拠金は約定代金基準＝価格が動いても変わらない。"""
    plan = OrderPlan(direction="long", entries=[EntryOrder(units=10.0)])
    cfg = AccountConfig(balance=200000.0)
    r = _run(plan, cfg, [_mk(1, 60000.0), _mk(2, 59000.0), _mk(3, 61000.0)])
    entry_ask = 60000.5
    expected = 10.0 * entry_ask * 0.10
    holding = [m for m in r.series.required_margin if m > 0]
    assert holding == [expected] * len(holding)


def test_losscut_threshold_sits_at_official_price():
    """発動閾値が公式閉形式 X=avgP(1+mr)−E/U の位置にある（X±0.05pt で発動有無が分かれる）。

    浮動小数の丸めがあるため「ちょうど X」での ≤/< は固定せず、X を挟む 2 点で閾値位置を固定する。
    """
    units, balance = 10.0, 70000.0
    entry_ask = 60000.5
    x = official_losscut_price("long", [(entry_ask, units)], balance, 0.10)
    above = [_mk(1, 60000.0), (2, x + 0.05, x + 1.0)]          # bid が X より 0.05 上
    r1 = _run(OrderPlan(direction="long", entries=[EntryOrder(units=units)]),
              AccountConfig(balance=balance), above)
    assert not r1.losscut_hit                  # X の手前では発動しない
    below = above + [(3, x - 0.05, x + 1.0)]                   # bid が X より 0.05 下
    r2 = _run(OrderPlan(direction="long", entries=[EntryOrder(units=units)]),
              AccountConfig(balance=balance), below)
    assert r2.losscut_hit
    lc = [e for e in r2.events if e.kind == "losscut"]
    assert lc[0].price == pytest.approx(x - 0.05)


def test_losscut_closes_worst_first_and_stops_when_recovered():
    """§1-2: 損失の大きい建玉から順に決済し、維持率が回復したら残りは保持する。"""
    plan = OrderPlan(direction="long", entries=[EntryOrder(units=6.0),
                                                EntryOrder(units=8.0, price=59000.0)])
    cfg = AccountConfig(balance=83000.0)
    # 成行 6u@60000.5 → 59000 で指値 8u 約定 → 58200 まで逆行（維持率 < 100%）
    ticks = [_mk(1, 60000.0), _mk(2, 59000.0), _mk(3, 58207.0), _mk(4, 58207.0)]
    r = _run(plan, cfg, ticks)
    lc = [e for e in r.events if e.kind == "losscut"]
    assert len(lc) == 1                       # 1 本の決済で回復＝残りは決済しない
    assert lc[0].units == 6.0                 # 損失最大（建値が高い成行 6u）が先
    assert not r.closed                       # 8u は保持されたまま終端


def test_losscut_has_priority_over_stop_in_same_tick():
    """§2(9)③: 同一 tick で損切りとロスカットが両立したらロスカットが先。"""
    units, balance = 10.0, 70000.0
    entry_ask = 60000.5
    x = official_losscut_price("long", [(entry_ask, units)], balance, 0.10)
    stop = x + 50.0                            # 損切りはロスカットより手前（上）に置く
    # 1 tick で stop と X の両方を同時に割る巨大ギャップ
    ticks = [_mk(1, 60000.0), _mk(2, x - 200.0)]
    r = _run(OrderPlan(direction="long", entries=[EntryOrder(units=units)], stop_price=stop),
             AccountConfig(balance=balance), ticks)
    kinds = [e.kind for e in r.events if e.kind in ("losscut", "stop")]
    assert kinds == ["losscut"]                # ロスカットが先に全量を処理し stop は残らない


def test_limit_fills_at_limit_price_when_favorable_side_reaches():
    """指値はロング ask≤price 到達 tick で指値価格約定（U3 の近似・変更検出用に固定）。"""
    plan = OrderPlan(direction="long", entries=[EntryOrder(units=5.0, price=59500.0)])
    r = _run(plan, AccountConfig(balance=100000.0),
             [_mk(1, 60000.0), _mk(2, 59500.2), _mk(3, 59400.0)])
    entry = [e for e in r.events if e.kind == "entry"][0]
    assert entry.price == 59500.0
    assert entry.ts == 3                       # tick2 は ask=59500.7 > 59500 で未達


def test_official_formula_differs_from_superseded_formula():
    """ISSUE-370: 公式式（約定代金固定）と修正前の時価連動式は別の値になる（記録の固定）。"""
    entries = [(65516.5, 25.0)]
    x_official = official_losscut_price("long", entries, 172000.0, 0.10)
    x_old = superseded_mark_based_losscut_price("long", entries, 172000.0, 0.10)
    # 65,516.5×1.1 − 172,000/25 = 65,188.15 ／ 旧式 (65,516.5−6,880)/0.9 = 65,151.67
    assert x_official == pytest.approx(65188.15, abs=0.01)
    assert x_official - x_old == pytest.approx(36.48, abs=0.01)
