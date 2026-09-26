"""settings 経路の実行（Phase 8 スライス 4・T-1）の結合検定。

固定する不変条件（設計 §18.4 スライス 4 の通過条件）:
    1. **回帰の錨**: `settings` を持たない spec の `stats.json` は、同じ `backtest` を
       `run_backtest` へ直接与えた出力と **byte 完全一致**する（settings 分岐が現行経路へ
       1 bit も漏れていないこと）。golden はファイルに固定せず**実行で採取**する
       （固定ファイルは実装と一緒に書き換えられ得るため錨にならない）。
    2. settings 有りの run は成果物（`stats.json` / `report.md` / `report.json`）を出す。
       `run_from_settings` は成果物を書かないため、T-1 の `run_settings_job` が
       `present_outputs` まで含めた 1 本の経路であることを固定する。
    3. `FromDate` / `ToDate` の窓が実際に適用される（N-15）。足が窓の外に出ない。
    4. `.ini` の `Symbol` が実行対象データセットと食い違う run は**失敗し、理由が残る**
       （沈黙で別の銘柄の結果を出さない）。
    5. `Model=3`（Math calculations）はバー系列を供給せずに完走し、建玉 0 になる
       （規則 S: バー系列を消費しない modelling にデータを与えない）。

権威値はすべて実物から引く（`SymbolSpecCatalog` の JP225 プロファイル・`.ini` の合成は
`tester_settings_engine_fixtures` / `tester_settings_synthetic`）。本ファイルに銘柄仕様の
数値や `.ini` の既定値を書き写さない。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from simulator.main import run_backtest
from simulator.main.tester_settings.kwargs_mapper import STOP_OUT_ACTION
from simulator.sim_ui.main import run_job
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.tester_settings_engine_fixtures import runnable_expert_mapping

#: MT5 ローダ EA（本データ実体が読める EA）。`.ini` の `Expert` と `backtest.ea_name`
#: は同じ EA を指す必要がある（受付検証 b と同じ規律）。
_EA_NAME = "MA_Slope_EA"

#: 本ファイルはデータ量ではなく**写像と経路**を固定する。実カタログの JP225 実体
#: （全期間・460 万行・2026-09-06 承認）で回すと 1 run が数十分になるため、e2e スタックと
#: 同じ継ぎ目でデータ実体だけを MT5 突合 fixture（2025-01・spread あり）へ差し替える。
#: 全期間実体の実行は `simulator/tests/integration/test_marketdata_dataset_run.py` が担う。
import pytest


@pytest.fixture(autouse=True)
def _fixture_dataset(monkeypatch):
    from marketdata.dataset_registry import sim_offered_refs

    from simulator.tests.ledger_entity_fixtures import point_entity_at

    # 台帳が提供すると宣言した先頭の系列（＝本ファイルが引く profile）の実体だけを差し替える。
    point_entity_at(
        monkeypatch,
        sim_offered_refs()[0],
        Path(__file__).resolve().parents[4] / "simulator" / "tests" / "fixtures" / "mt5"
        / "ma_slope_jp225_202501" / "input" / "JP225_M1_202501.csv",
    )

#: 本 EA は SL/TP を持たない（`stop_loss_points`/`take_profit_points` > 0 は未サポート）。
_NO_SL_TP = 0.0

#: `.ini` の `Deposit`（settings 経路の初期証拠金の権威）。`backtest.initial_deposit` とは
#: 別の値にして、結果（`stats.initial_deposit`）からどちらの経路を通ったか判定する。
_DEPOSIT = "10000"


def _profile():
    return [p for p in build_run_options_port().datasets() if p.symbol == "JP225"][0]


def _backtest(**overrides) -> dict:
    """front が settings ＋ profile から導く `backtest`（18 キー完全 body・T-4）。

    profile 由来の値は**カタログから引く**（テスト側に銘柄仕様を書き写さない）。
    """
    p = _profile()
    body = {
        "ea_name": _EA_NAME,
        "symbol": p.symbol,
        "period": p.period,
        "data_path": p.data_path,
        # `.ini` の `Deposit`（下の `_DEPOSIT`）と**別の値**にしてある。settings 経路では
        # `.ini` が権威になるため、`stats.initial_deposit` がどちらの値かで「settings 経路を
        # 通ったか」を結果から判定できる（引数の有無を覗くだけの検定にしない）。
        "initial_deposit": 1_000_000.0,
        "contract_size": p.contract_size,
        "digits": p.digits,
        "point_size": p.point_size,
        "leverage": p.leverage,
        "stops_level": p.stops_level,
        "volume_min": p.volume_min,
        "volume_max": p.volume_max,
        "volume_step": p.volume_step,
        "ma_period": 2,
        "ma_method": "sma",
        "lot_size": 0.1,
        "stop_loss_points": _NO_SL_TP,
        "take_profit_points": _NO_SL_TP,
        # カタログは決定論設定を供給しない（ISSUE-533 段階 2）。front は profile が
        # 持てば素通しするだけなので、不在は不在のまま空の受け口から始める。
        "config_overrides": dict(p.config_overrides or {}),
    }
    # 本データセット・本 EA は初期証拠金 10,000 JPY でストップアウトに達する（実測:
    # `MarginCallError`）。エンジン既定 "fail_stop" は部分結果を破棄して落ちるが、実 MT5 は
    # 強制決済のうえ完走する。settings 経路が既定で採る値と**同じ値**を明示指定して、
    # 両経路を同じ分岐に置く（値は写像層の宣言が単一ソース）。
    body["config_overrides"].setdefault("stop_out_action", STOP_OUT_ACTION)
    body.update(overrides)
    return body


def _tester(**overrides) -> dict:
    """保証境界の内側にある `[Tester]` マッピング（合成器が単一ソース）。"""
    p = _profile()
    base = {
        "Expert": f"{_EA_NAME}.ex5",
        "Symbol": p.symbol,
        "Period": p.period,
        "Deposit": _DEPOSIT,
        "Leverage": str(int(p.leverage)),
    }
    base.update(overrides)
    return runnable_expert_mapping(**base)


def _job(tmp_path: Path, name: str, *, backtest=None, settings=None) -> Path:
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest() if backtest is None else backtest,
                "sizing": None,
                "strategy": None,
                "settings": settings,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_dir


def _reason(job_dir: Path) -> str:
    path = job_dir / "failure.json"
    return json.loads(path.read_text(encoding="utf-8"))["reason"] if path.is_file() else ""


def _stats(job_dir: Path) -> dict:
    return json.loads((job_dir / "stats.json").read_text(encoding="utf-8"))["stats"]


def _assert_settings_path_was_used(job_dir: Path) -> None:
    """結果から「settings 経路を通った」ことを確認する。

    これが無いと、`run_job` が `settings` を読み飛ばして現行経路で走っても成果物は同じ 3 本
    生成されるため、検定が緑のまま結線を何も固定しない（無音の不作動）。初期証拠金は
    settings 経路では `.ini` の `Deposit` が権威であり、`backtest.initial_deposit` とは別の
    値にしてある。
    """
    stats = _stats(job_dir)
    assert stats["initial_deposit"] == float(_DEPOSIT)
    assert stats["initial_deposit"] != _backtest()["initial_deposit"]


# --- 1. 回帰の錨: settings 不在は現行経路と byte 完全一致 --------------------

def test_settings不在のstats_jsonは直接実行とbyte完全一致(tmp_path: Path) -> None:
    # Arrange
    job_dir = _job(tmp_path, "legacy")
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    golden_code, _ = run_backtest(output_dir=golden_dir, **_backtest())
    # Assert
    assert (code, golden_code) == (0, 0), _reason(job_dir)
    assert (job_dir / "stats.json").read_bytes() == (golden_dir / "stats.json").read_bytes()


# --- 2. settings 有りの成果物 -----------------------------------------------

def test_settings有りで成果物が生成される(tmp_path: Path) -> None:
    """`run_from_settings` は成果物を書かない。T-1 の経路が出力段まで通ることを固定する。"""
    # Arrange
    job_dir = _job(tmp_path, "settings", settings={"tester": _tester(), "inputs": []})
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code == 0, _reason(job_dir)
    for name in ("stats.json", "report.md", "report.json"):
        assert (job_dir / name).is_file(), f"{name} が生成されていない"
    _assert_settings_path_was_used(job_dir)


# --- 3. 期間窓の適用（N-15）-------------------------------------------------

def _bar_times(job_dir: Path) -> "list[int]":
    payload = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    return [int(bar["time"]) for bar in payload["segments"]["single"]["bars"]]


def _midnight(text: str) -> int:
    return int(datetime.strptime(text, "%Y.%m.%d").replace(tzinfo=timezone.utc).timestamp())


def test_FromDateとToDateの窓が足に適用される(tmp_path: Path) -> None:
    # Arrange: `Dates` を外し custom range にする（規則 E）
    from simulator.tests.unit.tester_settings_synthetic import OMIT

    from_date, to_date = "2025.01.06", "2025.01.10"
    tester = _tester(Dates=OMIT, FromDate=from_date, ToDate=to_date)
    windowed = _job(tmp_path, "window", settings={"tester": tester, "inputs": []})
    whole = _job(tmp_path, "whole", settings={"tester": _tester(), "inputs": []})
    # Act
    assert run_job.main(["--job-dir", str(windowed)]) == 0, _reason(windowed)
    assert run_job.main(["--job-dir", str(whole)]) == 0, _reason(whole)
    # Assert: 窓は [from 00:00Z, to+1day 00:00Z)
    times = _bar_times(windowed)
    assert times, "窓内の足が 0 本（窓の解決が壊れている）"
    assert min(times) >= _midnight(from_date)
    assert max(times) < _midnight(to_date) + 86_400
    assert len(times) < len(_bar_times(whole)), "窓が絞られていない"


# --- 4. データセットとの不一致は失敗し理由が残る ------------------------------

def test_Symbolがデータセットと不一致なら失敗し理由が残る(tmp_path: Path) -> None:
    # Arrange
    job_dir = _job(
        tmp_path, "mismatch", settings={"tester": _tester(Symbol="EURUSD"), "inputs": []}
    )
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code != 0
    reason = _reason(job_dir)
    assert "EURUSD" in reason, f"不一致の理由が残っていない: {reason!r}"


# --- 5. `ea_params` 残余の導出が写像層と一致する ------------------------------

def test_写像層が供給する引数名の導出が実際の写像と一致する() -> None:
    """`EngineBinding.ea_params` は「写像層が供給しない残余」でなければならない。

    重なれば `ConfigError`（権威の二重化）、足りなければ規則 R の欠落になる。導出
    （`run_job._settings_supplied_params`）は名前の表を手書きせず写像層の宣言から作って
    いるが、**その導出規則が写像層の実際の像と一致すること**は独立に固定しないと、写像層
    が束縛を 1 つ増やした日に静かにずれる。期待値を書き写さず、実際の写像結果と突き合わせる。
    """
    from simulator.framework.tester_settings import tester_settings_from_mapping
    from simulator.main.tester_settings.kwargs_mapper import effective_to_interactor_kwargs

    # Arrange
    effective = tester_settings_from_mapping(_tester(), []).effective()
    binding = run_job._build_engine_binding({"backtest": _backtest()}, effective)
    # Act
    kwargs = effective_to_interactor_kwargs(effective, binding)
    # Assert
    assert set(kwargs) - set(binding.ea_params) == run_job._settings_supplied_params()
    assert not (set(binding.ea_params) & run_job._settings_supplied_params())


# --- 6. Math calculations（規則 S）-------------------------------------------

def test_Model3はデータ非供給で完走し建玉0になる(tmp_path: Path) -> None:
    # Arrange: `Model` の値は列挙が単一ソース（数値を書き写さない）
    from simulator.usecase.tester_settings import TickModel

    settings = {
        "tester": _tester(Model=str(int(TickModel.MATH_CALCULATIONS))),
        "inputs": [],
    }
    job_dir = _job(tmp_path, "math", settings=settings)
    # Act
    code = run_job.main(["--job-dir", str(job_dir)])
    # Assert
    assert code == 0, _reason(job_dir)
    stats = _stats(job_dir)
    assert stats["trades"] == 0
    # settings 経路を通ったことの証明（`Math calculations` 版）: `Deposit` は inert になり、
    # 注入束の既定（`INERT_DEPOSIT`＝推定値を作らない 0.0）が現れる。現行経路なら
    # `backtest.initial_deposit` がそのまま出るため、両者は決して一致しない。
    from simulator.main.tester_settings.kwargs_mapper import INERT_DEPOSIT

    assert stats["initial_deposit"] == INERT_DEPOSIT
    assert stats["initial_deposit"] != _backtest()["initial_deposit"]


# ---- 実行トレース（ISSUE-508 段階 3・§6.5.1）: settings 経路でも書き出されること ----

def test_settings経路でも実行トレースが書き出される(tmp_path: Path) -> None:
    """trace ON の settings run が成果物 3 本を残すこと。

    なぜここに置くか: 構造の固定（書出し点が 1 箇所・settings 分岐がそこを飛び越えない）は
    `test_run_job_trace.py` が構文木で行うが、**`run_settings_job(extensions=...)` が
    `run_tracer` を Composition Root（`simulator/main/__init__.py`）まで運ぶか**は
    写像層を経由するため構文木からは
    分からない。実際に走らせて成果物で測る。本ファイルの autouse fixture（JP225 実体の
    差し替え）が要るので、fixture を写さずに済む本ファイルへ置く。

    これが無いと「現行経路だけトレースが出て settings 経路では無言で出ない」状態のまま
    緑になる（ISSUE-291 と同型の無言の欠落）。
    """
    # Arrange
    import pandas as pd

    from simulator.sim_ui.adapter import trace_writer

    job_dir = tmp_path / "settings_trace"
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest(), "sizing": None, "strategy": None,
                "settings": {"tester": _tester(), "inputs": []},
                "trace": {"enabled": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # Act
    code = run_job.main(["--job-dir", str(job_dir)])

    # Assert: run が成功し、書出しの失敗記録が残っていない。
    assert code == 0, _reason(job_dir)
    assert not (job_dir / run_job._TRACE_ERROR_FILE).exists()
    for name in (
        trace_writer.POINTS_FILENAME,
        trace_writer.INDICATORS_FILENAME,
        trace_writer.META_FILENAME,
    ):
        assert (job_dir / name).is_file(), name

    points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
    meta = json.loads(
        (job_dir / trace_writer.META_FILENAME).read_text(encoding="utf-8")
    )
    assert meta["rows"]["points"] == len(points)
    assert meta["job_id"] == job_dir.name
    # 正の対照: 0 行なら「書けた」以外の何も測れていない。
    assert len(points) > 0, "settings 経路の trace_points.parquet が 0 行"


def _trace_job(tmp_path: Path, name: str, *, tester: dict) -> Path:
    """trace ON の settings ジョブを組む（`_job` は trace ブロックを持たないため別に組む）。"""
    job_dir = tmp_path / name
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest(), "sizing": None, "strategy": None,
                "settings": {"tester": tester, "inputs": []},
                "trace": {"enabled": True},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return job_dir


def _trace_meta(job_dir: Path) -> dict:
    from simulator.sim_ui.adapter import trace_writer

    return json.loads(
        (job_dir / trace_writer.META_FILENAME).read_text(encoding="utf-8")
    )


def test_settings経路の窓の申告は実効kwargsから採る(tmp_path: Path) -> None:
    """§6.5.2.1: `.ini` で期間を絞った run は `marketdata_window: true` と申告する。

    是正前は `spec["backtest"]` を無条件に読んでいた。窓の供給元は経路で異なり
    （現行経路＝`backtest` ブロック／settings 経路＝`.ini` の `FromDate`/`ToDate` →
    `simulator/main/tester_settings/window.py` の窓境界解決 →
    `simulator/main/tester_settings/kwargs_mapper.py` の名前一致導出）、front が送る 11 キーに
    `marketdata_window` は含まれない。したがって**窓を絞った settings run ほど
    食い違いが大きいのに、まさにその run が「比較可能」と名乗っていた**。

    §6.5.2 が段階 3 に課した義務は「隠さないこと」ただ 1 つであり、その仕組みが
    settings 経路で機能していなければ義務の不履行である（ISSUE-509 は「範囲外・
    ただし隠すな」と裁定されている）。

    **両側で縛る**: 窓ありは true、窓なしは false。片側だけだと「settings 経路なら
    常に true」と書く実装が通り、申告が実際の窓を追わなくなる。
    """
    # Arrange: `Dates` を外し custom range にする（規則 E・既存検定と同じ形）。
    from simulator.tests.unit.tester_settings_synthetic import OMIT

    windowed = _trace_job(
        tmp_path, "trace_window",
        tester=_tester(Dates=OMIT, FromDate="2025.01.06", ToDate="2025.01.10"),
    )
    whole = _trace_job(tmp_path, "trace_whole", tester=_tester())

    # Act
    assert run_job.main(["--job-dir", str(windowed)]) == 0, _reason(windowed)
    assert run_job.main(["--job-dir", str(whole)]) == 0, _reason(whole)

    # Assert: 窓を絞った run は「窓あり＝指標の bar_index と突き合わせられない」と申告する。
    assert _trace_meta(windowed)["marketdata_window"] is True
    assert _trace_meta(windowed)["indicator_bar_index_is_comparable"] is False
    # もう片側: 窓を絞っていない run は従来どおり「窓なし」と申告する。
    assert _trace_meta(whole)["marketdata_window"] is False
    assert _trace_meta(whole)["indicator_bar_index_is_comparable"] is True
    # 正の対照: どちらの run も実際に記録している（0 行なら申告だけ見て中身が無い）。
    assert _trace_meta(windowed)["rows"]["points"] > 0
    assert _trace_meta(whole)["rows"]["points"] > 0
    # 正の対照: 窓が実際に効いている（記録量が違う）。同じなら「窓あり」の申告が空証明。
    assert _trace_meta(windowed)["rows"]["points"] < _trace_meta(whole)["rows"]["points"]
