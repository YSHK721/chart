"""`trace_api_controller`（adapter・RUN_TRACE_BASIC_DESIGN §9.2/§9.3）。

責務（SRP）: **翻訳だけ**。HTTP のパスから窓を読み、`query_trace` の答えを JSON にする。
窓・列・粒度の解釈も、返す量の決定も usecase 側が持ち、ここには写さない
（settings_schema_api_controller と同型）。

**なぜクエリ文字列を使わないか（実測・憶測ではない）**:
    sim core の GET は serve_sim.make_handler が
    `app.static_server.serve(self, urlparse(self.path).path)` を呼ぶ——
    **クエリはここで落ちる**。既存の GET JSON ルート 3 本（`/settings-schema` /
    `/run-options` / `/ea-series/{ea_name}`）はいずれもクエリを受け取っていない。
    受けるにはハンドラ（Phase 1 の実体）を変える必要があり、それは「内側を 1 バイトも
    変えない」という本段階の制約に反する。よって窓は**パスセグメント**で運ぶ。
"""
from __future__ import annotations

import json

import pytest

from simulator.sim_ui.adapter.trace_api_controller import (
    TRACE_PATH_PREFIX,
    UNBOUNDED_TOKEN,
    TraceApiController,
)
from simulator.sim_ui.usecase.derive_trace_events import (
    HALT,
    DeriveTraceEventsInteractor,
    TraceEvent,
)
from simulator.sim_ui.usecase.query_trace import (
    ANALYSIS_COLUMNS,
    TraceAnalysis,
    TraceWindowTooWideError,
)
from simulator.sim_ui.usecase.trace_query_ports import (
    TraceArtefactMissingError,
    TraceExtent,
)

_T0 = 1_704_067_200_000
_JOB = "0123456789abcdef01234567"


class _FakeQuery:
    """QueryTraceInteractor の代役（呼ばれ方を記録する Test Spy でもある）。"""

    def __init__(self, *, error=None):
        self._error = error
        self.analyse_calls: "list[dict]" = []
        self.extent_calls: "list[str]" = []

    def extent(self, job_id):
        self.extent_calls.append(job_id)
        if self._error is not None:
            raise self._error
        return TraceExtent(
            rows=1_036_394, first_time=_T0, last_time=_T0 + 1_000,
            initial_deposit=10_000.0, margin_level_floor=99.95,
        )

    def analyse(self, job_id, *, start=None, end=None):
        self.analyse_calls.append({"job_id": job_id, "start": start, "end": end})
        if self._error is not None:
            raise self._error
        columns = {name: [] for name in ANALYSIS_COLUMNS}
        columns["time"] = [_T0, _T0 + 500]
        columns["equity"] = [10_000.0, 9_900.0]
        columns["balance"] = [10_000.0, 10_000.0]
        columns["margin"] = [10.0, 10.0]
        columns["margin_level"] = [1_000.0, 500.0]
        columns["open_count"] = [0, 1]
        columns["halted"] = [False, True]
        return TraceAnalysis(
            window=(start, end), rows=2, columns=columns,
            events=(TraceEvent(_T0 + 500, HALT, False, True),),
            drawdown={"equity_dd_maximal": 100.0},
        )


def _sanitiser_calls_per_function(tree) -> "dict[str, int]":
    """関数名 → その関数内の `_json_safe` 呼出数。

    テスト本体に分岐を置かないための助力関数（Conditional Test Logic の回避）。
    テスト本体の実行経路が入力で変わると、どの経路が走ったのか読み手に分からない。
    """
    import ast as _ast

    functions = [
        node
        for node in _ast.walk(tree)
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef))
    ]
    return {
        func.name: sum(
            1
            for n in _ast.walk(func)
            if isinstance(n, _ast.Call)
            and isinstance(n.func, _ast.Name)
            and n.func.id == "_json_safe"
        )
        for func in functions
    }


def _payload(response):
    return json.loads(response.to_bytes().decode("utf-8"))


def _controller(**kwargs):
    query = _FakeQuery(**kwargs)
    return query, TraceApiController(trace=query)


# ---- 1: 範囲の問い合わせ ----

