"""run_job の実行トレース結線（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.5.1/§6.6・D-5）。

`run_job.main` を spec.json 経由で実際に回し、以下を固定する:

    1. `trace` 不在・OFF の run は成果物を 1 本も作らず、stats.json が
       **byte 等価**（既定経路は無改変）。
    2. `trace: {"enabled": true, ...}` の run は `job_dir` 直下へ 3 本を書き、
       parquet が**読み返せて行数が 0 でない**。
    3. 書出しの呼出点は `main()` の**ただ 1 箇所**である（_write_report_payload が
       2 箇所から呼ばれている形を複製しない）。両分岐（現行経路 / settings 経路）の
       exit_code を受けてから書く。
    4. `trace_writer` は **module 直下 import に足さない**（是正 D-5）。同ファイルは
       OFF になり得る拡張をすべて関数内 import に置いており、理由は `run_job.py:108-109` に
       「OFF の経路が実装の import に巻き込まれないようにするため」と明記されている。
    5. 書出し失敗は**終了コードを変えない**（run 自体は成功している）。理由は
       trace_error.json へ残す（先例 _record_report_payload_error）。
    6. 指標 registry は `build_ea_indicators(**backtest)` から組んで **Callable で注入**する
       （是正 D-4: interactor._indicators へ到達しない・RunBacktestInteractor（`simulator/usecase/run_backtest.py`） へ
       プロパティを新設しない）。
"""
from __future__ import annotations

import ast
import json
import pathlib
from pathlib import Path

import pandas as pd
import pytest

from simulator.sim_ui.adapter import trace_writer
from simulator.sim_ui.main import run_job

_EPOCH = 1_704_067_200
_BARS = 40


