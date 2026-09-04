"""比較対象モデル M0〜M3 の検証（仕様 §5.3）。"""

import sys
from pathlib import Path

import numpy as np
import pytest

_PKG_DIR = Path(__file__).resolve().parents[1]
if str(_PKG_DIR) not in sys.path:
    sys.path.insert(0, str(_PKG_DIR))

from src.benchmarks import (  # noqa: E402
    EWMA_LAMBDA,
    MA_WINDOW,
    _fit_garch11,
    forecast_ewma,
    forecast_garch11,
    forecast_har_plain,
    forecast_moving_average,
)
from src.dto import HAR_LAG_MONTH  # noqa: E402


def test_specification_constants():
    """M0 は直近 20 本、M1 は λ = 0.94（仕様 §5.3）。"""
    assert MA_WINDOW == 20
    assert EWMA_LAMBDA == 0.94


def test_moving_average_uses_only_past_bars():
    """M0：σ̂_t = sqrt(mean(V_{t−20..t−1}))。当該バーの V_t を含まない（因果性）。"""
    v = np.arange(1.0, 101.0)
    t0 = 30
    out = forecast_moving_average(v, t0)
    assert np.all(np.isnan(out[:t0]))
    assert out[50] == pytest.approx(np.sqrt(v[30:50].mean()))


def test_moving_average_ignores_the_current_bar():
    """直近バーの値を変えても当該バーの予測は変わらない（当該バーを見ていない）。"""
    v = np.full(100, 4.0)
    a = forecast_moving_average(v, 30)
    v2 = v.copy()
    v2[50] = 1e6
    b = forecast_moving_average(v2, 30)
    assert a[50] == b[50]
    assert a[51] != b[51]          # 次バーには反映される


def test_ewma_recursion_matches_definition():
    """M1：h_t = λ h_{t−1} + (1 − λ) V_{t−1}。"""
    v = np.full(60, 9.0)
    out = forecast_ewma(v, 30)
    # 定常値は V に一致する（全要素が等しいため）。
    assert out[59] == pytest.approx(3.0, rel=1e-9)
    assert np.all(np.isnan(out[:30]))


def test_har_plain_is_causal_and_finite():
    """M3：t0 以降のみ有限で、当該バーの C_t を用いない。"""
    rng = np.random.default_rng(3)
    n, n_har = 700, 500
    c = np.exp(rng.standard_normal(n) * 0.3) * 1e-4
    p_close = np.cumsum(rng.standard_normal(n) * 0.001)
    t0 = n_har + HAR_LAG_MONTH
    out = forecast_har_plain(c, p_close, t0, n_har)
    assert np.all(np.isnan(out[:t0]))
    assert np.all(np.isfinite(out[t0:]))
    assert np.all(out[t0:] > 0.0)

    c2 = c.copy()
    c2[t0 + 5] *= 100.0            # 当該バー自身の C を変える
    out2 = forecast_har_plain(c2, p_close, t0, n_har)
    assert out[t0 + 5] == out2[t0 + 5]      # 当該バーの予測は不変
    assert out[t0 + 6] != out2[t0 + 6]      # 次バーには反映される


def test_garch11_recovers_known_parameters():
    """GARCH(1,1) の最尤推定が既知パラメータの近傍へ収束する（scipy 非依存）。"""
    omega_t, alpha_t, beta_t = 2.0e-6, 0.08, 0.90
    rng = np.random.default_rng(4)
    n = 4_000
    r = np.empty(n)
    h = omega_t / (1.0 - alpha_t - beta_t)
    for i in range(n):
        r[i] = np.sqrt(h) * rng.standard_normal()
        h = omega_t + alpha_t * r[i] ** 2 + beta_t * h

    omega, alpha, beta = _fit_garch11(r)
    assert alpha + beta < 1.0
    # 持続性 α+β は最も安定に推定される量。±0.06 以内で一致することを固定する。
    assert abs((alpha + beta) - (alpha_t + beta_t)) < 0.06, (omega, alpha, beta)


def test_garch11_forecast_is_causal():
    """M2：t0 以降のみ有限。当該バーの収益を用いない。"""
    rng = np.random.default_rng(5)
    n, n_har = 700, 500
    r = rng.standard_normal(n) * 0.01
    t0 = n_har + HAR_LAG_MONTH
    out = forecast_garch11(r, t0, n_har)
    assert np.all(np.isnan(out[:t0]))
    assert np.all(np.isfinite(out[t0:])) and np.all(out[t0:] > 0.0)

    r2 = r.copy()
    r2[t0 + 5] *= 50.0
    out2 = forecast_garch11(r2, t0, n_har)
    assert out[t0 + 5] == out2[t0 + 5]
    assert out[t0 + 6] != out2[t0 + 6]


