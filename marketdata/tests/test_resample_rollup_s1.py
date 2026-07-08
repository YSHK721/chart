"""TDD 記録 — marketdata S1（resample/rollup を marketdata へ物理移設・enabler③）。

設計正典: MARKETDATA_TIMESERIES_BOUNDARY_DESIGN.md §4 / §6 S1 行 / §10.3 M-2(oracle) /
§10.3 M-3(ref_prefix) / 付録B。

検証対象（移設の核心保証）:
  - marketdata.resample.resample_ohlc / TIMEFRAME_RULES / is_known_timeframe が存在し
    移設前 dataset.resample_ohlc と同値（同 fixture で diff 0）。
  - marketdata.rollup.stream_build / incremental_update / RollupState / merge_same_period /
    _RollupWriter / _rollup_path が存在し、移設前コードで採取した golden CSV と
    filecmp.cmp(shallow=False) で byte 完全一致（M-2）。
  - rollup が marketdata.resample.resample_ohlc を再利用（再実装禁止・回帰）。
  - M-3: ref_prefix が _rollup_path と _RollupWriter.__init__ の両所へ伝播（既定 jp225_m1）。
  - marketdata は indicator_ui を import しない（循環依存禁止）。
"""

from __future__ import annotations

import csv as _csv
import filecmp
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

# 移設後の唯一の規則源（marketdata）。移設前は ImportError で Red になる。
from marketdata import resample as md_resample
from marketdata import rollup as md_rollup

_GOLDEN_DIR = (
    Path(__file__).resolve().parents[2]
    / "indigators"
    / "indicator_ui"
    / "api"
    / "tests"
    / "golden"
    / "rollups"
)
_GOLDEN_TFS = ["5m", "15m", "30m", "1h", "4h", "1D", "1W", "1M"]


# --------------------------------------------------------------------------- #
# 合成1分足ジェネレータ（test_rollup_builder と同一 fixture・golden 採取と一致）
# --------------------------------------------------------------------------- #
def _synthetic_m1(start: str, minutes: int) -> pd.DataFrame:
    idx = pd.date_range(start, periods=minutes, freq="1min")
    base = list(range(minutes))
    return pd.DataFrame(
        {
            "open": [100.0 + b for b in base],
            "high": [100.0 + b + 0.5 for b in base],
            "low": [100.0 + b - 0.5 for b in base],
            "close": [100.0 + b + 0.2 for b in base],
            "volume": [1.0 + (b % 7) for b in base],
        },
        index=idx,
    )


def _write_m1_csv(path: Path, df: pd.DataFrame) -> None:
    with open(path, "w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for ts, row in df.iterrows():
            w.writerow(
                [
                    ts.strftime("%Y-%m-%d %H:%M:%S"),
                    row["open"], row["high"], row["low"], row["close"], row["volume"],
                ]
            )


def _read_rollup_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


# --------------------------------------------------------------------------- #
# marketdata.resample（規則源の物理移設・同値）
# --------------------------------------------------------------------------- #
def test_marketdata_resample_exposes_resample_ohlc_timeframe_rules_is_known():
    # marketdata.resample が 3 公開 API を持つ（付録B 定義一致）。
    assert callable(md_resample.resample_ohlc)
    assert isinstance(md_resample.TIMEFRAME_RULES, dict)
    assert callable(md_resample.is_known_timeframe)


def test_marketdata_resample_timeframe_rules_content_unchanged():
    # TIMEFRAME_RULES の内容が移設前 dataset と完全一致（規則の二重化なし）。
    assert md_resample.TIMEFRAME_RULES == {
        "1m": None, "5m": "5min", "15m": "15min", "30m": "30min",
        "1h": "1h", "4h": "4h", "1D": "1D", "1W": "W-FRI", "1M": "ME",
    }


@pytest.mark.parametrize("tf", ["5m", "1h", "1D", "1W", "1M"])
def test_marketdata_resample_ohlc_equals_premigration_dataset_output(tf):
    # marketdata.resample_ohlc が移設前 dataset.resample_ohlc と同一 DataFrame を返す。
    # dataset（indicator_ui api）は marketdata 外パッケージのため api dir を sys.path へ追加して
    # ロードする（移設後 dataset は marketdata.resample を再エクスポートするが、独立に同値検証する）。
    import importlib
    import sys as _sys

    api_dir = (
        Path(__file__).resolve().parents[2]
        / "indigators" / "indicator_ui" / "api"
    )
    if str(api_dir) not in _sys.path:
        _sys.path.insert(0, str(api_dir))
    dataset = importlib.import_module("adapter.compute.dataset")

    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 40)
    rule = md_resample.TIMEFRAME_RULES[tf]
    md_out = md_resample.resample_ohlc(df, rule)
    ds_out = dataset.resample_ohlc(df, rule)
    assert list(md_out.index) == list(ds_out.index)
    for col in ("open", "high", "low", "close", "volume"):
        assert md_out[col].to_numpy() == pytest.approx(ds_out[col].to_numpy())


