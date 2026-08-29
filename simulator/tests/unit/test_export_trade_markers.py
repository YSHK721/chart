"""export_trade_markers.py 単体テスト（TDD）。

設計入力: CHART_TRADE_MARKERS_DETAILED_DESIGN.md §2.5（列ブリッジ・run・出力・集合包含検証）、
  §4（集合包含: 全マーカー time ⊆ candles time 集合・包含外件数をログ明示・0 件合格）。
構造: Arrange-Act-Assert（AAA）。既存データ非改変（tmp のみ書く）。

列ブリッジ・集合包含検証は純関数として単体テストし、実 marketdata の run は P6 結合検証で担保する
（本ファイルは I/O を伴わない決定論ユニットに限定）。
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.tools import export_trade_markers as ext

# 実 marketdata（読み取り専用）。Fix v2 受入 3（既定実行が MarginCallError を出さず完走）を
# 実機相当で検証する際に直近 tail を参照する（§8.3「実 marketdata 直近で run」許容）。
# パスは marketdata.paths.DATA_DIR（単一基点・Sd §10.1 C-1）配下＝被テスト側 _DEFAULT_CSV と同源。
_REAL_CSV = ext._DEFAULT_CSV


def test_bridge_renames_date_to_time_and_adds_zero_spread():
    # Arrange: marketdata 形式（date,open,high,low,close,volume）
    src = pd.DataFrame(
        {
            "date": ["2025-01-02 09:00:00", "2025-01-02 09:01:00"],
            "open": [8568.9, 8569.0],
            "high": [8570.0, 8571.0],
            "low": [8567.0, 8568.0],
            "close": [8569.0, 8570.0],
            "volume": [0.0, 0.0],
        }
    )
    # Act
    bridged = ext.bridge_marketdata_df(src)
    # Assert: engine 形式（time/open/high/low/close/volume/spread）に変換され spread=0
    assert list(bridged.columns) == ["time", "open", "high", "low", "close", "volume", "spread"]
    # ISSUE-411: engine の `Bar.time` 契約は epoch int / numpy.datetime64 であり文字列ではない。
    #   marketdata の `date` は naive 文字列（UTC・ユーザー裁定 2026-08-18）なので epoch 秒へ据える。
    assert list(bridged["time"]) == [1735808400, 1735808460]
    assert list(bridged["spread"]) == [0, 0]


def test_bridge_does_not_mutate_source_dataframe():
    # Arrange
    src = pd.DataFrame(
        {
            "date": ["2025-01-02 09:00:00"],
            "open": [8568.9], "high": [8570.0], "low": [8567.0],
            "close": [8569.0], "volume": [0.0],
        }
    )
    src_before = src.copy(deep=True)
    # Act
    ext.bridge_marketdata_df(src)
    # Assert: 既存データ非改変（C1）— src は変化しない
    pd.testing.assert_frame_equal(src, src_before)


def test_markers_outside_returns_empty_when_all_times_included():
    # Arrange: 全マーカー time が candle time 集合に含まれる
    payload = {"markers": [
        {"lwc": {"time": 100}}, {"lwc": {"time": 200}}, {"lwc": {"time": 200}},
    ]}
    candle_times = {100, 150, 200, 250}
    # Act
    outside = ext.markers_outside_candle_times(payload, candle_times)
    # Assert
    assert outside == []


def test_markers_outside_lists_each_time_not_in_candle_set():
    # Arrange: 300 と 999 は candle 集合外
    payload = {"markers": [
        {"lwc": {"time": 100}}, {"lwc": {"time": 300}}, {"lwc": {"time": 999}},
    ]}
    candle_times = {100, 200}
    # Act
    outside = ext.markers_outside_candle_times(payload, candle_times)
    # Assert: 包含外の time を漏れなく列挙（無音にしない＝§4）
    assert sorted(outside) == [300, 999]


def test_candle_times_reads_the_bridged_epoch_column_without_reconverting():
    # Arrange: ブリッジ後 time 列は既に epoch 秒（int）＝engine の Bar.time 契約
    bridged = pd.DataFrame({"time": [1735808400, 1735808460]})
    # Act
    times = ext.candle_unix_times(bridged)
    # Assert: 第 2 の変換規則を持たず、列の値をそのまま集合にする
    assert times == {1735808400, 1735808460}


def test_bridged_time_column_dtype_is_integer():
    # Arrange: marketdata 形式（date は naive 文字列）
    src = pd.DataFrame(
        {
            "date": ["2025-01-02 09:00:00", "2025-01-02 09:01:00"],
            "open": [8568.9, 8569.0], "high": [8570.0, 8571.0],
            "low": [8567.0, 8568.0], "close": [8569.0, 8570.0], "volume": [0.0, 0.0],
        }
    )
    # Act
    bridged = ext.bridge_marketdata_df(src)
    # Assert: 整数系 dtype（float 化すると to_csv が "1.7358084e+09" 等になり engine が壊れる）
    assert pd.api.types.is_integer_dtype(bridged["time"]), bridged["time"].dtype


def test_candle_times_equal_engine_bar_time_set_for_the_same_bridged_csv(tmp_path):
    # Arrange: ブリッジ結果を engine が読む形（CSV）で書き出し、同じ CSV を engine に読ませる
    from simulator.adapter.repository.ohlc_csv import CsvOHLCRepository

    src = pd.DataFrame(
        {
            "date": [f"2025-01-02 09:{i:02d}:00" for i in range(5)],
            "open": [8000.0 + i for i in range(5)],
            "high": [8001.0 + i for i in range(5)],
            "low": [7999.0 + i for i in range(5)],
            "close": [8000.5 + i for i in range(5)],
            "volume": [0.0 for _ in range(5)],
        }
    )
    bridged = ext.bridge_marketdata_df(src)
    csv = tmp_path / "engine.csv"
    bridged.to_csv(csv, index=False)
    # Act: 包含検証で突き合わせる 2 つの集合をそれぞれ生成する
    candle_times = ext.candle_unix_times(bridged)
    bar_times = {int(b.time) for b in CsvOHLCRepository().load(str(csv))}
    # Assert: 集合として一致する（step 6 の包含検証が意味を持つ前提）
    assert candle_times == bar_times


@pytest.mark.skipif(not _REAL_CSV.exists(), reason="real marketdata not present")
def test_run_and_export_reports_zero_markers_outside_candles_in_its_return_value(tmp_path):
    # Arrange: 実 marketdata 直近 tail（step 6 の包含検証を実経路で観測する）
    out = tmp_path / "trade_markers.json"
    # Act
    summary = ext.run_and_export(
        csv_path=_REAL_CSV, out_path=out, ea_name="TC24051901", rows=2500
    )
    # Assert: 包含外件数が戻り値で検証できる（print のみ＝サイレントにしない）かつ 0 件
    assert "markers_outside_candles" in summary
    assert summary["markers_outside_candles"] == 0


# ---- Fix v2 §8.1 Fix-A: 既定生成窓を「直近（tail）」へ ------------------------


def _write_csv(rows: int) -> Path:
    """date,open,high,low,close,volume の合成 CSV を tmp に書く（読み取り元スタブ）。"""
    df = pd.DataFrame(
        {
            "date": [f"2025-01-02 09:{i:02d}:00" for i in range(rows)],
            "open": [8000.0 + i for i in range(rows)],
            "high": [8001.0 + i for i in range(rows)],
            "low": [7999.0 + i for i in range(rows)],
            "close": [8000.5 + i for i in range(rows)],
            "volume": [0.0 for _ in range(rows)],
        }
    )
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8")
    df.to_csv(tmp.name, index=False)
    tmp.close()
    return Path(tmp.name)


def test_read_recent_returns_last_n_rows_with_header():
    # Arrange: 10 行の marketdata（先頭 9:00 … 末尾 9:09）
    src = _write_csv(10)
    try:
        # Act: 末尾 3 行を要求
        df = ext.read_recent_marketdata(src, 3)
        # Assert: header 付き（6 列）かつ末尾 3 行（9:07/9:08/9:09）が返る（先頭ではない＝tail）
        assert list(df.columns) == ["date", "open", "high", "low", "close", "volume"]
        assert list(df["date"]) == [
            "2025-01-02 09:07:00",
            "2025-01-02 09:08:00",
            "2025-01-02 09:09:00",
        ]
    finally:
        os.unlink(src)


def test_read_recent_returns_all_rows_when_total_le_n():
    # Arrange: 全 5 行に対し n=5（境界 total==n）と n=99（total<n）
    src = _write_csv(5)
    try:
        # Act
        df_eq = ext.read_recent_marketdata(src, 5)
        df_gt = ext.read_recent_marketdata(src, 99)
        # Assert: いずれも全 5 行（先頭〜末尾）が返る
        assert list(df_eq["date"]) == [f"2025-01-02 09:0{i}:00" for i in range(5)]
        assert list(df_gt["date"]) == [f"2025-01-02 09:0{i}:00" for i in range(5)]
    finally:
        os.unlink(src)


def test_read_recent_does_not_mutate_source_file_bytes():
    # Arrange: 読み取り元のバイト列を記録
    src = _write_csv(8)
    before = src.read_bytes()
    try:
        # Act
        ext.read_recent_marketdata(src, 3)
        # Assert: src は読み取り専用（バイト不変＝C1）
        assert src.read_bytes() == before
    finally:
        os.unlink(src)


# ---- Fix v2 §8.2 Fix-B: 既定 run config の堅牢化 ----------------------------


def test_meta_sets_close_and_halt_to_survive_margin_call():
    # Arrange / Act: 既定 run メタを取得
    meta = ext._meta("dummy.csv", "TC24051901")
    # Assert: 証拠金割れでも完走するよう config_overrides に close_and_halt を付与（§8.2）
    assert meta.get("config_overrides", {}).get("stop_out_action") == "close_and_halt"


def test_meta_uses_the_minimum_orderable_lot_size():
    # Arrange: 銘柄仕様の権威（供給元スナップショット）。期待値をここに書かない。
    spec = load_spec_fields(OANDA_JAPAN_MT5_LIVE, ext._SYMBOL)
    # Act
    meta = ext._meta("dummy.csv", "TC24051901")
    # Assert: lot は供給元の最小発注単位（ISSUE-445・2026-08-26 の是正）。
    #   旧 assert は `lot_size == 0.1`（§8.2 の「堅牢サイジング」）だった。0.1 は供給元の
    #   `volume_min=1.0` の下では発注不成立であり（実測: InvalidPriceError）、`contract_size`
    #   の誤り（10.0・真値 1.0）と積の上で相殺していた対の片割れである。
    #   TC24051901 は原典 `.mq5` を持たず lot を素通しするため、Root が発注可能値を供給する。
    assert meta["lot_size"] == spec["volume_min"]


# ---- Fix v2 §8.3 受入 3: 既定実行が MarginCallError を出さず完走 ------------


@pytest.mark.skipif(not _REAL_CSV.exists(), reason="real marketdata not present")
def test_default_run_uses_recent_tail_and_survives_margin_call(tmp_path):
    # Arrange: 実 marketdata 直近 tail（§8.3 許容）。
    #   OLD（先頭読み＋fail_stop）では (a) マーカーが最古=2012 になり UI 直近窓と重ならない、
    #   かつ (b) 実 tail 窓では MarginCallError でクラッシュする。Fix-A/B 双方が無いと不合格。
    out = tmp_path / "trade_markers.json"
    # Act: 既定 run（rows=2500 → Fix-A で直近 tail・Fix-B で堅牢 config）。例外送出なし＝完走。
    payload = ext.run_and_export(
        csv_path=_REAL_CSV, out_path=out, ea_name="TC24051901", rows=2500
    )
    # Assert: 完走し JSON 生成・trades>0（§8.3 受入 3）
    assert out.exists()
    assert payload["count"] > 0
    assert len(payload["markers"]) > 0
    # Assert: マーカー time が直近域（2026 年）に入る（§8.1 受入＝UI 直近窓と重なる前提）。
    #   先頭読み（OLD）では 2012 になり本 assert が失敗する＝Fix-A を駆動する。
    latest = max(m["lwc"]["time"] for m in payload["markers"])
    assert pd.Timestamp(latest, unit="s").year == 2026


# ---- ISSUE-411 🟡-3: 包含外マーカーを終了コードで表明する --------------------
#
# `run_and_export` は step 6（集合包含検証）の結果を summary へ載せるが、`main()` が
# 戻り値を捨てていたため CLI としては依然サイレントだった（包含外があっても exit 0）。


class _StubExport:
    """`run_and_export` の代役（包含外件数だけを制御する）。"""

    def __init__(self, outside: int) -> None:
        self.outside = outside
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return {"count": 2, "markers": [], "markers_outside_candles": self.outside}


def test_main_exits_zero_when_no_marker_is_outside_the_candle_set(monkeypatch, tmp_path):
    # Arrange: 包含外 0 件
    stub = _StubExport(outside=0)
    monkeypatch.setattr(ext, "run_and_export", stub)
    # Act
    code = ext.main(["--out", str(tmp_path / "m.json"), "--rows", "10"])
    # Assert: 従来どおり成功終了
    assert code == 0
    assert stub.calls == 1


def test_main_exits_one_and_reports_to_stderr_when_markers_fall_outside(
    monkeypatch, tmp_path, capsys
):
    # Arrange: 包含外 3 件（設計 §4 は 0 件合格）
    monkeypatch.setattr(ext, "run_and_export", _StubExport(outside=3))
    # Act
    code = ext.main(["--out", str(tmp_path / "m.json"), "--rows", "10"])
    # Assert: 非ゼロ終了かつ stderr へ件数を明示（無音で成功扱いにしない）
    assert code == 1
    err = capsys.readouterr().err
    assert "ERROR" in err
    assert "3" in err
