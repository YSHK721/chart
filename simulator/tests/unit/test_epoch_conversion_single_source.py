"""epoch 換算規則（datetime64 → epoch 秒）の写しを機械的に禁じるゲート（ISSUE-410）。

何が問題か（レビュー実測 2026-08-18）:
    「timestamp → epoch 秒」の規則が本番コードに複製されると、規則を 1 箇所直した日に
    残りが腐る（ISSUE-406 は ns 前提の写しが ms 列に対し 10^6 倍ずれた実害）。
    単一ソース宣言だけでは新規の写しを検出できないため、構文木で走査して落とす
    （AST ゲートの先例: test_layer_dependency_direction.py・ISSUE-405）。

本モジュールが固定する契約:
  1. **ns 前提式の不在**: `simulator/` 本番コードに「epoch 値を 10^9 で割って秒にする」
     式が 1 つも無い（ISSUE-406 の原型。解像度前提を持ち込む形はすべて禁止）。
  2. **datetime64[s] cast の所在**: 秒への cast（解像度非依存の正しい規則）も、書ける場所は
     単一ソース 2 モジュールだけ——Series 版 `simulator/adapter/repository/_tick_frame.py`・
     スカラ版 `simulator/domain/bar_time.py`（domain は pandas を import できないため
     Series 版へ畳めない。2 実体は表現の違いであって規則の複製ではなく、契約 4 が
     同値性を実測で固定する）。第 2 の写しが増えたらここが落ちる。
  3. **ゲート自身の検出力**: 違反 2 形態（ns 前提式・cast の写し）を注入したソースを
     ゲートが実際に検出する（検出できない検査は宣言と同じ）。
  4. **スカラ / Series の同値性**: `epoch_seconds_of_datetime`（datawindow）と
     `timestamp_epoch_seconds`（tick 列）が同じ瞬間へ同じ epoch 秒を返す。
     1970 年より前の秒未満値（丸め方向が割れていた領域・ISSUE-408）を含めて固定する。
  5. **計算量（発行 − 使用 = 0）**: 是正で共有実体へ寄せた `_df_to_bars` が、変換を
     出力に使う分しか発行しないこと。行数を増やしても変換の発行回数が増えないこと
     （オーダーの表明。回数そのものは期待値に焼き込まない）。

構造: Arrange-Act-Assert。
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

#: リポジトリ内の `simulator` パッケージ本体。
_SIMULATOR_DIR = Path(__file__).resolve().parents[2]

#: datetime64[s] cast を書いてよい単一ソース（`_SIMULATOR_DIR` からの相対 posix パス）。
_ALLOWED_CAST_MODULES = frozenset(
    {
        "adapter/repository/_tick_frame.py",  # Series 版の唯一実体（ISSUE-406）
        "domain/bar_time.py",  # スカラ版の唯一実体（domain は pandas 不可）
    }
)

_NS_PER_SECOND = 1_000_000_000


def _production_files() -> "list[Path]":
    """走査対象＝ `simulator/` 配下の本番 .py 全部（tests / __pycache__ を除く）。"""
    return sorted(
        p
        for p in _SIMULATOR_DIR.rglob("*.py")
        if "tests" not in p.parts and "__pycache__" not in p.parts
    )


def _ns_division_lines(source: str) -> "list[int]":
    """「10^9 で割って秒にする」式（ns 前提）の行番号を列挙する。

    除算（`/` と `//` の双方）の右辺が定数 10^9 である箇所をすべて数える。
    表記ゆれ（`1_000_000_000` / `1000000000`）は定数畳み込み後の値で同一視する。
    """
    lines: "list[int]" = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, (ast.FloorDiv, ast.Div))
            and isinstance(node.right, ast.Constant)
            and node.right.value == _NS_PER_SECOND
        ):
            lines.append(node.lineno)
    return lines


def _datetime64s_cast_lines(source: str) -> "list[int]":
    """`astype` に文字列 datetime64[s] を渡す呼出（秒 cast 規則）の行番号を列挙する。"""
    lines: "list[int]" = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "astype"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "datetime64[s]"
        ):
            lines.append(node.lineno)
    return lines


class TestTheGateOverProductionCode:
    """契約 1・2: 本番コード全体の走査。"""

    def test_no_nanosecond_presuming_division_exists(self):
        violations = [
            f"{path.relative_to(_SIMULATOR_DIR)}:{line}"
            for path in _production_files()
            for line in _ns_division_lines(path.read_text(encoding="utf-8"))
        ]
        assert violations == [], (
            "epoch 値を 10^9 で割る ns 前提式が本番コードに現れました。"
            "解像度非依存の単一ソース（timestamp_epoch_seconds / bar_time.epoch_seconds）"
            f"へ委譲してください: {violations}"
        )

    def test_datetime64s_casts_exist_only_in_the_single_source_modules(self):
        violations = [
            f"{rel}:{line}"
            for path in _production_files()
            for rel in [path.relative_to(_SIMULATOR_DIR).as_posix()]
            if rel not in _ALLOWED_CAST_MODULES
            for line in _datetime64s_cast_lines(path.read_text(encoding="utf-8"))
        ]
        assert violations == [], (
            "datetime64[s] cast（epoch 秒化の規則）の写しが単一ソースの外に現れました。"
            "timestamp_epoch_seconds（Series）/ bar_time.epoch_seconds（スカラ）へ"
            f"委譲してください: {violations}"
        )

    def test_the_allowed_modules_still_own_the_rule(self):
        # 許可リストが空振り（改名・移動で誰も規則を持たない）になっていないこと。
        for rel in sorted(_ALLOWED_CAST_MODULES):
            path = _SIMULATOR_DIR / rel
            assert _datetime64s_cast_lines(path.read_text(encoding="utf-8")), (
                f"単一ソース {rel} が datetime64[s] cast を持っていません。"
                "許可リストの追随漏れ（移動・改名）を疑ってください。"
            )


class TestTheGateHasDetectionPower:
    """契約 3: 注入した違反をゲートが実際に検出する。"""

    @pytest.mark.parametrize(
        "snippet",
        [
            'secs = df["t"].astype("int64") // 1_000_000_000\n',
            'secs = df["t"].astype("int64") // 1000000000\n',
            'secs = values.view("int64") / 1_000_000_000\n',
        ],
    )
    def test_ns_divisions_are_detected(self, snippet):
        assert _ns_division_lines(snippet) == [1]

    def test_a_datetime64s_cast_copy_is_detected(self):
        snippet = 'secs = idx.values.astype("datetime64[s]").astype("int64")\n'
        assert _datetime64s_cast_lines(snippet) == [1]

    def test_unrelated_divisions_are_not_flagged(self):
        assert _ns_division_lines("ratio = total // 2\nhalf = n / 2\n") == []


class TestScalarAndSeriesConvertersAgree:
    """契約 4: スカラ版と Series 版は同じ瞬間へ同じ epoch 秒を返す（ISSUE-408 を含む）。"""

    _INSTANTS = [
        "1969-12-31T23:59:59.500",  # 1970 年より前の秒未満（丸めが割れていた領域）
        "1969-12-31T23:59:59.999999",
        "1970-01-01T00:00:00.000",
        "2024-03-01T12:34:56.999999",
    ]

    @pytest.mark.parametrize("unit", ["ms", "us", "ns"])
    def test_series_converter_matches_the_scalar_rule_for_every_resolution(self, unit):
        from datawindow.half_open import epoch_seconds_of_datetime
        from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds

        series = pd.Series(pd.to_datetime(self._INSTANTS)).astype(f"datetime64[{unit}]")
        expected = [
            epoch_seconds_of_datetime(
                datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
            )
            for text in self._INSTANTS
        ]
        assert timestamp_epoch_seconds(series).tolist() == expected

    def test_the_pre_epoch_subsecond_floors_in_both_implementations(self):
        from datawindow.half_open import epoch_seconds_of_datetime
        from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds

        scalar = epoch_seconds_of_datetime(
            datetime(1969, 12, 31, 23, 59, 59, 500_000, tzinfo=timezone.utc)
        )
        series = timestamp_epoch_seconds(
            pd.Series(pd.to_datetime(["1969-12-31T23:59:59.5"]))
        ).tolist()
        assert (scalar, series) == (-1, [-1])


def _ohlc_frame(rows: int) -> pd.DataFrame:
    index = pd.to_datetime([1_700_000_000 + 60 * i for i in range(rows)], unit="s")
    return pd.DataFrame({"open": [1.0] * rows, "close": [2.0] * rows}, index=index)


class _ConverterSpy:
    """共有変換器の発行を数える Test Spy（発行した変換 − 出力に使った変換 = 0 の表明）。"""

    def __init__(self, real):
        self._real = real
        self.calls = 0
        self.elements_issued = 0

    def __call__(self, series):
        self.calls += 1
        self.elements_issued += len(series)
        return self._real(series)


class TestNoWastedConversions:
    """契約 5: 変換は出力に使う分しか発行されない（状態検証では落ちない浪費の遮断）。"""

    def _spy_on_df_to_bars(self, monkeypatch, rows: int) -> "tuple[_ConverterSpy, int]":
        from simulator.adapter.repository.tick_parquet import timestamp_epoch_seconds
        from simulator.replay_ui.adapter import causal_compute_gateway as module

        # 素の共有実体を包む（同一テスト内で 2 回 patch しても spy が入れ子にならない）。
        spy = _ConverterSpy(timestamp_epoch_seconds)
        monkeypatch.setattr(module, "timestamp_epoch_seconds", spy)
        bars = module.CausalComputeGateway._df_to_bars(_ohlc_frame(rows))
        return spy, len(bars)

    def test_issued_conversions_equal_used_conversions(self, monkeypatch):
        spy, used = self._spy_on_df_to_bars(monkeypatch, rows=16)
        assert spy.elements_issued - used == 0

    def test_more_rows_do_not_issue_more_converter_calls(self, monkeypatch):
        # オーダーの表明: 発行回数は行数に依存しない（入力 2 点で固定・値は焼き込まない）。
        spy_small, _ = self._spy_on_df_to_bars(monkeypatch, rows=8)
        spy_large, _ = self._spy_on_df_to_bars(monkeypatch, rows=800)
        assert spy_small.calls == spy_large.calls

    def test_index_secs_issues_exactly_what_it_returns(self, monkeypatch):
        from simulator.replay_ui.adapter import causal_candle_repository as module

        spy = _ConverterSpy(module.timestamp_epoch_seconds)
        monkeypatch.setattr(module, "timestamp_epoch_seconds", spy)
        secs = module.CausalCandleRepository._index_secs(_ohlc_frame(12))
        assert spy.elements_issued - len(secs) == 0