class TestTheExtentRoute:
    def test_it_answers_the_extent_for_a_job(self):
        # Arrange
        query, controller = _controller()

        # Act
        response = controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent")

        # Assert
        assert response.status == 200
        payload = _payload(response)
        assert payload["ok"] is True
        assert payload["rows"] == 1_036_394
        assert payload["first_time"] == _T0
        assert payload["margin_level_floor"] == 99.95
        assert query.extent_calls == [_JOB]

    def test_it_declares_the_columns_and_the_cap_so_the_front_writes_neither(self):
        """front が列名も上限も手書きしないで済むよう、サーバが宣言を配る。"""
        # Arrange
        from simulator.sim_ui.usecase.query_trace import MAX_RETURNED_ROWS

        _query, controller = _controller()

        # Act
        payload = _payload(controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent"))

        # Assert
        assert payload["columns"] == list(ANALYSIS_COLUMNS)
        assert payload["max_returned_rows"] == MAX_RETURNED_ROWS

    def test_it_declares_the_event_kinds(self):
        # Arrange
        from simulator.sim_ui.usecase.derive_trace_events import EVENT_KINDS

        _query, controller = _controller()

        # Act
        payload = _payload(controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent"))

        # Assert
        assert payload["event_kinds"] == list(EVENT_KINDS)


# ---- 2: 点列の問い合わせ ----

class TestThePointsRoute:
    def test_the_window_is_read_from_the_path(self):
        # Arrange
        query, controller = _controller()

        # Act
        response = controller.get(
            f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1000}"
        )

        # Assert
        assert response.status == 200
        assert query.analyse_calls == [
            {"job_id": _JOB, "start": _T0, "end": _T0 + 1000}
        ]

    def test_an_unbounded_side_is_expressed_by_a_token(self):
        # Arrange
        query, controller = _controller()

        # Act
        controller.get(
            f"{TRACE_PATH_PREFIX}/{_JOB}/points/{UNBOUNDED_TOKEN}/{UNBOUNDED_TOKEN}"
        )

        # Assert
        assert query.analyse_calls == [
            {"job_id": _JOB, "start": None, "end": None}
        ]

    def test_the_payload_carries_the_columns_events_and_drawdown(self):
        # Arrange
        _query, controller = _controller()

        # Act
        payload = _payload(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1000}")
        )

        # Assert
        assert payload["rows"] == 2
        assert list(payload["columns"]) == list(ANALYSIS_COLUMNS)
        assert payload["columns"]["time"] == [_T0, _T0 + 500]
        assert payload["events"] == [
            {"time": _T0 + 500, "kind": HALT, "previous": False, "value": True}
        ]
        assert payload["drawdown"] == {"equity_dd_maximal": 100.0}
        assert payload["window"] == {"start": _T0, "end": _T0 + 1000}

    def test_the_payload_is_json_serialisable_with_the_real_types(self):
        """`to_bytes` が落ちないこと（numpy 型が混ざると 500 になる）。"""
        # Arrange
        _query, controller = _controller()

        # Act / Assert
        response = controller.get(
            f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1000}"
        )
        assert isinstance(response.to_bytes(), bytes)


# ---- 3: 誤りは値でなく状態で表す ----

