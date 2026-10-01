"""run_job が売買履歴チャートへ描く成果物を書く（2026-09-26 依頼者指示）。

`run_job.main` を spec.json 経由で実際に回し、job-dir の成果物を独立計算と照合する:
    「`trade_markers.json`」: 全トレードの建て・決済のマーク（件数＝トレード数×2・時間足 1m）。
    「`chart_bars.parquet`」＋「`chart_bars.json`」（ISSUE-552/554 段階 2-1）:
        足     … run が読んだ CSV の足。ジョブの成果物のここ 1 か所にだけ在る（report.json は足を持たない）。
        指標   … EA が宣言した系列（CalcProbe は SMA）。値は終値の単純平均を独立に計算して照合。
        口座   … 足ごと。損益（確定の累計・含み）・DD（有効証拠金の最高値からの下落）を
                 独立に計算して照合。保有中は有効証拠金が残高と異なる足が在ること。
    実行トレースを有効にした run でも同じ成果物が出る（観測口の合成）。

計算量（絶対命令 2026-08-28・ISSUE-554）:
    足ごとの値を持つ成果物は足の成果物（chart_bars.parquet）だけである。取引数が同じで足の本数だけが
    違う 2 つの run を回し、ジョブが書いたファイルごとに大きさの増分を比べる（時間は測らない）。
    足ごとの列を文字で書くと 1 本あたり 2 byte 以上（値と区切り）増えるので、増分が足の増分より
    小さければ足ごとの値を持たない。ファイル名も個数も検定に書き写さない（実ジョブの 1 分足の全履歴では
    誰も読まない足ごとの JSON が 276MB・書出し 6.5 秒になっていた）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from simulator.adapter.trace import parquet_trace_store
from simulator.sim_ui.adapter import chart_overlay_writer
from simulator.sim_ui.main import run_job

_EPOCH = 1_704_067_200
_DEPOSIT = 100_000.0
_PERIOD = 3
#: 始値が SMA を上下に跨ぐ並び（買い・売りの途転が複数回起きる）。
_OPENS = [100, 102, 104, 103, 99, 97, 98, 101, 105, 104, 100, 96, 95, 99, 103, 106, 102, 98]


def _closes(opens: "list[float] | None" = None) -> "list[float]":
    return [o + (0.5 if i % 2 else -0.5) for i, o in enumerate(_OPENS if opens is None else opens)]


def _write_csv(path: Path, opens: "list[float] | None" = None) -> Path:
    opens = _OPENS if opens is None else opens
    rows = []
    for i, (o, c) in enumerate(zip(opens, _closes(opens))):
        rows.append(
            {
                "time": _EPOCH + 60 * i, "open": float(o), "high": max(o, c) + 1.0,
                "low": min(o, c) - 1.0, "close": c, "volume": 100, "spread": 3,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _run(tmp: Path, csv: Path, *, trace=None) -> Path:
    job_dir = tmp / "0123456789abcdef0123456789abcdef"
    job_dir.mkdir(parents=True)
    backtest = {
        "ea_name": "CalcProbe_EA", "symbol": "JP225", "period": "M1", "data_path": str(csv),
        "initial_deposit": _DEPOSIT, "contract_size": 10.0, "volume_min": 0.1,
        "volume_max": 100.0, "volume_step": 0.1, "stops_level": 0, "digits": 1,
        "point_size": 0.1, "leverage": 100.0, "ma_period": _PERIOD, "ma_method": "sma",
        "lot_size": 1.0, "stop_loss_points": 0, "take_profit_points": 0,
    }
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": backtest, "sizing": None, "strategy": None,
                    "settings": None, "trace": trace}),
        encoding="utf-8",
    )
    assert run_job.main(["--job-dir", str(job_dir)]) == 0
    return job_dir


def _load(job_dir: Path, name: str) -> dict:
    return json.loads((job_dir / name).read_text(encoding="utf-8"))


def _sma(values: "list[float]", period: int) -> "list[float | None]":
    return [
        None if i + 1 < period else sum(values[i + 1 - period : i + 1]) / period
        for i in range(len(values))
    ]


@pytest.fixture
def job_dir(tmp_path: Path) -> Path:
    return _run(tmp_path, _write_csv(tmp_path / "d.csv"))


def test_markers_cover_every_trade_on_the_run_timeframe(job_dir: Path) -> None:
    markers = _load(job_dir, "trade_markers.json")
    trades = _load(job_dir, "report.json")["segments"]["single"]["trades"]
    assert trades, "トレードが無い（検定が空虚）"
    assert markers["timeframe"] == "1m"
    assert markers["count"] == 2 * len(trades)
    assert len(markers["pairs"]) == len(trades)


def _chart_bars(job_dir: Path) -> "tuple[dict, dict[str, list]]":
    declared = _load(job_dir, chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME)
    columns = parquet_trace_store.read_columns(
        job_dir / chart_overlay_writer.CHART_BARS_FILENAME, columns=declared["columns"],
        time_column=chart_overlay_writer.INDEX_COLUMN,
    )
    return declared, columns


def _values(column: list) -> list:
    """parquet は値なしを NaN で持つ（配信の出口が null にする）。値なしを None に揃える。"""
    return [None if isinstance(v, float) and math.isnan(v) else v for v in column]


def test_account_rows_are_per_bar_and_the_derived_series_match(job_dir: Path) -> None:
    _declared, columns = _chart_bars(job_dir)
    account = {name: _values(columns[name]) for name in chart_overlay_writer.ACCOUNT_COLUMNS}
    balance, equity = account["balance"], account["equity"]
    assert len(balance) == len(_OPENS)
    # 損益: 確定の累計＝残高−初期資金 / 含み＝有効証拠金−残高。
    assert account["realized_pnl"] == pytest.approx([b - _DEPOSIT for b in balance])
    assert account["floating_pnl"] == pytest.approx([e - b for b, e in zip(balance, equity)])
    # 保有中は有効証拠金が残高と異なる（決済時だけでなく足ごとに動いている）。
    assert any(abs(f) > 1e-9 for f in account["floating_pnl"])
    # DD: 有効証拠金の最高値からの下落。
    peak, expected = -math.inf, []
    for e in equity:
        peak = max(peak, e)
        expected.append(peak - e)
    assert account["drawdown"] == pytest.approx(expected)
    # 証拠金維持率: 保有の無い足は値なし、保有中は 有効証拠金÷必要証拠金×100。
    rows = list(zip(account["margin"], account["margin_level"], equity))
    flat = [level for margin, level, _ in rows if margin == 0]
    held = [(margin, level, e) for margin, level, e in rows if margin != 0]
    assert flat and held, "保有の有無の両方を通らない並び（検定が空虚）"
    assert flat == [None] * len(flat)
    assert [level for _, level, _ in held] == pytest.approx([e / m * 100.0 for m, _, e in held])


def test_the_same_outputs_are_written_with_the_run_trace_enabled(tmp_path: Path) -> None:
    csv = _write_csv(tmp_path / "d.csv")
    job_dir = _run(tmp_path, csv, trace={"enabled": True, "start": None, "end": None})
    declared, columns = _chart_bars(job_dir)
    assert declared["rows"] == len(columns["balance"]) == len(_OPENS)
    assert (job_dir / "trade_markers.json").exists()


def test_the_bars_live_only_in_the_bars_artefact(job_dir: Path) -> None:
    """足はジョブの成果物の 1 か所（chart_bars.parquet）だけ。report.json には無い。"""
    declared, columns = _chart_bars(job_dir)
    segment = _load(job_dir, "report.json")["segments"]["single"]
    # 足は run が読んだ CSV そのもの。
    assert columns["time"] == [_EPOCH + 60 * i for i in range(len(_OPENS))]
    assert columns["open"] == [float(o) for o in _OPENS]
    assert columns["close"] == _closes()
    assert declared["rows"] == len(_OPENS)
    # 他の成果物は足を持たない（本数だけを名乗る）。
    assert segment["bars"] == []
    assert segment["meta"]["bars"] == len(_OPENS)


def test_the_declared_trigger_indicator_is_the_independent_sma(job_dir: Path) -> None:
    declared, columns = _chart_bars(job_dir)
    (indicator,) = declared["indicators"]
    assert (indicator["series"], indicator["placement"]) == ("sma", "price")
    assert _values(columns[indicator["column"]]) == pytest.approx(_sma(_closes(), _PERIOD))


def _sizes_of_a_job(tmp: Path, tail: int) -> "tuple[dict[str, int], int, int]":
    """途転の並びの後ろへ、始値が SMA を跨がない足を ``tail`` 本足した run の (ファイルごとの大きさ, 足, 取引)。"""
    opens = [*_OPENS, *(200.0 + i for i in range(tail))]
    job_dir = _run(tmp / str(tail), _write_csv(tmp / f"{tail}.csv", opens))
    trades = _load(job_dir, "report.json")["segments"]["single"]["trades"]
    sizes = {path.name: path.stat().st_size for path in job_dir.iterdir()}
    return sizes, len(opens), len(trades)


def test_only_the_bars_artefact_grows_with_the_bars(tmp_path: Path) -> None:
    # Arrange / Act: 取引数が同じで、足の本数だけが違う 2 つの run。
    small, small_bars, small_trades = _sizes_of_a_job(tmp_path, 200)
    large, large_bars, large_trades = _sizes_of_a_job(tmp_path, 2000)
    added = large_bars - small_bars

    # Assert: 前提（足だけが増えた・書いたファイルの顔ぶれは足の本数で変わらない）。
    assert small_trades == large_trades > 0
    assert set(small) == set(large)
    # 足の本数で大きくなった成果物 − 足の成果物 = 0。
    per_bar = {name for name in small if large[name] - small[name] >= added}
    assert chart_overlay_writer.CHART_BARS_FILENAME in per_bar, "足の成果物が足の本数で増えていない（検定が空虚）"
    assert per_bar - {chart_overlay_writer.CHART_BARS_FILENAME} == set(), {
        name: (small[name], large[name]) for name in per_bar
    }


def test_the_balance_curve_times_are_bar_times(job_dir: Path) -> None:
    """取引終了時の残高の時刻は足の時刻に含まれる（画面は足の範囲へ前方補完する）。"""
    _declared, columns = _chart_bars(job_dir)
    curve = _load(job_dir, "report.json")["segments"]["single"]["agg"]["balance_curve"]
    assert curve, "残高の列が空（検定が空虚）"
    assert {point["time"] for point in curve} <= set(columns["time"])


def test_a_csv_outside_the_ledger_has_no_dataset_ref(job_dir: Path) -> None:
    """台帳外の CSV は系列名を推測しない（チャート側は描かずに理由を出す）。"""
    assert _load(job_dir, chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME)["dataset_ref"] is None


def test_a_ledger_path_resolves_to_exactly_its_ref() -> None:
    from marketdata.dataset_registry import whitelist

    ref, path = next(iter(whitelist().items()))
    assert run_job._dataset_ref_of(path) == ref


def test_the_same_number_names_the_same_trade_in_the_report_and_the_markers(job_dir: Path) -> None:
    """ISSUE-539: 取引明細（report.json の `id`）と売買マーク（`pairs[].id`）が同じ番号で同じ取引を指す。

    期待値は番号の数え方を書き写さず、2 つの成果物の突き合わせだけで決める。
    """
    report = json.loads((job_dir / "report.json").read_text(encoding="utf-8"))
    markers = json.loads((job_dir / "trade_markers.json").read_text(encoding="utf-8"))
    segment = next(iter(report["segments"].values()))
    by_id = {t["id"]: t for t in segment["trades"]}

    assert len(markers["pairs"]) == len(by_id) > 1
    for pair in markers["pairs"]:
        trade = by_id[pair["id"]]
        assert (trade["entry_time"], trade["exit_time"]) == (pair["entry"]["time"], pair["exit"]["time"])
        assert (trade["entry_price"], trade["exit_price"]) == (pair["entry"]["price"], pair["exit"]["price"])
        assert trade["side"] == pair["side"]