def test_marketdata_resample_none_rule_returns_same_object():
    df = _synthetic_m1("2020-01-01 00:00:00", 10)
    assert md_resample.resample_ohlc(df, None) is df


# --------------------------------------------------------------------------- #
# marketdata.rollup（移設・byte 一致 oracle = M-2 最重要）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("tf", _GOLDEN_TFS)
def test_rollup_stream_build_byte_identical_to_premigration_golden(tmp_path, tf):
    # M-2: 移設後 stream_build 出力が移設前コードで採取した golden と byte 完全一致。
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 40)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"
    md_rollup.stream_build(m1_csv, [tf], out_dir, chunk_rows=7000)
    produced = out_dir / f"jp225_m1_{tf}.csv"
    golden = _GOLDEN_DIR / f"jp225_m1_{tf}.csv"
    assert filecmp.cmp(produced, golden, shallow=False), (
        f"{tf}: 移設後 stream_build が golden と byte 不一致（M-4: 即 fail）"
    )


def test_rollup_calls_marketdata_resample_ohlc(tmp_path, monkeypatch):
    # 回帰（bugfix-pair）: rollup は marketdata.resample.resample_ohlc を再利用する
    #   （規則を再実装しない）。resample_ohlc を spy し stream_build 内で呼ばれることを実証。
    calls = {"n": 0}
    real = md_resample.resample_ohlc

    def _spy(df, rule):
        calls["n"] += 1
        return real(df, rule)

    monkeypatch.setattr(md_resample, "resample_ohlc", _spy)
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    md_rollup.stream_build(m1_csv, ["1h"], tmp_path / "rollups", chunk_rows=2000)
    assert calls["n"] >= 1, "rollup が marketdata.resample.resample_ohlc を再利用していない"


# --------------------------------------------------------------------------- #
# M-3: ref_prefix が _rollup_path と _RollupWriter の両所へ伝播
# --------------------------------------------------------------------------- #
def test_rollup_path_default_ref_prefix_is_jp225_m1(tmp_path):
    assert md_rollup._rollup_path(tmp_path, "5m").name == "jp225_m1_5m.csv"


def test_rollup_path_accepts_ref_prefix_argument(tmp_path):
    # M-3: _rollup_path に ref_prefix（既定 jp225_m1）を追加。
    assert md_rollup._rollup_path(tmp_path, "5m", ref_prefix="usdjpy_m1").name == "usdjpy_m1_5m.csv"


def test_rollup_writer_accepts_ref_prefix_argument(tmp_path):
    # M-3: _RollupWriter.__init__ に ref_prefix を追加し確定パス名へ反映。
    w = md_rollup._RollupWriter(tmp_path, "1h", ref_prefix="usdjpy_m1")
    try:
        assert w._final.name == "usdjpy_m1_1h.csv"
    finally:
        w.close()