class TestTheErrorsAreDistinguishable:
    def test_an_unparsable_window_is_a_bad_request(self):
        # Arrange
        query, controller = _controller()

        # Act
        response = controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/points/abc/def")

        # Assert
        assert response.status == 400
        # 読みは発行されていない。
        assert query.analyse_calls == []

    def test_an_unknown_shape_is_not_found(self):
        # Arrange
        _query, controller = _controller()

        # Act / Assert
        assert controller.get(f"{TRACE_PATH_PREFIX}").status == 404
        assert controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}").status == 404
        assert controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/nope").status == 404

    def test_a_missing_artefact_is_not_found_with_a_reason(self):
        # Arrange
        _query, controller = _controller(
            error=TraceArtefactMissingError("トレースがありません")
        )

        # Act
        response = controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent")

        # Assert: 「0 行だった」と区別できる（別の事実である）。
        assert response.status == 404
        assert "トレース" in _payload(response)["error"]

    def test_an_incomplete_job_is_a_conflict(self):
        # Arrange
        from simulator.sim_ui.usecase.job_models import ResultNotAvailableError

        _query, controller = _controller(error=ResultNotAvailableError("未完了"))

        # Act / Assert: 既存の `/data/{job}/{file}` と同じ 409（規則を 2 つ持たない）。
        assert controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").status == 409

    def test_an_unknown_job_is_not_found(self):
        # Arrange
        from simulator.sim_ui.usecase.job_models import JobNotFoundError

        _query, controller = _controller(error=JobNotFoundError("無い"))

        # Act / Assert
        assert controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").status == 404

    def test_a_window_too_wide_is_reported_with_the_reason(self):
        # Arrange
        _query, controller = _controller(
            error=TraceWindowTooWideError("窓に入る点が 1036394 行あり上限 20000 行")
        )

        # Act
        response = controller.get(
            f"{TRACE_PATH_PREFIX}/{_JOB}/points/{UNBOUNDED_TOKEN}/{UNBOUNDED_TOKEN}"
        )

        # Assert: front が「窓を狭めよ」と表示できる（黙って間引かない）。
        assert response.status == 413
        assert "20000" in _payload(response)["error"]

    def test_a_reversed_window_is_a_bad_request(self):
        # Arrange
        _query, controller = _controller(error=ValueError("開始が終了より後"))

        # Act / Assert
        assert (
            controller.get(
                f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0 + 10}/{_T0}"
            ).status
            == 400
        )

    def test_a_rejected_identifier_is_not_found(self):
        """台帳が受理しない識別子（CWE-22 防御）は 404（存在を漏らさない）。"""
        # Arrange
        _query, controller = _controller(error=ValueError("ジョブ識別子として受理できません"))

        # Act / Assert
        assert controller.get(f"{TRACE_PATH_PREFIX}/../../etc/extent").status in (
            400, 404
        )


# ---- 4: 応答が**本物の JSON** であること（pre-mortem で見つかった欠陥） ----

