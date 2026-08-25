"""`run_backtest` の数値指紋を固定する回帰ゲート（ISSUE-398）。

なぜ必要か（実測に基づく欠落の指摘）:
    `run_backtest` 経路には MT5 等級のオラクルが存在しなかった。
      - MT5 突合（`test_ma_slope_reconcile.py`）は `build_interactor` ＋ `execute` を使い、
        `run_backtest` を**通らない**。
      - sim ジョブ検定（`sim_ui`）は `run_backtest` を差し替えて**引数だけ**を観測する。
      - 数値を測る検定は合成 6 バーの `test_end_to_end_run.py` のみ（トレード 1 件）。
    つまり `run_backtest` の内部を組み替えたとき、それが数値を動かしたか否かを機械的に
    判定する手段が無かった。本モジュールがその穴を埋める。

固定する仕様:
    1. 実 MT5 フィクスチャ（JP225 M1 2025-01・MA_Slope_EA プロファイル）を
       `run_backtest` で実走した `stats.json` 全フィールドと全確定トレードの
       sha256 が、既知の値と一致する。
    2. `trading_start` を渡した実行は、渡さない実行と**異なる**結果になる
       （＝黙って捨てられていない）。

なぜ sha256 で測るか:
    「トレード件数が同じ」「net profit が近い」で測ると、個々の約定価格・時刻が入れ替わる
    退行を通してしまう。stats 全フィールドと全トレードを 1 つのダイジェストへ畳むことで、
    どのフィールドが動いても落ちる。期待値は下の実測値としてここに固定する。

実測の経緯（ISSUE-398 の是正・本ゲートの初期値の出所）:
    是正前（`controller.run` 再ロード方式）と是正後（`controller.execute` 方式）で
    ケース A の 2 ダイジェストが**完全一致**することを実走で確認し、その値を採用した。
    ケース B は是正前には A と同一ダイジェストになった（`controller.run` が request を
    組み直し `trading_start` を落としていたため）。是正後は分岐し、その結果は MT5 突合
    テストが記録する実測（往復トレード 1164 / net -6173.9）と一致する。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.main import run_backtest
from simulator.tests.fixtures.mt5 import load_case

_CASE = "ma_slope_jp225_202501"

#: MT5 突合テストと同じ取引開始時刻（これ以前のバーは EMA seed 収束のみ）。
_TRADING_START = np.datetime64("2025-01-02T01:00:00")

# --- ケース A: `trading_start` なし（本番の全呼出がこの形） -------------------
# 是正前後で一致することを実走で確認した値（ISSUE-398 の byte 等価ゲート）。
_A_STATS_SHA256 = "2d696eb1539203f7a5141799a560aaab95588e7e4272b5a8820306805815ae6f"
_A_TRADES_SHA256 = "e2c1fa0be743f38722f180a1f29e54e33c4f4a05f147175f71ea97ea8074c84c"
_A_TRADE_COUNT = 1107

# --- ケース B: `trading_start` あり（是正で「黙って捨てる」が消えた） ---------
_B_STATS_SHA256 = "767255a5620d3ead33a64b50dacd539858099322c4d8b4d18ca0f56c6b2ef520"
_B_TRADES_SHA256 = "33d1670fdabe6c864821c94f6668a802bffe99b1efd0ac02fbd4ff84b721891d"
_B_TRADE_COUNT = 1164


def _meta(case, *, trading_start=None) -> dict:
    """MT5 突合テスト（`test_ma_slope_reconcile.py`）と同一の実走プロファイル。

    銘柄仕様 8 項目は突合テストと**同じ供給元**（スナップショット）から引く
    （ISSUE-445 段階 2）。ここにリテラルを書くと「同一プロファイル」という前提が
    黙って崩れる——実際、従来は `volume_min=0.1` / `volume_step=0.1` / `stops_level=0` を
    書き写しており、`case.yaml` の `contract_size` だけが是正されたとき
    「真値 × 非正規化ロット」という**どこにも存在しない組み合わせ**になった（実測:
    trades 1164 → 3315・設計書 §6 の V1 と同じ壊れ方）。下記の指紋は不変である。
    """
    c = case.config
    sym, acc, ea = c["symbol"], c["account"], c["expert"]
    meta = dict(
        data_path=case.warmup_csv,
        symbol=sym["name"],
        period="M1",
        ea_name="MA_Slope_EA",
        initial_deposit=float(acc["initial_deposit"]),
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, sym["name"]),
        ma_period=int(ea["ma_period"]),
        ma_method=ea["ma_method"],
        lot_size=float(ea["lot"]),
        stop_loss_points=int(ea["stop_loss"]),
        take_profit_points=int(ea["take_profit"]),
        slope_shift=int(ea["slope_shift"]),
        slope_min_points=float(ea["slope_min_points"]),
        config_overrides={
            "tick_model": "open_only",
            "entry_price_basis": "current_open",
            "stop_out_action": "close_and_halt",
            "prime_first_trading_bar": True,
            "floating_pnl_basis": "bid_ask",
        },
        stop_out_level=99.95,
    )
    if trading_start is not None:
        meta["trading_start"] = trading_start
    return meta


def _digest(result, stats_json: Path) -> "dict[str, object]":
    """`stats.json` 全フィールドと全確定トレードを 2 つの sha256 へ畳む。"""
    payload = json.loads(stats_json.read_text(encoding="utf-8"))
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    trades = "\n".join(
        json.dumps(asdict(t), sort_keys=True, default=str) for t in result.trades
    )
    return {
        "stats_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "trades_sha256": hashlib.sha256(trades.encode("utf-8")).hexdigest(),
        "trade_count": len(result.trades),
        "stats": payload["stats"],
    }


def _run(tmp_path: Path, *, trading_start=None) -> "dict[str, object]":
    case = load_case(_CASE)
    out = tmp_path / ("with_ts" if trading_start is not None else "no_ts")
    exit_code, result = run_backtest(
        output_dir=out, **_meta(case, trading_start=trading_start)
    )
    assert exit_code == 0
    assert result is not None, "run_backtest が成功時に result を返していない"
    digest = _digest(result, out / "stats.json")
    digest["exit_code"] = exit_code
    return digest


def _fixture_missing() -> bool:
    try:
        return not Path(load_case(_CASE).warmup_csv).is_file()
    except Exception:
        return True


pytestmark = pytest.mark.skipif(
    _fixture_missing(), reason="MT5 突合フィクスチャ（JP225 M1）が無い"
)


# 実走は 1 回あたり約 4 秒かかる。各テストで走らせ直すとこのモジュールだけで 6 回
# （約 30 秒）になるため、ケース A / B と「A の 2 回目」の計 3 回に畳んで共有する。
# 2 回目の A は決定性の検定に必要なので残す（1 回に畳むと固定値の意味が消える）。
@pytest.fixture(scope="module")
def run_a(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("a"))


@pytest.fixture(scope="module")
def run_a_again(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("a2"))


@pytest.fixture(scope="module")
def run_b(tmp_path_factory):
    return _run(tmp_path_factory.mktemp("b"), trading_start=_TRADING_START)


class TestRunBacktestNumericFingerprint:
    """`run_backtest` の実走結果が既知のダイジェストと一致すること。"""

    def test_the_default_profile_matches_the_known_fingerprint(self, run_a):
        assert run_a["trade_count"] == _A_TRADE_COUNT
        assert run_a["stats_sha256"] == _A_STATS_SHA256
        assert run_a["trades_sha256"] == _A_TRADES_SHA256

    def test_the_run_is_deterministic_across_invocations(self, run_a, run_a_again):
        # 同一入力で 2 回走らせて一致しないなら、上の固定値は無意味（ゲートが不安定）。
        assert run_a["stats_sha256"] == run_a_again["stats_sha256"]
        assert run_a["trades_sha256"] == run_a_again["trades_sha256"]


class TestRunBacktestHonoursTradingStart:
    """`trading_start` が実行へ効くこと（是正前は黙って捨てられていた・ISSUE-398）。

    是正前は `run_backtest` が `controller.run(...)` を呼び、`run()` が
    `RunBacktestRequest` を**組み直す**ため `build_interactor` が組んだ
    `request.trading_start` が伝わらなかった。その結果、`trading_start` を渡しても
    渡さなくても同一の結果になった（実測: 両者のダイジェストが一致）。
    """

    def test_passing_trading_start_changes_the_result(self, run_a, run_b):
        assert run_b["stats_sha256"] != run_a["stats_sha256"], (
            "trading_start が結果に効いていない（request 組み直しによる欠落の再発）"
        )
        assert run_b["trades_sha256"] != run_a["trades_sha256"]

    def test_the_trading_start_run_matches_the_known_fingerprint(self, run_b):
        assert run_b["trade_count"] == _B_TRADE_COUNT
        assert run_b["stats_sha256"] == _B_STATS_SHA256
        assert run_b["trades_sha256"] == _B_TRADES_SHA256

    def test_the_trading_start_run_reproduces_the_mt5_reconcile_observation(self, run_b):
        """`run_backtest` 経路が MT5 突合テストの実測（1164 / -6173.9）を再現する。

        MT5 突合テストは `build_interactor` ＋ `execute` を直接使う経路で
        「往復トレード = 1164 / net profit = -6173.9」を記録している。是正後は
        `run_backtest` も同じ request を実行するため、同じ観測へ到達する。
        これが `run_backtest` 経路に対する唯一の MT5 等級の裏付けである
        （上の sha256 は自分で採取した値なので、それ単独では外部の裏付けにならない）。
        """
        assert run_b["trade_count"] == 1164
        assert run_b["stats"]["profit"] == pytest.approx(-6173.9, abs=0.05)
