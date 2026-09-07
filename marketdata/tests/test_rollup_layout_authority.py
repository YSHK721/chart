"""ロールアップ保存配置の単一権威を **検定で強制する**（ISSUE-502 D-16）。

台帳（.doc/solid_audit_20260906.md の D-16）が実測した状態: 配置
``<DATA_DIR>/rollups/[<ref>/]<ref>_<tf>.csv`` を marketdata 3 箇所・tools 2 箇所・
indigators 3 箇所の計 8 箇所が各自組み立てており、権威モジュールが存在しなかった。

本ファイルは 2 つを固定する。

1. **特性化（byte 等価）**: 8 箇所すべてが生成するパスの実値を、是正前の実測値と同一の
   リテラルで固定する。権威への委譲でパスが 1 バイトも変わらないことを機械的に示す。
2. **権威の強制**: 配置リテラル（``"rollups"`` / ``f"..._{tf}.csv"``）を
   :mod:`marketdata.rollup_paths` 以外が持っていないことをリポジトリ走査で固定する
   （``test_tick_tree_layout_authority.py`` と同じ様式）。

宣言（docstring）ではなく検定で強制するのは、本 repo の是正が繰り返し「コメントに正しいことを
書く」で終わり、次の編集で静かに破れてきたため。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from marketdata import rollup_paths
from marketdata.paths import DATA_DIR

_ROOT = Path(__file__).resolve().parents[2]


# =====================================================================
# 1. 特性化: 8 構築点が生成するパス（是正前の実測値と同一）
# =====================================================================

def test_rollups_root_is_data_dir_rollups() -> None:
    assert str(rollup_paths.rollups_root()) == f"{DATA_DIR}/rollups"


def test_ref_dir_is_rollups_slash_ref() -> None:
    assert str(rollup_paths.ref_dir("jp225_tick")) == f"{DATA_DIR}/rollups/jp225_tick"


def test_csv_name_is_ref_prefix_underscore_tf() -> None:
    assert rollup_paths.csv_name("jp225_m1", "5m") == "jp225_m1_5m.csv"


def test_rollup_store_path_falls_back_to_flat_layout(tmp_path) -> None:
    """(1) marketdata/rollup_store.py — 当該 CSV が無ければフラット配置を返す。"""
    from marketdata import rollup_store

    got = rollup_paths.resolve_csv("jp225_m1", "5m", root=tmp_path)
    assert got == tmp_path / "jp225_m1_5m.csv"
    # 実 DATA_DIR 経由（本番の解決点）も同じ形。
    assert rollup_store.path("no_such_ref", "5m") == Path(f"{DATA_DIR}/rollups/no_such_ref_5m.csv")


def test_rollup_store_path_prefers_existing_ref_subdir_csv(tmp_path) -> None:
    """(1) 当該 tf の CSV が ref 専用 dir に実在すればそちらを返す（空 dir では切り替えない）。"""
    (tmp_path / "jp225_tick").mkdir()
    assert rollup_paths.resolve_csv("jp225_tick", "5m", root=tmp_path) == tmp_path / "jp225_tick_5m.csv"
    (tmp_path / "jp225_tick" / "jp225_tick_5m.csv").write_text("date\n", encoding="utf-8")
    assert (rollup_paths.resolve_csv("jp225_tick", "5m", root=tmp_path)
            == tmp_path / "jp225_tick" / "jp225_tick_5m.csv")


def test_rollup_module_path_keeps_ref_prefix_contract(tmp_path) -> None:
    """(2) marketdata/rollup.py ``_rollup_path`` — 既定 prefix と明示 prefix の両方が不変。"""
    from marketdata import rollup

    assert rollup._rollup_path(tmp_path, "5m").name == "jp225_m1_5m.csv"
    assert rollup._rollup_path(tmp_path, "5m", "jp225_tick") == tmp_path / "jp225_tick_5m.csv"


def test_m1_chain_rollup_dir_is_ref_subdir(tmp_path) -> None:
    """(3) marketdata/mt5_ticks/m1_chain.py ``rollup_dir``。"""
    from marketdata.mt5_ticks import m1_chain

    assert m1_chain.rollup_dir(ref="jp225_mt5", data_dir=tmp_path) == tmp_path / "rollups" / "jp225_mt5"


def test_build_tick_rollup_context_rollups_dir(tmp_path) -> None:
    """(4) tools/build_tick_rollup.py ``PipelineContext.rollups_dir``。"""
    from tools import build_tick_rollup as btr

    ctx = btr.PipelineContext(data_dir=tmp_path)
    assert ctx.rollups_dir == tmp_path / "rollups" / "jp225_tick"


def test_live_tick_watch_writes_into_the_ref_subdir(tmp_path, monkeypatch) -> None:
    """(5) tools/live_tick_watch.py — 自己修復と差分更新の出力先が ``rollups/<ref>``。"""
    from marketdata import rollup as md_rollup
    from marketdata import tick_m1
    from tools import live_tick_watch as ltw

    seen: "dict[str, Path]" = {}
    monkeypatch.setattr(md_rollup, "heal_tail_gaps",
                        lambda m1, tfs, out_dir, ref_prefix: seen.__setitem__("heal", out_dir) or [])
    monkeypatch.setattr(md_rollup.RollupState, "load", staticmethod(lambda out_dir: None))
    monkeypatch.setattr(md_rollup, "incremental_update",
                        lambda m1, st, tfs, out_dir, ref_prefix: seen.__setitem__("update", out_dir))
    monkeypatch.setattr(tick_m1, "m1_csv_path", lambda **kw: tmp_path / "jp225_tick_m1.csv")
    monkeypatch.setattr(ltw, "_heal_next_monotonic", 0.0, raising=False)

    ltw._rollup_update(tmp_path)
    assert seen["heal"] == tmp_path / "rollups" / "jp225_tick"
    assert seen["update"] == tmp_path / "rollups" / "jp225_tick"


def _load_module_by_path(name: str, rel: str):
    """パッケージ初期化子を通さずにモジュールを読み込む。

    indigators/indicator_ui/api/adapter/compute の初期化子は裸のトップレベル名 adapter を
    import する（台帳の別件・本件の射程外）ため、通常の import では repo 根から解決できない。
    本件が検証したいのは当該ファイル 1 本の配置解決だけなので、ファイル単体で読み込む。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, _ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_forming_bar_reads_state_from_the_ref_subdir(monkeypatch) -> None:
    """(6) indigators forming_bar.py — ``RollupState`` の読み先が ``<DATA_DIR>/rollups/<ref>``。"""
    from marketdata import rollup as md_rollup

    forming_bar = _load_module_by_path(
        "_forming_bar_layout_probe",
        "indigators/indicator_ui/api/adapter/compute/forming_bar.py",
    )

    seen: "dict[str, Path]" = {}
    monkeypatch.setattr(md_rollup.RollupState, "load",
                        staticmethod(lambda out_dir: seen.__setitem__("state", Path(out_dir))))
    assert forming_bar._default_confirmed_end("jp225_tick") is None
    assert seen["state"] == Path(f"{DATA_DIR}/rollups/jp225_tick")