class TestThePayloadIsValidJsonForABrowser:
    """非有限値（`inf` / nan）を JSON の外の綴りで書き出さない。

    実測でこうなっていた（2026-09-10・実ティック 1 ヶ月 run）:
        `Account.margin_level()` は建玉 0 の点で `math.inf` を返す。実 run
        1,036,394 点のうち **405,941 点（39.2%）** がそれに当たる。Python の
        `json.dumps` は既定でこれを `Infinity` と書くが、**`Infinity` は JSON では
        ない**——ブラウザの JSON.parse は `SyntaxError` で落ちる（node 実測）。
        Python 側の `json.loads` は非標準拡張として受理するため、Python だけで
        測っていると**この欠陥は緑のまま通る**。

    したがってここでは「Python で読み戻せる」ではなく「**厳格な JSON として
    読める**」を表明する（`json.loads(..., parse_constant=落とす)`）。
    """

    @staticmethod
    def _strict_loads(raw: bytes):
        """厳格な JSON として読む（`Infinity` / `NaN` を受理しない）。"""
        def reject(token):
            raise AssertionError(f"JSON でない綴りが出力されています: {token}")

        return json.loads(raw.decode("utf-8"), parse_constant=reject)

    def test_a_non_finite_margin_level_is_not_written_as_infinity(self):
        # Arrange: 建玉 0 の点（維持率は無限大）を含む応答。
        query = _FakeQuery()
        original = query.analyse

        def with_infinity(job_id, *, start=None, end=None):
            analysis = original(job_id, start=start, end=end)
            analysis.columns["margin_level"] = [float("inf"), 500.0]
            return analysis

        query.analyse = with_infinity
        controller = TraceApiController(trace=query)

        # Act
        response = controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1}")

        # Assert: 厳格な JSON として読める。
        payload = self._strict_loads(response.to_bytes())
        # 値は「有限でない」として null になる（欠測と読めるが、JSON に無限大は無い）。
        assert payload["columns"]["margin_level"] == [None, 500.0]

    @pytest.mark.parametrize(
        "value", [float("inf"), float("-inf"), float("nan")], ids=["inf", "-inf", "nan"]
    )
    def test_every_non_finite_value_becomes_null(self, value):
        # Arrange
        query = _FakeQuery()
        original = query.analyse

        def with_value(job_id, *, start=None, end=None):
            analysis = original(job_id, start=start, end=end)
            analysis.columns["equity"] = [value, 1.0]
            return analysis

        query.analyse = with_value
        controller = TraceApiController(trace=query)

        # Act
        payload = self._strict_loads(
            controller.get(
                f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1}"
            ).to_bytes()
        )

        # Assert
        assert payload["columns"]["equity"] == [None, 1.0]

    def test_a_non_finite_drawdown_is_also_sanitised(self):
        """初期資金 0 の run では DD の % が非有限になり得る。"""
        # Arrange
        query = _FakeQuery()
        original = query.analyse

        def with_value(job_id, *, start=None, end=None):
            analysis = original(job_id, start=start, end=end)
            return type(analysis)(
                window=analysis.window, rows=analysis.rows, columns=analysis.columns,
                events=analysis.events,
                drawdown={"equity_dd_maximal_percent": float("inf")},
            )

        query.analyse = with_value
        controller = TraceApiController(trace=query)

        # Act
        payload = self._strict_loads(
            controller.get(
                f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1}"
            ).to_bytes()
        )

        # Assert
        assert payload["drawdown"] == {"equity_dd_maximal_percent": None}

    def test_a_non_finite_event_value_is_also_sanitised(self):
        """維持率の割れ事象は margin_level の値を持つ（無限大になり得る）。"""
        # Arrange
        query = _FakeQuery()
        original = query.analyse

        def with_value(job_id, *, start=None, end=None):
            analysis = original(job_id, start=start, end=end)
            return type(analysis)(
                window=analysis.window, rows=analysis.rows, columns=analysis.columns,
                events=(TraceEvent(_T0, "margin_floor_breach", float("inf"), 90.0),),
                drawdown=analysis.drawdown,
            )

        query.analyse = with_value
        controller = TraceApiController(trace=query)

        # Act
        payload = self._strict_loads(
            controller.get(
                f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1}"
            ).to_bytes()
        )

        # Assert
        assert payload["events"][0]["previous"] is None
        assert payload["events"][0]["value"] == 90.0

    def test_the_extent_is_valid_json_too(self):
        # Arrange
        _query, controller = _controller()

        # Act / Assert
        payload = self._strict_loads(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").to_bytes()
        )
        assert payload["ok"] is True

    def test_finite_values_are_untouched(self):
        """正の対照: 有限値まで null にしていない（全部 null なら上は恒真）。"""
        # Arrange
        _query, controller = _controller()

        # Act
        payload = self._strict_loads(
            controller.get(
                f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 1000}"
            ).to_bytes()
        )

        # Assert
        assert payload["columns"]["equity"] == [10_000.0, 9_900.0]
        assert None not in payload["columns"]["margin_level"]


# ---- 5: 非有限値の null 化は**応答の唯一の出口**で行う（工程 5 レビュー 🔴-4） ----

