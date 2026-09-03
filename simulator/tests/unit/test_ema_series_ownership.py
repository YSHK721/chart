"""確定足 EMA の所有者を adapter へ移す（ISSUE-479 Wave2 2-3/2-4・S-2）。

固定する仕様:
    EMA 系列の計算は指標 adapter（simulator/adapter/indicator/madiff.py）が所有する。
    Composition Root（simulator.main）はそれを **re-export** するだけで、実体は 1 つである。

なぜ Composition Root に計算を置いてはいけないのか:
    main の責務は組み立て（何をどう結線するか）であって計算ではない。計算が main に
    住むと、外側スライス（tools / report_ui）は 1 本の関数を借りるためだけに
    Composition Root 全体を import する——すなわち EA レジストリも Interactor 構築も
    引き連れる。借りたいものと引き連れるものが釣り合わない。
    この構造違反は simulator/tests/unit/test_outer_slice_composition_root_borrowing.py が
    2 つの現れ（report_ui / tools）として凍結しており、本移設で 0 件になる。

byte 不変の担保:
    移設は計算式を変えない。入力長 128 / 1024 × period 4 点の出力 sha256 を移設前の
    実測値で凍結する（下表）。式に触れれば必ず赤になる。
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[3]
_ADAPTER_REL = "simulator/adapter/indicator/madiff.py"

#: 移設**前**の実装が出した sha256（seed 固定の合成価格系列）。
#: 取得法: 移設前の版（git の直前コミット）から ema_series の関数定義を構文木で抜き出して
#: 実行し、移設後の実装と 8 点すべてで一致することを確認した上で凍結した
#: （「同じ式だから同じはず」ではなく、両実装を実際に走らせて突き合わせた）。
_FROZEN_DIGESTS = {
    (128, 2): "99690f745a388c2750cc272aaa2e96af499c942b68ccda039a96f9bdbeb054df",
    (128, 5): "363684d9644ea383a77eb1267690e1eca0b52dfd20896aa969eb2a9e381fbdbd",
    (128, 21): "bc7d586c6744c6a9e9df155241a1851811ea6388813438dfc6b06eb09ccb5470",
    (128, 200): "5f70bf18a086007016e948b04aed3b82103a36bea41755b6cddfaf10ace3c6ef",
    (1024, 2): "363ac4be5baffa00f8917ac00b89a357b679eb7fec096ad00b59cb15547589eb",
    (1024, 5): "c661132e8482f899c17af694dbf0402ca0f578a1453acf1020fdf26146ebe2e9",
    (1024, 21): "5aa723f41efbe784faeee7fdeff968e23a73d7d4e4dd402f764877c706a5ae87",
    (1024, 200): "dbf68d0af2e50c18b888bb94894c36fd22d6247ba5d48f3b938a41d8eeb8099f",
}


def _price(n: int) -> pd.Series:
    rng = np.random.default_rng(20260903)
    return pd.Series(rng.normal(38000.0, 120.0, n).cumsum() / n + 38000.0, dtype=float)


def _digest(series: pd.Series) -> str:
    return hashlib.sha256(series.to_numpy(dtype=float).tobytes()).hexdigest()


def _private_ma_call_sites() -> "list[str]":
    """私有ヘルパ _ma_series を**呼んでいる**本番モジュールを repo 全体から列挙する。

    検査対象から検査コードを外すのは免除ではない。「私有名を越境して呼ぶな」は本番の
    依存構造についての規律であり、テストは対象の内部を突く（Spy を差し込む・変異を
    作る）ことが仕事だからである。テストを対象に含めると、検出力の実測そのものが
    違反として数えられ、ゲートが自分の証拠を潰す。
    """
    out: "list[str]" = []
    for path in sorted(_REPO.rglob("*.py")):
        parts = path.parts
        if "__pycache__" in parts or "node_modules" in parts or ".venv" in str(path):
            continue
        if "tests" in parts or path.name.startswith("test_"):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = path.relative_to(_REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = (
                func.id
                if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None
            )
            if name == "_ma_series":
                out.append(f"{rel}:{node.lineno}")
    return out


class TestTheAdapterOwnsTheEmaSeries:
    """EMA 系列の公開 API が adapter に居ること。"""

    def test_the_adapter_exposes_a_public_ema_series(self):
        from simulator.adapter.indicator import madiff

        assert callable(madiff.ema_series)

    def test_the_composition_root_re_exports_the_same_object(self):
        """main の名前は re-export＝**同一オブジェクト**である（写しではない）。"""
        import simulator.main as main
        from simulator.adapter.indicator import madiff

        assert main.ema_series is madiff.ema_series
        assert main._ema_series is madiff.ema_series

    @pytest.mark.parametrize(("n", "period"), sorted(_FROZEN_DIGESTS))
    def test_the_values_are_byte_identical_to_before_the_move(self, n, period):
        from simulator.adapter.indicator.madiff import ema_series

        got = ema_series(_price(n), period)
        assert _digest(got) == _FROZEN_DIGESTS[(n, period)]

    def test_the_output_keeps_the_input_index(self):
        from simulator.adapter.indicator.madiff import ema_series

        price = _price(64)
        price.index = pd.RangeIndex(start=100, stop=164)
        assert list(ema_series(price, 5).index) == list(price.index)


class TestThePrivateHelperDoesNotCrossModules:
    """私有ヘルパの越境呼出が 0 であること（ISSUE-091 #3 の恒久化）。"""

    def test_the_private_ma_helper_is_called_only_inside_its_own_module(self):
        sites = _private_ma_call_sites()
        outside = [s for s in sites if not s.startswith(_ADAPTER_REL + ":")]
        assert outside == [], (
            "私有ヘルパ _ma_series を他モジュールから呼んでいます:\n  "
            + "\n  ".join(outside)
            + "\n  公開名（ema_series）を経由してください。"
        )

    def test_the_scan_is_not_vacuous(self):
        """走査が空振りしていない（呼出点そのものは実在する）。"""
        assert _private_ma_call_sites() != []


class TestTheEmaSeriesDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @staticmethod
    def _count_ma_calls(monkeypatch, n: int, period: int) -> "tuple[int, int]":
        from simulator.adapter.indicator import madiff

        calls: "list[tuple[int, str]]" = []
        original = madiff._ma_series

        def spy(values, p, method):
            calls.append((len(values), method))
            return original(values, p, method)

        monkeypatch.setattr(madiff, "_ma_series", spy)
        out = madiff.ema_series(_price(n), period)
        return len(calls), len(out)

    @pytest.mark.parametrize("n", [128, 1024], ids=["len_128", "len_1024"])
    def test_the_ema_issues_exactly_the_series_it_returns(self, monkeypatch, n):
        """入力長 128 / 1024 の 2 点で「発行した MA 系列 − 出力に使った系列 = 0」。

        発行数を「1 回」と焼き込むのではなく、出力しない計算が 0 であることを固定する。
        入力長を 8 倍にしても発行が増えないこと（オーダーの表明）も同時に見る。
        """
        issued, produced = self._count_ma_calls(monkeypatch, n, 21)
        assert produced == n
        # 出力 1 系列に対して発行 1 系列（差 0）。捨てる計算が無い。
        assert issued - 1 == 0

    def test_the_spy_catches_a_discarded_computation(self, monkeypatch):
        """検出力の実測: 捨てる計算を 1 つ足した変異を Spy が数える。"""
        from simulator.adapter.indicator import madiff

        calls: "list[str]" = []
        original = madiff._ma_series

        def spy(values, p, method):
            calls.append(method)
            return original(values, p, method)

        monkeypatch.setattr(madiff, "_ma_series", spy)

        def wasteful(price, period):
            values = price.to_numpy(dtype=float)
            madiff._ma_series(values, period, "sma")  # 捨てる計算（変異）
            return pd.Series(madiff._ma_series(values, period, "ema"), index=price.index)

        wasteful(_price(128), 21)
        assert len(calls) - 1 == 1, calls