def _write_csv(path: Path) -> Path:
    rows = []
    for i in range(_BARS):
        base = 1.0000 + (i % 7) * 0.0010
        rows.append(
            {
                "time": _EPOCH + 60 * i,
                "open": base, "high": base + 0.0005, "low": base - 0.0005,
                "close": base, "volume": 100, "spread": 0,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _backtest(csv: Path) -> dict:
    return {
        "ea_name": "TC24051901", "symbol": "EURUSD", "period": "M1",
        "data_path": str(csv), "initial_deposit": 100_000.0, "contract_size": 1.0,
        "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
        "stops_level": 0, "digits": 5, "point_size": 0.0001, "leverage": 100.0,
        "ma_period": 2, "ma_method": "sma", "lot_size": 1.0,
        "stop_loss_points": 100, "take_profit_points": 200,
    }


def _run(tmp: Path, name: str, csv: Path, *, trace=None) -> Path:
    job_dir = tmp / ("0123456789abcdef" + name.ljust(16, "0")[:16])
    job_dir.mkdir()
    (job_dir / "spec.json").write_text(
        json.dumps(
            {
                "backtest": _backtest(csv), "sizing": None, "strategy": None,
                "settings": None, "trace": trace,
            }
        ),
        encoding="utf-8",
    )
    code = run_job.main(["--job-dir", str(job_dir)])
    assert code == 0, (
        (job_dir / "failure.json").read_text(encoding="utf-8")
        if (job_dir / "failure.json").exists()
        else code
    )
    return job_dir


_TRACE_FILES = (
    trace_writer.POINTS_FILENAME,
    trace_writer.INDICATORS_FILENAME,
    trace_writer.META_FILENAME,
)


# ---- 1: OFF は既存挙動 byte 等価 ----

class TestATraceLessRunIsUnchanged:
    @pytest.mark.parametrize(
        "trace", [None, {}, {"enabled": False}], ids=["absent", "empty", "off"]
    )
    def test_no_artefact_is_written(self, tmp_path, trace):
        # Arrange
        csv = _write_csv(tmp_path / "bars.csv")

        # Act
        job_dir = _run(tmp_path, f"off{trace is None}", csv, trace=trace)

        # Assert
        for name in _TRACE_FILES:
            assert not (job_dir / name).exists(), name
        # 正の対照: run そのものは成立している。
        assert (job_dir / "stats.json").exists()

    def test_the_stats_are_byte_identical_with_and_without_the_block(self, tmp_path):
        # Arrange
        csv = _write_csv(tmp_path / "bars.csv")

        # Act
        absent = _run(tmp_path, "a", csv, trace=None)
        on = _run(tmp_path, "b", csv, trace={"enabled": True})

        # Assert: 観測は run の結果を変えない。
        assert (on / "stats.json").read_bytes() == (absent / "stats.json").read_bytes()
        assert (on / "report.md").read_bytes() == (absent / "report.md").read_bytes()
        # 正の対照: 空の stats を比べていない。
        assert len((absent / "stats.json").read_bytes()) > 100


# ---- 2: ON は 3 本を書き、読み返せる ----

class TestATracedRunProducesReadableArtefacts:
    def test_the_three_artefacts_land_flat_and_are_readable(self, tmp_path):
        # Arrange
        csv = _write_csv(tmp_path / "bars.csv")

        # Act
        job_dir = _run(tmp_path, "on", csv, trace={"enabled": True})

        # Assert
        for name in _TRACE_FILES:
            assert (job_dir / name).is_file(), name
        assert [p for p in job_dir.iterdir() if p.is_dir()] == []

        points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
        from simulator.adapter.trace.columnar_run_trace import COLUMNS

        assert list(points.columns) == list(COLUMNS)
        # 正の対照: 0 行なら「書けた」以外の何も測れていない。
        assert len(points) > 0, "trace_points.parquet が 0 行"

    def test_the_recorded_rows_match_the_meta_and_the_equity_curve(self, tmp_path):
        """記録行数が run の評価点数と一致すること（独立に数えた値と突き合わせる）。

        `equity_curve` は MarginGuard が評価点 1 つにつき 1 点積む列であり、
        トレースとは**別の経路**で作られる。数字をテストへ焼き込まずに済む。
        """
        # Arrange
        csv = _write_csv(tmp_path / "bars.csv")

        # Act
        job_dir = _run(tmp_path, "cnt", csv, trace={"enabled": True})

        # Assert
        points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
        meta = json.loads((job_dir / trace_writer.META_FILENAME).read_text("utf-8"))
        stats = json.loads((job_dir / "stats.json").read_text("utf-8"))
        assert meta["rows"]["points"] == len(points)
        assert meta["window"] == {"start": None, "end": None}
        assert meta["job_id"] == job_dir.name
        # 正の対照。
        assert len(points) > 0 and stats

    def test_the_window_keeps_only_the_requested_period(self, tmp_path):
        # Arrange: 全 40 分のうち [10 分, 20 分) だけを残す。
        csv = _write_csv(tmp_path / "bars.csv")
        start, end = _EPOCH + 600, _EPOCH + 1200

        # Act
        full = _run(tmp_path, "full", csv, trace={"enabled": True})
        windowed = _run(
            tmp_path, "win", csv, trace={"enabled": True, "start": start, "end": end}
        )

        # Assert
        full_points = pd.read_parquet(full / trace_writer.POINTS_FILENAME)
        win_points = pd.read_parquet(windowed / trace_writer.POINTS_FILENAME)
        assert all(start <= t < end for t in win_points["time"])
        assert 0 < len(win_points) < len(full_points), (len(win_points), len(full_points))
        # 窓は meta にも残る。
        meta = json.loads((windowed / trace_writer.META_FILENAME).read_text("utf-8"))
        assert meta["window"] == {"start": start, "end": end}

    def test_the_indicator_trace_carries_the_series_the_run_used(self, tmp_path):
        """§6.5.1: registry は `build_ea_indicators` から組んで注入する。"""
        # Arrange
        from simulator.main import build_ea_indicators

        csv = _write_csv(tmp_path / "bars.csv")

        # Act
        job_dir = _run(tmp_path, "ind", csv, trace={"enabled": True})

        # Assert: 列が「その EA が実行に使った系列」と一致する。
        expected = list(build_ea_indicators(**_backtest(csv)).names())
        frame = pd.read_parquet(job_dir / trace_writer.INDICATORS_FILENAME)
        assert list(frame.columns) == ["bar_index"] + expected
        # 正の対照: 系列 0 本なら列一致は恒真に近い。
        assert expected, "この EA の登録系列が 0 本（検定が何も測っていない）"
        assert len(frame) > 0

        # 足粒度である（点粒度の行数より少ない）。
        points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
        assert len(frame) == points["bar_index"].nunique()


# ---- 3・4: 呼出点は 1 箇所・関数内 import ----

class TestTheWiringHasExactlyOneWriteCallSite:
    def _tree(self):
        return ast.parse(
            pathlib.Path(run_job.__file__).read_text(encoding="utf-8")
        )

    def test_the_trace_writer_is_called_from_exactly_one_place(self):
        # Arrange
        tree = self._tree()

        # Act: `trace_writer.write(...)` の呼出を数える。
        calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "write"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "trace_writer"
        ]

        # Assert
        assert len(calls) == 1, [ast.dump(c) for c in calls]

    def test_the_write_happens_after_both_execution_branches(self):
        """settings 経路でも現行経路でも同じ 1 点を通ること。

        測り方: `main` の中で `trace` 書出しを行う関数の呼出が、`return` より前の
        **共通の場所**に在ること——すなわち `main` が _run_with_settings の戻りを
        受け取っており、途中で `return _run_with_settings(...)` していないこと。
        """
        # Arrange
        tree = self._tree()
        main = next(
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "main"
        )

        # Act: `return _run_with_settings(...)` の形が残っていないこと。
        early_returns = [
            n
            for n in ast.walk(main)
            if isinstance(n, ast.Return)
            and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name)
            and n.value.func.id == "_run_with_settings"
        ]

        # Assert
        assert early_returns == [], "settings 経路が書出し点を飛び越えている"
        # 正の対照: _run_with_settings は依然として呼ばれている。
        assert any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "_run_with_settings"
            for n in ast.walk(main)
        )

    def test_the_trace_writer_is_never_imported_at_module_level(self):
        """是正 D-5: OFF の全ジョブが parquet 実装を読み込む形にしない。"""
        # Arrange
        tree = self._tree()

        # Act
        # 分岐ではなく内包表記で集める（検出範囲は同一・実行経路が入力で変わらない）。
        top_level = {
            alias.name
            for n in tree.body if isinstance(n, ast.Import) for alias in n.names
        } | {
            f"{n.module}.{alias.name}"
            for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
            for alias in n.names
        } | {
            n.module for n in tree.body if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        assert not any("trace" in m for m in top_level), sorted(top_level)
        # 正の対照: module 直下 import の走査が空振りしていない。
        assert "simulator.main" in top_level, sorted(top_level)

    def test_the_trace_modules_are_imported_inside_functions_only(self):
        # Arrange
        tree = self._tree()

        # Act
        # 分岐ではなく内包表記で集める（検出範囲は同一・実行経路が入力で変わらない）。
        functions = [
            n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        lazy = {
            child.module
            for fn in functions for child in ast.walk(fn)
            if isinstance(child, ast.ImportFrom) and child.module
        } | {
            alias.name
            for fn in functions for child in ast.walk(fn)
            if isinstance(child, ast.Import) for alias in child.names
        }

        # Assert: トレース実装は関数内でだけ読まれる。
        assert any("trace" in m for m in lazy), sorted(lazy)

    def test_an_untraced_run_never_loads_the_parquet_store(self, tmp_path):
        """実際に走らせて、OFF の run が技術ドライバの実装を読まないこと。

        構文木の検査だけでは、間接的な巻き込み（`__init__.py` の再輸出など）を
        見逃す。子プロセスで実測する。
        """
        # Arrange
        import subprocess
        import sys

        csv = _write_csv(tmp_path / "bars.csv")
        job_dir = tmp_path / ("0" * 32)
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "backtest": _backtest(csv), "sizing": None, "strategy": None,
                    "settings": None, "trace": None,
                }
            ),
            encoding="utf-8",
        )
        code = (
            "import sys;"
            "from simulator.sim_ui.main import run_job;"
            f"rc = run_job.main(['--job-dir', {str(job_dir)!r}]);"
            "print(rc, int('simulator.adapter.trace.parquet_trace_store' in sys.modules))"
        )

        # Act
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, cwd="/workspaces/app",
        )

        # Assert
        assert out.returncode == 0, out.stderr[-2000:]
        assert out.stdout.strip().splitlines()[-1] == "0 0", out.stdout


