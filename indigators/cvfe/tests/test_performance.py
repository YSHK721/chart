"""非機能要件の測定（仕様 §6）。

正本仕様: indigators/cvfe/CVFE_spec_v1.0.md §6

本ファイルは仕様 §6 の各項目のうち、**本リポジトリの実行環境で測定可能なもの**を
測定して閾値を固定する。`K = 4.2e7` ティック規模（段階 0 の p99 < 180 s・メモリ 4 GB）は
実データ（`data/marketdata/ticks/`）を要するため単体テストでは扱わず、
別途バッチで測定する（内部設計書 §8 に記録）。

測定は 1 回試行の実測値であり、仕様が要求する「20 回試行の p99」ではない。
閾値は仕様値に対して十分な余裕を取り、**回帰検出**を目的とする。
"""

import sys
import time
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from cvfe_synthetic import make_dataset  # noqa: E402
from src import (  # noqa: E402
    CvfeSequential,
    compute_cvfe,
    diagnose_quality,
    fit_state,
    measure_all_bars,
)
from src.dto import CvfeParams  # noqa: E402
from src.sampling import validate_edges, validate_ticks  # noqa: E402

N_HAR = 500
BAR_SEC = 3_600


def test_single_bar_incremental_update_under_50ms():
    """1 バー増分更新の p99 < 50 ms（単一コア）（仕様 §6）。"""
    ticks, edges = make_dataset(700, bar_sec=BAR_SEC, tick_sec=5, seed=41)
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)
    measures = measure_all_bars(times, logp, e, quality, params)
    state = fit_state(measures, quality, params)

    seq = CvfeSequential(state, params)
    durations = []
    for m in measures:
        t0 = time.perf_counter()
        seq.push(m)
        durations.append(time.perf_counter() - t0)

    p99 = float(np.percentile(np.array(durations), 99)) * 1000.0
    assert p99 < 50.0, f"1 バー増分更新 p99 = {p99:.3f} ms"


def test_bulk_5000_bars_under_60s():
    """段階 2〜7 の一括実行が N = 5,000 バーで 60 s 未満（仕様 §6）。

    段階 0（気配品質診断）は別項目のため、ここでは測定対象から外し
    診断結果を再利用して段階 2〜7 のみを計測する。
    """
    n_bars = 5_000
    ticks, edges = make_dataset(n_bars, bar_sec=BAR_SEC, tick_sec=60, seed=42)
    times, logp = validate_ticks(ticks)
    e = validate_edges(edges)
    params = CvfeParams(bar_interval_sec=BAR_SEC, n_har=N_HAR)
    quality = diagnose_quality(times, logp, e, params.n_har, params.freeze_thresh)

    t0 = time.perf_counter()
    measures = measure_all_bars(times, logp, e, quality, params)
    state = fit_state(measures, quality, params)
    seq = CvfeSequential(state, params)
    for m in measures:
        seq.push(m)
    elapsed = time.perf_counter() - t0

    assert len(measures) == n_bars
    assert elapsed < 60.0, f"段階 2〜7 の一括実行 = {elapsed:.2f} s"


def test_dependency_is_numpy_only():
    """本パッケージが numpy 以外の外部ライブラリへ依存しないこと（仕様 §6）。

    文字列一致では ``from scipy import ...`` を見逃すため、AST を走査して
    実際の import 文からトップレベルのモジュール名を取り出して判定する。
    """
    import ast

    allowed_external = {"numpy"}
    stdlib_ok = {"__future__", "math", "json", "sys", "datetime", "typing",
                 "dataclasses", "pathlib", "collections", "itertools", "functools"}
    internal_prefix = "common"

    src_dir = _PKG_DIR / "src"
    files = sorted(src_dir.glob("*.py"))
    assert files, "src が空では検証にならない"
    seen_numpy = False
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:            # 相対 import（同一パッケージ内）
                    continue
                roots = [(node.module or "").split(".")[0]]
            else:
                continue
            for root in roots:
                if root == "numpy":
                    seen_numpy = True
                assert root in allowed_external or root in stdlib_ok or root == internal_prefix, (
                    f"{path.name} が想定外のモジュール {root!r} を import している")
    assert seen_numpy, "numpy の import が 1 件も見つからない＝走査が機能していない"


@pytest.mark.parametrize("n_bars", [560])
def test_end_to_end_smoke(n_bars):
    """一括計算が最後まで通り、有効バーが存在すること。"""
    ticks, edges = make_dataset(n_bars, bar_sec=BAR_SEC, tick_sec=5, seed=43)
    res = compute_cvfe(ticks, edges, BAR_SEC, n_har=N_HAR)
    assert res.available.sum() > 0
    assert np.all(res.sigma_hat[res.available] > 0.0)
