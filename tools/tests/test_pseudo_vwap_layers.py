"""疑似VWAP 検証スクリプトの層分割と越境解消の検定（ISSUE-479 Wave2 M-4）。

なぜ必要か:
    ``tools/verify_pseudo_vwap.py`` は 940 行に 5 つの責務（素材化 Gateway・指標の純関数・
    統計検定の純関数・測定の組み立て・CLI 出力）を積んでおり、テストは 1 件も無かった。
    さらに他パッケージの private 名を 5 つ跨いで import していた（tick 列・mid 規則・
    ブローカー時間写像・滞在秒積分・tf 別価格単位のいずれも、公開名を持たなかった）。
    private への依存は「呼んでよい」と宣言されていない実装詳細への依存であり、権威側が内部を
    変えた瞬間に黙って壊れる。

安全網の順序（重要）:
    分割の前に **出力の凍結**を先に置く。合成ティック parquet から JSON 出力を作り、その
    バイト列を golden として固定する。分割は「出力が 1 バイトも変わらない」ことを条件に行う。
    実データ（``data/``）は読まない（read-only 環境でも走る・決定論）。

計算量（発行 − 使用 = 0）:
    素材化が発行する parquet 読取の回数 − 連結した日数 = 0（読んで捨てる日を作らない）。
    対象日数 2/4 の 2 点で比例し、窓の個数を変えても parquet 読取は増えない
    （測定の組み合わせ数が素材化の I/O を増やさないことの表明）。回数は焼き込まない。
"""

from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_GOLDEN = Path(__file__).resolve().parent / "fixtures" / "pseudo_vwap_frozen.json"
_GOLDEN_SESSION = (Path(__file__).resolve().parent / "fixtures"
                   / "pseudo_vwap_frozen_session.json")

#: 分割対象の表面（本 Wave の責任範囲）。ここに private 越境 import を 1 件も残さない。
_SURFACE = ("tools/verify_pseudo_vwap.py", "tools/pseudo_vwap")


# --------------------------------------------------------------------------- #
# 合成ティック（実データを読まない・決定論）
# --------------------------------------------------------------------------- #
def _synthetic_day(day: str, seed: int, step_sec: int = 15) -> pd.DataFrame:
    """1 日分の合成ティック（``step_sec`` 秒間隔・決定論のランダムウォーク）。"""
    rng = np.random.default_rng(seed)
    base = pd.Timestamp(day)
    offsets = np.arange(0, 24 * 60 * 60, step_sec)
    ts = base + pd.to_timedelta(offsets, unit="s")
    steps = rng.normal(0.0, 0.6, size=offsets.size)
    mid = 28000.0 + np.cumsum(steps)
    spread = 1.0
    return pd.DataFrame(
        {"timestamp": ts, "bidPrice": mid - spread / 2, "askPrice": mid + spread / 2}
    )