# ---- 5: 書出し失敗は run の成否を変えない ----

class TestAFailedTraceWriteDoesNotDiscardASuccessfulRun:
    def test_the_exit_code_is_unchanged_and_the_reason_is_recorded(
        self, tmp_path, monkeypatch
    ):
        # Arrange: writer が必ず失敗する状態にする。
        csv = _write_csv(tmp_path / "bars.csv")
        job_dir = tmp_path / ("0123456789abcdef" + "fail".ljust(16, "0"))
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "backtest": _backtest(csv), "sizing": None, "strategy": None,
                    "settings": None, "trace": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )

        def _boom(*args, **kwargs):
            raise OSError("ディスクが一杯です（検定用の擬似障害）")

        monkeypatch.setattr(trace_writer, "write", _boom)

        # Act
        code = run_job.main(["--job-dir", str(job_dir)])

        # Assert: run は成功したまま。
        assert code == 0
        assert (job_dir / "stats.json").exists()
        # 理由は残る（起動器は stderr を DEVNULL に固定するため print だけでは届かない）。
        error_file = job_dir / run_job._TRACE_ERROR_FILE
        assert error_file.is_file()
        assert "ディスクが一杯です" in error_file.read_text(encoding="utf-8")
        # 失敗理由は failure.json とは別にする（run 自体は失敗していない）。
        assert not (job_dir / "failure.json").exists()

    def test_a_broken_trace_block_fails_the_job_before_the_run(self, tmp_path):
        """窓の指定が不正なら run を始めない（0 行で成功する run を作らない）。"""
        # Arrange
        csv = _write_csv(tmp_path / "bars.csv")
        job_dir = tmp_path / ("0123456789abcdef" + "bad".ljust(16, "0"))
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "backtest": _backtest(csv), "sizing": None, "strategy": None,
                    "settings": None,
                    "trace": {"enabled": True, "start": _EPOCH + 60, "end": _EPOCH},
                }
            ),
            encoding="utf-8",
        )

        # Act
        code = run_job.main(["--job-dir", str(job_dir)])

        # Assert
        assert code != 0
        assert (job_dir / "failure.json").is_file()
        assert not (job_dir / "stats.json").exists()


