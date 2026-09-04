"""`common.event_quantiles` の集計単位（event_agg）解決を単一化する（ISSUE-479 Wave2 C-6）。

背景（実測）:
    - 検証は outlier_event_quantiles の 1 箇所だけにあり、step_events は
      `str(event_agg).lower() == "bar"` の比較だけで、**未知値を黙って episode へ縮退**
      させていた（実測: "epsiode" のタイプミスが例外にならず run_up に積まれる）。
    - outlier_event_quantiles はバーごとに step_events を呼び、その中で毎回正規化していた
      （実測: n=50 で 50 回 / n=500 で 500 回。出力に使う解決は 1 件だけ＝浪費）。

本ファイルが固定するもの:
    1. 未知 event_agg が step_events でも ValueError になること（黙認縮退の封鎖）
    2. 空入力でも未知 event_agg は同一文言の ValueError（現行挙動の保存）
    3. 統合前の出力の bit 等価（episode/bar x include_all の 8 キー全配列・digest 凍結）
    4. 解決の発行が呼び出しあたり 1 で、バー数 n を増やしても増えないこと（計算量）
"""
from __future__ import annotations

import hashlib
import importlib

import numpy as np
import pytest

event_quantiles = importlib.import_module("common.event_quantiles")


def _fresh_buffers() -> tuple[list, list, list, list]:
    return [], [], [], []


def test_step_events_rejects_an_unknown_event_agg() -> None:
    """未知の event_agg は黙って episode へ縮退せず ValueError になる。"""
    # Arrange: "episode" のタイプミス。
    up, dn, run_up, run_dn = _fresh_buffers()

    # Act / Assert
    with pytest.raises(ValueError) as excinfo:
        event_quantiles.step_events(5.0, -1.0, 1.0, "epsiode", up, dn, run_up, run_dn)
    assert str(excinfo.value) == "未知の event_agg です: epsiode（episode/bar）"
    assert (up, dn, run_up, run_dn) == ([], [], [], []), "拒否したのに状態が進んでいる"


@pytest.mark.parametrize("event_agg", ["episode", "bar", "EPISODE", "Bar"])
def test_step_events_accepts_the_known_aggs_case_insensitively(event_agg: str) -> None:
    """既知の集計単位は大文字小文字を問わず受理する（従来の str().lower() 規約を保存）。"""
    up, dn, run_up, run_dn = _fresh_buffers()
    event_quantiles.step_events(5.0, -1.0, 1.0, event_agg, up, dn, run_up, run_dn)
    # bar は即座に観測へ、episode は進行中エピソードへ積む。
    assert (up or run_up) == [5.0]


def test_unknown_event_agg_raises_the_same_message_even_for_empty_input() -> None:
    """空入力でも未知 event_agg は同一文言の ValueError（解決をループ内へ落とさない）。"""
    empty = np.array([], dtype=np.float64)
    with pytest.raises(ValueError) as excinfo:
        event_quantiles.outlier_event_quantiles(
            empty, empty, empty, q_high=0.95, event_agg="nope"
        )
    assert str(excinfo.value) == "未知の event_agg です: nope（episode/bar）"


def test_outlier_event_quantiles_matches_the_frozen_digest() -> None:
    """統合前の出力と bit 一致（episode/bar x include_all True/False の 8 キー全配列）。"""
    # Arrange
    rng = np.random.default_rng(777)
    n = 400
    values = rng.normal(0.0, 1.0, n)
    values[:5] = np.nan
    values[100:104] = np.nan
    low_band = np.full(n, -1.0)
    high_band = np.full(n, 1.0)

    # Act
    digest = hashlib.sha256()
    for event_agg in ("episode", "bar"):
        for include_all in (True, False):
            out = event_quantiles.outlier_event_quantiles(
                values, low_band, high_band,
                q_high=0.95, q_out=0.99, k_events=7,
                event_agg=event_agg, include_all=include_all,
            )
            for key in sorted(out):
                digest.update(key.encode())
                digest.update(out[key].tobytes())

    # Assert
    assert digest.hexdigest() == (
        "5cca56fae2208ba472ccd065d8a17c129c53548807e16b8c735a8dc0bc851a4b"
    )


