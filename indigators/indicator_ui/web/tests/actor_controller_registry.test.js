// actor_controller_registry.test.js — アクター駆動指標の委譲先レジストリの回帰固定。
//
// 背景: IndicatorController は台帳（actor_driven_ids.js）でアクター駆動型を判定した後、常に
//   this._mp（MarketProfileController）へ委譲していた。「台帳への 1 行追記で完結し本 controller は
//   不変」という同ファイルの主張は 2 つ目のアクター駆動指標で破綻し、tickvol_bands が MP の
//   コントローラへ誤配送される。computeId → コントローラのレジストリで解決することを固定する。
// 構造: Arrange-Act-Assert。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

function makeController() {
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: { renderLine: noop, renderHorizontal: noop, setData: noop, setVisible: noop, remove: noop, setCandles: noop },
    document: null,
  });
}

// 呼ばれたメソッドを記録するだけのアクターコントローラ（MarketProfileController と同じ面）。
function spyController(name, log) {
  const rec = (m) => (...args) => { log.push(`${name}.${m}`); return args[0]; };
  return {
    applyMarketProfile: rec('applyMarketProfile'),
    toggleVisible: rec('toggleVisible'),
    removeInstance: rec('removeInstance'),
    onGear: rec('onGear'),
    restoreInstance: rec('restoreInstance'),
    onLiveRecompute: rec('onLiveRecompute'),
  };
}

const MP_DEF = get('market_profile');
const TVB_DEF = get('tickvol_bands');

test('both actor-driven indicators are declared in the ledger', () => {
  const c = makeController();
  assert.equal(c._isMarketProfile(MP_DEF), true);
  assert.equal(c._isMarketProfile(TVB_DEF), true);
  assert.equal(c._isMarketProfile(get('tgp_btlm')), false);
});

test('a registered controller receives its own indicator, not the market-profile one', () => {
  // Arrange
  const c = makeController();
  const log = [];
  c._mp = spyController('mp', log);
  c._actorControllers.set('market_profile', c._mp);
  c.registerActorController('tickvol_bands', spyController('tvb', log));
  // Act
  c._applyMarketProfile(TVB_DEF, 'default', {});
  c._onGearMarketProfile({ instanceId: 'x' }, TVB_DEF);
  // Assert
  assert.deepEqual(log, ['tvb.applyMarketProfile', 'tvb.onGear']);
});

test('market_profile keeps routing to the market-profile controller', () => {
  const c = makeController();
  const log = [];
  c._mp = spyController('mp', log);
  c._actorControllers.set('market_profile', c._mp);
  c.registerActorController('tickvol_bands', spyController('tvb', log));
  c._applyMarketProfile(MP_DEF, 'default', {});
  assert.deepEqual(log, ['mp.applyMarketProfile']);
});

test('instance-based legend callbacks resolve the controller through the catalog', () => {
  // 凡例の eye/close は def を持たず instanceId しか渡されない（indicatorId から def を引く）。
  const c = makeController();
  const log = [];
  c._mp = spyController('mp', log);
  c._actorControllers.set('market_profile', c._mp);
  c.registerActorController('tickvol_bands', spyController('tvb', log));
  c._toggleMarketProfileVisible({ instanceId: 'tickvol_bands#1', indicatorId: 'tickvol_bands' });
  c._removeMarketProfile({ instanceId: 'market_profile#1', indicatorId: 'market_profile' });
  assert.deepEqual(log, ['tvb.toggleVisible', 'mp.removeInstance']);
});

test('an unregistered actor-driven id is routed to a no-op, never to another indicator controller', () => {
  // ISSUE-479 Wave2b J-1 OCP-5 S3: 旧版はここで「未登録は MP のコントローラへ退避する
  //   （レジストリ化前と同じ経路）」を固定していた。それは互換の保存に見えて、**誤配送そのもの**
  //   を仕様として据えていた——台帳にあるが未結線の指標が MP のオーケストレーションを受け取る。
  //   引き継ぎ先の性質はより強い: 未登録は誰のコントローラへも届かない。
  const c = makeController();
  const log = [];
  c.registerActorController('market_profile', spyController('mp', log));
  c._applyMarketProfile(TVB_DEF, 'default', {});
  assert.deepEqual(log, [], '未登録の指標が他指標のコントローラへ誤配送されている');
});

test('registerActorController is exposed for composition roots to wire new actors', () => {
  const c = makeController();
  assert.equal(typeof c.registerActorController, 'function');
  assert.equal(typeof c._actorControllerFor, 'function');
});
