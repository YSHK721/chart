"""因果性台帳の I/O（FS 実体・adapter）の結合検定。

固定する不変条件（Phase 3 構造設計 §台帳 schema・契約改訂裁定 A/C）:
    1. 置き場所は `<data_root>/indicator_causality.json`。
    2. 記録は **schema / measured_at / conditions / series** の 4 部。1 行 = 1 系列。
       測定条件（ref・timeframe・supply_bars・verify_bars・verify_coverage・timeout・
       supply_budget・limit・tolerance・probe_mode）を必ず伴う。条件の無い一致主張は
       再現できない。
    3. 読めないとき（不在・壊れた JSON・schema 不一致・必須キー欠落）は
       `CausalityLedgerUnavailableError`。空台帳へ倒さない（fail-closed）。
    4. 書いたものが同値で読み戻る（往復不変）。不一致・未検定の系列も理由つきで残る。
    5. **reason は 3 値固定**。範囲外の値が書かれた台帳は読み込みで拒否する
       （誤った台帳を黙って通さない）。
    6. 書き込みは置換（同名一時ファイル → `replace`）。途中で落ちた JSON を読ませない。

方式: 実ファイルシステム（`tmp_path`）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.file_indicator_causality_ledger import (
    FileIndicatorCausalityLedger,
)
from simulator.sim_ui.usecase.indicator_models import (
    REASON_MISMATCH,
    REASON_SUPPLY_COST_EXCEEDED,
    CausalityFinding,
    CausalityLedgerUnavailableError,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
)

_CONDITIONS = LedgerConditions(
    ref="jp225_tick", timeframe="5m", supply_bars=10_000, verify_bars=1_000,
    verify_coverage=1.0, timeout=600.0, supply_budget=1.0, limit=None,
    tolerance=0.0, probe_mode="full",
)


def _snapshot() -> LedgerSnapshot:
    return LedgerSnapshot(
        schema=1,
        measured_at="2026-08-11T12:00:00Z",
        conditions=_CONDITIONS,
        findings=(
            CausalityFinding(
                spec=IndicatorSpec("moving_averages", "default", {"length": 20}),
                series_name="MA", selectable=True, bars_compared=9_992,
                warmup_bars=8, max_abs_diff=0.0, supply_seconds=0.31,
            ),
            CausalityFinding(
                spec=IndicatorSpec("cvfe", "default", {"n_har": 500}),
                series_name="MID", selectable=False, reason=REASON_MISMATCH,
                detail="最初の不一致 time=1755000000 max_abs_diff=1.25",
                bars_compared=10_000, max_abs_diff=1.25,
                first_mismatch_time=1_755_000_000, supply_seconds=0.62,
            ),
            CausalityFinding(
                spec=IndicatorSpec("profit_band", "robust", {}),
                series_name="UPPER", selectable=False,
                reason=REASON_SUPPLY_COST_EXCEEDED,
                detail="供給 73.800 秒 > 予算 1.0 秒（供給窓 10000 本）",
                supply_seconds=73.8,
            ),
        ),
    )


# --- 1. 置き場所（不変条件 1）---------------------------------------------

def test_台帳の置き場所はdata_root直下(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path / "data")
    # Act
    ledger.write(_snapshot())
    # Assert
    assert (tmp_path / "data" / "indicator_causality.json").is_file()


def test_親ディレクトリが無くても書ける(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path / "a" / "b")
    # Act
    ledger.write(_snapshot())
    # Assert
    assert (tmp_path / "a" / "b" / "indicator_causality.json").is_file()


# --- 2. 往復（不変条件 4）-------------------------------------------------

def test_書いたものが同値で読み戻る(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    original = _snapshot()
    # Act
    ledger.write(original)
    restored = ledger.read()
    # Assert
    assert restored == original


def test_不一致系列も理由つきで残る(tmp_path: Path) -> None:
    """無音で消さない（一覧に出せなくなる）。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    restored = ledger.read()
    # Assert
    mismatched = [f for f in restored.findings if f.reason == REASON_MISMATCH]
    assert len(mismatched) == 1
    assert mismatched[0].series_name == "MID"
    assert mismatched[0].first_mismatch_time == 1_755_000_000
    assert mismatched[0].max_abs_diff == 1.25
    assert "1.25" in mismatched[0].detail