class TestTheSanitisationHappensAtTheSingleExit:
    """フィールドごとの手書き適用をやめ、payload 木を再帰的に通す。

    工程 3 の欠陥（実測で確認された）:
        _points の 4 群へ手書きで非有限値の変換を撒き、**/extent に適用し忘れた**。
        `initial_deposit=inf` / `margin_level_floor=nan` が `Infinity` / `NaN` として
        直列化され、ブラウザの `JSON.parse` が `SyntaxError` になる。
        **front は `/extent` を最初に叩く**ので、落ちると分析タブは全面が掲示のみになる。

        既存の検定 `test_the_extent_is_valid_json_too` は有限値 fixture のため
        **原理的に落ちない**（見かけの被覆）。ここでは非有限値を注入して測る。

    これは拡張点の欠如＝OCP 違反である。フィールドを増やしても取り落ちない形
    （応答の唯一の出口で木を再帰的に通す）にし、出口が 1 つであることを構文木で固定する。
    """

    @staticmethod
    def _strict(raw: bytes):
        def reject(token):
            raise AssertionError(f"JSON でない綴りが出力されています: {token}")

        return json.loads(raw.decode("utf-8"), parse_constant=reject)

    def _extent_with(self, **over):
        """非有限値を混ぜた extent を返す controller。"""
        query = _FakeQuery()

        def extent(job_id):
            base = dict(
                rows=10, first_time=_T0, last_time=_T0 + 1,
                initial_deposit=10_000.0, margin_level_floor=99.95,
                suggested_window=(_T0, _T0 + 2),
            )
            base.update(over)
            return TraceExtent(**base)

        query.extent = extent
        return TraceApiController(trace=query)

    def test_a_non_finite_initial_deposit_in_the_extent_is_nulled(self):
        # Arrange: 初期資金 0 の run では DD の基準比が非有限になり得る。
        controller = self._extent_with(initial_deposit=float("inf"))

        # Act
        payload = self._strict(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").to_bytes()
        )

        # Assert
        assert payload["initial_deposit"] is None

    def test_a_non_finite_margin_floor_in_the_extent_is_nulled(self):
        # Arrange
        controller = self._extent_with(margin_level_floor=float("nan"))

        # Act
        payload = self._strict(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").to_bytes()
        )

        # Assert
        assert payload["margin_level_floor"] is None

    def test_a_non_finite_value_nested_in_the_suggested_window_is_nulled(self):
        """入れ子（dict の中の dict）も通る＝再帰であることの表明。"""
        # Arrange
        controller = self._extent_with(suggested_window=(float("inf"), _T0 + 2))

        # Act
        payload = self._strict(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").to_bytes()
        )

        # Assert
        assert payload["suggested_window"] == {"start": None, "end": _T0 + 2}

    def test_the_finite_extent_fields_are_untouched(self):
        """正の対照: 全部 null にしているわけではない。"""
        # Arrange
        controller = self._extent_with()

        # Act
        payload = self._strict(
            controller.get(f"{TRACE_PATH_PREFIX}/{_JOB}/extent").to_bytes()
        )

        # Assert
        assert payload["initial_deposit"] == 10_000.0
        assert payload["margin_level_floor"] == 99.95
        assert payload["suggested_window"] == {"start": _T0, "end": _T0 + 2}

    def test_the_module_builds_its_responses_in_exactly_one_place(self):
        """応答の出口が 1 つであること（構文木で固定）。

        出口が複数あると、新しいルートを足した人がそこで消毒を忘れる——それが工程 3 で
        実際に起きたことである（_points には撒いたが `/extent` に忘れた）。
        出口を 1 つに閉じれば、忘れる場所が存在しない。
        """
        # Arrange
        import ast
        import pathlib

        import simulator.sim_ui.adapter.trace_api_controller as mod

        source = pathlib.Path(mod.__file__).read_text(encoding="utf-8")

        # Act
        sites = [
            node
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "ApiResponse"
        ]

        # Assert
        assert len(sites) == 1, [n.lineno for n in sites]

    def test_no_field_applies_the_sanitiser_by_hand(self):
        """手書き適用が残っていないこと（撒き直しの再発を赤にする）。

        出口で木を通すなら、フィールドごとの呼出は 0 件が正しい。1 件でも残ると
        「ここは通してあるから安心」という誤読を生み、次の追加で取り落ちる。
        """
        # Arrange
        import ast
        import pathlib

        import simulator.sim_ui.adapter.trace_api_controller as mod

        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))

        # Act: `_json_safe` の呼出を関数ごとに数える（分岐は助力関数が持つ）。
        calls = _sanitiser_calls_per_function(tree)

        # Assert: payload を組む関数（_extent / _points）は呼ばない。
        assert calls.get("_extent", 0) == 0, calls
        assert calls.get("_points", 0) == 0, calls
        # 正の対照: どこかでは呼んでいる（消毒そのものが消えていない）。
        assert sum(calls.values()) >= 1, calls


