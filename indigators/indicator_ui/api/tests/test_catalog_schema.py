"""CATALOG_SCHEMA / GET /catalog（ISSUE-092 ③ / ISSUE-180）— param 既定値の単一情報源の構造テスト。

single source（``adapter.compute.catalog_schema.PARAM_DEFAULTS``）が
  - ``call_binding._TABLE`` の compute_id 集合と一致（指標追加時の登録漏れを検出）
  - front 静的フォールバック契約 ``web/js/adapter/front/catalog_defaults.json`` と一致
    （back 既定値 == front 静的フォールバック値の乖離を検出・ISSUE-092 ③ 要件④）
  - ``call_binding._DEFAULT_SAMPLES`` と mcmc_samples 既定が一致（back 内二重定義の解消）
を固定する。``handle_catalog`` は正典契約（ok / nested error）で schema を配信する。

ISSUE-180（OCP）以降、既定値の定義位置は指標記述子 ``call_binding._TABLE`` の
``params_defaults`` であり ``PARAM_DEFAULTS`` はその導出値である。本ファイルは上記の乖離検出に
加えて「導出の整合」（記述子 1 エントリ = 1 指標の宣言／導出値と配信値の一致／配信 key 順の維持）
を固定する。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# catalog_defaults.json（back single source の配布シリアライズ＝front/back 既定値の同期契約オラクル）。
#   golden/ は *.json blanket ignore から除外された固定 fixture 置き場（.gitignore §10.3 M-2）。
#   front（catalog_schema_sync.test.js）も同一ファイルを読み、back 既定値 == front 静的フォールバック値
#   を双方から固定する。this file: api/tests/ → golden/catalog_defaults.json。
_CATALOG_JSON = Path(__file__).resolve().parent / "golden" / "catalog_defaults.json"


def test_catalog_defaults_covers_exactly_table_compute_ids():
    # single source は _TABLE の全 compute_id を過不足なく網羅する（登録漏れ・余剰を検出）。
    from adapter.compute import call_binding
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    table_ids = {compute_id for (compute_id, _variant) in call_binding._TABLE}
    assert set(PARAM_DEFAULTS) == table_ids


def test_param_defaults_are_derived_from_the_binding_table():
    # ISSUE-180: PARAM_DEFAULTS は指標記述子 _TABLE からの導出値であり独立定義を持たない。
    #   catalog_schema にリテラルを再び置く（二重定義の再発）とこの等式が壊れて検出される。
    from adapter.compute.call_binding import indicator_param_defaults
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    assert PARAM_DEFAULTS == indicator_param_defaults()


def test_param_defaults_declared_once_per_compute_id_in_the_binding_table():
    # 記述子は「1 指標 = 1 宣言」。宣言漏れ（指標追加時の登録漏れ）と、複数 variant への
    #   二重宣言（値の食い違いを生む）を構造的に禁じる。
    from adapter.compute import call_binding

    declared: dict[str, int] = {}
    for (compute_id, _variant), spec in call_binding._TABLE.items():
        if "params_defaults" in spec:
            declared[compute_id] = declared.get(compute_id, 0) + 1
    table_ids = {compute_id for (compute_id, _variant) in call_binding._TABLE}
    assert set(declared) == table_ids  # 宣言漏れ検出
    assert {compute_id for compute_id, n in declared.items() if n != 1} == set()  # 二重宣言検出


def test_derivation_rejects_duplicate_params_defaults_declaration():
    # 同一 compute_id の 2 variant が既定値を宣言したら導出は失敗する（黙って片方を採らない）。
    from adapter.compute import call_binding

    original = call_binding._TABLE
    patched = dict(original)
    key = ("profit_band", "robust")
    patched[key] = {**original[key], "params_defaults": {"min_obs": 999}}
    call_binding._TABLE = patched
    try:
        with pytest.raises(ValueError):
            call_binding.indicator_param_defaults()
    finally:
        call_binding._TABLE = original


def test_derivation_rejects_missing_params_defaults_declaration():
    # 指標を _TABLE へ足して既定値の宣言を忘れたら導出は失敗する（空の既定値を配信しない）。
    from adapter.compute import call_binding

    original = call_binding._TABLE
    patched = dict(original)
    patched[("brand_new_indicator", "default")] = {
        "loader": lambda: None, "output_kind": "line", "kind": "kw",
    }
    call_binding._TABLE = patched
    try:
        with pytest.raises(ValueError):
            call_binding.indicator_param_defaults()
    finally:
        call_binding._TABLE = original


def test_derived_defaults_do_not_alias_the_binding_table():
    # 導出値は deep copy。配信側（PARAM_DEFAULTS / catalog_defaults）の変更が記述子へ波及しない。
    from adapter.compute import call_binding

    derived = call_binding.indicator_param_defaults()
    derived["tgp_btlm"]["maxbars"] = -999
    assert call_binding._TABLE[("tgp_btlm", "default")]["params_defaults"]["maxbars"] == 100


def test_catalog_payload_key_order_follows_binding_table_order():
    # 配信 JSON の key 順は _TABLE のエントリ順（compute_id 初出順）＝従来の応答 byte 順を維持する。
    #   _TABLE の並べ替えは応答 JSON の key 順を変える（挙動変更）ため本テストで固定する。
    from adapter.compute import call_binding
    from adapter.controller.catalog_controller import handle_catalog

    expected: list[str] = []
    for compute_id, _variant in call_binding._TABLE:
        if compute_id not in expected:
            expected.append(compute_id)
    _status, payload = handle_catalog()
    assert list(payload["catalog"]) == expected


def test_catalog_defaults_returns_deep_copy():
    # serving 用の deep copy は呼び出し側の変更から source を守る（変更が PARAM_DEFAULTS へ波及しない）。
    from adapter.compute.catalog_schema import PARAM_DEFAULTS, catalog_defaults

    snapshot = catalog_defaults()
    snapshot["tgp_btlm"]["maxbars"] = -999
    assert PARAM_DEFAULTS["tgp_btlm"]["maxbars"] == 100


def test_catalog_defaults_matches_front_static_fallback_json():
    # back 既定値（単一情報源）== front 静的フォールバック契約（catalog_defaults.json）。乖離を検出。
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    with _CATALOG_JSON.open(encoding="utf-8") as fh:
        fixture = json.load(fh)
    assert PARAM_DEFAULTS == fixture


def test_default_samples_sourced_from_catalog_schema():
    # back 内の二重定義解消: _DEFAULT_SAMPLES は schema の mcmc_samples 既定を単一源とする。
    from adapter.compute import call_binding
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    assert call_binding._DEFAULT_SAMPLES == PARAM_DEFAULTS["tgp_btlm"]["mcmc_samples"]
    assert call_binding._DEFAULT_SAMPLES in call_binding._BTE_PRESETS


def test_handle_catalog_returns_ok_schema():
    # GET /catalog controller は (200, {ok:true, catalog:{...}}) を返す（正典 ok 形）。
    from adapter.compute.catalog_schema import PARAM_DEFAULTS
    from adapter.controller.catalog_controller import handle_catalog

    status, payload = handle_catalog()
    assert status == 200
    assert payload["ok"] is True
    assert payload["catalog"] == PARAM_DEFAULTS


def test_catalog_defaults_reexported_from_compute_facade():
    # 安定公開 Facade（ISSUE-092 ②）から catalog_defaults を参照できる（再エクスポート）。
    from adapter.compute import catalog_defaults as facade_catalog_defaults
    from adapter.compute.catalog_schema import PARAM_DEFAULTS

    assert facade_catalog_defaults() == PARAM_DEFAULTS