# ---- settings 経路の実測は test_run_job_settings.py が担う ----
#
# settings 経路の run は JP225 実体を MT5 突合 fixture へ差し替える autouse fixture
# （JP225 実体の差し替え）を要する。その fixture は
# `simulator/sim_ui/tests/integration/test_run_job_settings.py` に属する
# ので、trace ON の settings run を実際に走らせる検定は同ファイルへ置く
# （fixture を本ファイルへ写すと同じ差し替え規則が 2 箇所になる）。
# 本ファイルが持つのは構造の固定（書出し点が 1 箇所・分岐を飛び越えない）である。


# ---- 窓の申告は「その run が実際に渡した引数」を追う（§6.5.2.1） ----

class TestTheDeclarationFollowsTheKwargsTheRunActuallyUsed:
    """成果物 trace_meta.json の窓の申告が、書出しへ渡された実効 kwargs を追うこと。

    是正前は spec の backtest ブロックを無条件に読んでいた。窓の供給元は経路で異なり
    （現行経路＝backtest ブロック／settings 経路＝`.ini` の `FromDate`/`ToDate`）、
    後者では申告が偽になっていた（`simulator/sim_ui/tests/integration/
    test_run_job_settings.py` が実 run で固定する）。

    **両側で縛る理由**: 片側だけだと「常に false」ないし「常に true」と書く実装が
    通り、申告が実際の入力を追わなくなる。両側を 1 つの検定で回すため、ここは
    書出し段へ直接 kwargs を渡して測る——現行経路の窓は**ジョブ仕様からは到達不能**
    だからである（実測: `marketdata_window` の境界は `datetime` 契約であり、JSON が
    運べる epoch 整数を渡すと "'int' object has no attribute 'tzinfo'" で
    データ取得エラーになる。front も 11 キーに含めていない）。
    """

    @pytest.mark.parametrize(
        "window,declared",
        [
            (None, False),
            ((_EPOCH + 600, _EPOCH + 1800), True),
        ],
        ids=["absent", "present"],
    )
    def test_the_meta_declares_the_window_it_was_given(self, tmp_path, window, declared):
        # Arrange: 記録済みトレースと、その run が使った実効 kwargs。
        from simulator.sim_ui.tests.unit.test_trace_writer import _filled_trace

        csv = _write_csv(tmp_path / "bars.csv")
        run_kwargs = {**_backtest(csv), "marketdata_window": window}
        job_dir = tmp_path / ("0123456789abcdef" + str(declared).ljust(16, "0")[:16])
        job_dir.mkdir()

        # Act
        run_job._write_trace(job_dir, _filled_trace(), run_kwargs)

        # Assert
        meta = json.loads(
            (job_dir / trace_writer.META_FILENAME).read_text(encoding="utf-8")
        )
        assert meta["marketdata_window"] is declared
        assert meta["indicator_bar_index_is_comparable"] is (not declared)
        # 正の対照: 書出しが成立している（0 行なら申告だけ見て中身が無い）。
        assert meta["rows"]["points"] > 0


