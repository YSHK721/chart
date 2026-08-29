// template_binding_reader — 注入された **読み取り専用** storage から 8 時間足の instance 束を組む。
//
// 設計入力:
//   - arch-spec §0 T-2: unified_root（Composition Root）が live スコープの storage を読み取り専用で
//     注入する。View は自分でスコープを選ばない。束はサーバへ Input Model の一部として送る。
//   - 設計書 §3.4: 時間足 ↔ テンプレートの紐付け（`1m`→tpl#4 / `5m`→tpl#5 / … / `1M`→tpl#4）。
//     `1m` と `1M` が同一テンプレートを共有するのは**正しい設定**（依頼者確認済み）。
//   - キー名と値の形は既存 adapter が唯一源:
//     indigators/indicator_ui/web/js/adapter/front/local_storage_template_gateway.js
//       `indicatorUi.templates.v1`        → {templates: [{templateId, name, instances: [...]}]}
//       `indicatorUi.templateBindings.v1` → {bindings: {<timeframe>: <templateId>}}
//     instance の 5 属性は indigators/indicator_ui/web/js/usecase/chart_templates.js の
//     `toTemplateInstance`（indicatorId / variant / params / visible / styles）。
//   - 束の組み方の参照実装: tools/measure/issue449/probe_inverse.py:62-77
//     （binds[tf] → byid[...]["instances"] → params の `timeframe` で軸を解決）。
//
// 無言縮退の禁止（設計書 §5.2 と同じ規約）: 紐付けが無い・JSON が壊れている場合は、空の束を
//   静かに返さず**理由を持つエラー**として返す。空で返すと「水準が 1 つも無い相場」と
//   区別が付かない。
//
// 構造は AAA。テスト名は「対象_条件_期待結果」。

import { test, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  readInstanceBundle,
  TEMPLATE_STORAGE_KEYS,
  DASHBOARD_TIMEFRAMES,
} from '../js/adapter/front/template_binding_reader.js';

/** 読み取り専用 storage の Test Stub（unified_ui の readOnlyStorage と同じ契約）。 */
function readOnlyStub(map) {
  const refuse = (op) => () => { throw new TypeError(`readOnlyStorage: ${op} は許可されていない`); };
  return {
    getItem: (key) => (Object.prototype.hasOwnProperty.call(map, key) ? map[key] : null),
    key: () => null,
    get length() { return Object.keys(map).length; },
    setItem: refuse('setItem'),
    removeItem: refuse('removeItem'),
    clear: refuse('clear'),
  };
}

/** §3.4 の紐付けをそのまま持つ storage（1m と 1M が tpl#4 を共有する）。 */
function designDocStorage() {
  const templates = [
    {
      templateId: 'tpl#4',
      name: 'A',
      instances: [
        { indicatorId: 'ma_marod', variant: 'default', params: { source: 'hlc3', length: 5 }, visible: true, styles: null },
        { indicatorId: 'profit_rsi', variant: 'default', params: { rsi_period: 6 }, visible: true, styles: null },
      ],
    },
    {
      templateId: 'tpl#5',
      name: 'B',
      instances: [
        // params の `timeframe` は**軸の明示**（MTF 固定水準）。表示足に依らず同一値になる。
        { indicatorId: 'btlm_trail_marod', variant: 'default', params: { maxbars: 300, timeframe: '4h' }, visible: true, styles: null },
      ],
    },
  ];
  const bindings = {
    '1m': 'tpl#4', '5m': 'tpl#5', '15m': 'tpl#4', '1h': 'tpl#5',
    '4h': 'tpl#4', '1D': 'tpl#5', '1W': 'tpl#4', '1M': 'tpl#4',
  };
  return readOnlyStub({
    [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates }),
    [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings }),
  });
}

