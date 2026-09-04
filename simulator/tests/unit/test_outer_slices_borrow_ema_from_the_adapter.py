"""外側スライスが EMA を adapter から直接借りることを固定する（ISSUE-479 Wave2 2-4・S-2）。

固定する仕様:
    simulator/report_ui/tools/export_report_payload.py と
    simulator/tools/run_scan_contacts_cli.py は、EMA 系列を Composition Root
    （simulator.main）からではなく指標 adapter から借りる。main から借りてよいのは
    組み立て・実行・設定変換（build_ / run_ / known_ / present_ / tester_settings）だけである。

なぜ「動くから main 経由でよい」では駄目か:
    main を import すると EA レジストリ構築も Interactor 組み立ても道連れになる。
    借りたい 1 本の関数と、引き連れる依存の量が釣り合わない。さらに main が計算の
    通り道になっている限り、そこへ新しい計算が生えるたび外側スライスは何も抵抗なく
    それを借りられる。責務の宣言（0-2 ゲート）と、実際の借り先（本ファイル）の
    両方が要る。

出力不変の担保:
    差し替えは同一オブジェクトへの経路変更にすぎない。ma_values（bar_index → EMA 値）の
    sha256 を差し替え前の実測値で凍結する。経路が別実装へすり替われば必ず赤になる。
"""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[3]

#: EMA を借りる外側スライスのモジュール（借り先を検査する対象）。
_CONSUMERS = (
    "simulator/report_ui/tools/export_report_payload.py",
    "simulator/tools/run_scan_contacts_cli.py",
)

#: Composition Root。ここから EMA を借りてはならない。
_COMPOSITION_ROOT = "simulator.main"

#: EMA 系列の所有者（借りてよい先）。
_OWNER = "simulator.adapter.indicator.madiff"

#: 差し替え**前**に実測した ma_values の sha256（seed 固定の合成終値・ma_period=60）。
_FROZEN_MA_VALUES = {
    64: "efb139c3429e49b2e13b1ca1ca1abb35c5b487f8a9e466b8f223c0dc5d4a42af",
    512: "6139c53fd040b6719ccd7da7d602cdc6aba41044c8c5eee50cb6f426b47b7b31",
}


def _ema_imports_of(rel: str) -> "list[tuple[str, str, int]]":
    """`rel` が EMA 系列（ema_series / _ema_series）をどのモジュールから借りたか。"""
    path = _REPO / rel
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
    out: "list[tuple[str, str, int]]" = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name in ("ema_series", "_ema_series"):
                    out.append((node.module, alias.name, node.lineno))
    return out


def _closes(n: int) -> pd.Series:
    """seed 固定の合成終値（同じ n なら常に同じ系列）。"""
    rng = np.random.default_rng(20260904)
    return pd.Series(rng.normal(38000.0, 90.0, n).cumsum() / n + 38000.0, dtype=float)