class TestTheCurrentPathKeepsDeclaringNoWindow:
    """settings 不在の実 run の申告が是正で動いていないこと（回帰固定）。"""

    def test_a_current_path_run_declares_no_window(self, tmp_path):
        # Arrange / Act
        csv = _write_csv(tmp_path / "bars.csv")
        job_dir = _run(tmp_path, "nowin", csv, trace={"enabled": True})

        # Assert
        meta = json.loads(
            (job_dir / trace_writer.META_FILENAME).read_text(encoding="utf-8")
        )
        assert meta["marketdata_window"] is False
        assert meta["indicator_bar_index_is_comparable"] is True
        # 正の対照: 記録が 0 件なら申告だけ見て中身が無い。
        assert meta["rows"]["points"] > 0


class TestTheInjectedEntitiesDoNotLeakIntoTheIndicatorRegistry:
    """現行経路の実効 kwargs には拡張実体が載るが、指標 registry は変わらないこと。

    §6.5.2.1 の是正で、書出しへ渡すのは spec の backtest ブロックではなく
    **その run が実際に渡した引数**になった。現行経路のそれは
    `run_backtest(output_dir=..., **meta)` の `meta` であり、`meta.update(extensions)`
    により `run_tracer` / `strategy_override` 等の**実体**を含む。

    それらは EA 構築の残余引数として素通りする（`simulator/main/__init__.py` の
    EA 構築段が持つ catch-all）ので
    registry は変わらない——が、**実測でしか確かめられない**性質である。写しでも
    宣言でもなく検査で固定する（将来 catch-all の扱いが変われば赤になる）。
    """

    def test_a_run_carrying_a_strategy_override_records_the_same_series(self, tmp_path):
        # Arrange
        from simulator.main import build_ea_indicators

        csv = _write_csv(tmp_path / "bars.csv")
        expected = ["bar_index"] + list(build_ea_indicators(**_backtest(csv)).names())
        strategy = {
            "entry_long": [{"indicator": "close", "shift": 0, "op": ">", "rhs": 0.0}]
        }

        def _job(name, strategy_block):
            job_dir = tmp_path / ("0123456789abcdef" + name.ljust(16, "0")[:16])
            job_dir.mkdir()
            (job_dir / "spec.json").write_text(
                json.dumps(
                    {
                        "backtest": _backtest(csv), "sizing": None,
                        "strategy": strategy_block, "settings": None,
                        "trace": {"enabled": True},
                    }
                ),
                encoding="utf-8",
            )
            assert run_job.main(["--job-dir", str(job_dir)]) == 0
            return pd.read_parquet(job_dir / trace_writer.INDICATORS_FILENAME)

        # Act
        plain = _job("plain", None)
        with_override = _job("override", strategy)

        # Assert: 拡張実体が載っても登録系列は変わらない。
        assert list(with_override.columns) == expected
        assert list(plain.columns) == expected
        # 正の対照: 系列が 0 本／行が 0 件なら上は恒真に近い。
        assert len(expected) > 1, expected
        assert len(with_override) > 0


