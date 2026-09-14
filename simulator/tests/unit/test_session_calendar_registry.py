"""セッションカレンダーの選択を表 1 つに集約する（ISSUE-479 Wave2 フェーズ 1-C）。

固定する仕様:
    config の session_calendar キーから実装を選ぶ規則は `SESSION_CALENDAR_REGISTRY`
    という**モジュール定数の表**が唯一の宣言であり、既定は
    `_DEFAULT_SESSION_CALENDAR` である。キーの文字列を条件式で比較する箇所は 0。

なぜ表にするか（tick model と対称に）:
    同じ Composition Root の中で、tick model はすでにレジストリ（TICK_MODEL_REGISTRY）
    ＋既定値（_DEFAULT_TICK_MODEL）という形を採っている。カレンダーだけが if 分岐の
    ままだと、「決定論 config のキーから実装を選ぶ」という同一の判断が 2 つの異なる
    形で書かれることになる。同じ判断は同じ形で書く（読み手が形から意味を推測できる）。

なぜモジュール定数か（計算量）:
    表を関数の中で組み立てると、呼び出しのたびに辞書と実装クラスの束縛をやり直す。
    出力は同じなので状態検証では落ちない。ここでは「1 回の選択につき構築は 1 個だけ」
    （選ばれなかった実装を作って捨てない）を発行回数で固定する。

対象外:
    カレンダーの判定ロジックそのもの（adapter/calendar/session_calendar.py）は
    本サイクルで触らない。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.adapter.calendar.session_calendar import Jp225SessionCalendar, NullCalendar
from simulator.main import (
    _DEFAULT_SESSION_CALENDAR,
    SESSION_CALENDAR_REGISTRY,
    _make_session_calendar,
)

_MAIN_SOURCE = Path(__file__).resolve().parents[2] / "main" / "__init__.py"


class TestTheSessionCalendarRegistryIsTheSingleDeclaration:
    """選択規則が表 1 つに集約されていること。"""

    def test_the_registry_maps_the_jp225_key_to_its_calendar(self):
        assert SESSION_CALENDAR_REGISTRY == {"jp225": Jp225SessionCalendar}

    def test_the_default_is_the_always_open_calendar(self):
        # 既定 "broker" / "none" / 未知値は常時開場（既定経路を byte-identical に保つ）。
        assert _DEFAULT_SESSION_CALENDAR is NullCalendar

    def test_no_calendar_key_literal_is_compared_outside_the_registry(self):
        """`== "jp225"` のようなキー比較が Composition Root に 0 個。"""
        tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(_MAIN_SOURCE))
        hits = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(
                isinstance(c, ast.Constant) and c.value in set(SESSION_CALENDAR_REGISTRY)
                for c in node.comparators
            )
        ]
        assert hits == [], f"カレンダーキーの比較が残っています: {hits}"

    def test_the_registry_is_a_module_level_constant(self):
        """表が関数の中で組み立てられていないこと（毎回の組み直しを構造で禁じる）。"""
        tree = ast.parse(_MAIN_SOURCE.read_text(encoding="utf-8"), filename=str(_MAIN_SOURCE))
        module_level = [
            node
            for node in tree.body
            if isinstance(node, (ast.Assign, ast.AnnAssign))
            and any(
                isinstance(t, ast.Name) and t.id == "SESSION_CALENDAR_REGISTRY"
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
            )
        ]
        assert len(module_level) == 1


class TestTheFactoryReadsTheRegistry:
    """キーから得られる実装が現行と一致すること。"""

    def test_the_jp225_key_yields_the_jp225_calendar(self):
        assert isinstance(_make_session_calendar("jp225"), Jp225SessionCalendar)

    @pytest.mark.parametrize(
        "key", ["broker", "none", "", "unknown_value"],
        ids=["broker_default", "none", "empty", "unknown"],
    )
    def test_any_other_key_yields_the_always_open_calendar(self, key):
        assert isinstance(_make_session_calendar(key), NullCalendar)


class TestTheCalendarSelectionDoesNotWasteWork:
    """計算量検定（Test Spy・発行 − 使用 = 0）。測るのは時間ではなく回数。"""

    @staticmethod
    def _counted(monkeypatch) -> "list[str]":
        """表と既定を計数ラッパへ差し替える（表の同一性は保つ＝setitem）。"""
        built: "list[str]" = []
        for key, factory in list(SESSION_CALENDAR_REGISTRY.items()):
            monkeypatch.setitem(
                SESSION_CALENDAR_REGISTRY,
                key,
                lambda _key=key, _inner=factory: (built.append(_key), _inner())[1],
            )
        import simulator.main as main_module

        default = _DEFAULT_SESSION_CALENDAR
        monkeypatch.setattr(
            main_module,
            "_DEFAULT_SESSION_CALENDAR",
            lambda _inner=default: (built.append("<default>"), _inner())[1],
        )
        return built

    def test_one_selection_builds_exactly_one_calendar(self, monkeypatch):
        built = self._counted(monkeypatch)
        _make_session_calendar("jp225")
        # 発行（構築）− 使用（返した 1 個）= 0。選ばれなかった実装を作って捨てない。
        assert len(built) - 1 == 0
        assert built == ["jp225"]

    def test_an_unknown_key_builds_only_the_default(self, monkeypatch):
        built = self._counted(monkeypatch)
        _make_session_calendar("unknown_value")
        assert built == ["<default>"]

    @pytest.mark.parametrize("calls", [1, 8], ids=["calls_1", "calls_8"])
    def test_the_build_count_is_determined_by_the_call_count_alone(
        self, monkeypatch, calls
    ):
        """呼出 1 / 8 の 2 点で「構築数 == 呼出数」（オーダーの表明・表の要素数に非比例）。"""
        built = self._counted(monkeypatch)
        for _ in range(calls):
            _make_session_calendar("jp225")
        assert len(built) - calls == 0

    def test_the_registry_object_is_not_rebuilt_between_calls(self):
        """表そのものは呼び出しをまたいで同一実体（毎回組み直していない）。"""
        import simulator.main as main_module

        before = main_module.SESSION_CALENDAR_REGISTRY
        _make_session_calendar("jp225")
        _make_session_calendar("broker")
        assert main_module.SESSION_CALENDAR_REGISTRY is before