@pytest.mark.parametrize("n", [50, 500])
@pytest.mark.parametrize("calls", [1, 2])
def test_stepper_resolution_is_issued_once_per_call(
    monkeypatch: pytest.MonkeyPatch, n: int, calls: int
) -> None:
    """計算量テスト: 発行したステッパ解決 − 出力に使った解決 = 0。

    「使った解決」は outlier_event_quantiles の呼び出し 1 回につき 1 件（1 回の走査は
    1 つの集計単位しか使わない）。解決をバー走査の内側に置くと発行が n に比例して赤になる
    （分割前の実測: n=50 で 50 回 / n=500 で 500 回）。回数を焼き込まず**無駄の不在**を
    固定し、バー数 50/500 の 2 点で不変（発行が入力量に比例しない＝オーダーの表明）。
    """
    # Arrange
    issued: list[str] = []
    original = event_quantiles.event_stepper

    def _spy(event_agg):
        issued.append(str(event_agg))
        return original(event_agg)

    monkeypatch.setattr(event_quantiles, "event_stepper", _spy)
    rng = np.random.default_rng(1)
    values = rng.normal(0.0, 1.0, n)
    low_band = np.full(n, -1.0)
    high_band = np.full(n, 1.0)

    # Act
    for _ in range(calls):
        event_quantiles.outlier_event_quantiles(
            values, low_band, high_band,
            q_high=0.95, q_out=0.99, k_events=5, event_agg="episode",
        )

    # Assert
    assert len(issued) - calls == 0


def test_the_stepper_table_is_the_single_source_of_the_known_aggs() -> None:
    """既知集計単位の集合はステッパ表から導出される（第 2 の列挙を作らない）。"""
    assert set(event_quantiles._EVENT_AGGS) == set(event_quantiles._EVENT_STEPPERS)
    assert set(event_quantiles._EVENT_STEPPERS) == {"episode", "bar"}


# --------------------------------------------------------------------------- #
# 5. 既知集計単位の列挙が repo に 1 つだけ（ISSUE-479 Wave2 追随 B）
#
# 表を単一情報源にしても、消費者が `event_agg not in ("episode", "bar")` と書き写せば
# 集計単位が 1 つ増えた日にそこだけ取り残される（＝未知値として黙って別経路へ落ちる）。
# 列挙の複製そのものを構文で禁じる。
# --------------------------------------------------------------------------- #
import ast  # noqa: E402
import pathlib  # noqa: E402

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CANONICAL_SOURCE = pathlib.Path(event_quantiles.__file__).resolve()
#: 走査から外す木（第三者コード・生成物・仮想環境・テスト）。
_EXCLUDED_PARTS = {".venv", "venv", "node_modules", "__pycache__", ".git", "out", "site-packages"}
_KNOWN_AGGS = frozenset({"episode", "bar"})


def _literal_strings(node: ast.AST) -> set[str]:
    """コンテナリテラル（tuple/list/set）に直接並ぶ文字列定数の集合。"""
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set()
    return {
        e.value for e in node.elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    }


def _agg_enumeration_sites(tree: ast.AST) -> list[int]:
    """既知集計単位の集合をリテラルで書き写している ``in`` / ``not in`` 判定の行番号。"""
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        ops = {type(op) for op in node.ops}
        if not ops & {ast.In, ast.NotIn}:
            continue
        if any(_KNOWN_AGGS <= _literal_strings(c) for c in node.comparators):
            out.append(node.lineno)
    return out


def _production_sources() -> list[pathlib.Path]:
    return [
        p for p in _REPO_ROOT.rglob("*.py")
        if not (_EXCLUDED_PARTS & set(p.parts)) and "tests" not in p.parts
    ]


def test_the_known_aggs_are_enumerated_in_exactly_one_place() -> None:
    """既知集計単位の列挙は正典モジュールの 1 か所だけ（消費者は正規化関数へ委譲する）。"""
    offenders = [
        f"{path.relative_to(_REPO_ROOT)}:{lineno}"
        for path in _production_sources()
        if path.resolve() != _CANONICAL_SOURCE
        for lineno in _agg_enumeration_sites(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert offenders == [], (
        "集計単位の列挙が書き写されている（common.event_quantiles.normalize_event_agg へ"
        "委譲すること。列挙の複製は集計単位が増えた日に取り残される）:\n" + "\n".join(offenders)
    )


def test_the_enumeration_detector_catches_a_synthetic_copy() -> None:
    """検出器の自己検定: 書き写しを実際に捕捉し、正規化関数の呼出は捕捉しない。"""
    copied = ast.parse('def f(a):\n    return a not in ("episode", "bar")\n')
    delegated = ast.parse('def f(a):\n    return normalize_event_agg(a)\n')
    assert _agg_enumeration_sites(copied) == [2]
    assert _agg_enumeration_sites(delegated) == []
