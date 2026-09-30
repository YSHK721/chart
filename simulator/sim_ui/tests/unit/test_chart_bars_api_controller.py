"""`chart_bars_api_controller`（adapter・ISSUE-552/554 段階 2-1）。

固定する契約:
    1. `/chart-bars/{job}/extent` は宣言（行数・列・指標・1 回の上限）をそのまま配る。
    2. `/chart-bars/{job}/rows/{start}/{end}` は位置の半開区間の列を配る。
    3. 失敗は値ではなく状態で表す（404 / 409 / 413 / 400）。翻訳表は共有の実体。
    4. 非有限値は出口 1 箇所で null になる（ブラウザの JSON.parse が読める）。
    5. 解けないパスは 404（推測で補完しない）。

計算量: 応答の組み立ては usecase が渡した列の実体をそのまま出口へ渡す（写しを挟まない）。
    観測は usecase の代役が持つ列の実体と、応答が運ぶ要素数で行う。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

import simulator.sim_ui.adapter.chart_bars_api_controller as module
from simulator.sim_ui.adapter import json_api_translation
from simulator.sim_ui.adapter.chart_bars_api_controller import (
    CHART_BARS_PATH_PREFIX,
    ChartBarsApiController,
)
from simulator.sim_ui.usecase.chart_bars_ports import (
    ChartBarsArtefactMissingError,
    ChartBarsDeclaration,
)
from simulator.sim_ui.usecase.job_models import JobNotFoundError, ResultNotAvailableError
from simulator.sim_ui.usecase.query_chart_bars import (
    MAX_RETURNED_BARS,
    ChartBarsRangeTooWideError,
    ChartBarsRows,
)

_JOB = "c" * 32
_COLUMNS = ("bar_index", "time", "close", "margin_level", "indicator_0")


class _Query:
    """usecase の代役。渡された区間を記録し、列の実体を保持する。"""

    def __init__(self, *, error: "Exception | None" = None) -> None:
        self._error = error
        self.asked: "list[tuple[int, int]]" = []
        self.columns = {
            "bar_index": [3, 4], "time": [1_000, 1_060], "close": [1.5, 2.5],
            "margin_level": [float("inf"), float("nan")], "indicator_0": [None, 7.0],
        }

    def extent(self, job_id: str) -> ChartBarsDeclaration:
        if self._error is not None:
            raise self._error
        return ChartBarsDeclaration(
            rows=2_152_183, index_column="bar_index", columns=_COLUMNS,
            indicators=({"series": "sma", "placement": "price", "column": "indicator_0"},),
            timeframe="1m", ea_name="CalcProbe_EA", dataset_ref="jp225_mt5_spread",
            time_unit="epoch_seconds",
        )

    def rows(self, job_id: str, *, start: int, end: int) -> ChartBarsRows:
        self.asked.append((start, end))
        if self._error is not None:
            raise self._error
        return ChartBarsRows(start=start, end=end, rows=2, columns=self.columns)


def _strict(response) -> dict:
    """ブラウザと同じ厳格さで読む（`Infinity` / `NaN` を受理しない）。"""
    def refuse(token):
        raise AssertionError(f"JSON に無い綴り: {token}")
    return json.loads(response.to_bytes().decode("utf-8"), parse_constant=refuse)


class TestTheExtentCarriesTheDeclaration:
    def test_it_hands_out_the_rows_columns_indicators_and_the_cap(self):
        response = ChartBarsApiController(bars=_Query()).get(
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/extent"
        )
        assert response.status == 200
        assert _strict(response) == {
            "ok": True, "job_id": _JOB, "rows": 2_152_183, "index_column": "bar_index",
            "columns": list(_COLUMNS),
            "indicators": [{"series": "sma", "placement": "price", "column": "indicator_0"}],
            "timeframe": "1m", "ea_name": "CalcProbe_EA", "dataset_ref": "jp225_mt5_spread",
            "time_unit": "epoch_seconds", "max_returned_rows": MAX_RETURNED_BARS,
        }


class TestTheRowsCarryTheRange:
    def test_the_range_reaches_the_usecase_as_integers(self):
        query = _Query()
        response = ChartBarsApiController(bars=query).get(
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/3/5"
        )
        assert response.status == 200 and query.asked == [(3, 5)]
        payload = _strict(response)
        assert (payload["start"], payload["end"], payload["rows"]) == (3, 5, 2)
        assert list(payload["columns"]) == list(query.columns)
        assert payload["columns"]["close"] == [1.5, 2.5]

    def test_non_finite_values_become_null(self):
        payload = _strict(
            ChartBarsApiController(bars=_Query()).get(f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/3/5")
        )
        assert payload["columns"]["margin_level"] == [None, None]
        assert payload["columns"]["indicator_0"] == [None, 7.0]

    def test_a_query_string_does_not_change_the_range(self):
        query = _Query()
        ChartBarsApiController(bars=query).get(f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/3/5?v=1")
        assert query.asked == [(3, 5)]


class TestFailuresAreStates:
    @pytest.mark.parametrize(
        "error, status",
        [
            (JobNotFoundError("無い"), 404),
            (ChartBarsArtefactMissingError("足の成果物がありません"), 404),
            (ResultNotAvailableError("未完了"), 409),
            (ChartBarsRangeTooWideError("広すぎる"), 413),
            (ValueError("不正"), 400),
        ],
    )
    @pytest.mark.parametrize("tail", ["extent", "rows/0/10"])
    def test_each_failure_has_its_status_and_reason(self, error, status, tail):
        response = ChartBarsApiController(bars=_Query(error=error)).get(
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/{tail}"
        )
        assert (response.status, _strict(response)) == (status, {"error": str(error)})

    @pytest.mark.parametrize("tail", ["rows/a/10", "rows/0/-", "rows/1.5/3"])
    def test_a_position_that_is_not_an_integer_is_a_bad_request(self, tail):
        query = _Query()
        response = ChartBarsApiController(bars=query).get(f"{CHART_BARS_PATH_PREFIX}/{_JOB}/{tail}")
        assert response.status == 400 and query.asked == []

    @pytest.mark.parametrize(
        "path",
        [
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}",
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/0",
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/0/1/2",
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/extent/extra",
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/points/0/1",
            f"/other/{_JOB}/extent",
        ],
    )
    def test_an_unknown_path_is_not_found(self, path):
        query = _Query()
        assert ChartBarsApiController(bars=query).get(path).status == 404
        assert query.asked == []


class TestTheExitIsShared:
    def test_the_sanitiser_and_the_translation_are_the_shared_ones(self):
        """消毒と失敗の翻訳を本モジュールへ写していない（共有の実体を呼ぶ）。"""
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        defined = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
        }
        assert {"json_safe", "guarded", "_json_safe", "_guarded"} & defined == set()
        assert module.json_safe is json_api_translation.json_safe
        assert module.guarded is json_api_translation.guarded
        # 失敗の翻訳（except 節）を自前で持たない。
        assert [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)
                and not _is_integer_parse_guard(n)] == []

    def test_there_is_one_response_exit(self):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        sites = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "ApiResponse"
        ]
        assert len(sites) == 1


def _is_integer_parse_guard(handler: ast.ExceptHandler) -> bool:
    """位置のトークンを整数へ解く箇所の `except ValueError`（翻訳ではなく入力検査）。"""
    return isinstance(handler.type, ast.Name) and handler.type.id == "ValueError"


class TestNoDefensiveCopyIsBuilt:
    def test_the_carried_elements_equal_the_usecase_elements(self):
        # Arrange
        query = _Query()

        # Act
        payload = _strict(
            ChartBarsApiController(bars=query).get(f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/3/5")
        )

        # Assert: usecase が渡した要素数 − 応答が運ぶ要素数 = 0。
        handed = sum(len(values) for values in query.columns.values())
        carried = sum(len(values) for values in payload["columns"].values())
        assert handed - carried == 0 and carried > 0

    def test_the_payload_holds_the_usecase_lists_themselves(self):
        """出口へ渡す列は usecase の実体そのもの（写しは出力を変えずに割当だけ増やす）。"""
        query = _Query()
        status, payload = ChartBarsApiController(bars=query).route(
            f"{CHART_BARS_PATH_PREFIX}/{_JOB}/rows/3/5"
        )
        assert status == 200
        assert all(payload["columns"][name] is query.columns[name] for name in query.columns)
