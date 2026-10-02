"""足の API を**端から端まで**結線する（ISSUE-552/554 段階 2-1・ISSUE-291 再発防止）。

固定する不変条件:
    1. 実ジョブ（run_job.main）→ 書き手 → 実 parquet → 実 HTTP → JSON で、run が読んだ CSV の
       足と独立計算の指標が位置の区間で返る（代役で緑にならない）。
    2. wrapper を足す前の面には `/chart-bars` が無い（この 1 本だけが増分である）。
    3. wrapper を足す前と後で、既存面の応答が 1 バイトも変わらない（委譲・OCP）。
    4. 未完了 409・成果物なし 404・上限超過 413・区間不正 400 が、値ではなく状態で区別できる。

計算量（絶対命令 2026-08-28・検査側の設計 2026-09-25）:
    観測口は読み口が宣言する `parquet_trace_store.set_read_observer` だけを使う。
    実 HTTP の経路で、IO 段が組み立てた行 − 応答が運ぶ行 = 0・組み立てた列 − 応答の列 = 0・
    413 のとき IO 段の読み 0・run の長さ 2 点で同じ区間の読む行数が一致、を表明する。
    位置の列の型に入らない位置は 400 で、IO 段の読み 0（run の長さ 2 点で同じ）。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd
import pytest

from simulator.adapter.trace import parquet_trace_store
from simulator.sim_ui.adapter import chart_overlay_writer
from simulator.sim_ui.adapter.chart_bars_api_controller import CHART_BARS_PATH_PREFIX
from simulator.sim_ui.framework.serve_sim_chart_bars import SimChartBarsApp
from simulator.sim_ui.framework.serve_sim_display import make_server
from simulator.sim_ui.main import run_job
from simulator.sim_ui.main.composition_root_display import build_sim_display_app
from simulator.sim_ui.tests.app_chain import inside
from simulator.sim_ui.usecase import query_chart_bars
from simulator.sim_ui.usecase.query_chart_bars import MAX_RETURNED_BARS

_ROOT = Path(__file__).resolve().parents[4]
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"
_EPOCH = 1_704_067_200
_PERIOD = 3
#: 始値が SMA を上下に跨ぐ並び（買い・売りの途転が複数回起きる）。
_OPENS = [100, 102, 104, 103, 99, 97, 98, 101, 105, 104, 100, 96, 95, 99, 103, 106, 102, 98]


def _closes() -> "list[float]":
    return [o + (0.5 if i % 2 else -0.5) for i, o in enumerate(_OPENS)]


def _job_id(prefix: str) -> str:
    return (prefix + "0" * 32)[:32]


def _mark(job_dir: Path, status: str) -> None:
    (job_dir / "state.json").write_text(
        json.dumps({"job_id": job_dir.name, "status": status, "failure_reason": None}),
        encoding="utf-8",
    )


def _run_real_job(data_root: Path) -> str:
    """CalcProbe_EA を実際に走らせる（成果物は書き手が書いた実物）。"""
    job_dir = data_root / _job_id("a")
    job_dir.mkdir(parents=True)
    csv = data_root / "d.csv"
    pd.DataFrame(
        [
            {"time": _EPOCH + 60 * i, "open": float(o), "high": max(o, c) + 1.0,
             "low": min(o, c) - 1.0, "close": c, "volume": 100, "spread": 3}
            for i, (o, c) in enumerate(zip(_OPENS, _closes()))
        ]
    ).to_csv(csv, index=False)
    backtest = {
        "ea_name": "CalcProbe_EA", "symbol": "JP225", "period": "M1", "data_path": str(csv),
        "initial_deposit": 100_000.0, "contract_size": 10.0, "volume_min": 0.1,
        "volume_max": 100.0, "volume_step": 0.1, "stops_level": 0, "digits": 1,
        "point_size": 0.1, "leverage": 100.0, "ma_period": _PERIOD, "ma_method": "sma",
        "lot_size": 1.0, "stop_loss_points": 0, "take_profit_points": 0,
    }
    (job_dir / "spec.json").write_text(
        json.dumps({"backtest": backtest, "sizing": None, "strategy": None,
                    "settings": None, "trace": None}),
        encoding="utf-8",
    )
    assert run_job.main(["--job-dir", str(job_dir)]) == 0
    _mark(job_dir, "completed")
    return job_dir.name


class _Bar:
    __slots__ = ("open", "high", "low", "close")

    def __init__(self, value: float) -> None:
        self.open = self.high = self.low = self.close = value


def _write_long_job(data_root: Path, prefix: str, rows: int, *, status: str = "completed") -> str:
    """``rows`` 本の足の成果物だけを持つジョブ（書き手の実体で書く・形式を写さない）。"""
    job_dir = data_root / _job_id(prefix)
    job_dir.mkdir(parents=True)
    (job_dir / "spec.json").write_text(json.dumps({"backtest": {}}), encoding="utf-8")
    values = [float(i) for i in range(rows)]
    chart_overlay_writer.write_chart_bars(
        job_dir, bars=[_Bar(v) for v in values],
        bar_times=[_EPOCH + 60 * i for i in range(rows)],
        account_columns={name: values for name in chart_overlay_writer.ACCOUNT_COLUMNS},
        series=[], ea_name="CalcProbe_EA", dataset_ref=None, stop_out_level=100.0,
    )
    _mark(job_dir, status)
    return job_dir.name


def _serve(app):
    srv = make_server(app, "127.0.0.1", None)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{srv.server_address[1]}"


def _request(base: str, path: str) -> "tuple[int, bytes]":
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path), timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _strict(body: bytes) -> dict:
    """ブラウザと同じ厳格さで読む（`Infinity` / `NaN` を受理しない）。"""
    def refuse(token):
        raise AssertionError(f"JSON に無い綴り: {token}")
    return json.loads(body.decode("utf-8"), parse_constant=refuse)


def _stored_position_ceiling(path: Path) -> int:
    """成果物の位置の列の型が表せる最大の整数（footer のスキーマから導く）。"""
    import numpy as np
    import pyarrow.parquet as pq

    stored = pq.ParquetFile(path).schema_arrow.field(chart_overlay_writer.INDEX_COLUMN).type
    return int(np.iinfo(stored.to_pandas_dtype()).max)


@pytest.fixture(scope="module")
def wired(tmp_path_factory):
    data_root = tmp_path_factory.mktemp("chart_bars_e2e") / "data"
    data_root.mkdir()
    jobs = {
        "real": _run_real_job(data_root),
        "short": _write_long_job(data_root, "b", 10_000),
        "long": _write_long_job(data_root, "c", 1_000_000),
        "running": _write_long_job(data_root, "d", 10, status="running"),
    }
    # 足の成果物を持たない完了ジョブ（本段階より前に走らせたジョブの形）。
    bare = data_root / _job_id("e")
    bare.mkdir()
    (bare / "spec.json").write_text(json.dumps({"backtest": {}}), encoding="utf-8")
    _mark(bare, "completed")
    jobs["bare"] = bare.name

    after = build_sim_display_app(repo_root=_ROOT, web_dir=_SIM_WEB, data_root=data_root)
    before = inside(after, SimChartBarsApp)
    srv_a, thread_a, base_a = _serve(after)
    srv_b, thread_b, base_b = _serve(before)
    try:
        yield base_a, base_b, jobs, data_root
    finally:
        for srv, thread in ((srv_a, thread_a), (srv_b, thread_b)):
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)


@pytest.fixture
def io_reads():
    seen: "list[tuple[int, tuple[str, ...]]]" = []
    parquet_trace_store.set_read_observer(lambda rows, columns: seen.append((rows, columns)))
    yield seen
    parquet_trace_store.set_read_observer(None)


class TestTheBarsApiIsWiredEndToEnd:
    def test_the_extent_declares_the_rows_columns_and_cap(self, wired):
        base, _before, jobs, _root = wired
        status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/extent")
        payload = _strict(body)
        assert status == 200
        assert payload["rows"] == len(_OPENS)
        assert payload["max_returned_rows"] == MAX_RETURNED_BARS
        assert payload["index_column"] == chart_overlay_writer.INDEX_COLUMN
        assert payload["timeframe"] == chart_overlay_writer.RUN_TIMEFRAME
        assert payload["ea_name"] == "CalcProbe_EA"
        (indicator,) = payload["indicators"]
        assert (indicator["series"], indicator["placement"]) == ("sma", "price")
        assert payload["columns"] == [
            *chart_overlay_writer.BAR_COLUMNS, *chart_overlay_writer.ACCOUNT_COLUMNS,
            indicator["column"],
        ]

    def test_the_rows_are_the_bars_the_run_read(self, wired):
        base, _before, jobs, _root = wired
        _status, extent = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/extent")
        status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/rows/4/9")
        payload = _strict(body)
        assert status == 200
        assert (payload["start"], payload["end"], payload["rows"]) == (4, 9, 5)
        columns = payload["columns"]
        # 足は run が読んだ CSV そのもの（位置 4〜8）。
        assert columns["bar_index"] == [4, 5, 6, 7, 8]
        assert columns["time"] == [_EPOCH + 60 * i for i in range(4, 9)]
        assert columns["open"] == [float(o) for o in _OPENS[4:9]]
        assert columns["close"] == _closes()[4:9]
        # 指標は終値の単純平均を独立に計算して照合。
        closes = _closes()
        expected = [sum(closes[i + 1 - _PERIOD : i + 1]) / _PERIOD for i in range(4, 9)]
        column = _strict(extent)["indicators"][0]["column"]
        assert columns[column] == pytest.approx(expected)
        # 口座の列は足の成果物の同じ位置と同じ値（配信を通さず store から直接読んで照合）。
        stored = parquet_trace_store.read_columns(
            _root / jobs["real"] / chart_overlay_writer.CHART_BARS_FILENAME,
            columns=list(chart_overlay_writer.ACCOUNT_COLUMNS), start=4, end=9,
            time_column=chart_overlay_writer.INDEX_COLUMN,
        )
        for name in chart_overlay_writer.ACCOUNT_COLUMNS:
            expected = [None if isinstance(v, float) and v != v else v for v in stored[name]]
            assert columns[name] == expected, name

    def test_the_whole_short_run_is_strict_json(self, wired):
        """保有の無い足の維持率（値なし）を含む全区間が、ブラウザの読める JSON で返る。"""
        base, _before, jobs, _root = wired
        status, body = _request(
            base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/rows/0/{len(_OPENS)}"
        )
        payload = _strict(body)
        assert status == 200 and payload["rows"] == len(_OPENS)
        levels = payload["columns"]["margin_level"]
        assert None in levels and any(v is not None for v in levels)


class TestTheWrapperAddsExactlyOneRouteAndChangesNothingElse:
    def test_the_inner_surface_has_no_bars_route(self, wired):
        _base, before, jobs, _root = wired
        status, _body = _request(before, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/extent")
        assert status == 404

    @pytest.mark.parametrize("path", ["/settings-schema", "/run-options", "/index.html"])
    def test_the_existing_routes_are_byte_identical(self, wired, path):
        base, before, _jobs, _root = wired
        after_answer, before_answer = _request(base, path), _request(before, path)
        assert after_answer == before_answer
        assert len(after_answer[1]) > 0

    def test_a_prefix_neighbour_falls_through_to_the_static_surface(self, wired):
        base, before, _jobs, _root = wired
        assert _request(base, "/chart-bars-extra.js") == _request(before, "/chart-bars-extra.js")


class TestTheFailuresAreDistinguishable:
    def test_an_unfinished_job_is_a_conflict(self, wired):
        base, _before, jobs, _root = wired
        for tail in ("extent", "rows/0/5"):
            status, _body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['running']}/{tail}")
            assert status == 409, tail

    def test_a_job_without_the_artefact_is_not_found_with_the_reason(self, wired):
        base, _before, jobs, _root = wired
        for tail in ("extent", "rows/0/5"):
            status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['bare']}/{tail}")
            assert status == 404 and "足の成果物" in _strict(body)["error"], tail

    def test_an_unknown_job_is_not_found(self, wired):
        base, _before, _jobs, _root = wired
        status, _body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{'f' * 32}/extent")
        assert status == 404

    @pytest.mark.parametrize("tail", ["rows/9/3", "rows/x/3", "rows/-1/3"])
    def test_a_malformed_range_is_a_bad_request(self, wired, tail):
        base, _before, jobs, _root = wired
        status, _body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/{tail}")
        assert status == 400


class TestAPositionTheArtefactCannotHoldIsABadRequest:
    """位置の列の型に入らない位置は、読み口へ届く前に 400 になる（独立レビュー 🟡-2）。"""

    def test_the_declared_bound_is_the_ceiling_of_the_stored_position_type(self, wired):
        """上界の宣言は、書き手が書いた成果物の位置の列の型と一致する（値を書き写さない）。"""
        _base, _before, jobs, root = wired
        ceiling = _stored_position_ceiling(
            root / jobs["real"] / chart_overlay_writer.CHART_BARS_FILENAME
        )
        assert query_chart_bars.MAX_POSITION == ceiling

    @pytest.mark.parametrize("over_start, over_end", [(None, 1), (1, 2), (None, 10**30)])
    @pytest.mark.parametrize("key", ["short", "long"])
    def test_it_is_refused_with_the_reason_and_without_reading(
        self, wired, io_reads, key, over_start, over_end
    ):
        # Arrange: 成果物の型の天井からの超過ぶんで区間を作る。
        base, _before, jobs, root = wired
        ceiling = _stored_position_ceiling(
            root / jobs[key] / chart_overlay_writer.CHART_BARS_FILENAME
        )
        start = 0 if over_start is None else ceiling + over_start

        # Act
        status, body = _request(
            base, f"{CHART_BARS_PATH_PREFIX}/{jobs[key]}/rows/{start}/{ceiling + over_end}"
        )

        # Assert: 既存の失敗翻訳と同じ形（error つきの JSON）・IO 段の読み 0。
        payload = _strict(body)
        assert status == 400 and set(payload) == {"error"} and payload["error"]
        assert io_reads == []

    def test_the_ceiling_itself_returns_the_rows_that_exist(self, wired, io_reads):
        """正の対照: 天井ちょうどは受理し、run の末尾で切る（既存仕様を狭めない）。"""
        base, _before, jobs, root = wired
        ceiling = _stored_position_ceiling(
            root / jobs["real"] / chart_overlay_writer.CHART_BARS_FILENAME
        )
        status, body = _request(
            base, f"{CHART_BARS_PATH_PREFIX}/{jobs['real']}/rows/{len(_OPENS) - 3}/{ceiling}"
        )
        payload = _strict(body)
        assert status == 200 and payload["rows"] == 3
        assert sum(rows for rows, _columns in io_reads) - payload["rows"] == 0


class TestTheReadCostIsTheReturnedRange:
    def test_the_io_layer_builds_exactly_what_the_response_carries(self, wired, io_reads):
        # Arrange
        base, _before, jobs, _root = wired

        # Act
        status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['long']}/rows/500000/501500")
        payload = _strict(body)

        # Assert: 組み立てた行 − 応答が運ぶ行 = 0・組み立てた列 − 応答の列 = 0。
        assert status == 200
        built_rows = sum(rows for rows, _columns in io_reads)
        built_columns = {name for _rows, columns in io_reads for name in columns}
        assert built_rows - payload["rows"] == 0, (built_rows, payload["rows"])
        assert built_columns ^ set(payload["columns"]) == set()
        assert {len(v) for v in payload["columns"].values()} == {payload["rows"]}
        assert payload["rows"] == 1_500  # 正の対照。

    def test_a_range_over_the_cap_is_refused_without_reading(self, wired, io_reads):
        base, _before, jobs, _root = wired
        status, body = _request(
            base, f"{CHART_BARS_PATH_PREFIX}/{jobs['long']}/rows/0/{MAX_RETURNED_BARS + 1}"
        )
        assert status == 413 and str(MAX_RETURNED_BARS) in _strict(body)["error"]
        assert io_reads == []

    def test_the_extent_reads_no_row(self, wired, io_reads):
        base, _before, jobs, _root = wired
        status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{jobs['long']}/extent")
        assert status == 200 and _strict(body)["rows"] == 1_000_000
        assert io_reads == []

    def test_two_run_lengths_read_the_same_rows_for_the_same_range(self, wired, io_reads):
        base, _before, jobs, _root = wired
        observed = []
        for job in (jobs["short"], jobs["long"]):
            del io_reads[:]
            status, body = _request(base, f"{CHART_BARS_PATH_PREFIX}/{job}/rows/2000/3500")
            assert status == 200
            observed.append(
                (len(io_reads), sum(rows for rows, _c in io_reads), _strict(body)["rows"])
            )
        # run を 100 倍にしても、同じ区間の読みの数と組み立てる行数は変わらない。
        assert observed[0] == observed[1], observed
        assert observed[0][1] == 1_500

    def test_the_declaration_size_does_not_depend_on_the_run_length(self, wired):
        _base, _before, jobs, root = wired
        name = chart_overlay_writer.CHART_BARS_DECLARATION_FILENAME
        sizes = [
            (root / jobs[key] / name).stat().st_size - len(str(rows))
            for key, rows in (("short", 10_000), ("long", 1_000_000))
        ]
        assert sizes[0] == sizes[1], sizes