def test_garch11_is_deterministic():
    """同一入力で 2 回推定し同一値（§6 数値再現性）。"""
    rng = np.random.default_rng(6)
    r = rng.standard_normal(1_000) * 0.01
    assert _fit_garch11(r) == _fit_garch11(r)


# =========================================================================== #
# 最適化ルーチン（Nelder-Mead）が repo 内で 1 実装であること（ISSUE-479 Wave2 追随 C）
#
# 単体法の 30 行（反射・拡大・収縮・縮小）が common.nelder_mead と本モジュールに別々に
# 存在していた。片方だけ直すと当てはめ結果が指標間で静かに食い違う（出力は「それらしい値」
# のままなので状態検証では落ちない）。
#
# 固定するもの:
#   1. 単体法の実装が repo に 1 件だけ（AST 指紋・検出器の自己検定つき）
#   2. 委譲後の数値が委譲前と bit 一致（凍結 digest。ゼロ初期点・微小初期点・実経路を含む）
#   3. cvfe の最適化が共有ルーチンを実際に通ること（共有側を差し替えると結果が変わる）
#   4. 当てはめ 1 回につき最適化の発行が 1 件で、系列長を増やしても増えないこと（計算量）
# =========================================================================== #
import ast  # noqa: E402
import hashlib  # noqa: E402
import pathlib  # noqa: E402

from src import benchmarks  # noqa: E402
from src.benchmarks import _fit_garch11, _nelder_mead  # noqa: E402


def _synthetic_returns(n: int) -> np.ndarray:
    """決定論的な合成収益系列（ボラティリティのうねりを持つ・乱数を使わない）。"""
    t = np.arange(n, dtype=np.float64)
    return 0.01 * np.sin(0.7 * t) * (1.0 + 0.5 * np.sin(0.013 * t))

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
#: 走査から外す木（第三者コード・生成物・仮想環境）。
_EXCLUDED_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages"}


# --------------------------------------------------------------------------- #
# 1. 単体法の実装は repo に 1 件
# --------------------------------------------------------------------------- #
def _is_simplex_search(fn: ast.AST) -> bool:
    """単体法の指紋: 目的値の順序付け（argsort）と重心（mean）を同一関数内に持つ。"""
    dumped = ast.dump(fn)
    return "argsort" in dumped and "'centroid'" in dumped and "mean" in dumped


def _simplex_implementations() -> list[str]:
    """本番コード（テストを除く）に存在する単体法の実装を列挙する。

    テストは除く: 指紋そのもの（判定に使う識別子名）を文字列として持つため、走査対象に
    含めると検出器が自分自身を offender として数える。
    """
    sites: list[str] = []
    for path in _REPO_ROOT.rglob("*.py"):
        if _EXCLUDED_PARTS & set(path.parts) or "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover - 走査対象外の壊れた木
            continue
        sites.extend(
            f"{path.relative_to(_REPO_ROOT)}:{node.lineno}:{node.name}"
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_simplex_search(node)
        )
    return sorted(sites)


def test_the_simplex_search_is_implemented_exactly_once_in_repo() -> None:
    sites = _simplex_implementations()
    assert len(sites) == 1, (
        "Nelder–Mead 単体法の実装が複数ある（common.nelder_mead へ委譲すること。"
        "複製は片方だけ直された日に当てはめ結果を静かに食い違わせる）:\n" + "\n".join(sites)
    )


def test_the_canonical_implementation_lives_in_the_shared_layer() -> None:
    """検出器の自己検定: 正典実装そのものを検出できている（空振りでない）。"""
    assert _simplex_implementations() == [
        s for s in _simplex_implementations() if s.startswith("common/nelder_mead.py")
    ]


# --------------------------------------------------------------------------- #
# 2. 委譲前後の bit 等価（凍結 digest）
# --------------------------------------------------------------------------- #
#: 委譲**前**の私有実装（cvfe/src/benchmarks.py の _nelder_mead）が出した値の digest。
#: ゼロ初期点 / 微小初期点 / 通常初期点の最小点と、GARCH(1,1) 最尤推定 21 本を含む。
_FROZEN_DIGEST = "afea1595421d6a59fdee0635bdff308b86626d7e9217811d63771c9abf857af4"


