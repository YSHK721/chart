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

# --- trades ピンの更新履歴（ISSUE-445 段階 2・2026-08-25）--------------------
#
# `trades_sha256` は `asdict(TradeRecord)` 全列を畳んだ値であり、**銘柄仕様そのもの**
# （`volume` と `contract_size`）を含む。段階 2 で銘柄仕様の権威を供給元スナップショットへ
# 移した結果、記録される 2 列が誤り（`volume=0.1` / `contract_size=10.0`）から真値
# （`volume=1.0` / `contract_size=1.0`）へ変わり、ダイジェストが動いた。
#
# 退行でないことを実測で確定させてから更新した（旧プロファイルと新プロファイルを
# 同一データで実走して全列比較・2026-08-25）:
#   - 差がある列は **`contract_size` と `volume` の 2 列のみ**（A・B とも）。
#   - この 2 列を除いたダイジェストは **bit-exact 一致**（時刻・価格・pnl・exit_reason 不変）。
#   - `stats_sha256` と `trade_count` は **旧ピンと完全一致**（＝損益統計は 1 ビットも動かない）。
# 積 `volume × contract_size` は 0.1×10 = 1.0×1.0 で不変であり、損益・証拠金に効かない。
# 旧ピン（参考・退行との識別用）:
#   A trades e2c1fa0be743f38722f180a1f29e54e33c4f4a05f147175f71ea97ea8074c84c
#   B trades 33d1670fdabe6c864821c94f6668a802bffe99b1efd0ac02fbd4ff84b721891d

# --- ケース A: `trading_start` なし（本番の全呼出がこの形） -------------------
# 是正前後で一致することを実走で確認した値（ISSUE-398 の byte 等価ゲート）。
_A_STATS_SHA256 = "2d696eb1539203f7a5141799a560aaab95588e7e4272b5a8820306805815ae6f"
_A_TRADES_SHA256 = "3942ad9a43746e867b02a61b7e8f0e679444fae9de90149ca378c6c51610517c"
_A_TRADE_COUNT = 1107

# --- ケース B: `trading_start` あり（是正で「黙って捨てる」が消えた） ---------
_B_STATS_SHA256 = "767255a5620d3ead33a64b50dacd539858099322c4d8b4d18ca0f56c6b2ef520"
_B_TRADES_SHA256 = "a2535a03273585e1aa2ecec2d0c313a8515c3ab64ce90151c4133c2c891e8353"
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


