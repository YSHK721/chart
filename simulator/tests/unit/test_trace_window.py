"""TraceWindow: どこを残すか（ISSUE-508 段階 3・RUN_TRACE_BASIC_DESIGN §6.4）。

固定する仕様:

    1. 窓の判定は **1 箇所**（`TraceWindow.contains`）が所有し、半開 `[start, end)` の
       規則そのものは共有実体 `datawindow.half_open.HalfOpenEpochWindow` を使う
       （境界規則の第 2 の実装を作らない）。
    2. `contains` の引数は **epoch 秒（int）**であって EvaluationPoint（`simulator/usecase/evaluation_point.py`） ではない
       （§6.5.0: 点 → epoch の導出点は ColumnarRunTrace（`simulator/adapter/trace/columnar_run_trace.py`） ただ 1 つ）。
    3. `start` / `end` は domain.bar_time.epoch_seconds を通す（時刻表現の正規化規則を
       書き直さない）。両方 `None` は窓なし＝全区間。
    4. `start > end` は **`TraceWindow` が `ConfigError` を送出する**。
       `HalfOpenEpochWindow` は空窓として黙って `contains=False` を返す仕様
       （`datawindow/half_open.py:76-78` に明記・妥当性検査は呼出側の責務）であり、
       そこに頼ると「窓を間違えたのに 0 行で成功する」run ができる。
    5. `adapter/trace` は pandas / pyarrow を import しない（D-5 の隔離）。

計算量（プロジェクト絶対命令 2026-08-28）:
    窓判定は評価点ごと（実測 952,832 回／run）に走る hot path である。判定 1 回につき
    共有実体への委譲が**ちょうど 1 回**であり、点数を増やしても 1 点あたりの発行が
    増えないことを Spy で表明する（回数そのものは焼き込まない）。
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np
import pytest

from datawindow.half_open import HalfOpenEpochWindow
from simulator.adapter.trace.trace_window import TraceWindow
from simulator.domain.exceptions import ConfigError

_EPOCH = 1_704_067_200  # 2024-01-01T00:00:00Z


# ---- 1・2: 半開 [start, end) を epoch 秒で判定する ----

class TestTheWindowKeepsTheHalfOpenRule:
    """半開 `[start, end)` の境界規則。"""

    @pytest.mark.parametrize(
        "epoch,expected",
        [
            (_EPOCH - 1, False),   # 下限の 1 つ手前
            (_EPOCH, True),        # 下限（含む）
            (_EPOCH + 59, True),   # 内側
            (_EPOCH + 60, False),  # 上限（含まない＝半開）
            (_EPOCH + 61, False),  # 上限の 1 つ先
        ],
    )
    def test_the_boundaries_follow_the_half_open_rule(self, epoch, expected):
        # Arrange
        window = TraceWindow.of(_EPOCH, _EPOCH + 60)

        # Act / Assert
        assert window.contains(epoch) is expected

    def test_the_predicate_takes_an_epoch_second_not_an_evaluation_point(self):
        """`contains` が点を知らないこと（§6.5.0: 導出点を 2 つ作らない）。"""
        # Arrange
        window = TraceWindow.of(_EPOCH, _EPOCH + 60)

        class _PointLike:
            bar_index = 0
            tick_time = None
            bar = None

        # Act / Assert: 点を渡しても比較できない（int 契約であることの表明）。
        with pytest.raises(TypeError):
            window.contains(_PointLike())

    def test_a_window_without_bounds_keeps_everything(self):
        # Arrange
        window = TraceWindow.of(None, None)

        # Act / Assert: 全区間（極端な値でも通る）。
        assert window.contains(0) is True
        assert window.contains(_EPOCH) is True
        assert window.contains(-(10**12)) is True
        assert window.contains(10**12) is True


class TestTheWindowDoesNotReimplementTheHalfOpenRule:
    """境界規則の第 2 実装を作っていないこと。"""

    def test_the_bounded_window_delegates_to_the_shared_half_open_window(self):
        # Arrange / Act
        window = TraceWindow.of(_EPOCH, _EPOCH + 60)

        # Assert: 共有実体をそのまま持つ（比較式を自前で書いていない）。
        assert isinstance(window.half_open, HalfOpenEpochWindow)
        assert (window.half_open.start, window.half_open.end) == (_EPOCH, _EPOCH + 60)

    def test_the_module_declares_no_comparison_of_its_own(self):
        """構文木に `<=` / `<` による境界比較が現れないこと。

        比較式を書き写した瞬間、半開規則が 2 箇所になる（片方だけ直る形の食い違いが
        例外を出さずに起きる）。
        """
        # Arrange
        import simulator.adapter.trace.trace_window as mod

        # Act
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        comparisons = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.Lt, ast.LtE)) for op in node.ops)
            # `start > end` の妥当性検査（Gt）は本モジュールの責務なので数えない。
        ]

        # Assert
        assert comparisons == [], [ast.dump(c) for c in comparisons]
        # 正の対照: 走査が空振りしていない（何らかのノードは読めている）。
        assert list(ast.walk(tree)), "構文木が空（走査が空振り）"


# ---- 3: 時刻表現の正規化は epoch_seconds が単一ソース ----

class TestTheBoundsAreNormalisedByTheSingleSource:
    """domain.bar_time.epoch_seconds を通すこと（規則を書き直さない）。"""

    @pytest.mark.parametrize(
        "raw",
        [
            _EPOCH,
            np.int64(_EPOCH),
            np.datetime64("2024-01-01T00:00:00"),
        ],
        ids=["int", "numpy_int64", "datetime64"],
    )
    def test_every_supported_representation_normalises_to_the_same_window(self, raw):
        # Arrange / Act
        window = TraceWindow.of(raw, _EPOCH + 60)

        # Assert
        assert window.half_open.start == _EPOCH
        assert isinstance(window.half_open.start, int)
        assert window.contains(_EPOCH) is True

    def test_an_unsupported_representation_fails_loudly(self):
        # Arrange / Act / Assert: 推測で解釈しない（epoch_seconds の契約）。
        with pytest.raises(ConfigError):
            TraceWindow.of("2024-01-01", _EPOCH + 60)

    def test_a_one_sided_window_is_refused_instead_of_being_guessed(self):
        """片側だけの指定は既定値で黙って埋めない（§7「既定値で黙って埋めない」）。

        §6.4 は「`epoch_seconds()` を通してから `HalfOpenEpochWindow` を持つ／両方
        `None` は窓なし」と定める。片側 `None` はその 2 つのどちらでもなく、欠けた側の
        境界を発明しない限り解釈できない。発明すれば「指定していない期間まで記録された
        （されなかった）」が静かに起きる。
        """
        # Arrange / Act / Assert
        with pytest.raises(ConfigError):
            TraceWindow.of(_EPOCH, None)
        with pytest.raises(ConfigError):
            TraceWindow.of(None, _EPOCH)


# ---- 4: start > end は空窓で黙らず失敗する ----

class TestAnInvertedWindowFailsInsteadOfSilentlyRecordingNothing:
    """`start > end` を `ConfigError` にすること。"""

    def test_the_inverted_window_raises(self):
        # Arrange / Act / Assert
        with pytest.raises(ConfigError) as excinfo:
            TraceWindow.of(_EPOCH + 60, _EPOCH)

        # context が**両方の境界**を正規化済み epoch 秒で運ぶ（何を間違えたかが届く）。
        # 「文言か context のどちらか」では、境界を 1 つしか載せない実装が通る。
        assert excinfo.value.context == {"start": _EPOCH + 60, "end": _EPOCH}

    def test_the_shared_window_alone_would_have_stayed_silent(self):
        """正の対照: 共有実体に委ねたままなら「0 行で成功」になっていたこと。

        この検定が無いと、上の表明が「そもそも起こり得ない事象」を守っているだけなのか、
        実在する沈黙を塞いでいるのかが分からない。
        """
        # Arrange / Act
        silent = HalfOpenEpochWindow(_EPOCH + 60, _EPOCH)

        # Assert: 共有実体は例外を出さず、全点を弾く（＝0 行で成功する run になる）。
        assert silent.contains(_EPOCH) is False
        assert silent.contains(_EPOCH + 60) is False

    def test_an_empty_but_valid_window_is_not_an_error(self):
        """`start == end` は「空だが正しい指定」であって誤りではない（境界値）。"""
        # Arrange / Act
        window = TraceWindow.of(_EPOCH, _EPOCH)

        # Assert
        assert window.contains(_EPOCH) is False


# ---- 5: 技術ドライバの隔離 ----

class TestTheWindowModuleStaysFreeOfTheStorageDrivers:
    """`trace_window` が pandas / pyarrow を読まないこと（D-5）。"""

    def test_the_module_imports_neither_pandas_nor_pyarrow(self):
        # Arrange
        import simulator.adapter.trace.trace_window as mod

        # Act
        tree = ast.parse(pathlib.Path(mod.__file__).read_text(encoding="utf-8"))
        # 分岐ではなく内包表記で集める: 入力によって実行経路が変わらないので、
        # 「どの枝を通ったか」に依らず同じ集合が出る（検出範囲は同一）。
        nodes = list(ast.walk(tree))
        imported = {
            alias.name.split(".")[0]
            for n in nodes if isinstance(n, ast.Import) for alias in n.names
        } | {
            n.module.split(".")[0]
            for n in nodes if isinstance(n, ast.ImportFrom) and n.module
        }

        # Assert
        assert imported & {"pandas", "pyarrow"} == set(), imported
        # 正の対照: 走査が空振りしていない。
        assert imported, "import が 1 件も読めていない（走査が空振り）"

    def test_importing_the_window_does_not_pull_in_pyarrow(self):
        """実際に読み込んでも技術ドライバが `sys.modules` へ載らないこと。

        構文木の検査だけでは、再輸出する `__init__.py` 経由の巻き込みを見逃す
        （§6.5.3 が `adapter/trace/__init__.py` を docstring のみと定めた理由）。
        """
        # Arrange
        import subprocess
        import sys

        code = (
            "import sys;"
            "import simulator.adapter.trace.trace_window;"
            "print(int('pyarrow' in sys.modules), int('pandas' in sys.modules))"
        )

        # Act
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=True
        ).stdout.strip()

        # Assert
        assert out == "0 0", out


class TestTheTracePackageDoesNotReExport:
    """`adapter/trace/__init__.py` が docstring のみであること（§6.5.3）。"""

    def test_the_package_init_declares_nothing(self):
        # Arrange
        import simulator.adapter.trace as pkg

        # Act
        tree = ast.parse(pathlib.Path(pkg.__file__).read_text(encoding="utf-8"))
        body = [n for n in tree.body if not isinstance(n, ast.Expr)]

        # Assert: docstring（Expr）以外の文を 1 つも持たない。
        assert body == [], [ast.dump(n) for n in body]
        # 正の対照: docstring は在る（空ファイルではない＝走査対象が実在する）。
        assert ast.get_docstring(tree), "パッケージ docstring が無い"


# ---- 計算量: hot path の判定 1 回につき委譲 1 回 ----

class _CountingHalfOpen:
    """共有実体を包み `contains` の発行回数を数える Spy。"""

    def __init__(self, inner):
        self._inner = inner
        self.calls = 0

    @property
    def start(self):
        return self._inner.start

    @property
    def end(self):
        return self._inner.end

    def contains(self, epoch):
        self.calls += 1
        return self._inner.contains(epoch)


class TestTheWindowIssuesNoThrowawayWork:
    """判定 1 回につき委譲がちょうど 1 回であること（作って捨てる形の不在）。"""

    def test_the_delegations_minus_the_decisions_is_zero(self):
        # Arrange
        window = TraceWindow.of(_EPOCH, _EPOCH + 600)
        spy = _CountingHalfOpen(window.half_open)
        window = TraceWindow(spy)

        # Act: 判定を発行する。
        decisions = [window.contains(_EPOCH + i) for i in range(0, 1200, 7)]

        # Assert: 発行（委譲）− 使用（判定結果）= 0。
        assert spy.calls - len(decisions) == 0, (spy.calls, len(decisions))
        # 正の対照: 判定 0 件なら差 0 は恒真になる。
        assert decisions, "判定が 0 件（検定が何も測っていない）"
        assert any(decisions) and not all(decisions), "窓が効いていない（全通し/全弾き）"

    def test_the_work_per_decision_does_not_grow_with_the_number_of_points(self):
        """オーダー: 点数を変えた 2 点で「1 点あたりの発行」が変わらないこと。"""
        # Arrange / Act
        measured = {}
        for count in (10, 400):
            window = TraceWindow.of(_EPOCH, _EPOCH + 600)
            spy = _CountingHalfOpen(window.half_open)
            window = TraceWindow(spy)
            for i in range(count):
                window.contains(_EPOCH + i)
            measured[count] = spy.calls

        # Assert: 発行は点数だけで決まる（1 点あたりが増えない）。
        assert measured[400] - measured[10] == 400 - 10, measured
        # 正の対照。
        assert measured[10] > 0, measured