class TestTheSanitiserWalksTheWholeTree:
    """`_json_safe` の単体契約（出口 1 箇所で全体を賄えることの根拠）。"""

    def test_it_replaces_non_finite_floats_at_any_depth(self):
        from simulator.sim_ui.adapter.trace_api_controller import _json_safe

        got = _json_safe(
            {
                "a": float("inf"),
                "b": [1.0, float("-inf"), {"c": float("nan")}],
                "d": {"e": {"f": (float("inf"), 2.0)}},
            }
        )
        assert got == {
            "a": None, "b": [1.0, None, {"c": None}], "d": {"e": {"f": [None, 2.0]}},
        }

    def test_it_leaves_everything_else_alone(self):
        from simulator.sim_ui.adapter.trace_api_controller import _json_safe

        payload = {"i": 3, "s": "x", "b": True, "n": None, "f": 1.5, "l": [1, "y"]}
        assert _json_safe(payload) == payload

    def test_bool_is_not_treated_as_a_number(self):
        """`bool` は `float` ではないが、取り違えると halted 列が壊れる。"""
        from simulator.sim_ui.adapter.trace_api_controller import _json_safe

        assert _json_safe({"halted": [True, False]}) == {"halted": [True, False]}


# ---- 6: 応答の出口段の計算量ゲート（絶対命令・工程 5 再レビュー 🟡-B） ----
#
# なぜここに要るか: §9.6 の 4 表明は store 段と事象導出段だけを覆っていた。🔴-4 の是正で
# 「payload 木を歩く」仕事がこの層へ移ったのに、計器が追随していなかった。実測では
# 出力を 1 bit も変えない冗長な中間リストが入り込み、**一過性割当が +92.9%**
# （20,000 行 × 7 列・tracemalloc peak 2281.6 KiB → 1182.9 KiB）になっていた。
#
# 計器の選定（実測で決めた・憶測ではない）:
#   * 列を `list` 部分型にして __iter__ を数える案は**識別できない**（実測 7 対 7）。
#     `list(col)` が作る写しは素の `list` であり、以後の走査は計器の外で起きる。
#   * `tracemalloc` の割当ブロック数は識別できる（63 対 8）が、実装ノイズを含む生の数を
#     期待値にすることになり「回数の焼き込み」に当たる。
#   * 採ったのは**同一性**である。冗長な写しが挟まると、消毒器が歩くのは usecase が作った
#     list ではなく**その写し**になる。id の一致は決定論的で、リテラルの回数を持たない。
#
# 限界を隠さない: 下の「歩いた要素数 − 運ぶ要素数 = 0」**だけでは冗長な写しを検出できない**
# （写しは同じ長さなので差が 0 のまま。実測で確認済み）。同一性ゲートと併せて初めて
# 「materialise した要素数 − 応答が運ぶ要素数 = 0」を表明できる。

class _SanitiserSpy:
    """応答の出口（`_json_safe`）が歩いた list を記録する Test Spy。

    再帰呼出も module 大域を引くため、木の全段が観測できる。
    """

    def __init__(self, real):
        self._real = real
        self.walked_ids: "set[int]" = set()
        self.walked_lists: "list[tuple[int, int]]" = []

    def __call__(self, value):
        if isinstance(value, list):
            self.walked_ids.add(id(value))
            self.walked_lists.append((id(value), len(value)))
        return self._real(value)


class _ColumnCarryingQuery:
    """列の実体（list オブジェクト）を保持し、その同一性を検定へ渡す代役。"""

    def __init__(self, rows: int = 50):
        self.columns = {
            name: [float(i) for i in range(rows)] for name in ANALYSIS_COLUMNS
        }
        self.columns["time"] = [_T0 + i for i in range(rows)]
        self.columns["halted"] = [False] * rows
        self.columns["open_count"] = [0] * rows
        self.rows = rows

    def extent(self, job_id):
        raise AssertionError("この代役は points だけを扱う")

    def analyse(self, job_id, *, start=None, end=None):
        return TraceAnalysis(
            window=(start, end), rows=self.rows, columns=self.columns,
            events=(TraceEvent(_T0, HALT, False, True),),
            drawdown={"equity_dd_maximal": 1.0},
        )


