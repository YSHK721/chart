"""読み手を数える計測器の計算量検定（CX・プロジェクト絶対命令 2026-08-28）。

なぜ計算量で固定するか:
    本器は**リポジトリ全体のソースを読む**。読み方が退化しても出力（読み手 0 の欄の一覧）は
    1 ビットも変わらないため、状態検証では原理的に落ちない。ありうる退化は 2 つある——
    同じファイルを何度も読み直す形と、対象 DTO / 欄を 1 つ増やすたびに走査をやり直す形
    （後者は対象が増えるほど二次に伸びる）。どちらも出力を変えない。

    測るのは時間ではなく**発行の回数と配られた量**である（時間の閾値はマシン負荷で揺れ、
    緩んで浪費を通す）。

継ぎ目:
    ファイルを開く口と、そこから配られた量 … `simulator/tests/file_read_spy.py`
    （同じ Spy を手書き複製しない）。**使用**の側は本器自身のファイル列挙
    （`declared_field_readers.python_files`）から引く——1 回の走査が要るソースは
    「範囲に在るファイル 1 つにつき 1 回」である。

表明（**回数そのものは焼き込まない**）:
    CX-1  発行 − 使用 = 0（ファイルあたり読取 1 回）。
    CX-2  配られた量 − 実体の大きさ = 0（同じ中身を 2 度配らない）。
    CX-3  対象 DTO の数を増やしても走査の費用は増えない（差 0）。線形どころか不変である。
    CX-4  欄の数を増やしても読取の発行はファイル数のまま（差 0）。
    CX-5  規模 2 点（読み手側のファイル数）で、発行の増分は**増えたファイル数ぶんだけ**。
"""
from __future__ import annotations

import pytest

from simulator.tests.declared_field_readers import python_files, unread_declared_fields
from simulator.tests.file_read_spy import spy_file_reads