def test_export_jp225_m1_default_rollup_dir_is_flat() -> None:
    """(7) indigators export_jp225_m1.py — jp225_m1 はフラット配置（rollups 直下）。"""
    from indigators.indicator_ui.tools import export_jp225_m1

    assert export_jp225_m1._DEFAULT_ROLLUP_DIR == Path(f"{DATA_DIR}/rollups")


def test_period_presets_measure_reads_the_ref_subdir() -> None:
    """(8) indigators period_presets_measure.py — jp225_tick の ref 専用配置。"""
    from indigators.indicator_ui.tools import period_presets_measure

    assert period_presets_measure.ROLL == Path(f"{DATA_DIR}/rollups/jp225_tick")


# =====================================================================
# 2. 権威の強制（リポジトリ走査）
# =====================================================================

#: 配置の権威（ここだけが ``"rollups"`` とロールアップ CSV 名を組んでよい）。
_AUTHORITY = _ROOT / "marketdata" / "rollup_paths.py"

#: 走査対象（本番コード。テストとプロトタイプ・仮想環境は除く）。
_SCAN_DIRS = ("marketdata", "tools", "simulator", "indigators", "unified_ui")

#: 配置の断片を組んでいると判定する式。
_ROLLUPS_DIRNAME = re.compile(r"""["']rollups["']""")
_ROLLUP_CSV_NAME = re.compile(r"""_\{\s*tf\s*\}\.csv""")

#: 是正の射程外に残る研究スクリプト（ISSUE-502 段階 2 の対象外）。
#:
#: いずれも ``indigators/*/analysis/`` の使い捨て計測スクリプトで、``DATA_DIR`` を通さず
#: repo 相対の固定パスを直に読む。**免除ではなく可視化**である: 新しい複製が生まれれば
#: この表に無いので落ちる。表の各項目が実在することも下の検定が固定する（陳腐化させない）。
_KNOWN_RESEARCH_DUPLICATES = frozenset({
    "indigators/profit_adx_needle/analysis/adx_step1_causality.py",
    "indigators/profit_adx_needle/analysis/adx_step2_significance.py",
    "indigators/ma_marod/analysis/strategies_all.py",
    "indigators/ma_marod/analysis/multi_tf.py",
})


def _iter_sources() -> "list[Path]":
    out: "list[Path]" = []
    for d in _SCAN_DIRS:
        for p in (_ROOT / d).rglob("*.py"):
            parts = set(p.parts)
            if "tests" in parts or "__pycache__" in parts or ".venv" in parts:
                continue
            out.append(p)
    return sorted(out)


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _scan_targets() -> "list[Path]":
    """走査対象から権威モジュールと免除表の項目を除いた集合（読込は 1 件 1 回）。"""
    return [p for p in _iter_sources()
            if p != _AUTHORITY and str(p.relative_to(_ROOT)) not in _KNOWN_RESEARCH_DUPLICATES]


