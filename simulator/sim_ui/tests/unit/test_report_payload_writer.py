"""report_payload_writer（F-8: ジョブ結果 → report.json）の単体検定。

固定する不変条件:
    1. 完了ジョブの job-dir へ `report.json` を書く（sim core の `/data/{job_id}/report.json`
       がそのまま配信できる位置）。
    2. 区間は 1 つ（`segments` は "single" の 1 キー）。IS/OOS を名乗らない。
    3. **ReportMeta の既定値（StopEntryProbe 実験の所与）を持ち込まない**。既定の
       ``split="2026-04-15"`` / ``note="IS/OOS 単純分割…"`` / ``params="ProbeDir=2…"`` は
       別実験の事実であり、そのまま載せると**実施していない分割の記述**になる。
    4. meta（銘柄・時間足・EA 名）と SL/TP は **job の spec.json 由来**（推測で埋めない）。
       spec に SL/TP が無いジョブでは空文字（価格を捏造しない）。
    5. 時刻は int（UNIX 秒）へ正規化される（UC は int 時刻のみを受ける契約）。
    6. 書けない入力（spec.json 不在）では例外にし、壊れた report.json を残さない。
写像そのもの（trades 16 キー・summary の式）は report_ui の UC が単一ソースであり、
ここでは再検定しない（複製 0）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from simulator.report_ui.tests.unit.test_build_report_payload import _make_result
from simulator.sim_ui.adapter import report_payload_writer


@dataclass
class _Bar:
    time: Any
    open: float
    high: float
    low: float
    close: float


@dataclass
class _SymbolSpec:
    stops_level: int = 0
    digits: int = 1
    point_size: float = 0.1


def _bars(times):
    return [_Bar(time=t, open=39400.0, high=39410.0, low=39390.0, close=39405.0)
            for t in times]


def _spec_json(**overrides):
    backtest = {
        "ea_name": "PRO_fit_Band_EA",
        "symbol": "JP225",
        "period": "M5",
        "data_path": "/does/not/matter.csv",
        "stop_loss_points": 200,
        "take_profit_points": 500,
    }
    backtest.update(overrides)
    return {"backtest": backtest}


def _job_dir(tmp_path: Path, **overrides) -> Path:
    job_dir = tmp_path / "20260811-abc"
    job_dir.mkdir(parents=True)
    (job_dir / "spec.json").write_text(
        json.dumps(_spec_json(**overrides), ensure_ascii=False), encoding="utf-8")
    return job_dir


def _result():
    return _make_result(
        [100.0, -50.0, 30.0], [2000, 3000, 4000], [10100.0, 10050.0, 10080.0])


def _loader(bars=None, spec=None):
    """`build_interactor` の代わりに (bars, symbol_spec) を返す注入口。"""
    def load(_backtest):
        return (bars if bars is not None else _bars([1000, 2000, 3000, 4000]),
                spec or _SymbolSpec())
    return load


def _write(tmp_path: Path, *, bars=None, spec=None, **overrides) -> Path:
    job_dir = _job_dir(tmp_path, **overrides)
    return report_payload_writer.write(
        job_dir, _result(), load_run_inputs=_loader(bars, spec))


def _payload(tmp_path: Path, **kwargs) -> dict:
    return json.loads(_write(tmp_path, **kwargs).read_text(encoding="utf-8"))


# --- 1. 書き出し位置 -----------------------------------------------------------

def test_writes_report_json_into_the_job_dir(tmp_path: Path) -> None:
    out = _write(tmp_path)
    assert out.name == report_payload_writer.REPORT_FILENAME == "report.json"
    assert out.parent.name == "20260811-abc"
    assert out.is_file()


# --- 2. 単一区間（IS/OOS を名乗らない）------------------------------------------

def test_the_payload_has_exactly_one_segment_named_single(tmp_path: Path) -> None:
    data = _payload(tmp_path)
    assert list(data["segments"]) == ["single"]
    assert list(data["summary"]) == ["single"]


def test_no_comparison_verdict_is_fabricated(tmp_path: Path) -> None:
    data = _payload(tmp_path)
    assert data["degradation"] == {}
    assert data["verdict"]["result"] == ""
    assert data["verdict"]["result"] not in {"pass", "warn", "fail"}


def test_the_payload_keeps_the_six_top_level_contract_keys(tmp_path: Path) -> None:
    assert set(_payload(tmp_path)) == {
        "meta", "segments", "summary", "degradation", "verdict", "_contract_notes",
    }


def test_contract_notes_record_that_it_is_a_single_segment(tmp_path: Path) -> None:
    notes = _payload(tmp_path)["_contract_notes"]
    assert any("単一区間" in n for n in notes)


# --- 3. 別実験の所与（ReportMeta 既定値）を持ち込まない --------------------------

def test_the_stop_entry_probe_split_default_is_not_leaked(tmp_path: Path) -> None:
    """既定の split/note は別実験（IS/OOS 分割）の事実。単一 run に載せない。"""
    meta = _payload(tmp_path)["meta"]
    assert meta["split"] == ""
    assert "IS/OOS 単純分割" not in meta["note"]


def test_the_stop_entry_probe_params_default_is_not_leaked(tmp_path: Path) -> None:
    meta = _payload(tmp_path)["meta"]
    assert "ProbeDir" not in meta["params"]


def test_the_note_states_that_there_is_no_split(tmp_path: Path) -> None:
    assert "単一区間" in _payload(tmp_path)["meta"]["note"]


# --- 4. meta / SL / TP は spec.json 由来 ----------------------------------------

def test_meta_comes_from_the_job_spec(tmp_path: Path) -> None:
    meta = _payload(tmp_path)["meta"]
    assert meta["symbol"] == "JP225"
    assert meta["timeframe"] == "M5"
    assert meta["strategy"] == "PRO_fit_Band_EA"


def test_segment_meta_reports_the_measured_bar_and_trade_counts(tmp_path: Path) -> None:
    seg = _payload(tmp_path)["segments"]["single"]
    assert seg["meta"]["bars"] == 4
    assert seg["meta"]["trades"] == 3


def test_sl_and_tp_are_derived_from_the_spec_points(tmp_path: Path) -> None:
    trades = _payload(tmp_path)["segments"]["single"]["trades"]
    # buy / entry 39402.0 / point_size 0.1 → SL=39402-20=39382.0 / TP=39402+50=39452.0
    assert trades[0]["sl"] == "39382.0"
    assert trades[0]["tp"] == "39452.0"


def test_missing_stop_levels_yield_empty_strings_not_invented_prices(tmp_path: Path) -> None:
    """SL/TP を持たない EA のジョブで価格を捏造しない。"""
    data = _payload(tmp_path, stop_loss_points=None, take_profit_points=None)
    trades = data["segments"]["single"]["trades"]
    assert trades[0]["sl"] == ""
    assert trades[0]["tp"] == ""


# --- 5. 時刻の int 正規化 --------------------------------------------------------

def test_bar_times_are_normalised_to_unix_seconds(tmp_path: Path) -> None:
    import pandas as pd
    bars = _bars([pd.Timestamp("2026-04-01 00:00:00", tz="UTC"),
                  pd.Timestamp("2026-04-01 00:05:00", tz="UTC")])
    seg = _payload(tmp_path, bars=bars)["segments"]["single"]
    # 期待値は stdlib datetime で独立に確認した値（実装の出力を写していない）:
    #   datetime(2026,4,1,0,0,tzinfo=utc).timestamp() == 1775001600
    assert [b["time"] for b in seg["bars"]] == [1775001600, 1775001900]
    assert all(isinstance(b["time"], int) for b in seg["bars"])


def test_integer_bar_times_pass_through_unchanged(tmp_path: Path) -> None:
    seg = _payload(tmp_path)["segments"]["single"]
    assert [b["time"] for b in seg["bars"]] == [1000, 2000, 3000, 4000]


def test_trade_times_are_integers(tmp_path: Path) -> None:
    trades = _payload(tmp_path)["segments"]["single"]["trades"]
    assert all(isinstance(t["entry_time"], int) for t in trades)
    assert all(isinstance(t["exit_time"], int) for t in trades)


# --- 6. 書けない入力では壊れた report.json を残さない -----------------------------

def test_a_job_without_a_spec_raises_and_writes_nothing(tmp_path: Path) -> None:
    job_dir = tmp_path / "empty-job"
    job_dir.mkdir()
    with pytest.raises(Exception):
        report_payload_writer.write(job_dir, _result(), load_run_inputs=_loader())
    assert not (job_dir / "report.json").exists()


def test_the_report_is_valid_json_without_nan_or_infinity(tmp_path: Path) -> None:
    raw = _write(tmp_path).read_text(encoding="utf-8")
    assert "NaN" not in raw and "Infinity" not in raw
    json.loads(raw)


# --- 7. 接点（agg.contacts）の結線（Phase 5 F-7）---------------------------------
# 接点そのものの算出は `contacts_supply`（＝report_ui の単一ソースを呼ぶ結線）が持つ。
# writer が持つのは「読み込み済みの足と job 仕様を供給へ渡し、戻りを payload へ載せる」
# ことだけである。足を**もう一度読まない**ことをここで固定する（同じ CSV を 2 回読むと、
# 表示している足と接点を算出した足が別物になり得る）。

def test_接点は供給の戻りがそのままaggへ載る(tmp_path: Path) -> None:
    contacts = [{"time": 2000, "price": 39405.0, "dir": "up"}]
    job_dir = _job_dir(tmp_path)
    out = report_payload_writer.write(
        job_dir, _result(), load_run_inputs=_loader(),
        contacts_supply=lambda bars, backtest: contacts,
    )
    agg = json.loads(out.read_text(encoding="utf-8"))["segments"]["single"]["agg"]
    assert agg["contacts"] == contacts


def test_接点供給が無ければaggにcontactsキーは生えない(tmp_path: Path) -> None:
    """Phase 4 までの payload と byte 等価（載せないものを空配列で捏造しない）。"""
    agg = _payload(tmp_path)["segments"]["single"]["agg"]
    assert "contacts" not in agg


def test_接点供給は読み込み済みの足と仕様を受け取る(tmp_path: Path) -> None:
    seen: "list[tuple]" = []
    loads = []

    def loader(backtest):
        loads.append(backtest)
        return _bars([1000, 2000, 3000, 4000]), _SymbolSpec()

    job_dir = _job_dir(tmp_path)
    report_payload_writer.write(
        job_dir, _result(), load_run_inputs=loader,
        contacts_supply=lambda bars, backtest: seen.append((bars, backtest)) or [],
    )
    assert len(loads) == 1, "足を 2 回読んでいます（表示足と接点の足が別物になり得る）"
    bars, backtest = seen[0]
    assert [b.time for b in bars] == [1000, 2000, 3000, 4000]
    assert backtest["symbol"] == "JP225"


def test_接点供給へ渡る足はint時刻ビューである(tmp_path: Path) -> None:
    """生の足をそのまま渡すと `int(Timestamp)` がナノ秒になり、接点の time が壊れる。"""
    import pandas as pd
    seen: "list" = []
    bars = _bars([pd.Timestamp("2026-04-01 00:00:00", tz="UTC"),
                  pd.Timestamp("2026-04-01 00:05:00", tz="UTC")])
    report_payload_writer.write(
        _job_dir(tmp_path), _result(), load_run_inputs=_loader(bars=bars),
        contacts_supply=lambda b, _spec: seen.append(b) or [],
    )
    assert [b.time for b in seen[0]] == [1775001600, 1775001900]


# --- 8. R-4: 足の供給は Composition Root が束ねる（adapter→main import の解消）----

def test_足の供給は必須引数である() -> None:
    """既定束縛（`simulator.main.build_interactor`）を adapter が持たない＝依存の向きが
    外向きにならない。束ねるのは main 層（run_job.py）である。

    実引数を省いた呼び出しの例外型では判定しない——既定束縛が残っていても
    `build_interactor` の必須引数不足で TypeError になり、同じ結果で通ってしまう
    （通る理由が違う＝弱い assertion）。署名そのものを見る。
    """
    import inspect

    parameter = inspect.signature(report_payload_writer.write).parameters["load_run_inputs"]
    assert parameter.default is inspect.Parameter.empty


def test_writerはmain層をimportしない() -> None:
    """依存方向の機械強制（宣言では守れない）。"""
    source = Path(report_payload_writer.__file__).read_text(encoding="utf-8")
    assert "from simulator.main import" not in source
    assert "import simulator.main" not in source