class TestNothingIsMaterialisedAndDiscardedWhenBuildingTheResponse:
    """応答の組み立てで materialise した要素数 − 応答が運ぶ要素数 = 0。"""

    def _run(self, monkeypatch):
        import simulator.sim_ui.adapter.trace_api_controller as mod

        query = _ColumnCarryingQuery()
        spy = _SanitiserSpy(mod._json_safe)
        monkeypatch.setattr(mod, "_json_safe", spy)
        response = mod.TraceApiController(trace=query).get(
            f"{TRACE_PATH_PREFIX}/{_JOB}/points/{_T0}/{_T0 + 10_000}"
        )
        return query, spy, json.loads(response.to_bytes())

    def test_the_sanitiser_walks_the_usecase_lists_themselves(self, monkeypatch):
        """冗長な中間リストを挟まない（挟むと歩くのは写しになる）。"""
        # Arrange / Act
        query, spy, _payload = self._run(monkeypatch)

        # Assert: 宣言の全列について、usecase が作った list そのものを歩いている。
        walked_directly = [
            name for name in ANALYSIS_COLUMNS
            if id(query.columns[name]) in spy.walked_ids
        ]
        assert walked_directly == list(ANALYSIS_COLUMNS), (
            "応答の組み立てが列を写している（写しは出力を変えないまま割当だけ増やす）: "
            f"直に歩いた列={walked_directly}"
        )
        # 正の対照: 列が空なら上は恒真になる。
        assert query.rows > 0 and len(ANALYSIS_COLUMNS) >= 2

    def test_the_walked_element_count_equals_the_carried_element_count(
        self, monkeypatch
    ):
        """歩いた要素数 − 運ぶ要素数 = 0（余分なものを歩いていない）。

        **この表明だけでは冗長な写しを捕らえられない**（写しは同じ長さなので差が 0 の
        まま・実測）。上の同一性ゲートと対で初めて「作ってから捨てる」の不在になる。
        """
        # Arrange / Act
        query, spy, payload = self._run(monkeypatch)

        # Assert
        column_ids = {id(query.columns[name]) for name in ANALYSIS_COLUMNS}
        walked = sum(
            length for ident, length in spy.walked_lists if ident in column_ids
        )
        carried = sum(len(payload["columns"][name]) for name in ANALYSIS_COLUMNS)
        assert walked - carried == 0, (walked, carried)
        assert carried == query.rows * len(ANALYSIS_COLUMNS)

    def test_the_response_carries_every_declared_column_exactly_once(
        self, monkeypatch
    ):
        """正の対照: 運ぶ量が宣言どおり（0 件の一致を見ていない）。"""
        # Arrange / Act
        _query, _spy, payload = self._run(monkeypatch)

        # Assert
        assert list(payload["columns"]) == list(ANALYSIS_COLUMNS)

    def test_the_identity_gate_catches_a_defensive_copy(self, monkeypatch):
        """**検出力の自己検定**: 写しを挟む組み立てを同一性ゲートが赤にする。

        production へ変異を入れずに、計器そのものが写しを識別できることを表明する。

        Spy は **module 大域へ据える**（`_json_safe` の再帰は大域を引くため、据えないと
        木の内側が観測できない——最初この据え忘れで自己検定が自分から赤になり、計器の
        観測範囲が足りないことがその場で露見した）。
        """
        # Arrange
        import simulator.sim_ui.adapter.trace_api_controller as mod

        real = mod._json_safe
        source = {name: [1.0, 2.0] for name in ANALYSIS_COLUMNS}

        # Act: 写しを挟む組み立てと、挟まない組み立てを同じ計器で測る。
        def build(with_copy):
            spy = _SanitiserSpy(real)
            monkeypatch.setattr(mod, "_json_safe", spy)
            spy(
                {
                    "columns": {
                        name: (list(source[name]) if with_copy else source[name])
                        for name in ANALYSIS_COLUMNS
                    }
                }
            )
            return [
                name for name in ANALYSIS_COLUMNS if id(source[name]) in spy.walked_ids
            ]

        # Assert
        assert build(with_copy=False) == list(ANALYSIS_COLUMNS)
        assert build(with_copy=True) == [], "写しを挟んでも同一性ゲートが素通りしている"