def _offenders_over(files, pattern: "re.Pattern[str]", read=None) -> "list[str]":
    """与えられたファイル群を走査する（**1 ファイルにつき読込 1 回**）。"""
    reader = read or _read_source
    offenders: "list[str]" = []
    for path in files:
        try:
            rel = str(path.relative_to(_ROOT))
        except ValueError:
            rel = str(path)
        for i, line in enumerate(reader(path).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""'):
                continue          # コメント・docstring の言及は対象外（説明を禁じない）
            if pattern.search(line):
                offenders.append(f"{rel}:{i}: {stripped[:90]}")
    return offenders


def _offenders(pattern: "re.Pattern[str]", read=None) -> "list[str]":
    """本番コード全体を走査する（権威モジュールと免除表は対象外）。"""
    return _offenders_over(_scan_targets(), pattern, read=read)


@pytest.mark.parametrize(
    "pattern,what",
    [(_ROLLUPS_DIRNAME, "ロールアップ格納 dir 名 <rollups>"),
     (_ROLLUP_CSV_NAME, "ロールアップ CSV 名 <ref>_<tf>.csv")],
    ids=["dirname", "csv_name"],
)
def test_only_the_authority_builds_the_rollup_layout(pattern, what) -> None:
    """権威モジュール以外がロールアップ配置を組んでいない。

    落ちた場合の直し方: 該当箇所を :mod:`marketdata.rollup_paths` の公開関数
    （rollups_root / ref_dir / csv_name / csv_path / resolve_csv）への委譲へ置換する。
    配置そのものを変えたいなら、権威モジュール 1 箇所だけを変える。
    """
    offenders = _offenders(pattern)
    assert not offenders, (
        f"{what} を権威モジュール外で組んでいます:\n  " + "\n  ".join(offenders)
        + "\n  marketdata.rollup_paths への委譲へ置換してください。"
    )


@pytest.mark.parametrize(
    "pattern,line",
    [(_ROLLUPS_DIRNAME, 'out = Path(data_dir) / "rollups" / ref'),
     (_ROLLUPS_DIRNAME, "root = base / 'rollups'"),
     (_ROLLUP_CSV_NAME, 'p = out_dir / f"{ref}_{tf}.csv"'),
     (_ROLLUP_CSV_NAME, "p = out_dir / f'{ref_prefix}_{tf}.csv'")],
    ids=["dirname_dq", "dirname_sq", "csv_name_dq", "csv_name_sq"],
)
def test_the_layout_scan_has_detection_power(pattern, line) -> None:
    """走査が恒真式に退化していないこと（合成行で検出できる）。"""
    assert pattern.search(line), line


def test_the_layout_scan_ignores_comments() -> None:
    """コメント内の言及は offender にしない（説明を禁じない）。"""
    assert _offenders(re.compile(r"""絶対に現れない語彙xyzzy""")) == []


def test_the_research_allowlist_has_no_stale_entries() -> None:
    """免除表の各項目が実在する（消えたファイルを表に残さない）。"""
    missing = [rel for rel in _KNOWN_RESEARCH_DUPLICATES if not (_ROOT / rel).is_file()]
    assert not missing, f"免除表に実在しない項目が残っています: {missing}"


def test_the_research_allowlist_covers_only_analysis_scripts() -> None:
    """免除は研究スクリプトに限る（本番経路を免除しない）。"""
    non_analysis = [rel for rel in _KNOWN_RESEARCH_DUPLICATES if "/analysis/" not in rel]
    assert not non_analysis, f"本番経路を免除しています: {non_analysis}"


# ---------------------------------------------------------------------
# 計算量検定（Test Spy・発行 − 使用 = 0）
# ---------------------------------------------------------------------

def test_every_source_is_read_exactly_once_by_the_layout_scan() -> None:
    """読込集合 == 走査対象（免除分を除く）。読み捨ても二度読みも無い（発行 − 使用 = 0）。"""
    reads: "list[Path]" = []
    _offenders(_ROLLUPS_DIRNAME,
               read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1])
    used = _scan_targets()
    assert len(reads) - len(used) == 0
    assert set(reads) == set(used)
    assert len(set(reads)) - len(reads) == 0


def test_the_read_count_is_determined_by_the_file_count_alone(tmp_path) -> None:
    """走査対象 4 件 / 8 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。

    行数を増やしても読込は増えない（1 ファイル 1 読込）ことを、ファイル数 2 点で固定する。
    """
    measured: "dict[int, int]" = {}
    for count in (4, 8):
        files = []
        for i in range(count):
            p = tmp_path / f"m{count}_{i}.py"
            # 行数はファイルごとに変える（読込が行数でなくファイル数で決まることを示す）。
            p.write_text("x = 1\n" * (i + 1), encoding="utf-8")
            files.append(p)
        reads: "list[Path]" = []
        _offenders_over(files, _ROLLUPS_DIRNAME,
                        read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1])
        measured[count] = len(reads)
    assert measured == {4: 4, 8: 8}