describe('template_binding_reader — 8 時間足の instance 束', () => {
  test('the_reader_never_writes_to_the_injected_storage', () => {
    // Arrange: 書き込みを試みたら throw する storage（T-2 の注入契約そのもの）。
    const storage = designDocStorage();
    // Act / Assert: 読むだけなので例外は出ない。
    assert.doesNotThrow(() => readInstanceBundle({ storage }));
  });

  test('every_bound_timeframe_produces_instances_from_its_template', () => {
    // Act
    const { ok, instances } = readInstanceBundle({ storage: designDocStorage() });
    // Assert: 紐付いた 8 足すべてが束に現れる（§3.4）。
    assert.equal(ok, true);
    const covered = new Set(instances.map((i) => i.timeframe_binding));
    assert.deepEqual([...covered].sort(), [...DASHBOARD_TIMEFRAMES].sort());
  });

  test('two_timeframes_sharing_one_template_each_get_their_own_instances', () => {
    // §3.4: `1m` と `1M` は同一 tpl#4 を共有する（正しい設定）。束としては別々に現れる。
    const { instances } = readInstanceBundle({ storage: designDocStorage() });
    const forOneMinute = instances.filter((i) => i.timeframe_binding === '1m');
    const forOneMonth = instances.filter((i) => i.timeframe_binding === '1M');
    assert.equal(forOneMinute.length, forOneMonth.length);
    assert.ok(forOneMinute.length > 0);
    // 同じテンプレート由来でも instance_id は衝突しない（サーバ側の畳み込みキーと別物）。
    const ids = instances.map((i) => i.instance_id);
    assert.equal(new Set(ids).size, ids.length, 'instance_id が衝突しています');
  });

  test('an_explicit_timeframe_param_resolves_the_axis_and_leaves_the_binding_visible', () => {
    // 参照実装 probe_inverse.py:70-72（`own = params.timeframe or "chart"` / `axis = own if own != "chart" else tf`）。
    const { instances } = readInstanceBundle({ storage: designDocStorage() });
    const mtf = instances.find((i) => i.indicator_id === 'btlm_trail_marod' && i.timeframe_binding === '1h');
    // Assert: 軸は明示された 4h（表示足 1h ではない）。params から timeframe は抜く。
    assert.equal(mtf.timeframe, '4h');
    assert.equal('timeframe' in mtf.params, false);
  });

  test('a_chart_following_instance_takes_the_bound_timeframe_as_its_axis', () => {
    const { instances } = readInstanceBundle({ storage: designDocStorage() });
    const following = instances.find((i) => i.indicator_id === 'ma_marod' && i.timeframe_binding === '15m');
    assert.equal(following.timeframe, '15m');
  });

  test('the_bundle_carries_exactly_the_fields_the_server_contract_needs', () => {
    // arch-spec §9: instances は {instance_id, indicator_id, variant, params, timeframe?}。
    const { instances } = readInstanceBundle({ storage: designDocStorage() });
    for (const instance of instances) {
      assert.equal(typeof instance.instance_id, 'string');
      assert.equal(typeof instance.indicator_id, 'string');
      assert.equal(typeof instance.variant, 'string');
      assert.equal(typeof instance.params, 'object');
      assert.ok(DASHBOARD_TIMEFRAMES.includes(instance.timeframe));
    }
  });

  test('the_bundle_is_stable_across_repeated_reads', () => {
    // F.I.R.S.T Repeatable: 開き直すたびに instance_id が変わると、サーバ側の畳み込みと
    //   到達時刻の同一性が読むたびに揺れる。
    const storage = designDocStorage();
    assert.deepEqual(readInstanceBundle({ storage }), readInstanceBundle({ storage }));
  });

  test('missing_bindings_are_reported_as_an_error_not_as_an_empty_bundle', () => {
    // Arrange: templates は在るが紐付けキーが無い。
    const storage = readOnlyStub({
      [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates: [] }),
    });
    // Act
    const result = readInstanceBundle({ storage });
    // Assert: 空の束で静かに返さない（§5.2 の規約）。
    assert.equal(result.ok, false);
    assert.match(result.error.message, /紐付/);
  });

  test('broken_json_is_reported_as_an_error_naming_the_key', () => {
    const storage = readOnlyStub({
      [TEMPLATE_STORAGE_KEYS.templates]: '{"templates": [',
      [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings: { '1m': 'tpl#4' } }),
    });
    const result = readInstanceBundle({ storage });
    assert.equal(result.ok, false);
    assert.ok(result.error.message.includes(TEMPLATE_STORAGE_KEYS.templates));
  });

  test('a_binding_pointing_at_a_missing_template_is_reported_as_an_error', () => {
    const storage = readOnlyStub({
      [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({ templates: [] }),
      [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings: { '1m': 'tpl#99' } }),
    });
    const result = readInstanceBundle({ storage });
    assert.equal(result.ok, false);
    assert.match(result.error.message, /tpl#99/);
  });

  test('an_unknown_timeframe_in_the_bindings_is_reported_instead_of_silently_dropped', () => {
    // 未知の足を黙って落とすと、設定側の誤りが表の欠落として現れて原因が追えない。
    const storage = readOnlyStub({
      [TEMPLATE_STORAGE_KEYS.templates]: JSON.stringify({
        templates: [{ templateId: 'tpl#4', name: 'A', instances: [] }],
      }),
      [TEMPLATE_STORAGE_KEYS.bindings]: JSON.stringify({ bindings: { '1m': 'tpl#4', '3s': 'tpl#4' } }),
    });
    const result = readInstanceBundle({ storage });
    assert.equal(result.ok, false);
    assert.match(result.error.message, /3s/);
  });

  test('a_storage_that_throws_on_read_is_reported_as_an_error', () => {
    // 読み取り自体が落ちる環境（storage 無効化）でも、無言で空にしない。
    const storage = { getItem: () => { throw new Error('SecurityError'); } };
    const result = readInstanceBundle({ storage });
    assert.equal(result.ok, false);
    assert.equal(typeof result.error.message, 'string');
  });

  test('a_missing_storage_injection_is_reported_as_an_error', () => {
    const result = readInstanceBundle({ storage: null });
    assert.equal(result.ok, false);
  });
});
