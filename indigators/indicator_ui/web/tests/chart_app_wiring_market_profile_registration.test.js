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
import { MARKET_PROFILE_HOST_CONTRACT } from '../js/adapter/front/indicator_controller.js';

const noop = () => {};

// 契約が宣言する全メンバー名（host_role_contract.test.js と同一の畳み方）。
function contractMembers(contract) {
  return new Set([...contract.methods, ...contract.fields, ...contract.optionalFields]);
}

// wireControllerCollaborators は多くの協働子を組むが、本検定が見るのは登録の 1 点だけ。
//   他の口は最小スタブで塞ぐ（テーマ・テンプレート・計算機は未注入＝縮退経路）。
//
// host の面は MarketProfileHost 契約を**構造的に満たす**ところまで用意する（ISSUE-479 Wave2 🟡-1）。
//   理由: 本 rig は「合成根が MP へ何を渡すか」を測る。渡す先が契約射影なのかを判定するには、
//   rig 自身が契約を満たしていなければ「射影だから読めない」のか「rig に無いから undefined」なのかを
//   区別できない。契約充足は fake の拡張のみで足りる（既存アサーションは不変）。
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
    // --- MarketProfileHost 契約の残余（fake の拡張のみ・呼ばれない面は no-op） ---
    _isMarketProfile: () => true,
    _paramsObject: (p) => ({ ...p }),
    _renderLegend: noop,
    _defaultVariant: () => null,
    _withParams: (p) => ({ ...p }),
    _defaultParams: () => ({}),
    _persistAll: noop,
    _commitState: noop,
    _state: {},
    _catalog: { listIndicators: () => [], get: () => null },
    _meta: {},
    _datasetRef: 'sample',
    _document: null,
    // --- 契約外（合成根の持ち物）。射影を通せば MP からは触れない ---
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

// ---- 🟡-1: 登録実体の host は契約射影であること（生 host を渡さない・ISSUE-479 Wave2 再レビュー）----
//
// なぜ在るか: IndicatorController の ctor 側は `createHostView(this, MARKET_PROFILE_HOST_CONTRACT)` を
//   通しており、その遮断は host_role_contract.test.js §(4)(5) が固定している。ところがその走査は
//   indicator_controller.js のソースだけを見るため、**共有配線からの登録**は射程外だった。
//   本 Wave が登録を合成根へ寄せたとき、そこだけ生 host が渡って ISP 射影が失われても
//   既存検定は全て緑のままになる（＝本 Wave 自身が導入した検査の弱化）。ここで塞ぐ。
//
// 何を固定するか: 「射影を通していること」を宣言（ソース文字列）ではなく**実行時の遮断**で測る。
//   生 host なら契約外の面が素通りするため、この検定は必ず落ちる。

test('🟡-1: 共有配線が MP へ渡す host は MarketProfileHost 契約の射影である（生 host でない）', () => {
  // Arrange
  const actor = { setParams: () => {}, applyGrowthState: () => {} };
  const { registered, controller } = makeRig({ marketProfile: actor });
  const mp = registered.find(([id]) => id === 'market_profile')[1];
  // Act
  const host = mp._host;
  // Assert
  assert.ok(host, 'MP コントローラの host が取得できない（保持名が変わった可能性）');
  assert.notEqual(host, controller, '生 host（controller）をそのまま渡している＝ISP 射影が無い');
});

test('🟡-1: 共有配線が渡す host は契約面を通し、契約外の面は実行時に遮断する', () => {
  // Arrange
  const actor = { setParams: () => {}, applyGrowthState: () => {} };
  const { registered, controller } = makeRig({ marketProfile: actor });
  const host = registered.find(([id]) => id === 'market_profile')[1]._host;
  const allowed = contractMembers(MARKET_PROFILE_HOST_CONTRACT);
  // Act / Assert: 契約面は読める（代表 1 つ）。
  assert.ok(allowed.has('_mpParams'), '測定の前提（契約面の代表）が崩れている');
  assert.doesNotThrow(() => host._mpParams);
  // host には実在するが契約に無い面（合成根の登録口）は例外になる。
  assert.equal(typeof controller.registerActorController, 'function',
    '測定の前提（契約外の面が host に実在する）が崩れている');
  assert.ok(!allowed.has('registerActorController'), '測定の前提（当該面が契約外）が崩れている');
  assert.throws(() => host.registerActorController, /契約外の host メンバー/,
    '契約外の host 面が MP から素通しになっている（ISP 射影を失っている）');
});

test('🟡-1 前提: rig の host は MarketProfileHost 契約を構造的に満たす（射影の可否を測れる状態）', () => {
  // Arrange
  const { controller } = makeRig();
  // Act / Assert
  for (const m of MARKET_PROFILE_HOST_CONTRACT.methods) {
    assert.equal(typeof controller[m], 'function', `rig に method 欠落: ${m}`);
  }
  for (const f of MARKET_PROFILE_HOST_CONTRACT.fields) {
    assert.ok(f in controller, `rig に field 欠落: ${f}`);
  }
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
