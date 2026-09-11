"""分析 API を**端から端まで**結線する（ISSUE-291 再発防止・RUN_TRACE_BASIC_DESIGN §9.2）。

固定する不変条件:
    1. 実 HTTP → 分析 API → **実 parquet 読み取り** → JSON で返る（fake で緑にならない）。
    2. wrapper を足す前の面には `/trace` が**無い**（この 1 本だけが増分である）。
    3. wrapper を足す前と後で、既存面（静的・`/settings-schema` 等）の応答が
       **1 バイトも変わらない**（委譲・OCP）。
    4. front が叩くパスと、サーバが受けるパスが**同一の宣言**から来る
       （口だけ作って呼ばれない箇所を作らない＝ISSUE-291 の形）。
    5. 記録 OFF の run・未完了ジョブ・広すぎる窓が、**値ではなく状態**で区別できる。

**なぜ実 parquet で測るか**: Port を fake にすると「読み口を作ったが誰も呼んでいない」
状態が緑になる。ISSUE-291 は「サーバ分岐を作っても front が送らなければ無言で死ぬ」
という同型の壊れ方であり、実測で確認する以外に検出手段が無い。
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simulator.sim_ui.adapter.trace_api_controller import (
    TRACE_PATH_PREFIX,
    UNBOUNDED_TOKEN,
)
from simulator.sim_ui.adapter.trace_writer import POINTS_FILENAME
from simulator.sim_ui.framework.serve_sim_display import make_server
from simulator.sim_ui.framework.serve_sim_trace import SimTraceApp
from simulator.sim_ui.tests.app_chain import inside
from simulator.sim_ui.main.composition_root_display import build_sim_display_app
from simulator.sim_ui.usecase.query_trace import MAX_RETURNED_ROWS
from marketdata.symbol_spec_snapshot import OANDA_JAPAN_MT5_LIVE, load_spec_fields
from simulator.tests.fixtures.mt5 import load_case

_ROOT = Path(__file__).resolve().parents[4]
_SIM_WEB = _ROOT / "simulator" / "sim_ui" / "web"
#: run の stop-out 水準（維持率の閾値の出所。分析側が発明する値ではない）。
_STOP_OUT_LEVEL = 99.95


def _serve(app):
    srv = make_server(app, "127.0.0.1", None)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, f"http://127.0.0.1:{port}"


def _request(base, path):
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


#: 実 MT5 突合フィクスチャ（JP225 M1 2025-01・MA_Slope_EA）。
#:
#: **なぜ合成 40 バーで測らないか（実測）**: TC24051901 に合成系列（一定振幅・
#: 単調・ジグザグの 3 形）を与えても **建玉が 1 つも立たない**（open_count は
#: 全点 0・`stats.trades` = 0）。事象が 0 件の入力で「事象が返る」ことは測れず、
#: 恒真な検定になる。実プロファイルは 36,019 点・1,107 トレード・halt 1 回を生む。
_CASE = "ma_slope_jp225_202501"
#: フィクスチャの所在（`load_case` を通す前に**存在だけ**を見る）。
_CASE_DIR = _ROOT / "simulator" / "tests" / "fixtures" / "mt5" / _CASE


def _fixture_missing() -> bool:
    """フィクスチャ**そのものが無い**か。

    例外を握り潰さない: `load_case` が投げるのは「フィクスチャが壊れている」ときで
    あり、それを skip に化けさせると検定が無言で消える（実行されない検定は緑と
    区別できない）。ここで見るのは所在だけで、内容の不良は素で落とす。
    """
    if not _CASE_DIR.is_dir():
        return True
    return not Path(load_case(_CASE).warmup_csv).is_file()


_needs_mt5_fixture = pytest.mark.skipif(
    _fixture_missing(), reason="MT5 突合フィクスチャ（JP225 M1）が無い"
)


def _backtest() -> dict:
    """指紋ゲート（`tests/integration/test_run_backtest_fingerprint.py`）と同一プロファイル。

    銘柄仕様 8 項目は同じ供給元（スナップショット）から引く。ここにリテラルを書くと
    「同一プロファイル」という前提が黙って崩れる（ISSUE-445 段階 2 で実際に崩れた）。
    """
    case = load_case(_CASE)
    c = case.config
    sym, acc, ea = c["symbol"], c["account"], c["expert"]
    return dict(
        data_path=str(case.warmup_csv), symbol=sym["name"], period="M1",
        ea_name="MA_Slope_EA", initial_deposit=float(acc["initial_deposit"]),
        **load_spec_fields(OANDA_JAPAN_MT5_LIVE, sym["name"]),
        ma_period=int(ea["ma_period"]), ma_method=ea["ma_method"],
        lot_size=float(ea["lot"]), stop_loss_points=int(ea["stop_loss"]),
        take_profit_points=int(ea["take_profit"]),
        slope_shift=int(ea["slope_shift"]),
        slope_min_points=float(ea["slope_min_points"]),
        config_overrides={
            "tick_model": "open_only", "entry_price_basis": "current_open",
            "stop_out_action": "close_and_halt", "prime_first_trading_bar": True,
            "floating_pnl_basis": "bid_ask",
        },
        stop_out_level=_STOP_OUT_LEVEL,
    )


def _run_traced_job(data_root: Path, *, job_suffix: str = "a") -> str:
    """実ジョブを 1 本走らせて `job_id` を返す（成果物は実 parquet）。"""
    from simulator.sim_ui.main import run_job

    # 台帳の採番規則は 32 桁の 16 進（file_job_ledger._JOB_ID_RE）。ここを外すと
    # 「関門が識別子を受理しない」だけの赤になり、結線を測れない。
    job_id = (job_suffix + "0" * 32)[:32]
    job_dir = data_root / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest(), "sizing": None, "strategy": None,
                "settings": None, "trace": {"enabled": True},
            }
        ),
        encoding="utf-8",
    )
    assert run_job.main(["--job-dir", str(job_dir)]) == 0, (
        (job_dir / "failure.json").read_text(encoding="utf-8")
        if (job_dir / "failure.json").exists() else "run failed"
    )
    # 台帳が「完了」と読むための state.json（配信の関門はこれを見る）。
    (job_dir / "state.json").write_text(
        json.dumps({"job_id": job_id, "status": "completed", "failure_reason": None}),
        encoding="utf-8",
    )
    assert (job_dir / POINTS_FILENAME).is_file()
    return job_id


@pytest.fixture(scope="module")
def wired(tmp_path_factory):
    """実 run は 1 回だけ（約 16 秒）。全検定でこの 1 本を共有する。"""
    tmp_path = tmp_path_factory.mktemp("trace_e2e")
    data_root = tmp_path / "data"
    data_root.mkdir()
    job_id = _run_traced_job(data_root)

    after = build_sim_display_app(
        repo_root=_ROOT, web_dir=_SIM_WEB, data_root=data_root
    )
    # 層はクラスで指す（ホップ数を手書きしない・`tests/app_chain.py` の理由参照）。
    before = inside(after, SimTraceApp)
    srv_a, thread_a, base_a = _serve(after)
    srv_b, thread_b, base_b = _serve(before)
    try:
        yield base_a, base_b, job_id, after, data_root
    finally:
        for srv, thread in ((srv_a, thread_a), (srv_b, thread_b)):
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)


def _event_window(job_dir: Path) -> "tuple[int, int]":
    """事象を確かに含み、かつ上限に収まる窓を**成果物から**決める。

    なぜ「先頭から N 割」のような固定の切り方にしないか（実測・2026-09-10）:
        このプロファイル（`open_only`）は評価点がバー 1 本につき 1 つなので、同一バー内で
        開いて閉じる往復は open_count に現れない。実際 36,019 点のうち open_count の
        変化は **2 回**（idx 2 で建ち、idx 9,381 の stop-out halt で落ちる）だけである
        （`stats.trades` は 1,107 だが、その大半はバー内で完結している）。時間の割合で
        窓を切ると事象 0 件の区間を掴み、「事象が返る」という表明が**恒真**になる。

    そこで、どこに事象があるかは成果物を読んで決め、**API がそれを返せるか**を測る。
    窓の決め方はテストの都合、測っているのは HTTP 経路である。
    """
    import pandas as pd

    frame = pd.read_parquet(
        job_dir / POINTS_FILENAME, columns=["time", "open_count", "halted"]
    )
    changed = frame.index[
        (frame["open_count"].diff().fillna(0) != 0)
        | (frame["halted"].astype(int).diff().fillna(0) != 0)
    ]
    assert len(changed) >= 2, "成果物に状態変化が 2 件未満（fixture が事象を生んでいない）"
    first, last = int(changed[0]), int(changed[-1])
    # 変化点を全部囲む窓が上限に収まるならそれを使う（収まらなければ末尾側へ寄せる）。
    if last - first + 1 <= MAX_RETURNED_ROWS - 2:
        lo = max(0, first - 1)
        hi = min(len(frame) - 1, last + 1)
    else:
        half = MAX_RETURNED_ROWS // 4
        lo, hi = max(0, last - half), min(len(frame) - 1, last + half)
    return int(frame["time"].iloc[lo]), int(frame["time"].iloc[hi]) + 1


# ---- 1: 端から端まで通っている ----

@_needs_mt5_fixture
class TestTheAnalysisApiIsWiredEndToEnd:
    def test_the_extent_comes_back_from_the_real_artefact(self, wired):
        # Arrange
        base, _before, job_id, _app, _root = wired

        # Act
        status, body = _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/extent")

        # Assert
        assert status == 200, body
        payload = json.loads(body)
        assert payload["ok"] is True
        assert payload["rows"] > 0, "実 run の記録が 0 行（何も測れていない）"
        assert payload["time_unit"] == "epoch_millis"
        # §9.0: time は epoch ミリ秒。秒だった時期の値の 1000 倍の桁になる。
        assert len(str(payload["first_time"])) == 13, payload["first_time"]

    def test_the_margin_floor_reaches_the_api_from_the_runs_own_spec(self, wired):
        """閾値は run が設定した stop_out_level（分析側が発明しない）。"""
        # Arrange
        base, _before, job_id, _app, _root = wired

        # Act
        payload = json.loads(
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/extent")[1]
        )

        # Assert
        assert payload["margin_level_floor"] == _STOP_OUT_LEVEL

    def test_the_points_come_back_with_columns_events_and_drawdown(self, wired):
        # Arrange
        base, _before, job_id, _app, _root = wired
        start, end = _event_window(_root / job_id)

        # Act
        status, body = _request(
            base, f"{TRACE_PATH_PREFIX}/{job_id}/points/{start}/{end}"
        )

        # Assert
        assert status == 200, body
        payload = json.loads(body)
        assert payload["rows"] > 0
        assert len(payload["columns"]["equity"]) == payload["rows"]
        assert len(payload["columns"]["margin_level"]) == payload["rows"]
        assert "equity_dd_maximal" in payload["drawdown"]
        # 正の対照: 事象が 1 件も出ないなら導出が呼ばれていないかもしれない。
        assert payload["events"], "事象が 0 件（導出まで結線が届いていない疑い）"

    def test_the_real_response_is_strict_json_a_browser_can_parse(self, wired):
        """実 run の応答が**厳格な JSON**であること（pre-mortem で見つかった欠陥）。

        `Account.margin_level()` は建玉 0 の点で `math.inf` を返し、実ティック 1 ヶ月 run
        では 1,036,394 点のうち 405,941 点（39.2%）がそれに当たる。`json.dumps` の既定は
        これを `Infinity` と書くが、その綴りは JSON の文法に無く、ブラウザの JSON.parse
        は `SyntaxError` で落ちる。**Python の `json.loads` は受理してしまう**ので、
        ここでは `parse_constant` で厳格に読む（Python だけで測ると緑のまま通る欠陥）。
        """
        # Arrange
        base, _before, job_id, _app, _root = wired
        start, end = _event_window(_root / job_id)

        def reject(token):
            raise AssertionError(f"JSON でない綴りが応答に含まれています: {token}")

        # Act
        _status, body = _request(
            base, f"{TRACE_PATH_PREFIX}/{job_id}/points/{start}/{end}"
        )

        # Assert
        payload = json.loads(body.decode("utf-8"), parse_constant=reject)
        # 正の対照: 空でない実データを読んでいる。
        assert payload["rows"] > 0
        # 維持率に非有限値が実在する run で測っている（null が出ていること）。
        assert None in payload["columns"]["margin_level"], (
            "この窓に建玉 0 の点が無く、非有限値の経路を通っていない"
        )

    def test_the_window_narrows_what_comes_back(self, wired):
        # Arrange
        base, _before, job_id, _app, _root = wired
        extent = json.loads(
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/extent")[1]
        )
        start, end = _event_window(_root / job_id)
        wide = json.loads(
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/points/{start}/{end}")[1]
        )

        # Act: 窓を半分にする。
        half = start + (end - start) // 2
        status, body = _request(
            base, f"{TRACE_PATH_PREFIX}/{job_id}/points/{start}/{half}"
        )

        # Assert
        assert status == 200
        narrowed = json.loads(body)
        assert 0 < narrowed["rows"] < wide["rows"], (narrowed["rows"], wide["rows"])
        assert all(start <= t < half for t in narrowed["columns"]["time"])
        # 窓は run 全長より確かに狭い（全量が返っていない）。
        assert wide["rows"] < extent["rows"]

    def test_the_events_can_be_reconciled_with_the_trades_of_the_same_run(self, wired):
        """事象一覧が確定トレード（stats.json）と突合できること（§9.1）。

        report.json は集約（meta / segments / summary / verdict）であってトレードの
        並びを持たない。件数の権威は stats.json である（実測）。
        """
        # Arrange
        base, _before, job_id, _app, _root = wired
        stats = json.loads(
            (_root / job_id / "stats.json").read_text(encoding="utf-8")
        )
        # 全区間は上限を超えるので、窓ぶんで測る。
        start, end = _event_window(_root / job_id)
        payload = json.loads(
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/points/{start}/{end}")[1]
        )

        # Act
        opened = [e for e in payload["events"] if e["kind"] == "position_opened"]
        closed = [e for e in payload["events"] if e["kind"] == "position_closed"]

        # Assert: 同じ run の 2 つの見方が矛盾しない。
        assert opened and closed, (len(opened), len(closed))
        assert stats["stats"]["trades"] > 0, "比較対象の確定トレードが 0 件"
        # 窓ぶんの建玉開始は run 全体の確定トレード数を超えない。
        assert len(opened) <= stats["stats"]["trades"]
        # 事象の時刻は窓の中にある（列と事象が同じ読みから来ている）。
        assert all(start <= e["time"] < end for e in payload["events"])

    def test_a_window_covering_the_whole_run_is_refused_not_thinned(self, wired):
        """全区間は上限超過で **413**。黙って間引かない（§9.0 と同型の対症療法の禁止）。"""
        # Arrange
        base, _before, job_id, _app, _root = wired
        extent = json.loads(
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/extent")[1]
        )

        # Act
        status, body = _request(
            base,
            f"{TRACE_PATH_PREFIX}/{job_id}/points/{UNBOUNDED_TOKEN}/{UNBOUNDED_TOKEN}",
        )

        # Assert
        assert extent["rows"] > extent["max_returned_rows"], extent["rows"]
        assert status == 413, body
        # front が「窓を狭めよ」と表示できる（件数が入っている）。
        assert str(extent["rows"]) in json.loads(body)["error"]


# ---- 2: 増えたのは 1 本だけ・既存は 1 バイトも動かない ----

class TestTheWrapperAddsExactlyOneRouteAndChangesNothingElse:
    def test_the_inner_surface_has_no_trace_route(self, wired):
        # Arrange
        _base, before, job_id, _app, _root = wired

        # Act
        status, _body = _request(before, f"{TRACE_PATH_PREFIX}/{job_id}/extent")

        # Assert: 内側にはこの経路が無い（増分がこの 1 本であることの表明）。
        assert status == 404

    @pytest.mark.parametrize(
        "path", ["/settings-schema", "/run-options", "/index.html"]
    )
    def test_the_existing_routes_are_byte_identical(self, wired, path):
        # Arrange
        base, before, _job, _app, _root = wired

        # Act
        after_status, after_body = _request(base, path)
        before_status, before_body = _request(before, path)

        # Assert
        assert (after_status, after_body) == (before_status, before_body)
        # 正の対照: 空応答同士を比べていない。
        assert len(after_body) > 0

    def test_a_prefix_neighbour_falls_through_to_the_static_surface(self, wired):
        """`/trace-extra` のような別資産を JSON 経路へ吸い込まない（prefix 境界）。"""
        # Arrange
        base, before, _job, _app, _root = wired

        # Act / Assert
        assert _request(base, "/trace-extra.js") == _request(before, "/trace-extra.js")


# ---- 3: front とサーバが同じ宣言を読む（口だけ作らない） ----

_FRONT_DIR = _SIM_WEB / "js" / "adapter" / "front"
#: 分析タブの front 3 点（通信・面・結線）。責務が分かれているので走査先も分ける。
_TRACE_CLIENT = _FRONT_DIR / "trace_analysis_client.js"
_TRACE_VIEW = _FRONT_DIR / "sim_trace_view.js"
_ANALYSIS_ROOT = _FRONT_DIR / "composition_root_analysis.js"


class TestTheFrontAndTheServerAgreeOnThePath:
    """ISSUE-291 の壊れ方は「サーバ分岐を作っても front が送らなければ無言で死ぬ」である。

    両者が同じ綴りを使っていることを機械的に固定する。
    """

    def test_the_display_root_calls_the_analysis_root_as_a_statement(self):
        """呼び出しが**文として**在ること（自分の変異試験で判明した穴の是正）。

        当初は `mountTraceAnalysis(` の出現だけを見ていたため、
        `void 0 && mountTraceAnalysis({...})` のように**到達しない形**へ変えても
        緑のまま通った。文としての形（先頭が void / await / 素の呼び出し）に限る。

        **担保の分担（実在する検定を名指す・工程 5 レビュー 🔴-2 の是正）**:
        静的検査で「到達可能」は証明できない（`if (false) { ... }` で囲む変異は本検定を
        通る・実測）。到達可能性は
        `simulator/sim_ui/tests/e2e/verify_sim_display_parity_peripheral.py` の **P19**
        （実 chromium で分析ペインに面が生えることを観測する）が担う。工程 3 は
        `verify_sim_display_parity.py` を担保先として名指したが、**同ファイルには
        analysis / trace の参照が 0 件**であり担保は存在しなかった（実証なき担保先の記述）。

        本検定を残す理由（P19 との重複ではない）: P19 は chromium 不在環境で skip し、
        既定の pytest 収集にも載らない（`verify_*.py` 命名・ISSUE-378 #2）。本検定は
        既定スイートで常に走る近似として「呼び出しが式の一部へ潰されていないこと」を固定する。
        """
        # Arrange
        import re

        source = (_FRONT_DIR / "composition_root_front.js").read_text(encoding="utf-8")

        # Act
        # di-ok(C2): 呼び出しの形は JS を実行しない本検定では走査でしか固定できない
        statements = re.findall(
            r"^[ \t]*(?:void |await )?mountTraceAnalysis\(\{", source, re.M
        )

        # Assert
        assert len(statements) == 1, statements
        # di-ok(C2): 分析ペインを渡していることも静的な性質である
        assert "pane: view.elements.paneAnalysis" in source

    def test_the_front_client_uses_the_declared_prefix_and_token(self):
        # Arrange
        source = _TRACE_CLIENT.read_text(encoding="utf-8")

        # Act / Assert: サーバが受ける根と、無制限を表すトークンが front と一致する。
        # di-ok(C2): Python と JS の**宣言の一致**は、JS を実行しないこの検定では
        #   ソース走査でしか固定できない（構造禁止と同型・先例 tools/tests/test_mt5_tick_feed.py）。
        # di-ok(C2): Python と JS の宣言一致・写しの不在は、JS を実行しない本検定では走査でしか固定できない
        assert TRACE_PATH_PREFIX in source, TRACE_PATH_PREFIX
        # di-ok(C2): Python と JS の宣言一致・写しの不在は、JS を実行しない本検定では走査でしか固定できない
        assert f'"{UNBOUNDED_TOKEN}"' in source, UNBOUNDED_TOKEN

    def test_the_front_does_not_hand_write_the_column_names(self):
        """列名はサーバが `extent` で配る宣言から取る（2 箇所化させない）。"""
        # Arrange
        from simulator.sim_ui.usecase.query_trace import ANALYSIS_COLUMNS

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (_TRACE_CLIENT, _TRACE_VIEW, _ANALYSIS_ROOT)
        )

        # Act: 宣言の列名がリテラルとして front に並んでいないこと。
        # time は横軸として名指しする 1 つだけ許す（系列にしない列の指定であり、
        # 「どの列を描くか」の写しではない）。
        # di-ok(C2): 「宣言の写しが front に無い」は**不在**の主張であり、実行では
        #   観測できない（写しが在っても動くから通る）。走査でしか固定できない。
        listed = [
            name for name in ANALYSIS_COLUMNS
            if f'"{name}"' in source and name != "time"
        ]

        # Assert
        assert listed == [], listed

    def test_the_front_does_not_hand_write_the_event_kinds(self):
        """事象種別も front に写さない（凡例はサーバの `event_kinds` から）。"""
        # Arrange
        from simulator.sim_ui.usecase.derive_trace_events import EVENT_KINDS

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (_TRACE_CLIENT, _TRACE_VIEW, _ANALYSIS_ROOT)
        )

        # Act / Assert
        # di-ok(C2): 上と同じ理由（写しの不在は走査でしか固定できない）。
        listed = [kind for kind in EVENT_KINDS if f'"{kind}"' in source]
        assert listed == [], listed

    def test_the_front_does_not_compute_the_window_from_the_cap(self):
        """窓はサーバの答え（`suggested_window`）を読むだけ（front が計算しない）。

        実測（2026-09-10）で分かったこと: 「上限 ÷ 全行数」の比で幅を決める形は、
        ティック密度が一様でない実 run（1,036,394 行）で 59,030 行の窓を作り、初回表示が
        413 になった。**front は上限を知る必要がない**——どの窓なら返せるかは行の分布を
        持つサーバが測って答える。したがって上限のリテラルも `max_returned_rows` の
        参照も front には無いのが正しい状態である（判断の所有者を 1 つに保つ）。
        """
        # Arrange
        from simulator.sim_ui.usecase.query_trace import MAX_RETURNED_ROWS

        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (_TRACE_CLIENT, _TRACE_VIEW, _ANALYSIS_ROOT)
        )

        # Act / Assert
        # di-ok(C2): 上限リテラルの不在・提案窓の参照はいずれも静的な性質である
        assert str(MAX_RETURNED_ROWS) not in source
        # di-ok(C2): 上限を front が読んでいないこと（判断の 2 つ目の所有者を作らない）
        assert "max_returned_rows" not in source
        # di-ok(C2): サーバの答えを読んでいること（窓を計算していないことの裏返し）
        assert "suggested_window" in source, "サーバの提案窓を読んでいない"

# ---- 4: 失敗は値でなく状態で区別できる ----

class TestTheFailuresAreDistinguishable:
    def test_a_run_without_a_trace_is_not_found(self, tmp_path):
        # Arrange
        data_root = tmp_path / "data"
        data_root.mkdir()
        job_id = "ae" + "0" * 30
        job_dir = data_root / job_id
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps({"backtest": {}, "trace": None}), encoding="utf-8"
        )
        (job_dir / "state.json").write_text(
            json.dumps(
                {"job_id": job_id, "status": "completed", "failure_reason": None}
            ),
            encoding="utf-8",
        )
        app = build_sim_display_app(
            repo_root=_ROOT, web_dir=_SIM_WEB, data_root=data_root
        )
        srv, thread, base = _serve(app)

        # Act / Assert
        try:
            status, body = _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/extent")
            assert status == 404
            # 「0 行だった」と区別できる（別の事実である）。
            assert "トレース" in json.loads(body)["error"]
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=2)

    def test_a_bad_window_is_a_bad_request(self, wired):
        # Arrange
        base, _before, job_id, _app, _root = wired

        # Act / Assert
        assert (
            _request(base, f"{TRACE_PATH_PREFIX}/{job_id}/points/abc/def")[0] == 400
        )

    def test_an_unknown_job_is_not_found(self, wired):
        # Arrange
        base, _before, _job, _app, _root = wired

        # Act / Assert
        assert (
            _request(
                base,
                f"{TRACE_PATH_PREFIX}/{'f' * 32}/extent",
            )[0]
            == 404
        )