#: ケース A / B は実 MT5 フィクスチャを要する。ケース C（ISSUE-483 案 1）は tmp_path に
#: 自前の系列を書くため要さないので、skip はモジュール全体ではなく**当該クラスに掛ける**
#: （モジュールに掛けるとフィクスチャの無い環境で錨まで一緒に消える）。
_needs_mt5_fixture = pytest.mark.skipif(
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


@_needs_mt5_fixture
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


@_needs_mt5_fixture
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


# ======================================================================================
# ケース C: `tick_model="real_ticks"` の指紋錨（ISSUE-483 案 1・承認のうえ実施）
# ======================================================================================
#
# 起票時に棄却された形（ISSUE-483）:
#     ケース A/B と同一プロファイル（実 MT5 JP225）で `tick_model` だけを `real_ticks` へ
#     差し替える案は、tick-store が本チェックアウトに存在しないため実走が
#     **trades 0 件**になり、`trades_sha256` が sha256("") になった。固定されるのは
#     空入力のダイジェストであり、`real_ticks` の数値挙動を 1 ビットも拘束しない。
#
# 案 1（本ケース）:
#     `test_composition_real_ticks.py` と同様に tmp_path へ**自前の tick 系列**を書く。
#     錨は成立するが、MT5 JP225 フィクスチャの指紋ではなくなる（A/B と素材が異なる）。
#     素材が違うため A/B の値とは比較しない——A/B は「実データの指紋」、C は
#     「tick 経路が動いていることの指紋」という別々の役目を負う。
#
# 実測で確かめた性質（本ケースを設計するにあたって・2026-09-04）:
#   1. 確定トレードは **4 件**（空ではない）。`trades_sha256` は sha256("") ではない。
#   2. 同一入力の 2 回実行でダイジェストが一致する（決定的）。
#   3. **同じバー・同じ tick で `tick_model` を `open_only` に替えると `stats_sha256` が
#      変わる**。これが「tick 経路を実際に通った」ことの証拠である。
#
# 重要な限定（ピンの射程・誤読を防ぐために明記する）:
#     `trades_sha256` は `open_only` と **一致する**（実測）。本プロファイルは
#     `entry_price_basis="current_open"` であり、MT5 の every-tick 意味論では新規バーの
#     成行はティック価格ではなく**バー open クォート**で約定するため、確定トレードは
#     バー系列だけで決まりティックに依らない（`test_composition_real_ticks.py` が
#     「約定がティック価格に**ならない**こと」を値で固定しているのと同じ性質）。
#     したがって tick モデルを識別しているのは `stats_sha256`（ティックごとに評価される
#     含み損益・ドローダウン）である。この非対称性を検定自身が主張する
#     （`test_the_tick_model_actually_changes_the_outcome`）ので、将来ケース C が
#     「実は open_only と同じものを測っていた」状態へ退化したら赤になる。

#: 2024-01-01T00:00:00Z。comma 形式 CSV の time 列は UNIX 秒 int が契約
#: （CsvOHLCRepository._extract は値をそのまま Bar.time に載せる）。
_C_EPOCH = 1_704_067_200

#: 合成 M1 バー（open, high, low, close, spread[points]）。
#: SMA(2) の向きが複数回反転する形にして、確定トレードが**複数**成立するようにした。
_C_BARS = [
    (1.1000, 1.1010, 1.0990, 1.0995, 0),
    (1.1000, 1.1010, 1.0985, 1.0990, 0),
    (1.0990, 1.1050, 1.0990, 1.1040, 200),
    (1.1040, 1.1100, 1.1040, 1.1090, 0),
    (1.1090, 1.1120, 1.1080, 1.1110, 0),
    (1.1110, 1.1130, 1.0900, 1.0950, 0),
    (1.0950, 1.0960, 1.0880, 1.0900, 0),
    (1.0900, 1.0910, 1.0850, 1.0870, 0),
    (1.0870, 1.1000, 1.0870, 1.0990, 100),
    (1.0990, 1.1080, 1.0985, 1.1070, 0),
    (1.1070, 1.1120, 1.1060, 1.1110, 0),
    (1.1110, 1.1120, 1.0950, 1.0980, 0),
    (1.0980, 1.0990, 1.0900, 1.0920, 0),
    (1.0920, 1.0930, 1.0860, 1.0880, 0),
    (1.0880, 1.1010, 1.0880, 1.1000, 150),
    (1.1000, 1.1090, 1.0995, 1.1080, 0),
]

#: 採取値（2026-09-04 実測・2 回実行一致）。空 trades では**ない**ことを検定が主張する。
_C_STATS_SHA256 = "6aeea6e6eff07fcc2a2e9157e2433f9703f8869061fa79b75352cac7d9832f12"
_C_TRADES_SHA256 = "d1d9b1aa0175d55e3bd739f03615535447133587a7af2d87c2af652df7df6d53"
_C_TRADE_COUNT = 4

#: 空トレードのダイジェスト（ISSUE-483 が棄却した値）。錨がここへ退化したら赤にする。
_EMPTY_TRADES_SHA256 = hashlib.sha256(b"").hexdigest()


def _write_c_bars(path: Path) -> Path:
    lines = ["time,open,high,low,close,volume,spread"]
    for i, (o, h, low, c, spread) in enumerate(_C_BARS):
        lines.append(f"{_C_EPOCH + 60 * i},{o},{h},{low},{c},1.0,{spread}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_c_ticks(root: Path, symbol: str = "EURUSD") -> Path:
    """hive layout（<root>/<symbol>/year=/month=/day=）へ日別 parquet を書く。

    1 バーにつき 5 ティック（open→high→low→中間→close）。timestamp は naive UTC・昇順
    （`simulator/adapter/repository/_tick_frame.py` の TICK_COLUMNS 契約）。
    """
    import pandas as pd

    part = root / symbol / "year=2024" / "month=01" / "day=01" / "part.parquet"
    part.parent.mkdir(parents=True, exist_ok=True)
    base = pd.Timestamp("2024-01-01T00:00:00")
    rows = []
    for i, (o, h, low, c, _spread) in enumerate(_C_BARS):
        bar_start = base + pd.Timedelta(seconds=60 * i)
        for offset, price in ((5, o), (17, h), (29, low), (41, (h + low) / 2), (53, c)):
            rows.append(
                {
                    "timestamp": bar_start + pd.Timedelta(seconds=offset),
                    "bid": round(price - 0.0001, 5),
                    "ask": round(price + 0.0001, 5),
                    "last": round(price, 5),
                    "volume": 1.0,
                }
            )
    pd.DataFrame(rows).to_parquet(part, index=False)
    return root


def _run_c(tmp_path: Path, *, tick_model: str = "real_ticks", tag: str = "c") -> dict:
    """合成素材で `run_backtest` を実走してダイジェストを採る。"""
    bars_csv = _write_c_bars(tmp_path / "synth_m1.csv")
    tick_root = _write_c_ticks(tmp_path / "ticks")
    out = tmp_path / tag
    exit_code, result = run_backtest(
        output_dir=out,
        data_path=bars_csv,
        symbol="EURUSD",
        period="M1",
        ea_name="TC24051901",
        initial_deposit=10_000.0,
        contract_size=1.0,
        volume_min=0.01,
        volume_max=100.0,
        volume_step=0.01,
        stops_level=0,
        digits=5,
        point_size=0.0001,
        leverage=100.0,
        ma_period=2,
        ma_method="sma",
        lot_size=1.0,
        stop_loss_points=500,
        take_profit_points=3000,
        config_overrides={
            "tick_model": tick_model,
            "entry_price_basis": "current_open",
        },
        tick_store_root=tick_root,
    )
    assert exit_code == 0
    assert result is not None, "run_backtest が成功時に result を返していない"
    digest = _digest(result, out / "stats.json")
    digest["exit_code"] = exit_code
    return digest


@pytest.fixture(scope="module")
def run_c(tmp_path_factory):
    return _run_c(tmp_path_factory.mktemp("c"))


@pytest.fixture(scope="module")
def run_c_again(tmp_path_factory):
    return _run_c(tmp_path_factory.mktemp("c2"))


@pytest.fixture(scope="module")
def run_c_open_only(tmp_path_factory):
    return _run_c(tmp_path_factory.mktemp("c_oo"), tick_model="open_only", tag="oo")


class TestRealTicksFingerprint:
    """`tick_model="real_ticks"` 経路の指紋錨（ISSUE-483 案 1）。"""

    def test_the_real_ticks_run_matches_the_known_fingerprint(self, run_c):
        assert run_c["trade_count"] == _C_TRADE_COUNT
        assert run_c["stats_sha256"] == _C_STATS_SHA256
        assert run_c["trades_sha256"] == _C_TRADES_SHA256

    def test_the_anchor_is_not_an_empty_result(self, run_c):
        """ISSUE-483 が棄却した「空 trades の指紋」へ退化していないこと。

        検定自身がこれを主張することが本ケースの存在条件である。件数が 0 になれば
        `trades_sha256` は sha256("") になり、ピンは緑のまま何も拘束しなくなる。
        """
        assert run_c["trade_count"] >= 2, "複数トレードが成立していない（錨が痩せている）"
        assert run_c["trades_sha256"] != _EMPTY_TRADES_SHA256, (
            "trades_sha256 が sha256('') です。tick-store が空で実走が 0 トレードに"
            " なっています（ISSUE-483 で棄却された形）。"
        )

    def test_the_run_is_deterministic_across_invocations(self, run_c, run_c_again):
        """2 回実行で一致しないなら、上の固定値は無意味（ゲートが不安定）。"""
        assert run_c["stats_sha256"] == run_c_again["stats_sha256"]
        assert run_c["trades_sha256"] == run_c_again["trades_sha256"]

    def test_the_tick_model_actually_changes_the_outcome(self, run_c, run_c_open_only):
        """同じ素材で `open_only` に替えると結果が変わる（tick 経路を通った証拠）。

        これが無いと、ケース C は「real_ticks と名乗っているが実は bar 経路の指紋」へ
        静かに退化しうる（ISSUE-483 の「偽の被覆」と同型の失敗）。

        識別しているのは `stats_sha256` である。`trades_sha256` は open_only と一致する
        ——本プロファイルは entry_price_basis="current_open" で、新規バーの成行は
        バー open クォートで約定するため確定トレードがティックに依らないからである
        （実測。この非対称性ごと固定して、将来どちらが動いても気づけるようにする）。
        """
        assert run_c["stats_sha256"] != run_c_open_only["stats_sha256"], (
            "tick_model を替えても stats が変わりません。real_ticks 経路を実際には"
            " 通っていない可能性があります（錨が bar 経路の指紋に退化）。"
        )
        assert run_c["trades_sha256"] == run_c_open_only["trades_sha256"], (
            "確定トレードが tick 依存になりました。current_open のバー open クォート"
            " 約定という前提が変わっています（退行の可能性・要調査）。"
        )
