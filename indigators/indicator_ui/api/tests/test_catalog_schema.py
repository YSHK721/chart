"""CATALOG_SCHEMA / GET /catalog（ISSUE-092 ③）— param 既定値の単一情報源の構造テスト。

single source（``adapter.compute.catalog_schema.PARAM_DEFAULTS``）が
  - ``call_binding._TABLE`` の compute_id 集合と一致（指標追加時の登録漏れを検出）
  - front 静的フォールバック契約 ``web/js/adapter/front/catalog_defaults.json`` と一致
    （back 既定値 == front 静的フォールバック値の乖離を検出・ISSUE-092 ③ 要件④）
  - ``call_binding._DEFAULT_SAMPLES`` と mcmc_samples 既定が一致（back 内二重定義の解消）
を固定する。``handle_catalog`` は正典契約（ok / nested error）で schema を配信する。
"""

from __future__ import annotations

import json
from pathlib import Path

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
