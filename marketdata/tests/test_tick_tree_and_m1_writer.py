"""tick 木レイアウトの分離と M1 永続化の境界の検定（ISSUE-479 Wave2 M-2）。

なぜ必要か:
    ``marketdata/tick_m1.py`` は 3 つの無関係な責務を 1 モジュールに抱えていた:
      1. tick 木レイアウトの権威（``<DATA_DIR>/ticks/YYYY/MM/DD/<symbol>_ticks.parquet`` の解決）
      2. ticks → M1 の素材化（集計・清掃・CSV 出力）
      3. CLI（``python -m`` の入口）
    1 は 44 ファイル 164 箇所から参照される「木の形」の唯一源であり、2 の集計規則とは変更理由が
    別である（木を動かす理由と、集計を変える理由は一致しない）。3 に至っては合成点であり、
    権威モジュールに置くと CLI の都合が権威へ混ざる。

    本検定は分離後も**参照側が 1 箇所も変わらない**こと（再輸出の同一オブジェクト性）を固定し、
    かつ M1 の書き出し先を差し替えられる境界（:class:`M1Writer`）を持つことを固定する。

計算量（発行 − 使用 = 0）:
    ``day_parquet_files`` の実在判定（``Path.is_file``）の発行数 − 走査対象日数 = 0。
    期間長を変えた 2 点で比例し、実在日数を変えても発行が動かない（実在しない日を安く飛ばす
    最適化と引き換えに、実在日ごとの余分な stat を発行しないことの表明）。
    ``build_m1_from_ticks`` の書式化（``_format_m1_for_csv``）の発行 − 1 = 0（日数を変えても
    出力 1 本につき 1 回であり、日ごとに作って捨てない）。回数そのものは焼き込まない。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from marketdata import tick_m1


def _ticks(rows: "list[tuple[str, float, float]]") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime([r[0] for r in rows]),
            "bidPrice": [r[1] for r in rows],
            "askPrice": [r[2] for r in rows],
        }
    )


def _write_day(data_dir: Path, day: str, *, symbol: str = "JP225") -> Path:
    """``day`` の日別ティック parquet を tick 木レイアウトへ 1 本書く（テスト素材）。"""
    d = pd.Timestamp(day)
    out = (data_dir / "ticks" / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
           / f"{symbol}_ticks.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    _ticks([(f"{day} 00:00:0{i}", 100.0 + i, 102.0 + i) for i in range(3)]).to_parquet(out)
    return out


# =========================================================================== #
# (a) tick 木の権威を専用モジュールへ移し、tick_m1 は再輸出する
# =========================================================================== #

_TREE_NAMES = (
    "tick_root",
    "day_parquet_path",
    "day_empty_marker_path",
    "day_parquet_name",
    "day_parquet_files",
)


@pytest.mark.parametrize("name", _TREE_NAMES)
def test_tick_m1_re_exports_the_tree_authority_object_itself(name):
    """再輸出は**同一オブジェクト**である（写しではない＝挙動が二重化しない）。

    44 ファイル 164 箇所の既存参照（``tick_m1.day_parquet_path`` / ``from ... import ...``）を
    1 箇所も変えずに済むことの根拠。
    """
    from marketdata import tick_tree

    assert getattr(tick_m1, name) is getattr(tick_tree, name)


def test_the_default_symbol_is_owned_by_the_tree_authority():
    """既定 symbol も木のレイアウト語彙であり、権威側が持つ（tick_m1 は再輸出）。"""
    from marketdata import tick_tree

    assert tick_m1._DEFAULT_SYMBOL is tick_tree._DEFAULT_SYMBOL


def test_the_m1_output_path_stays_with_the_material_module():
    """``m1_csv_path`` は tick 木ではなく M1 出力の語彙なので移さない（線の引き方の固定）。"""
    from marketdata import tick_tree

    assert not hasattr(tick_tree, "m1_csv_path")
    assert callable(tick_m1.m1_csv_path)


def test_the_tree_authority_does_not_depend_on_the_material_module():
    """権威は素材化モジュールを import しない（依存の向きが逆流しないこと）。"""
    import ast

    from marketdata import tick_tree

    tree = ast.parse(Path(tick_tree.__file__).read_text(encoding="utf-8"))
    imported = {
        alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
    }
    assert "marketdata.tick_m1" not in imported


def test_patching_the_re_exported_authority_still_moves_the_journal(monkeypatch, tmp_path):
    """既存の単一注入点（``tick_m1.day_parquet_path`` の monkeypatch）が効き続ける。

    ``mt5_ticks.journal`` は module 属性経由で権威を引くため、再輸出後も差し替えが届く。
    """
    from marketdata.mt5_ticks import journal

    moved = tmp_path / "elsewhere" / "X.parquet"
    monkeypatch.setattr(tick_m1, "day_parquet_path", lambda *a, **k: moved)

    got = journal.journal_path(pd.Timestamp("2026-01-05").date(), symbol="MT5",
                               data_dir=tmp_path)
    assert got == moved.with_suffix(".ndjson")


def test_patching_the_re_exported_name_does_not_reach_the_enumeration(monkeypatch, tmp_path):
    """一方 ``day_parquet_files`` の列挙は差し替えの影響を受けない（波及範囲を可視化する）。

    列挙は権威モジュール内で自分の日別パス解決を呼ぶ。再輸出名を差し替えても
    列挙の解決先は動かない＝差し替えは「journal のような外部の派生」にだけ届く。
    この境界を暗黙にせず検定で見えるようにしておく。
    """
    _write_day(tmp_path, "2026-01-05")
    monkeypatch.setattr(tick_m1, "day_parquet_path",
                        lambda *a, **k: tmp_path / "elsewhere" / "X.parquet")

    found = tick_m1.day_parquet_files("2026-01-05", "2026-01-05", data_dir=tmp_path)
    assert found == [_write_day(tmp_path, "2026-01-05")]


# =========================================================================== #
# (b) M1 永続化の境界（M1Writer）
# =========================================================================== #

class _SpyM1Writer:
    """M1Writer プロトコルの Test Spy（何も永続化せず、受け取ったフレームを記録する）。"""

    def __init__(self) -> None:
        self.whole: "list[tuple[pd.DataFrame, Path]]" = []
        self.appended: "list[tuple[pd.DataFrame, Path]]" = []

    def write_whole(self, m1: pd.DataFrame, path: Any) -> None:
        self.whole.append((m1.copy(), Path(path)))

    def append(self, m1_new: pd.DataFrame, path: Any) -> None:
        self.appended.append((m1_new.copy(), Path(path)))


def test_build_m1_from_ticks_writes_through_the_injected_writer(tmp_path):
    """全構築は永続化の具象を自分で決めず、注入された writer へ渡す（DIP）。"""
    _write_day(tmp_path, "2026-01-05")
    spy = _SpyM1Writer()

    out = tick_m1.build_m1_from_ticks("2026-01-05", "2026-01-05", data_dir=tmp_path, writer=spy)

    assert len(spy.whole) == 1
    frame, path = spy.whole[0]
    assert path == out == tick_m1.m1_csv_path(data_dir=tmp_path)
    assert not frame.empty
    assert not out.exists(), "注入した writer を迂回して CSV が書かれています"


def test_append_m1_from_ticks_writes_through_the_injected_writer(tmp_path):
    """増分追記も注入された writer へ渡す（フォールバック時は全構築側へ伝播する）。"""
    _write_day(tmp_path, "2026-01-05")
    _write_day(tmp_path, "2026-01-06")
    # 既存 CSV を実在させる（健全 tail ＝増分経路に入る条件）。
    tick_m1.build_m1_from_ticks("2026-01-05", "2026-01-05", data_dir=tmp_path)
    spy = _SpyM1Writer()

    tick_m1.append_m1_from_ticks("2026-01-05", "2026-01-06", data_dir=tmp_path, writer=spy)

    assert len(spy.appended) == 1 and spy.whole == []
    frame, _ = spy.appended[0]
    assert not frame.empty


def test_the_default_m1_writer_satisfies_the_protocol(tmp_path):
    """既定の具象は公開プロトコル ``M1Writer`` の構造部分型である（境界が名だけでない）。"""
    assert isinstance(tick_m1.CsvM1Writer(), tick_m1.M1Writer)
    assert isinstance(_SpyM1Writer(), tick_m1.M1Writer)


def test_the_default_writer_keeps_the_output_byte_identical(tmp_path):
    """既定（未注入）と ``CsvM1Writer`` 明示注入の出力が byte 一致する（既定値の同一性）。"""
    implicit = tmp_path / "implicit"
    explicit = tmp_path / "explicit"
    for d in (implicit, explicit):
        _write_day(d, "2026-01-05")

    a = tick_m1.build_m1_from_ticks("2026-01-05", "2026-01-05", data_dir=implicit)
    b = tick_m1.build_m1_from_ticks("2026-01-05", "2026-01-05", data_dir=explicit,
                                    writer=tick_m1.CsvM1Writer())
    assert a.read_bytes() == b.read_bytes()


# =========================================================================== #
# (c) CLI は合成点へ出す
# =========================================================================== #

def test_the_cli_entry_point_lives_outside_the_authority():
    """CLI（合成点）は権威モジュールに置かない。"""
    from marketdata.tools import tick_m1_cli

    assert callable(tick_m1_cli.main)
    assert not hasattr(tick_m1, "main")


# =========================================================================== #
# 計算量（発行 − 使用 = 0）
# =========================================================================== #

@pytest.mark.parametrize("days,existing", [(3, 1), (6, 1), (6, 5)])
def test_day_enumeration_issues_exactly_one_probe_per_scanned_day(monkeypatch, tmp_path,
                                                                  days, existing):
    """実在判定の発行数 − 走査対象日数 = 0（期間長に比例・実在日数には依存しない）。

    期間 3/6 日の 2 点で比例を、実在 1/5 日の 2 点で「実在日ごとの余分な stat を出さない」ことを
    固定する。回数そのものは期待値へ焼き込まない。
    """
    for i in range(existing):
        _write_day(tmp_path, f"2026-01-{5 + i:02d}")

    probes = {"n": 0}
    real_is_file = Path.is_file

    def counting_is_file(self):
        probes["n"] += 1
        return real_is_file(self)

    monkeypatch.setattr(Path, "is_file", counting_is_file)
    end = pd.Timestamp("2026-01-05") + pd.Timedelta(days=days - 1)
    tick_m1.day_parquet_files("2026-01-05", end, data_dir=tmp_path)

    assert probes["n"] - days == 0


@pytest.mark.parametrize("days", [2, 4])
def test_build_formats_the_output_exactly_once(monkeypatch, tmp_path, days):
    """書式化の発行 − 出力本数(1) = 0（日ごとに作って捨てない・日数を変えても増えない）。"""
    for i in range(days):
        _write_day(tmp_path, f"2026-01-{5 + i:02d}")

    calls = {"n": 0}
    real = tick_m1._format_m1_for_csv

    def counting(m1):
        calls["n"] += 1
        return real(m1)

    monkeypatch.setattr(tick_m1, "_format_m1_for_csv", counting)
    end = pd.Timestamp("2026-01-05") + pd.Timedelta(days=days - 1)
    tick_m1.build_m1_from_ticks("2026-01-05", end, data_dir=tmp_path)

    assert calls["n"] - 1 == 0