def _write_days(root: Path, n: int, *, seed0: int, step_sec: int) -> "list[Path]":
    """``n`` 日分の合成ティック parquet を書き、そのパス列を返す。"""
    root.mkdir(parents=True, exist_ok=True)
    paths = []
    for i in range(n):
        day = (pd.Timestamp("2026-01-05") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        out = root / f"{day}.parquet"
        _synthetic_day(day, seed=seed0 + i, step_sec=step_sec).to_parquet(out)
        paths.append(out)
    return paths


@pytest.fixture()
def synthetic_tick_days(tmp_path):
    """合成ティック parquet を 4 日分書き、そのパス列を返す（日中足シナリオ用）。"""
    return _write_days(tmp_path / "intraday", 4, seed0=1000, step_sec=15)


def _material_module():
    """素材化（day_parquet_files を引く側）を持つモジュール。分割の前後どちらでも解決する。"""
    try:
        return importlib.import_module("tools.pseudo_vwap.data")
    except ModuleNotFoundError:
        return importlib.import_module("tools.verify_pseudo_vwap")


def _cli_module():
    """CLI（main）を持つモジュール（分割後も Composition Root は同じファイル）。"""
    return importlib.import_module("tools.verify_pseudo_vwap")


def _patch_day_source(monkeypatch, paths):
    monkeypatch.setattr(_material_module(), "day_parquet_files",
                        lambda lo, hi, symbol=None: list(paths))


#: 日中足シナリオ（5m）。滞在秒加重は使わない（--no-dwell）。
_FROZEN_ARGV = [
    "--periods", "2026-01-05:2026-01-08",
    "--tfs", "5m",
    "--windows", "20",
    "--horizons", "1",
    "--dev-horizons", "5",
    "--perms", "5",
    "--band-window", "50",
    "--no-dwell",
    "--seed", "1",
]

#: セッション足シナリオ（1D）。ブローカー暦日への index 写像と滞在秒加重（活発地図・
#: 滞在秒積分）を通す。日中足シナリオだけでは、この 2 経路が 1 度も実行されない。
_FROZEN_SESSION_ARGV = [
    "--periods", "2026-01-05:2026-01-24",
    "--tfs", "1D",
    "--windows", "3",
    "--horizons", "1",
    "--dev-horizons", "2",
    "--perms", "3",
    "--band-window", "5",
    "--seed", "7",
]

#: シナリオ名 → (golden ファイル, argv, 生成する日数, 乱数 seed 基点, ティック間隔秒)
_SCENARIOS = {
    "intraday": (_GOLDEN, _FROZEN_ARGV, 4, 1000, 15),
    "session": (_GOLDEN_SESSION, _FROZEN_SESSION_ARGV, 20, 2000, 60),
}


def _run_frozen(monkeypatch, tmp_path, paths, argv=None) -> str:
    """凍結条件で ``main`` を回し、JSON 出力（環境依存部を正規化済み）を返す。"""
    _patch_day_source(monkeypatch, paths)
    tmp_path.mkdir(parents=True, exist_ok=True)
    out = tmp_path / "pseudo_vwap.json"
    _cli_module().main([*(argv or _FROZEN_ARGV), "--json", str(out)])
    payload = json.loads(out.read_text(encoding="utf-8"))
    # measure_forming の "file" は絶対パス（tmp_path）なのでファイル名だけに正規化する。
    for row in payload.get("forming", []):
        row["file"] = Path(row["file"]).name
    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# 出力の凍結（分割の前後で 1 バイトも変わらない）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_the_json_output_matches_the_frozen_golden(monkeypatch, tmp_path, scenario):
    """合成ティックからの JSON 出力が golden と完全一致する（分割前の実装で採取済み）。

    分割・公開名化・台帳化のいずれも「出力を変えない構造変更」である。値が動いたなら、
    それは移設ではなく仕様変更であり、意図した変更なら golden を明示的に作り直す。

    シナリオを 2 つ置く理由: 日中足（5m）だけでは、セッション足のブローカー暦日写像と
    滞在秒加重（活発地図・滞在秒積分）が 1 度も実行されない。公開名へ差し替えた経路が
    golden の外に残ると、凍結は「通った所だけ」の保証になる。
    """
    golden, argv, days, seed0, step = _SCENARIOS[scenario]
    paths = _write_days(tmp_path / "ticks", days, seed0=seed0, step_sec=step)

    got = _run_frozen(monkeypatch, tmp_path / "run", paths, argv)
    assert golden.exists(), (
        f"golden 未生成: {golden}。分割前の実装で作成してからコミットすること。"
    )
    assert got == golden.read_text(encoding="utf-8")


def test_the_frozen_run_is_deterministic(monkeypatch, tmp_path, synthetic_tick_days):
    """同じ入力・同じ seed で 2 回回すと同じ出力になる（凍結が意味を持つ前提）。"""
    a = _run_frozen(monkeypatch, tmp_path / "a", synthetic_tick_days)
    b = _run_frozen(monkeypatch, tmp_path / "b", synthetic_tick_days)
    assert a == b


# --------------------------------------------------------------------------- #
# 越境: private 名への依存を残さない
# --------------------------------------------------------------------------- #
def _surface_sources() -> "list[Path]":
    out: "list[Path]" = []
    for rel in _SURFACE:
        p = _ROOT / rel
        if p.is_dir():
            out += [q for q in sorted(p.rglob("*.py")) if "__pycache__" not in q.parts]
        elif p.is_file():
            out.append(p)
    return out


def _cross_package_private_imports(path: Path) -> "list[str]":
    """1 ファイル中の「他パッケージの private 名を import している箇所」を列挙する。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: "list[str]" = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level:
            continue
        module = node.module or ""
        if module.split(".")[0] == "tools":
            continue                          # 自パッケージ内の private は越境ではない
        found += [
            f"{path.relative_to(_ROOT)}:{node.lineno}: from {module} import {alias.name}"
            for alias in node.names
            if alias.name.startswith("_") and not alias.name.startswith("__")
        ]
    return found


def _private_import_offenders() -> "list[str]":
    return [x for p in _surface_sources() for x in _cross_package_private_imports(p)]


def test_no_private_name_is_imported_across_package_boundaries():
    """他パッケージのアンダースコア始まりの名前を import していない（実装詳細への依存を断つ）。

    落ちた場合の直し方: 権威モジュール側に公開名を**加法で**足し（private 名は同一
    オブジェクトのまま温存する）、こちらの import を公開名へ差し替える。
    """
    offenders = _private_import_offenders()
    assert offenders == [], (
        "他パッケージの private 名へ依存しています:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("private,public,module", [
    ("_TICK_COLUMNS", "TICK_COLUMNS", "marketdata.tick_m1"),
    ("_to_broker_naive_index", "to_broker_naive_index", "marketdata.resample"),
    ("_session_dwell", "session_dwell",
     "market_profile_api.compute.market_profile_dwell_kernel"),
    ("_UNIT_BY_TF", "UNIT_BY_TF",
     "market_profile_api.controller.tf_period_profile_controller"),
])
def test_the_public_name_is_the_same_object_as_the_private_one(private, public, module):
    """公開名化は**加法**である（private 名は同一オブジェクトのまま残る＝既存参照は無改変）。"""
    mod = importlib.import_module(module)
    assert getattr(mod, public) is getattr(mod, private)


# --------------------------------------------------------------------------- #
# 層: 純関数の層が marketdata / market_profile_api を知らない
# --------------------------------------------------------------------------- #
def _imported_roots(path: Path) -> "set[str]":
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and not node.level:
            roots.add((node.module or "").split(".")[0])
    return roots


@pytest.mark.parametrize("name", ["indicators", "stats"])
def test_the_pure_layers_do_not_know_the_data_sources(name):
    """指標・検定は numpy/pandas だけの純関数層である（素材の出所を知らない）。

    ここが marketdata を知っていると、式を検証するのに実データの木が要るようになる。
    """
    path = _ROOT / "tools" / "pseudo_vwap" / f"{name}.py"
    assert path.is_file(), f"{path} がありません（層分割が未実施）"
    forbidden = {"marketdata", "market_profile_api"} & _imported_roots(path)
    assert not forbidden, f"{name}.py が {sorted(forbidden)} に依存しています（純関数層を保つ）"


def test_the_marketdata_dependency_is_confined_to_the_gateway():
    """marketdata への依存は素材化 Gateway（data.py）だけに閉じる。"""
    pkg = _ROOT / "tools" / "pseudo_vwap"
    assert pkg.is_dir(), f"{pkg} がありません（層分割が未実施）"
    leaking = sorted(
        p.name for p in pkg.rglob("*.py")
        if p.name != "data.py" and "__pycache__" not in p.parts
        and "marketdata" in _imported_roots(p)
    )
    assert leaking == [], f"marketdata 依存が Gateway の外へ漏れています: {leaking}"


def _is_sys_path_mutation(node: ast.AST) -> bool:
    """``sys.path.insert`` / ``sys.path.append`` の呼び出しか。"""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in ("insert", "append")
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path")


def _sys_path_mutation_files() -> "list[str]":
    """``sys.path`` を書き換えているファイル（表面内・重複なし・相対パス）。"""
    tree_of = {p: ast.parse(p.read_text(encoding="utf-8")) for p in _surface_sources()}
    hits = {
        str(p.relative_to(_ROOT))
        for p, tree in tree_of.items()
        for node in ast.walk(tree) if _is_sys_path_mutation(node)
    }
    return sorted(hits)


def test_the_sys_path_bootstrap_lives_only_in_the_composition_root():
    """``sys.path`` への注入は合成点 1 箇所だけ（ライブラリ側は自分で環境を作らない）。"""
    assert _sys_path_mutation_files() == ["tools/verify_pseudo_vwap.py"]


# --------------------------------------------------------------------------- #
# 計算量（発行 − 使用 = 0）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("days", [2, 4])
def test_material_reads_exactly_the_days_it_concatenates(monkeypatch, tmp_path,
                                                         synthetic_tick_days, days):
    """parquet 読取の発行 − 連結日数 = 0（読んで捨てる日を作らない・日数に比例）。"""
    mod = _material_module()
    paths = synthetic_tick_days[:days]
    _patch_day_source(monkeypatch, paths)

    reads = {"n": 0}
    real = mod.pd.read_parquet

    def counting(*a, **k):
        reads["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(mod.pd, "read_parquet", counting)
    m1 = mod.build_m1("2026-01-05", "2026-01-08", "JP225", with_dwell=False)

    assert not m1.empty
    assert reads["n"] - len(paths) == 0


@pytest.mark.parametrize("windows", [["20"], ["20", "30", "40"]])
def test_parquet_reads_do_not_grow_with_the_measurement_grid(monkeypatch, tmp_path,
                                                             synthetic_tick_days, windows):
    """測定の組み合わせ（--windows の個数）を増やしても parquet 読取は増えない。

    素材化は 1 回で、その上に測定を重ねる。窓ごとに読み直すと入力量に比例しない浪費になる。
    """
    mod = _material_module()
    _patch_day_source(monkeypatch, synthetic_tick_days)

    reads = {"n": 0}
    real = mod.pd.read_parquet

    def counting(*a, **k):
        reads["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(mod.pd, "read_parquet", counting)
    argv = [a for a in _FROZEN_ARGV if a != "20"]
    argv[argv.index("--windows") + 1: argv.index("--windows") + 1] = windows
    _cli_module().main([*argv, "--json", str(tmp_path / "out.json")])

    # 素材化 4 日 ＋ 測定 4（形成中バーの検算で 1 日を読み直す）＝ 窓の本数に依らず一定。
    assert reads["n"] == len(synthetic_tick_days) + 1