def test_供給コスト超過の系列も残る(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    restored = ledger.read()
    # Assert
    costly = [f for f in restored.findings
              if f.reason == REASON_SUPPLY_COST_EXCEEDED]
    assert [f.supply_seconds for f in costly] == [73.8]


def test_供給所要秒が記録される(tmp_path: Path) -> None:
    """通過条件 3（供給窓での供給時間）の証拠。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    restored = ledger.read()
    # Assert
    assert [f.supply_seconds for f in restored.findings] == [0.31, 0.62, 73.8]


# --- 3. 記録の形（不変条件 2）---------------------------------------------

def test_記録は測定条件を伴う(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    raw = json.loads((tmp_path / "indicator_causality.json").read_text(encoding="utf-8"))
    # Assert
    assert raw["schema"] == 1
    assert raw["measured_at"] == "2026-08-11T12:00:00Z"
    assert raw["conditions"] == {
        "ref": "jp225_tick", "timeframe": "5m",
        "supply_bars": 10_000, "verify_bars": 1_000, "verify_coverage": 1.0,
        "timeout": 600.0, "supply_budget": 1.0,
        "limit": None, "tolerance": 0.0, "probe_mode": "full",
    }


def test_記録は系列単位(tmp_path: Path) -> None:
    """裁定 A: 選択可否の単位は系列（戦略は系列名で参照する）。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    raw = json.loads((tmp_path / "indicator_causality.json").read_text(encoding="utf-8"))
    # Assert
    assert [(i["indicator"], i["series"]) for i in raw["series"]] == [
        ("moving_averages", "MA"), ("cvfe", "MID"), ("profit_band", "UPPER")
    ]
    assert raw["series"][0]["measured"]["bars_compared"] == 9_992
    assert raw["series"][1]["reason"] == "mismatch"


def test_warmup本数が記録される(tmp_path: Path) -> None:
    """案 ii だけが値を持った先頭区間の本数（案 i では出せない値の範囲）を残す。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    restored = ledger.read()
    raw = json.loads((tmp_path / "indicator_causality.json").read_text(encoding="utf-8"))
    # Assert
    assert restored.findings[0].warmup_bars == 8
    assert raw["series"][0]["measured"]["warmup_bars"] == 8


# --- 4. fail-closed（不変条件 3・5）---------------------------------------

def test_台帳が無ければ明示エラー(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


def test_壊れたJSONは明示エラー(tmp_path: Path) -> None:
    # Arrange
    (tmp_path / "indicator_causality.json").write_text("{not json", encoding="utf-8")
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


@pytest.mark.parametrize("payload", [
    {"schema": 2, "measured_at": "x", "conditions": {}, "series": []},
    {"measured_at": "x", "conditions": {}, "series": []},
    {"schema": 1, "measured_at": "x", "series": []},
    {"schema": 1, "measured_at": "x", "conditions": {}},
    {"schema": 1, "measured_at": "x", "conditions": {}, "series": {}},
    {"schema": 1, "measured_at": "x", "conditions": {}, "series": []},
    [],
])
def test_schemaが合わなければ明示エラー(tmp_path: Path, payload) -> None:
    """未知の版・必須キー欠落・型違いを「空台帳」に倒さない。"""
    # Arrange
    (tmp_path / "indicator_causality.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


#: 解釈可能な conditions（series 要素の異常だけを検定対象にするための土台）。
#:   conditions を空にすると `conditions["ref"]` の KeyError で**先に**弾かれ、
#:   series 要素の検査へ到達しない（＝欠陥を突かないテストになる）。
_PARSEABLE_CONDITIONS = {
    "ref": "jp225_tick", "timeframe": "5m",
    "supply_bars": 10_000, "verify_bars": 1_000,
}


@pytest.mark.parametrize("series", [
    ["oops"],                       # 要素が str
    [None],                         # 要素が None
    [123],                          # 要素が int
    [{"indicator": "i", "variant": "v", "series": "s", "selectable": True,
      "measured": "oops"}],         # measured が str
])
def test_series要素の型が不正なら明示エラー(tmp_path: Path, series) -> None:
    """不変条件 3。台帳が壊れていても **503 に翻訳できる例外** で止める。

    未翻訳の例外（AttributeError 等）が漏れると `/indicators` は文書化された 503 では
    なく応答なし（接続断）になり、利用者には「サーバが落ちた」としか見えない。
    """
    # Arrange
    (tmp_path / "indicator_causality.json").write_text(
        json.dumps({
            "schema": 1, "measured_at": "x",
            "conditions": _PARSEABLE_CONDITIONS, "series": series,
        }),
        encoding="utf-8",
    )
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


def test_reasonが3値の外なら読み込みを拒否する(tmp_path: Path) -> None:
    """不変条件 5。自由文の理由が混ざると機械判定（絞り込み）が壊れる。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    path = tmp_path / "indicator_causality.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["series"][1]["reason"] = "なんとなく不一致"
    path.write_text(json.dumps(payload), encoding="utf-8")
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


def test_選択可能なのに理由があれば読み込みを拒否する(tmp_path: Path) -> None:
    """矛盾した台帳（使えるのに理由つき）を黙って通さない。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    path = tmp_path / "indicator_causality.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["series"][0]["reason"] = "mismatch"
    path.write_text(json.dumps(payload), encoding="utf-8")
    # Act / Assert
    with pytest.raises(CausalityLedgerUnavailableError):
        ledger.read()


def test_系列0件の台帳は読める(tmp_path: Path) -> None:
    """境界値: 記録 0 件。読めてはいるので例外ではない。"""
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(LedgerSnapshot(
        schema=1, measured_at="2026-08-11T00:00:00Z",
        conditions=_CONDITIONS, findings=(),
    ))
    # Act
    restored = ledger.read()
    # Assert
    assert restored.findings == ()


# --- 5. 置換書き込み（不変条件 6）-----------------------------------------

def test_上書きしても一時ファイルを残さない(tmp_path: Path) -> None:
    # Arrange
    ledger = FileIndicatorCausalityLedger(data_root=tmp_path)
    ledger.write(_snapshot())
    # Act
    ledger.write(_snapshot())
    # Assert
    assert [p.name for p in tmp_path.iterdir()] == ["indicator_causality.json"]