def _ma_values_digest(n: int) -> str:
    """export_report_payload が組む ma_values と**同じ手順**で写像を作り指紋を取る。"""
    import simulator.report_ui.tools.export_report_payload as payload

    ema = payload.ema_series(_closes(n), payload.COMMON["ma_period"])
    ma_values = {i: float(v) for i, v in enumerate(ema.to_numpy())}
    blob = json.dumps({str(k): repr(v) for k, v in sorted(ma_values.items())}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


class TestTheConsumersBorrowFromTheOwner:
    """借り先が adapter であること（構文で固定する）。"""

    @pytest.mark.parametrize("rel", _CONSUMERS, ids=lambda r: Path(r).stem)
    def test_the_ema_is_not_borrowed_from_the_composition_root(self, rel):
        borrowed = _ema_imports_of(rel)
        from_root = [b for b in borrowed if b[0] == _COMPOSITION_ROOT]
        assert from_root == [], (
            f"{rel} が Composition Root から EMA を借りています: {from_root}\n"
            f"  借り先は {_OWNER} です（main は組み立てだけを担う）。"
        )

    @pytest.mark.parametrize("rel", _CONSUMERS, ids=lambda r: Path(r).stem)
    def test_the_ema_is_borrowed_from_the_adapter(self, rel):
        borrowed = _ema_imports_of(rel)
        assert borrowed != [], f"{rel} が EMA を借りている箇所が見つかりません"
        assert all(module == _OWNER for module, _, _ in borrowed), borrowed

    @pytest.mark.parametrize("rel", _CONSUMERS, ids=lambda r: Path(r).stem)
    def test_the_public_name_is_used(self, rel):
        """私有名（_ema_series）ではなく公開名を借りる（ISSUE-091 #3）。"""
        borrowed = _ema_imports_of(rel)
        private = [b for b in borrowed if b[1].startswith("_")]
        assert private == [], private


class TestTheReportPayloadKeepsItsValues:
    """借り先を変えても出力が 1 bit も動かないこと。"""

    def test_the_consumer_holds_the_owners_object(self):
        import simulator.report_ui.tools.export_report_payload as payload
        from simulator.adapter.indicator import madiff

        assert payload.ema_series is madiff.ema_series

    @pytest.mark.parametrize("n", sorted(_FROZEN_MA_VALUES), ids=lambda n: f"bars_{n}")
    def test_the_ma_values_digest_is_unchanged(self, n):
        assert _ma_values_digest(n) == _FROZEN_MA_VALUES[n]


class TestTheScanHasDetectionPower:
    """検査が空振りしていないこと。"""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("from simulator.main import ema_series\n", [("simulator.main", "ema_series", 1)]),
            ("from simulator.main import _ema_series\n", [("simulator.main", "_ema_series", 1)]),
            ("from simulator.main import build_interactor\n", []),
            ("import simulator.main\n", []),
        ],
        ids=["public_from_root", "private_from_root", "duty", "module_binding"],
    )
    def test_the_import_form_is_classified(self, tmp_path, source, expected, monkeypatch):
        target = tmp_path / "probe.py"
        target.write_text(source, encoding="utf-8")
        monkeypatch.setattr(
            "simulator.tests.unit.test_outer_slices_borrow_ema_from_the_adapter._REPO",
            tmp_path,
        )
        assert _ema_imports_of("probe.py") == expected


class TestTheConsumerDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @pytest.mark.parametrize("n", [64, 512], ids=["bars_64", "bars_512"])
    def test_building_ma_values_issues_one_series_per_output(self, monkeypatch, n):
        """足 64 / 512 の 2 点で「発行した EMA 系列 − 出力に使った系列 = 0」。

        足数を 8 倍にしても発行は増えない（オーダーの表明）。発行数そのものは
        期待値へ焼き込まない。
        """
        import simulator.report_ui.tools.export_report_payload as payload
        from simulator.adapter.indicator import madiff

        calls: "list[int]" = []
        original = madiff.ema_series

        def spy(price, period):
            calls.append(len(price))
            return original(price, period)

        monkeypatch.setattr(payload, "ema_series", spy)

        ema = payload.ema_series(_closes(n), payload.COMMON["ma_period"])
        ma_values = {i: float(v) for i, v in enumerate(ema.to_numpy())}

        assert len(ma_values) == n
        assert len(calls) - 1 == 0  # 出力 1 系列に対して発行 1 系列（捨てる計算が 0）

    def test_the_spy_catches_a_second_issue(self, monkeypatch):
        """検出力の実測: 系列をもう 1 本作る変異を Spy が数える。"""
        from simulator.adapter.indicator import madiff

        calls: "list[int]" = []
        original = madiff.ema_series

        def spy(price, period):
            calls.append(len(price))
            return original(price, period)

        monkeypatch.setattr(madiff, "ema_series", spy)
        closes = pd.Series(np.arange(64, dtype=float) + 38000.0)
        madiff.ema_series(closes, 60)
        madiff.ema_series(closes, 60)  # 捨てる計算（変異）
        assert len(calls) - 1 == 1, calls