def _sweep_digest() -> str:
    digest = hashlib.sha256()
    target = np.array([1.0, -2.0, 0.5])

    def quadratic(x):
        return float(np.sum((np.asarray(x) - target) ** 2))

    starts = ([0.0, 0.0, 0.0], [1e-9, 1.0, 0.0], [0.1, 0.2, 0.3],
              [-3.0, 4.0, -0.25], [0.0, 1e-12, 5.0])
    for x0 in starts:
        digest.update(_nelder_mead(quadratic, np.array(x0, dtype=np.float64)).tobytes())
    rng = np.random.default_rng(seed=20260903)
    for i in range(20):
        returns = rng.normal(0.0, 0.008 + 0.002 * (i % 3), 400)
        digest.update(np.array(_fit_garch11(returns), dtype=np.float64).tobytes())
    # 分散 0（縮退）— 初期点に 0 が現れる経路。初期単体の刻み方針の違いがここで表面化する。
    digest.update(np.array(_fit_garch11(np.zeros(300)), dtype=np.float64).tobytes())
    return digest.hexdigest()


def test_delegation_keeps_the_numbers_bit_identical() -> None:
    """委譲後の数値が委譲前と bit 一致（初期単体の刻み方針・タイブレークを含めて同一）。"""
    assert _sweep_digest() == _FROZEN_DIGEST


# --------------------------------------------------------------------------- #
# 3. 共有ルーチンを実際に通っている
# --------------------------------------------------------------------------- #
def test_the_optimisation_actually_goes_through_the_shared_routine(monkeypatch) -> None:
    """共有側を差し替えると cvfe の当てはめ結果が変わる（＝私有実装が残っていない）。"""
    # Arrange: 最小化せず初期点をそのまま返す stub。
    monkeypatch.setattr(benchmarks, "nelder_mead", lambda f, x0, **kw: np.asarray(x0, float))

    # Act
    omega, alpha, beta = _fit_garch11(np.full(200, 0.01))

    # Assert: stub の初期点（α=0.08 / β=0.88）がそのまま出る。
    assert (alpha, beta) == (0.08, 0.88)
    assert omega == 1e-18  # var0 = 0 → max(0.0, 1e-18)


# --------------------------------------------------------------------------- #
# 4. 計算量テスト: 当てはめ 1 回あたりの最適化発行 − 使用 = 0
# --------------------------------------------------------------------------- #
class _OptimiserSpy:
    def __init__(self, original):
        self._original = original
        self.issued: list[int] = []

    def __call__(self, f, x0, **kwargs):
        self.issued.append(len(self.issued))
        return self._original(f, x0, **kwargs)


@pytest.mark.parametrize("n", [400, 4000])
def test_one_optimiser_run_per_fit_regardless_of_series_length(monkeypatch, n: int) -> None:
    """発行した最適化 − 出力に使った最適化(=1) = 0。系列長 400/4000 の 2 点で不変。

    当てはめは「系列全体で 1 回」の推定である。バーごと・窓ごとの再当てはめへ退化すると
    発行が系列長に比例して赤になる（値は正しいまま所要だけ跳ねるので状態検証では落ちない）。
    """
    # Arrange
    spy = _OptimiserSpy(benchmarks.nelder_mead)
    monkeypatch.setattr(benchmarks, "nelder_mead", spy)
    returns = _synthetic_returns(n)

    # Act
    fitted = _fit_garch11(returns)

    # Assert
    assert len(fitted) == 3
    assert len(spy.issued) - 1 == 0, f"当てはめ 1 回で最適化を {len(spy.issued)} 回発行している"


def test_the_complexity_gate_detects_a_refit_mutation(monkeypatch) -> None:
    """負の対照: 当てはめを 2 回発行する変異は上の検査で赤になる（検出力の実測）。"""
    # Arrange
    spy = _OptimiserSpy(benchmarks.nelder_mead)
    monkeypatch.setattr(benchmarks, "nelder_mead", spy)
    returns = _synthetic_returns(400)

    # Act: 捨てられる当てはめを 1 回混ぜる（浪費の再現）。
    _fit_garch11(returns)
    _fit_garch11(returns)

    # Assert
    assert len(spy.issued) - 1 != 0, "変異を検出できていない（検査が空振り）"
