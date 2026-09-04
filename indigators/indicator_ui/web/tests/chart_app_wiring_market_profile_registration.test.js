// chart_app_wiring_market_profile_registration.test.js — MP をアクター駆動指標の
//   **共通の登録口**へ寄せたことの固定（ISSUE-479 Wave2 J-1 OCP-5 S2）。
//
// なぜ在るか: アクター駆動指標（/compute を持たない指標）の登録は
//   `controller.registerActorController(computeId, controller)` の 1 行で完結する設計であり、
//   取引密度帯（tickvol_bands）は既にその形になっている。MP だけが「controller の ctor が
//   自分で `new MarketProfileController` して `_actorControllers` へ入れる」という別経路のままで、
//   指標を足すときに見るべき場所が 2 通りに割れていた。共有配線から同一行様式で登録する。
//
// 加法である: controller の ctor 側の既定登録は残る（本登録はそれを同一挙動で上書きする）。
//   未注入（marketProfile 省略）でも登録し、アクターは host 読みへ縮退する＝replay も無改変。
//
// 構造は AAA。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { wireControllerCollaborators } from '../js/adapter/front/chart_app_wiring.js';

const noop = () => {};

// wireControllerCollaborators は多くの協働子を組むが、本検定が見るのは登録の 1 点だけ。
//   他の口は最小スタブで塞ぐ（テーマ・テンプレート・計算機は未注入＝縮退経路）。
function makeRig({ marketProfile } = {}) {
  const registered = [];
  const controller = {
    _marketProfile: { setParams: () => {}, applyGrowthState: () => {} },
    _timeframe: '1D',
    _untilTime: undefined,
    setTimeframe: () => {},
    setAppliedObserver: noop,
    _mpParams: (p) => ({ ...p }),
    _mpModeResolver: null,
    _mpGrowthResolver: null,
    setTimeframeObserver: noop,
    applyPaneOrder: noop,
    registerActorController: (id, ctrl) => { registered.push([id, ctrl]); },
  };
  const renderer = {
    attachBackgroundPrimitive: () => null,
    setPaneAreaHeightProvider: noop, setPaneOrderObserver: noop,
    subscribeVisibleRange: noop, addChromeObserver: () => () => {},
    setCandleObserver: noop, setTfPeriodHoverHandler: noop,
  };
  const args = {
    controller,
    renderer,
    doc: null,
    fetch: async () => ({ ok: false }),
    datasetRef: 'sample',
    templateStore: {
      loadTemplates: () => [], saveTemplates: noop,
      loadBindings: () => ({}), saveBindings: noop,
      loadTemplateSeq: () => 0, saveTemplateSeq: noop,
    },
    timeframe: '1D',
    recentBars: 100,
    lwc: {},
    mainSeries: {},
    chart: {},
    container: null,
    currentPriceView: null,
  };
  if (marketProfile !== undefined) {
    args.marketProfile = marketProfile;
  }
  wireControllerCollaborators(args);
  return { registered, controller };
}

test('S2: 共有配線が MP をアクター駆動指標の登録口へ登録する（tickvol_bands と同一行様式）', () => {
  // Arrange
  const actor = { setParams: () => {}, applyGrowthState: () => {} };
  // Act
  const { registered } = makeRig({ marketProfile: actor });
  // Assert
  const ids = registered.map(([id]) => id);
  assert.ok(ids.includes('market_profile'), `MP が登録されていない: ${ids.join(', ')}`);
  // 既存のアクター駆動指標と**同じ口**から登録する（登録経路を 2 通りに分けない）。
  assert.ok(ids.includes('tickvol_bands'), '前例（取引密度帯）が同じ口を使っていない＝測定の前提が崩れている');
});

test('S2: 登録された MP コントローラは注入されたアクターを使う（host のフィールド名に依存しない）', () => {
  // Arrange
  const calls = [];
  const actor = { setParams: (p) => calls.push(p), applyGrowthState: () => {} };
  const { registered, controller } = makeRig({ marketProfile: actor });
  const mp = registered.find(([id]) => id === 'market_profile')[1];
  // Act: host のフィールドには別のアクターが居るが、注入した方へ渡る。
  mp.applyMpParams({ va: 0.7 });
  // Assert
  assert.equal(calls.length, 1, '注入したアクターへ渡っていない');
  assert.ok(controller._marketProfile !== actor, '測定の前提（host 側は別実体）が崩れている');
});

test('S2: marketProfile 未注入でも登録し、アクターは host 読みへ縮退する（replay 無改変）', () => {
  // Arrange
  const { registered, controller } = makeRig();   // 注入なし＝replay 合成根と同じ形
  const entry = registered.find(([id]) => id === 'market_profile');
  const calls = [];
  controller._marketProfile = { setParams: (p) => calls.push(p), applyGrowthState: () => {} };
  // Act
  entry[1].applyMpParams({ va: 0.7 });
  // Assert: 構築後に host へ差し込む既存経路（replay 合成根）がそのまま効く。
  assert.equal(calls.length, 1, 'host 読みへ縮退していない');
});

test('S2 前提: 両合成根が marketProfile を共有配線へ明示的に転送する（暗黙チャネルを持たない）', () => {
  // Arrange: 受け口だけ作って呼び出し側が送らない壊れ方（ISSUE-291）を静的に塞ぐ。
  const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
  const roots = {
    live: read('../js/adapter/front/composition_root_front.js'),
    replay: read('../../../../simulator/replay_ui/web/js/adapter/front/composition_root_front.js'),
  };
  const argsOf = (src) => {
    const start = src.indexOf('wireControllerCollaborators({');
    assert.ok(start >= 0, 'wireControllerCollaborators の呼び出しが見つからない');
    let depth = 0;
    for (let i = src.indexOf('{', start); i < src.length; i += 1) {
      if (src[i] === '{') { depth += 1; }
      if (src[i] === '}') { depth -= 1; if (depth === 0) { return src.slice(start, i + 1); } }
    }
    return '';
  };
  // Act / Assert
  for (const [name, src] of Object.entries(roots)) {
    assert.match(argsOf(src), /\bmarketProfile\b/,
      `${name} root が marketProfile を共有配線へ転送していない（登録が無言で死ぬ）`);
  }
});