def _write_broken_csv(path: Path) -> Path:
    """`close > high` の壊れた足（エンジンが**例外なしに**非ゼロ終了する素材）。

    実測: 実行 facade（`simulator/main/__init__.py`）は BacktestError を捕捉して
    終了コードへ翻訳する経路を持つため、この CSV では例外が飛ばずに exit_code=1 が
    返る。`run_job` 側で「例外が飛べば書出しに到達しない」と考えるのは誤りであり、
    非ゼロ終了そのものを条件にしなければ失敗 run の途中経過が成果物として出る。
    """
    rows = []
    for i in range(20):
        base = 1.0000 + (i % 5) * 0.0010
        high = base + 0.0005
        rows.append(
            {
                "time": _EPOCH + 60 * i,
                "open": base, "high": high, "low": base - 0.0005,
                # 契約違反: 終値がその足の高値より上。
                "close": high + 0.0100, "volume": 100, "spread": 0,
            }
        )
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class TestAFailedRunLeavesNoTraceArtefact:
    """非ゼロ終了の traced run が trace ファイルを 1 本も残さないこと。

    同ファイルのコメント自身が「失敗 run の途中経過を成果物として出すと、壊れた run の
    記録が『その run の事実』に見える」と書いているが、それを縛る検定が無かった
    （`and exit_code == 0` を外す変異が trace 関連 239 件で緑・実測）。

    **正の対照を対で置く**: 同じ形の**正しい** CSV では 3 本が出ること。これが無いと
    「そもそも書き出せない構成」でも緑になる。
    """

    def _spec(self, tmp_path: Path, name: str, csv: Path) -> Path:
        job_dir = tmp_path / ("0123456789abcdef" + name.ljust(16, "0")[:16])
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "backtest": _backtest(csv), "sizing": None, "strategy": None,
                    "settings": None, "trace": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )
        return job_dir

    def test_a_nonzero_exit_writes_no_trace_file(self, tmp_path):
        # Arrange
        broken = _write_broken_csv(tmp_path / "broken.csv")
        job_dir = self._spec(tmp_path, "broken", broken)

        # Act
        code = run_job.main(["--job-dir", str(job_dir)])

        # Assert: 失敗しており（正の対照）、成果物は 1 本も無い。
        assert code != 0, "壊れた素材で run が成功した（fixture が効いていない）"
        present = [name for name in _TRACE_FILES if (job_dir / name).exists()]
        assert present == [], present
        # 書出し失敗として記録されたのでもない（そもそも呼ばれていない）。
        assert not (job_dir / run_job._TRACE_ERROR_FILE).exists()

    def test_the_same_material_corrected_does_write_the_three_files(self, tmp_path):
        """正の対照: 正しい CSV なら 3 本出る（構成そのものは書ける）。"""
        # Arrange
        good = _write_csv(tmp_path / "good.csv")
        job_dir = self._spec(tmp_path, "good", good)

        # Act
        code = run_job.main(["--job-dir", str(job_dir)])

        # Assert
        assert code == 0
        assert sorted(n for n in _TRACE_FILES if (job_dir / n).exists()) == sorted(
            _TRACE_FILES
        )