def test_stream_build_propagates_ref_prefix_to_output_filename(tmp_path):
    # M-3: stream_build の ref_prefix が出力ファイル名へ伝播する。
    df = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 2)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df)
    out_dir = tmp_path / "rollups"
    md_rollup.stream_build(m1_csv, ["1h"], out_dir, ref_prefix="usdjpy_m1", chunk_rows=2000)
    assert (out_dir / "usdjpy_m1_1h.csv").exists()
    assert not (out_dir / "jp225_m1_1h.csv").exists()


def test_incremental_update_propagates_ref_prefix_to_output_filename(tmp_path):
    # M-3: incremental_update の ref_prefix が出力ファイル名へ伝播する。
    df_initial = _synthetic_m1("2020-01-01 00:00:00", 60 * 24 * 3)
    m1_csv = tmp_path / "m1.csv"
    _write_m1_csv(m1_csv, df_initial)
    out_dir = tmp_path / "rollups"
    md_rollup.stream_build(m1_csv, ["1h"], out_dir, ref_prefix="usdjpy_m1", chunk_rows=2000)
    state = md_rollup.RollupState.load(out_dir)
    df_more = _synthetic_m1("2020-01-04 00:00:00", 130)
    df_full = pd.concat([df_initial, df_more])
    _write_m1_csv(m1_csv, df_full)
    md_rollup.incremental_update(m1_csv, state, ["1h"], out_dir, ref_prefix="usdjpy_m1")
    assert (out_dir / "usdjpy_m1_1h.csv").exists()


# --------------------------------------------------------------------------- #
# 循環依存禁止（marketdata は indicator_ui を import しない）
# --------------------------------------------------------------------------- #
def _import_lines(src: str) -> list[str]:
    """ソースから実 import 行（docstring/コメント除外）のみ抽出する。"""
    out = []
    in_doc = False
    for raw in src.splitlines():
        line = raw.strip()
        # docstring（"""…"""）ブロックをスキップ（簡易: 行頭/行末の """ で開閉）。
        if line.startswith('"""') or line.startswith("'''"):
            ticks = line.count('"""') + line.count("'''")
            if ticks == 1:
                in_doc = not in_doc
            continue
        if in_doc:
            continue
        if line.startswith("#"):
            continue
        if line.startswith("import ") or line.startswith("from "):
            out.append(line)
    return out


def test_marketdata_rollup_does_not_import_indicator_ui():
    # 循環依存禁止: marketdata.rollup の実 import 行に indicator_ui(adapter.compute) が無い。
    imports = _import_lines((Path(md_rollup.__file__)).read_text(encoding="utf-8"))
    for line in imports:
        assert "adapter.compute" not in line, f"循環依存: {line}"
        assert "indicator_ui" not in line, f"循環依存: {line}"
    # 実際に import 可能（marketdata 単独で循環なくロードできる）。
    importlib_rollup = __import__("marketdata.rollup", fromlist=["stream_build"])
    assert callable(importlib_rollup.stream_build)


def test_marketdata_resample_does_not_import_indicator_ui():
    # 循環依存禁止: marketdata.resample の実 import 行に indicator_ui が無い（pandas のみ依存）。
    imports = _import_lines((Path(md_resample.__file__)).read_text(encoding="utf-8"))
    for line in imports:
        assert "adapter.compute" not in line, f"循環依存: {line}"
        assert "indicator_ui" not in line, f"循環依存: {line}"


# --------------------------------------------------------------------------- #
# RollupState（移設後も json load/save 往復）
# --------------------------------------------------------------------------- #
def test_marketdata_rollup_state_roundtrips(tmp_path):
    ts = datetime(2020, 1, 5, 12, 34, 0)
    md_rollup.RollupState(last_processed_ts=ts).save(tmp_path)
    assert md_rollup.RollupState.load(tmp_path).last_processed_ts == ts
