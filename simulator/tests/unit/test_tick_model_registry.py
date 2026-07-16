"""tick_model 単一レジストリの回帰ガード（ISSUE-097 🟡-5・OCP）。

`framework/config_loader` の Literal 許容値・`main` の `_TICK_MODELS` dict・
`main` の `real_ticks` 別分岐に三分散していた tick_model の許容値／構築知識を、
`adapter/execution/tick_model_registry` の単一レジストリへ集約した。本テストは:

  1. レジストリの id 集合が従来の 4 モデル（every_tick/ohlc_expand/open_only/
     real_ticks）と一致すること
  2. config_loader の Literal 許容値がレジストリから導出されていること（同一集合）
  3. 各 id が従来と同一の TickModelPort 実装／同一分岐（synthetic か real_ticks か）
     へ解決されること

を固定し、「単一レジストリへ集約しても既存 4 モデルの挙動・分岐先は完全不変」を
実証する（byte 不変の構造ガード）。
"""
from __future__ import annotations

import typing

from simulator.adapter.execution.tick_model import (
    EveryTickModel,
    OhlcExpandTickModel,
    OpenOnlyTickModel,
)
from simulator.adapter.execution.tick_model_registry import (
    TICK_MODEL_IDS,
    TICK_MODEL_REGISTRY,
)
from simulator.usecase.ports import TickModelPort


# --- レジストリ id 集合の不変性 ---------------------------------------------

def test_registry_ids_match_the_four_known_models():
    # 従来 config_loader の Literal 4 値・main の _TICK_MODELS(+real_ticks) と同一集合。
    assert set(TICK_MODEL_IDS) == {
        "every_tick",
        "ohlc_expand",
        "open_only",
        "real_ticks",
    }


def test_registry_ids_preserve_config_loader_literal_order():
    # config_loader の Literal 記載順（every_tick→ohlc_expand→open_only→real_ticks）を保つ。
    assert TICK_MODEL_IDS == (
        "every_tick",
        "ohlc_expand",
        "open_only",
        "real_ticks",
    )


# --- config_loader Literal 許容値がレジストリ由来であること -------------------

def test_config_loader_literal_is_derived_from_registry():
    # 単一情報源化: _ConfigModel.tick_model の Literal 許容値 == レジストリ id 集合。
    from simulator.framework.config_loader import _ConfigModel

    annotation = _ConfigModel.model_fields["tick_model"].annotation
    assert set(typing.get_args(annotation)) == set(TICK_MODEL_IDS)


# --- 各 id の分岐先（synthetic / real_ticks）の不変性 ------------------------

def test_synthetic_models_are_not_flagged_real_ticks():
    for key in ("every_tick", "ohlc_expand", "open_only"):
        assert TICK_MODEL_REGISTRY[key].requires_real_ticks is False
        assert TICK_MODEL_REGISTRY[key].synthetic_builder is not None


def test_real_ticks_is_flagged_and_has_no_synthetic_builder():
    spec = TICK_MODEL_REGISTRY["real_ticks"]
    assert spec.requires_real_ticks is True
    assert spec.synthetic_builder is None


# --- synthetic_builder が従来と同一実装を生成すること ------------------------

def test_every_tick_builder_constructs_every_tick_model():
    impl = TICK_MODEL_REGISTRY["every_tick"].synthetic_builder("ohlc")
    assert isinstance(impl, EveryTickModel)
    assert isinstance(impl, TickModelPort)


def test_open_only_builder_constructs_open_only_model():
    impl = TICK_MODEL_REGISTRY["open_only"].synthetic_builder("ohlc")
    assert isinstance(impl, OpenOnlyTickModel)


def test_ohlc_expand_builder_threads_ohlc_order_argument():
    # 従来 main._make_tick_model は ohlc_expand のみ order を渡す（他は無視）。
    impl = TICK_MODEL_REGISTRY["ohlc_expand"].synthetic_builder("olhc")
    assert isinstance(impl, OhlcExpandTickModel)
    assert impl._order == "olhc"


# --- main._make_tick_model がレジストリ経由で従来と同一挙動を保つこと ----------

def test_make_tick_model_resolves_each_key_to_expected_type():
    from simulator.main import _make_tick_model

    assert isinstance(_make_tick_model("every_tick"), EveryTickModel)
    assert isinstance(_make_tick_model("open_only"), OpenOnlyTickModel)
    assert isinstance(
        _make_tick_model("ohlc_expand", ohlc_order="olhc"), OhlcExpandTickModel
    )


def test_make_tick_model_unknown_key_falls_back_to_ohlc_expand_default():
    # 従来 _DEFAULT_TICK_MODEL=OhlcExpandTickModel()（order 既定="ohlc"）へフォールバック。
    from simulator.main import _make_tick_model

    impl = _make_tick_model("nonexistent_key")
    assert isinstance(impl, OhlcExpandTickModel)
    assert impl._order == "ohlc"
