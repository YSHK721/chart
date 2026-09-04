"""口座状態エンジンの回帰ゲート fixture を再生成する（ISSUE-479 Wave2 フェーズ 1-D）。

本モジュールは `prototype_260811-01/make_regression_fixture.py` の本体を移設したもので
ある。移設の理由は所在であって内容ではない: 回帰ゲートが読む固定値の生成器が試作の
中に居ると、レビューも回帰ゲートも通らないコードが**ゲートの期待値そのもの**を作る
ことになる（ゲートが自分の答えを試作から受け取る構図）。生成器は本体に置き、試作は
それを指すだけにする。

生成物（`simulator/tests/fixtures/account_engine/` へ出力・追跡対象）:
    jp225_ticks_20260806_0000_0110.csv   実 tick 断片（2026-08-06 00:00–01:10 UTC・ts_ms,bid,ask）
    expected_gate.json                   3 ゲートシナリオのエンジン出力固定値
        - events / summary（全量）
        - series_sha256（全系列 JSON の SHA-256＝byte 一致の圧縮表現）

ゲートシナリオ（断片内で決定論的に完結する 3 種）:
    G1 単一ロング 25u・E=172,000・stop/tp なし → ロスカット（00:53）
    G2 単一ロング 20u・E=172,000・stop=65,100 → 損切り（01:01）
    G3 難平ロング 6+8+10u（成行/65,300/65,000）・E=172,000 → 部分約定のまま断片終端

再生成: MARKETDATA_DATA_DIR=... <venv python> simulator/tools/regenerate_account_engine_fixtures.py
（過去 UTC 日の tick は再取得＝保存に完全一致（実測済み）のため、いつ再生成しても同一になる）

移設にあたり構造を変えた点は 2 つだけである:
    1. 出力先はリポジトリ根からの導出で持つ（絶対パスを書かない。worktree で走らせた
       ときに本チェックアウト側の fixture を書き換えてしまうのを構造的に防ぐ）。
    2. 期待値の組み立てを `build_expected` として切り出し、tick の入手（marketdata）と
       分離した。これにより回帰ゲートは**コミット済みの tick 断片**から期待値を
       再生成して byte 一致を検証でき、marketdata の実体が無い環境でも生成器の同一性を
       確かめられる。`iter_ticks` の import が main の中にあるのも同じ理由である
       （import しただけで pandas と marketdata を引き込まないため）。
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from simulator.usecase.account_engine import (
    AccountConfig, AccountEngine, EntryOrder, OrderPlan,
)

#: リポジトリ根（このファイル: <repo>/simulator/tools/ → parents[2]）。
_REPO = Path(__file__).resolve().parents[2]

FIXTURE_DIR = _REPO / "simulator" / "tests" / "fixtures" / "account_engine"
CSV_NAME = "jp225_ticks_20260806_0000_0110.csv"
T0 = 1785974400000            # 2026-08-06T00:00:00Z (ms)
T1 = T0 + 70 * 60 * 1000      # +70 分


def gate_scenarios() -> "dict[str, tuple[OrderPlan, AccountConfig]]":
    """回帰ゲートの 3 シナリオ（リポジトリ内で唯一の定義）。"""
    return {
        "G1_long_losscut": (
            OrderPlan(direction="long", entries=[EntryOrder(units=25.0)]),
            AccountConfig(balance=172000.0)),
        "G2_long_stop": (
            OrderPlan(direction="long", entries=[EntryOrder(units=20.0)], stop_price=65100.0),
            AccountConfig(balance=172000.0)),
        "G3_split_partial": (
            OrderPlan(direction="long", entries=[EntryOrder(units=6.0),
                                                 EntryOrder(units=8.0, price=65300.0),
                                                 EntryOrder(units=10.0, price=65000.0)]),
            AccountConfig(balance=172000.0)),
    }


def series_sha256(series) -> str:
    """状態時系列（全配列）の SHA-256＝byte 一致の圧縮表現。"""
    payload = json.dumps({
        "ts": series.ts, "bid": series.bid, "ask": series.ask,
        "balance": series.balance, "equity": series.equity,
        "required_margin": series.required_margin, "margin_ratio": series.margin_ratio,
        "open_units": series.open_units,
    }, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_tick_fragment() -> "list[tuple[int, float, float]]":
    """コミット済み tick 断片 CSV を読む（回帰ゲートと同一の入力）。"""
    out: "list[tuple[int, float, float]]" = []
    with (FIXTURE_DIR / CSV_NAME).open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for ts, bid, ask in reader:
            out.append((int(ts), float(bid), float(ask)))
    return out


def build_expected(ticks, *, on_scenario=None) -> "dict[str, dict]":
    """tick 列へ全シナリオを適用し expected_gate.json の中身を組み立てる。

    事前条件: `ticks` は (ts_ms, bid, ask) の並び。
    事後条件: シナリオごとに **エンジン実行はちょうど 1 回**（作って捨てる実行を持たない）。
    """
    expected: "dict[str, dict]" = {}
    for name, (plan, cfg) in gate_scenarios().items():
        r = AccountEngine(plan, cfg).run(iter(ticks))
        expected[name] = {
            "events": [{"ts": e.ts, "kind": e.kind, "price": e.price, "units": e.units,
                        "pnl": e.pnl, "note": e.note} for e in r.events],
            "summary": {"final_balance": r.final_balance, "closed": r.closed,
                        "losscut_hit": r.losscut_hit},
            "ticks_applied": len(r.series.ts),
            "series_sha256": series_sha256(r.series),
        }
        if on_scenario is not None:
            # 既に組み立てた entry を渡す（sha を報告のために二度計算しない）。
            on_scenario(name, r, expected[name])
    return expected


def expected_gate_json(expected: "dict[str, dict]") -> str:
    """expected_gate.json のテキスト表現（書き出しと検証で同一の 1 箇所）。"""
    return json.dumps(expected, ensure_ascii=False, indent=1)


def main() -> None:
    from simulator.tools.run_account_scenario import iter_ticks  # marketdata を遅延で引く

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    ticks = [(ts, bid, ask) for ts, bid, ask in iter_ticks("2026-08-06", "2026-08-06")
             if T0 <= ts < T1]
    csv_path = FIXTURE_DIR / CSV_NAME
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts_ms", "bid", "ask"])
        for row in ticks:
            w.writerow([row[0], repr(row[1]), repr(row[2])])   # repr = 浮動小数を桁落ちなく往復
    print(f"tick 断片 {len(ticks):,} 件 → {csv_path}")

    def _report(name: str, r, entry: dict) -> None:
        print(f"  {name}: events={len(r.events)} final=¥{r.final_balance:,.2f} "
              f"sha={entry['series_sha256'][:12]}…")

    expected = build_expected(ticks, on_scenario=_report)
    (FIXTURE_DIR / "expected_gate.json").write_text(
        expected_gate_json(expected), encoding="utf-8")
    print(f"期待値 → {FIXTURE_DIR / 'expected_gate.json'}")


if __name__ == "__main__":
    main()