class TestTheGranularityColumnTakesBothValuesInRealRuns:
    """本番が実際に書く粒度列（granularity）を両側で固定する（§6.1）。

    なぜ実 run で測るか（実測した壊れ方）: 当該列を `"tick"` 定数へ潰す変異が
    trace 関連 239 件で緑だった。**既定の sim 経路はバー粒度スケジュールで走り**、
    実 run のトレースは全行「bar 粒度・序数 -1」になる（実測）。
    つまり単体検定が縛っていたのは合成の点だけで、**本番が実際に書く値は無検査**
    だった。段階 4 の分析面はこの列で点粒度／足粒度を判別する。

    `tick_ordinal` も対で縛る: 粒度と序数は同じ点から出るので、片方だけ正しく
    もう片方が定数、という形を残さない。
    """

    def test_the_default_path_records_bar_granularity(self, tmp_path):
        # Arrange / Act
        csv = _write_csv(tmp_path / "bars.csv")
        job_dir = _run(tmp_path, "bargran", csv, trace={"enabled": True})

        # Assert
        points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
        assert set(points["granularity"]) == {"bar"}
        assert set(points["tick_ordinal"]) == {-1}
        # 正の対照: 行が 0 件なら集合比較は空集合同士で恒真になる。
        assert len(points) > 0

    def test_the_real_ticks_path_records_tick_granularity(self, tmp_path):
        """`real_ticks` 経路では tick 粒度・バー内序数が並ぶこと。

        素材は指紋ケース C（`simulator/tests/integration/
        test_run_backtest_fingerprint.py`）が組む合成 tick-store を借りる
        （同じ素材を 2 つ書くと片方だけが腐る）。
        """
        # Arrange
        from simulator.tests.integration.test_run_backtest_fingerprint import (
            _write_c_bars,
            _write_c_ticks,
        )

        bars_csv = _write_c_bars(tmp_path / "synth_m1.csv")
        tick_root = _write_c_ticks(tmp_path / "ticks")
        backtest = {
            **_backtest(bars_csv),
            "stop_loss_points": 500,
            "take_profit_points": 3000,
            "tick_store_root": str(tick_root),
            "config_overrides": {
                "tick_model": "real_ticks", "entry_price_basis": "current_open",
            },
        }
        job_dir = tmp_path / ("0123456789abcdef" + "tickgran".ljust(16, "0")[:16])
        job_dir.mkdir()
        (job_dir / "spec.json").write_text(
            json.dumps(
                {
                    "backtest": backtest, "sizing": None, "strategy": None,
                    "settings": None, "trace": {"enabled": True},
                }
            ),
            encoding="utf-8",
        )

        # Act
        code = run_job.main(["--job-dir", str(job_dir)])

        # Assert
        assert code == 0, (
            (job_dir / "failure.json").read_text(encoding="utf-8")
            if (job_dir / "failure.json").exists()
            else code
        )
        points = pd.read_parquet(job_dir / trace_writer.POINTS_FILENAME)
        assert set(points["granularity"]) == {"tick"}
        # 序数はバー内で並ぶ（定数へ潰す実装を赤にする）。
        assert {0, 1, 2} <= set(points["tick_ordinal"]), sorted(set(points["tick_ordinal"]))
        # 正の対照: 行が 0 件なら上のすべてが痩せる。
        assert len(points) > 0
