"""外側スライスが Composition Root から借りる名前を固定するゲート（ISSUE-479 Wave2 フェーズ 0-2）。

固定する仕様:
    simulator の**外側スライス**（`simulator/tools`・`simulator/report_ui`・
    `simulator/sim_ui`・`simulator/replay_ui`）が simulator.main から import して
    よいのは、Composition Root の責務——**組み立て・実行・設定変換の入口**——に属する
    名前だけである。計算そのもの（指標系列を作る関数）を Composition Root 経由で
    借りてはならない。

なぜ既存の層検定では捉えられないか（射程の穴）:
    `simulator/tests/unit/test_layer_dependency_direction.py` は走査対象を
    「adapter / usecase / domain / framework という名前を持つパッケージ」から導く。
    CLI・ツール群が住む `simulator/tools` や `simulator/report_ui/tools` はどの層名も
    持たないので、**構造上走査対象に入らない**
    （test_the_outer_slice_files_are_not_covered_by_the_inner_gate が実測で固定する）。
    しかも既存ゲートが見るのは「simulator.main を import したか」という**依存の向き**
    だけであり、外側スライスにとって Composition Root の参照は正当なので、向きだけでは
    何も言えない。捉えるべきは**何を借りたか**である。

    実測（本ゲート新設時）: 外側スライスが simulator.main から借りている名前は 22 種。
    そのうち 20 種は build_interactor / run_backtest / known_ea_names /
    tester_settings 配下、すなわち組み立てと設定変換であり、残る 2 件だけが
    EMA 系列の計算（ema_series とその旧名）だった。

なぜ「借りてよい責務」を表で宣言するか:
    禁止名の列挙（ブラックリスト）は、新しく main へ生えた計算関数を永久に見逃す。
    許可を**責務**で宣言し、それ以外を既定で違反にする（ホワイトリスト）ことで、
    main に計算が増えるたびに外側スライスがそれを借りれば必ず赤になる。

違反の履歴（S-2・解消済み）:
    新設時に凍結した 2 件は「EMA 系列の計算が Composition Root に居座り、2 つの外側
    スライスがそこから借りている」という 1 つの構造違反（S-2）の 2 つの現れだった。
    フェーズ 2-3 で当該計算を `simulator/adapter/indicator/madiff.py` へ移し、
    2-4 で消費側 2 行を差し替えて解消した。そのとき
    test_no_frozen_offender_is_stale が実際に赤くなり、凍結の除去を強制した
    （凍結は単調減少しかできない＝静的品質ゲートの baseline と同じ規律）。
    現在 `_FROZEN_OFFENDERS` は空であり、以後は新しい借用が 1 件でも生えれば赤になる。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

#: リポジトリ根（このファイル: <repo>/simulator/tests/unit/ → parents[3]）。
_REPO = Path(__file__).resolve().parents[3]

#: Composition Root。
_COMPOSITION_ROOT = "simulator.main"

#: 走査する外側スライス（層名を持たないため既存の層検定の射程外にある）。
_OUTER_SLICES = (
    "simulator/tools",
    "simulator/report_ui",
    "simulator/sim_ui",
    "simulator/replay_ui",
)

#: Composition Root が外側スライスへ差し出してよい**責務**。名前の列挙ではない。
#: 前方一致で判定する（tester_settings は設定→実行の変換サブパッケージ全体）。
_COMPOSITION_ROOT_DUTIES = (
    "build_",          # build_interactor / build_ea_indicators / build_ea_strategy
    "run_",            # run_backtest
    "known_",          # known_ea_names
    "present_",        # present_outputs
    "tester_settings",  # 設定ファイル → Interactor 引数の変換一式
)

#: 解消待ちの違反（単調減少のみ）。
#: 新設時の 2 件（export_report_payload.py:27 ema_series /
#: run_scan_contacts_cli.py:114 _ema_series）はフェーズ 2-4 で解消し、
#: test_no_frozen_offender_is_stale が実際に赤くなって除去を強制した。
#: 借り先が adapter であることは
#: simulator/tests/unit/test_outer_slices_borrow_ema_from_the_adapter.py が固定する。
_FROZEN_OFFENDERS: "tuple[str, ...]" = ()


@dataclass(frozen=True)
class Borrowing:
    """外側スライスが Composition Root から借りた 1 件。"""

    path: str
    line: int
    name: str
    whole_module: bool

    def ident(self) -> str:
        """凍結台帳の鍵。行番号は含めない（上の行を足すだけで鍵が変わるのを避ける）。"""
        return f"{self.path}::{self.name}"


def _is_composition_root(module: str) -> bool:
    """`simulator.main` そのもの、またはその配下モジュールか。"""
    return module == _COMPOSITION_ROOT or module.startswith(_COMPOSITION_ROOT + ".")


def _suffix_of(module: str) -> str:
    """"simulator.main.a.b" を "a.b" へ（`simulator.main` 自身は空文字）。"""
    return module[len(_COMPOSITION_ROOT):].lstrip(".")


def _is_duty(name: str) -> bool:
    """借りた名前が Composition Root の責務に属するか。"""
    return any(name.startswith(duty) for duty in _COMPOSITION_ROOT_DUTIES)


def _package_of(path: Path) -> str:
    """モジュールファイルの所属パッケージ名（相対 import の基点）。"""
    parts = list(path.relative_to(_REPO).parts)[:-1]
    return ".".join(parts)


def _resolve_relative(package: str, level: int, module: "str | None") -> str:
    """相対 import を絶対モジュール名へ解決する（`from ... import` の意味論）。"""
    base_parts = package.split(".")
    if level - 1 > len(base_parts):
        return ""
    base = base_parts[: len(base_parts) - (level - 1)]
    if module:
        base = base + module.split(".")
    return ".".join(base)


def _borrowings_in_source(source: str, package: str, filename: str) -> "list[Borrowing]":
    """ソース 1 本が Composition Root から借りた名前を列挙する。

    事前条件: `package` は `source` が置かれるパッケージ名（相対 import の基点）。
    事後条件: from 形で借りた名前（配下モジュール経由なら「サブパス.名前」）と、
             モジュール束縛の形（`import simulator.main`＝借りた名前を構文木から
             特定できない形）を返す。
    """
    out: "list[Borrowing]" = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_composition_root(alias.name):
                    out.append(Borrowing(filename, node.lineno, alias.name, True))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base = _resolve_relative(package, node.level, node.module)
            if not _is_composition_root(base):
                continue
            prefix = _suffix_of(base)
            for alias in node.names:
                name = f"{prefix}.{alias.name}" if prefix else alias.name
                out.append(Borrowing(filename, node.lineno, name, False))
    return out


def _read_source(path: Path) -> str:
    """走査の読込点（計算量検定が発行回数を数えるための単一の入口）。"""
    return path.read_text(encoding="utf-8")


def _outer_slice_files() -> "list[Path]":
    """外側スライスの全 `.py`（`__pycache__` を除く）。"""
    return sorted(
        path
        for slice_dir in _OUTER_SLICES
        for path in (_REPO / slice_dir).rglob("*.py")
        if "__pycache__" not in path.parts
    )


def _borrowing_scan_over(files, read=None) -> "tuple[list[Path], list[Borrowing]]":
    """`files` を走査して違反を返す。**1 ファイルにつき読込 1 回**。

    読込点を差し替えられるのは、計算量検定が「読み捨てが無い」ことを数えるためである
    （免除リストを持たない＝走査ファイル数 == 判定に使ったファイル数）。
    """
    reader = read or _read_source
    scanned: "list[Path]" = []
    offenders: "list[Borrowing]" = []
    for path in files:
        scanned.append(path)
        rel = str(path.relative_to(_REPO)) if path.is_relative_to(_REPO) else str(path)
        package = _package_of(path) if path.is_relative_to(_REPO) else ""
        for borrowing in _borrowings_in_source(reader(path), package, rel):
            if borrowing.whole_module or not _is_duty(borrowing.name):
                offenders.append(borrowing)
    return scanned, offenders


def _borrowing_scan() -> "tuple[list[Path], list[Borrowing]]":
    return _borrowing_scan_over(_outer_slice_files())


class TestOuterSlicesBorrowOnlyCompositionRootDuties:
    """外側スライスが Composition Root から計算を借りていないこと。"""

    def test_no_new_borrowing_outside_the_declared_duties(self):
        _, offenders = _borrowing_scan()
        new = [o for o in offenders if o.ident() not in _FROZEN_OFFENDERS]
        assert new == [], (
            "Composition Root の責務外の名前を外側スライスが借りています:\n  "
            + "\n  ".join(f"{o.path}:{o.line} {o.name}" for o in new)
            + f"\n  借りてよい責務: {list(_COMPOSITION_ROOT_DUTIES)}"
            + "\n  計算は adapter から直接借りてください（main は組み立てだけを担う）。"
        )

    def test_no_frozen_offender_is_stale(self):
        """解消済み違反の凍結残留を禁じる（凍結は単調減少しかできない）。"""
        _, offenders = _borrowing_scan()
        live = {o.ident() for o in offenders}
        stale = sorted(set(_FROZEN_OFFENDERS) - live)
        assert stale == [], (
            "解消済みなのに凍結に残っています（_FROZEN_OFFENDERS から外してください）:\n  "
            + "\n  ".join(stale)
        )


class TestTheBorrowingGateHasDetectionPower:
    """ゲートが空振りしていないこと（恒真式に退化していないこと）。"""

    def test_the_gate_actually_scans_files(self):
        scanned, _ = _borrowing_scan()
        assert len(scanned) > 0

    def test_the_outer_slice_files_are_not_covered_by_the_inner_gate(self):
        """射程の穴の実測: 違反 2 件のファイルは既存の層検定の走査対象に入っていない。"""
        from simulator.tests.unit.test_layer_dependency_direction import (
            _inner_layer_dirs,
            _layer_modules,
        )

        covered = {
            path
            for layer_dir in _inner_layer_dirs()
            for path in _layer_modules(layer_dir)
        }
        for rel in (
            "simulator/tools/run_scan_contacts_cli.py",
            "simulator/report_ui/tools/export_report_payload.py",
        ):
            assert (_REPO / rel) not in covered, rel

    @pytest.mark.parametrize(
        "form,source",
        [
            ("from 形（公開名）", "from simulator.main import ema_series"),
            ("from 形（私有名）", "from simulator.main import _ema_series"),
            ("モジュール束縛", "import simulator.main as sim_main"),
            ("配下モジュール", "from simulator.main.indicators import ema_series"),
        ],
        ids=["public_name", "private_name", "whole_module", "submodule"],
    )
    def test_every_borrowing_form_outside_the_duties_is_detected(self, form, source):
        found = _borrowings_in_source(source, package="simulator.tools", filename="x.py")
        assert [b for b in found if b.whole_module or not _is_duty(b.name)], (form, found)

    @pytest.mark.parametrize(
        "source",
        [
            "from simulator.main import build_interactor",
            "from simulator.main import run_backtest",
            "from simulator.main import known_ea_names",
            "from simulator.main.tester_settings.window import DataWindow",
        ],
        ids=["build", "run", "known", "tester_settings"],
    )
    def test_a_composition_root_duty_is_not_flagged(self, source):
        found = _borrowings_in_source(source, package="simulator.tools", filename="x.py")
        assert [b for b in found if b.whole_module or not _is_duty(b.name)] == []

    def test_a_non_composition_root_import_is_out_of_scope(self):
        found = _borrowings_in_source(
            "import pandas\nfrom simulator.adapter.indicator.madiff import ema_series\n",
            package="simulator.tools",
            filename="x.py",
        )
        assert found == []

    def test_a_similarly_named_module_is_not_a_false_positive(self):
        assert not _is_composition_root("simulator.maintenance")
        assert not _is_composition_root("simulator.sim_ui.main")

    def test_the_frozen_ledger_key_ignores_line_numbers(self):
        a = Borrowing("p.py", 27, "ema_series", False)
        b = Borrowing("p.py", 999, "ema_series", False)
        assert a.ident() == b.ident()


class TestTheBorrowingGateDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    def test_every_scanned_file_is_read_exactly_once(self):
        reads: "list[Path]" = []
        scanned, _ = _borrowing_scan_over(
            _outer_slice_files(), read=lambda p: (reads.append(p), _read_source(p))[1]
        )
        # 発行（読込）− 使用（判定に用いたファイル）= 0。読み捨てが 1 件も無い。
        assert len(reads) - len(scanned) == 0
        assert len(set(scanned)) - len(scanned) == 0  # 同じファイルを二度走査しない

    def test_the_read_count_is_determined_by_the_file_count_alone(self, tmp_path):
        """走査対象 3 件 / 6 件の 2 点で「読込数 == ファイル数」（オーダーの表明）。"""
        measured = {}
        for count in (3, 6):
            files = []
            for i in range(count):
                path = tmp_path / f"m{count}_{i}.py"
                path.write_text(
                    "from simulator.main import build_interactor\n", encoding="utf-8"
                )
                files.append(path)
            reads: "list[Path]" = []
            scanned, _ = _borrowing_scan_over(
                files, read=lambda p: (reads.append(p), p.read_text(encoding="utf-8"))[1]
            )
            measured[count] = (len(reads), len(scanned), count)
        for count, (reads_done, scanned_done, given) in measured.items():
            assert reads_done - scanned_done == 0, (count, measured)
            assert scanned_done - given == 0, (count, measured)
