"""PARAM SCOPES（ISSUE-278 #8）— 「宣言した param 集合 == その variant の実受理引数」を固定する。

背景（実測・実 HTTP :8000）: ``params_defaults`` の宣言粒度が compute_id、実契約（add_* の
シグネチャ）が variant だったため、差分を ``_accepted_kwargs`` が無言で捨てていた。UI は
効かないコントロールを出し続けた。

  | 指標 | variant | 動かしても応答 byte 同一だった param |
  |---|---|---|
  | profit_band | global | normalize / window / atr_period / min_obs |
  | profit_band | robust | require_full |
  | profit_hlband | overlay | draw_levels |

是正: 宣言粒度を variant へ揃え、無言破棄を廃した（未受理キーは ValueError）。本ファイルは
その宣言が **実装（シグネチャ）から乖離しないこと** を構造的に固定する。宣言を 1 つずらすと
Red になる＝識別力を持つ（宣言と実装の二重管理を検定で 1 つに縛る）。
"""

from __future__ import annotations

import pytest


def _table():
    from adapter.compute import call_binding

    return call_binding._TABLE


def test_every_entry_declares_its_own_params_defaults():
    # 宣言粒度は variant。1 エントリでも欠けると導出が ValueError（漏れの構造的検出）。
    from adapter.compute.call_binding import indicator_param_defaults

    for key, spec in _table().items():
        assert "params_defaults" in spec, f"params_defaults 未宣言: {key}"
    indicator_param_defaults()  # 食い違い・漏れがあればここで ValueError


@pytest.mark.parametrize("key", list(_table()))
def test_declared_params_match_the_real_signature(key):
    """宣言キー集合 == add_* の受理引数（invoke 自身が消費する param を除く）。

    ``**kwargs`` を持つ callable は受理集合が定義できないため、宣言が受理集合の
    部分集合であることだけを課す（過剰宣言の検出）。
    """
    from adapter.compute.call_binding import (
        _KIND_CONSUMED_PARAMS,
        LAYER_CONSUMED_PARAMS,
        accepted_param_names,
    )

    spec = _table()[key]
    declared = set(spec["params_defaults"])
    consumed = set(LAYER_CONSUMED_PARAMS) | set(_KIND_CONSUMED_PARAMS[spec["kind"]])
    accepted = accepted_param_names(spec["loader"]())
    if accepted is None:
        return  # **kwargs＝無制限。乖離を定義できない。
    assert declared - consumed - accepted == set(), (
        f"{key}: 宣言しているが add_* が受理しない param（＝UI に出しても効かない）"
    )


def test_param_scopes_are_derived_from_the_declaration():
    # PARAM_SCOPES は宣言からの導出値であり独立定義を持たない（二重定義の再発を検出）。
    from adapter.compute.call_binding import indicator_param_scopes
    from adapter.compute.catalog_schema import PARAM_SCOPES

    assert PARAM_SCOPES == indicator_param_scopes()


def test_layer_consumed_param_is_in_every_scope():
    # 計算.時間足は全 variant で送られる（front が必ず載せる）。scope から漏れると
    #   front が送信を落とし、上位足計算が静かに効かなくなる。
    from adapter.compute.call_binding import LAYER_CONSUMED_PARAMS, indicator_param_scopes

    for compute_id, by_variant in indicator_param_scopes().items():
        for variant, names in by_variant.items():
            assert LAYER_CONSUMED_PARAMS <= set(names), (compute_id, variant)


def test_variant_specific_params_are_not_shared():
    # 実測で「効かないコントロール」だった 3 件が、正しい variant にのみ属することを固定する。
    from adapter.compute.call_binding import indicator_param_scopes

    scopes = indicator_param_scopes()
    pb = scopes["profit_band"]
    assert "require_full" in pb["global"] and "require_full" not in pb["robust"]
    for name in ("normalize", "window", "atr_period", "min_obs"):
        assert name in pb["robust"] and name not in pb["global"], name
    hl = scopes["profit_hlband"]
    assert "draw_levels" in hl["separate"] and "draw_levels" not in hl["overlay"]


def test_unaccepted_param_fails_closed_instead_of_being_dropped():
    # 無言破棄の撤去（ISSUE-278 #8）: 受理しない param は例外＝validation エラーへ翻訳される。
    from adapter.compute.call_binding import _bind_kwargs

    def add_dummy(chart, df, *, alpha=1):
        return None

    assert _bind_kwargs(add_dummy, {"alpha": 2}) == {"alpha": 2}
    with pytest.raises(ValueError) as exc:
        _bind_kwargs(add_dummy, {"alpha": 2, "beta": 3})
    assert "beta" in str(exc.value)


def test_varkw_callable_passes_through():
    from adapter.compute.call_binding import _bind_kwargs

    def add_any(chart, df, **kwargs):
        return None

    assert _bind_kwargs(add_any, {"whatever": 1}) == {"whatever": 1}