def _write(root, relative: str, source: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def _dto_source(name: str, fields: int) -> str:
    """欄 ``fields`` 個の DTO（最初の 1 欄だけ読み手が在る）。"""
    declared = "".join(f"    field_{i}: str\n" for i in range(fields))
    return (
        "from dataclasses import dataclass\n"
        "\n"
        "\n"
        "@dataclass(frozen=True)\n"
        f"class {name}:\n"
        f"{declared}"
    )


def _reader_source(name: str) -> str:
    return (
        f"from pkg.dto_{name.lower()} import {name}\n"
        "\n"
        "\n"
        f"def consume(bundle: {name}) -> str:\n"
        "    return bundle.field_0\n"
    )


def _tree(root, *, dtos: int, fields: int) -> None:
    """DTO を ``dtos`` 本・各 ``fields`` 欄で置き、それぞれに読み手 1 本を添える。"""
    for index in range(dtos):
        name = f"Bundle{index}"
        _write(root, f"pkg/dto_{name.lower()}.py", _dto_source(name, fields))
        _write(root, f"pkg/reader_{index}.py", _reader_source(name))


def _measure(monkeypatch, root, *, declared_under, read_under) -> dict:
    """1 回の走査で、読取の発行・配られた量・使用（範囲のファイル数）を測る。"""
    sources = python_files(root, read_under)
    log = spy_file_reads(monkeypatch)
    reported = unread_declared_fields(
        root=root, declared_under=declared_under, read_under=read_under, exemptions={},
    )
    measured = {
        "issued": sum(log.reads(path) for path in sources),
        "used": len(sources),
        "delivered": sum(log.delivered(path) for path in sources),
        "size": sum(path.stat().st_size for path in sources),
        "reported": len(reported),
    }
    monkeypatch.undo()
    return measured


class TestTheScanIssuesNoReadItDoesNotUse:
    """CX-1 / CX-2: 同じ実体を 2 度読まない・同じ中身を 2 度配らない。"""

    @pytest.mark.parametrize(
        ("dtos", "fields"), [(2, 2), (4, 2), (2, 4)], ids=["2x2", "4x2", "2x4"],
    )
    def test_each_source_is_read_once_and_delivered_once(self, dtos, fields, monkeypatch, tmp_path):
        # Arrange
        _tree(tmp_path, dtos=dtos, fields=fields)

        # Act
        measured = _measure(
            monkeypatch, tmp_path, declared_under=("pkg",), read_under=("pkg",),
        )

        # Assert: 正の対照（測定が空振りしていない＝実際に読んで、実際に報告している）。
        assert measured["used"] > 0
        assert measured["issued"] > 0
        assert measured["reported"] == dtos * (fields - 1)
        # Assert: CX-1 発行 − 使用 = 0。
        assert measured["issued"] - measured["used"] == 0, measured
        # Assert: CX-2 配られた量 − 実体の大きさ = 0。
        assert measured["delivered"] - measured["size"] == 0, measured


class TestTheCostDoesNotGrowWithTheDeclaredScope:
    """CX-3 / CX-4: 対象 DTO・欄をいくら増やしても走査の費用は増えない。"""

    def test_widening_the_declared_scope_costs_nothing_extra(self, monkeypatch, tmp_path):
        """同じソースに対し、対象 DTO 1 本と 4 本で読取の発行・配られた量が同じであること。

        ここが差を持つなら、対象 1 つごとに走査をやり直している（対象数に対して掛け算に
        なる）ということである。
        """
        # Arrange
        _tree(tmp_path, dtos=4, fields=3)

        # Act
        one = _measure(
            monkeypatch, tmp_path, declared_under=("pkg/dto_bundle0.py",), read_under=("pkg",),
        )
        four = _measure(monkeypatch, tmp_path, declared_under=("pkg",), read_under=("pkg",))

        # Assert: 正の対照（対象の広さは実際に違っている）。
        assert four["reported"] - one["reported"] > 0, (one, four)
        # Assert: CX-3 走査の費用は変わらない。
        assert four["issued"] - one["issued"] == 0, (one, four)
        assert four["delivered"] - one["delivered"] == 0, (one, four)

    def test_doubling_the_fields_does_not_add_reads(self, monkeypatch, tmp_path):
        """欄の数を 2 倍にしても、読取の発行はファイル数のままであること。"""
        # Arrange / Act
        narrow_root = tmp_path / "narrow"
        wide_root = tmp_path / "wide"
        _tree(narrow_root, dtos=2, fields=3)
        _tree(wide_root, dtos=2, fields=6)
        narrow = _measure(
            monkeypatch, narrow_root, declared_under=("pkg",), read_under=("pkg",),
        )
        wide = _measure(monkeypatch, wide_root, declared_under=("pkg",), read_under=("pkg",))

        # Assert: 正の対照（欄は実際に増えている＝報告件数が増えている）。
        assert wide["reported"] - narrow["reported"] > 0, (narrow, wide)
        # Assert: CX-4 ファイル数は同じなので発行も同じ（欄の数に引っ張られない）。
        assert narrow["used"] == wide["used"], (narrow, wide)
        assert wide["issued"] - narrow["issued"] == 0, (narrow, wide)
        assert wide["issued"] - wide["used"] == 0, (narrow, wide)


class TestTheReadsGrowOnlyWithTheNumberOfSources:
    """CX-5: 規模 2 点（読み手側のファイル数）でのオーダーの表明。"""

    def test_the_issue_count_grows_only_by_the_added_sources(self, monkeypatch, tmp_path):
        # Arrange
        small_root = tmp_path / "small"
        large_root = tmp_path / "large"
        _tree(small_root, dtos=2, fields=3)
        _tree(large_root, dtos=6, fields=3)

        # Act
        small = _measure(monkeypatch, small_root, declared_under=("pkg",), read_under=("pkg",))
        large = _measure(monkeypatch, large_root, declared_under=("pkg",), read_under=("pkg",))

        # Assert: 正の対照（ファイル数は実際に増えている）。
        assert large["used"] - small["used"] > 0, (small, large)
        # Assert: CX-5 発行の増分は増えたファイル数ぶんだけ（超線形にならない）。
        assert (large["issued"] - small["issued"]) - (large["used"] - small["used"]) == 0, (
            small, large,
        )
