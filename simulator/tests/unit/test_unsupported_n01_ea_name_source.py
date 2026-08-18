"""N-01 の判定源（実行可能な EA 名の集合）を固定する（🟡-4 の是正・基本設計 §4.6）。

背景（実測）:
    `unsupported._detect_unknown_ea` が読むのは **注入された** `binding.known_ea_names`
    である（`_EA_FACTORIES` を import すらしていない）。にもかかわらず N-01 の `reason`
    は「実行可能な EA は現行 `_EA_FACTORIES` の登録集合に限られます」と書いており、
    実装と食い違っていた。両者は実際に一致しない——`_EA_FACTORIES` のキーは 5 件、
    注入元の `SymbolSpecCatalog.ea_names()` は 6 件（既定フォールバック EA を含む）。

固定する仕様:
    1. `reason` は実装が読む集合（注入集合）を指す。実装が読まない `_EA_FACTORIES`
       を判定源として名指さない。
    2. 注入集合 ⊇ `_EA_FACTORIES` のキー。差分は**既定フォールバック EA 名のみ**。
    3. 上の 2 が意味するとおり、`_EA_FACTORIES` 未登録でも注入集合に載る名前は
       N-01 を通り、注入集合に無い名前は N-01 で止まる（振る舞いで測る）。

なぜ `reason` を測るのか:
    `reason` は例外 `context` に載って呼出側へ届く唯一の説明であり、これが誤っていると
    「登録表に足せば直る」という誤った是正へ人を誘導する（実際の是正は注入側の変更）。
    文言の正誤は安全性ではなく**診断の正しさ**の問題であり、テストで固定する対象である。
"""
from __future__ import annotations

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.main import _EA_FACTORIES
from simulator.main.tester_settings.kwargs_mapper import to_interactor_kwargs
from simulator.main.tester_settings.unsupported import RULES
from simulator.main import DEFAULT_EA_NAME as _DEFAULT_EA
from simulator.sim_ui.main.composition_root_jobs import build_run_options_port
from simulator.tests.tester_settings_engine_fixtures import (
    DEFAULT_EA_NAME,
    engine_binding,
    runnable_settings,
)

#: 判定源のうち実装が実際に読む側（注入集合）の出所。テストが名前を再宣言しない。
INJECTED_EA_NAMES = frozenset(build_run_options_port().ea_names())
FACTORY_KEYS = frozenset(_EA_FACTORIES)


class TestReasonMatchesTheImplementation:
    """`reason` が、実装が読む判定源を指していること。"""

    def test_the_reason_does_not_name_a_registry_the_detector_never_reads(self):
        # `_detect_unknown_ea` は `_EA_FACTORIES` を参照しない（import もしていない）
        assert "_EA_FACTORIES" not in RULES["N-01"].reason

    def test_the_reason_names_the_injected_set(self):
        assert "注入" in RULES["N-01"].reason


class TestInjectedSetVersusTheFactoryRegistry:
    """注入集合と登録表の関係（⊇ と、差分が既定フォールバック名のみ）。"""

    def test_every_registered_factory_key_is_executable(self):
        assert FACTORY_KEYS <= INJECTED_EA_NAMES

    def test_the_only_extra_name_is_the_default_fallback_ea(self):
        assert INJECTED_EA_NAMES - FACTORY_KEYS == {_DEFAULT_EA}

    def test_the_default_fallback_ea_is_not_a_registered_factory_key(self):
        # これが偽なら上の差分は空になり、2 集合を区別するテストが退化する
        assert _DEFAULT_EA not in FACTORY_KEYS

    def test_the_test_fixture_does_not_hold_a_stale_copy_of_the_fallback_name(self):
        # `tester_settings_engine_fixtures.DEFAULT_EA_NAME` は同じ名前を指す。
        # ISSUE-405 以降はどちらも `simulator.main.DEFAULT_EA_NAME`（フォールバック先の
        # 所有者）から引くため、写しは存在しない。その関係をここで固定する。
        assert DEFAULT_EA_NAME == _DEFAULT_EA


class TestRejectionFollowsTheInjectedSet:
    """振る舞い: 受理・拒否は注入集合が決める（登録表ではない）。"""

    def test_a_name_absent_from_the_factory_registry_is_accepted_when_injected(self):
        # `_DEFAULT_EA` は登録表に無いが注入集合には載る＝N-01 を通る
        kwargs = to_interactor_kwargs(
            runnable_settings(Expert=f"{_DEFAULT_EA}.ex5"),
            engine_binding(data_path="/synthetic/jp225.csv"),
        )
        assert kwargs["ea_name"] == _DEFAULT_EA

    def test_a_name_absent_from_the_injected_set_is_rejected(self):
        with pytest.raises(ConfigError) as excinfo:
            to_interactor_kwargs(
                runnable_settings(Expert="NotAnExecutableEA.ex5"),
                engine_binding(data_path="/synthetic/jp225.csv"),
            )
        assert excinfo.value.context["unsupported_id"] == "N-01"

    def test_a_registered_factory_key_is_rejected_when_it_is_not_injected(self):
        # 判定源が登録表なら通ってしまう入力。注入集合が権威であることを分ける実例。
        registered = sorted(FACTORY_KEYS)[0]
        with pytest.raises(ConfigError) as excinfo:
            to_interactor_kwargs(
                runnable_settings(Expert=f"{registered}.ex5"),
                engine_binding(data_path="/synthetic/jp225.csv", known_ea_names=()),
            )
        assert excinfo.value.context["value"] == registered
