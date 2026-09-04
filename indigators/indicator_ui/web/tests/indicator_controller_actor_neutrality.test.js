// indicator_controller_actor_neutrality.test.js — IndicatorController が特定のアクター駆動指標を
//   名指さないことの固定（ISSUE-479 Wave2b J-1 OCP-5 S3）。
//
// なぜ在るか:
//   アクター駆動指標（/compute を持たない指標）の追加は「台帳（actor_driven_ids.js）へ 1 行 ＋
//   合成根の registerActorController 1 行」で完結する、というのが本 controller の設計主張だった。
//   ところが Market Profile だけは ctor 引数 3 本（marketProfile / mpModeResolver / mpGrowthResolver）・
//   協働子の構築・レジストリの初期値・未登録時のフォールバック先・委譲メソッド 2 本という形で
//   controller 本体に焼き付いており、主張が実体を伴っていなかった。**2 つ目のアクター駆動指標を
//   足したときに MP のコントローラへ誤配送される**という具体的な壊れ方も、この焼き付きが原因である。
//
// 何を固定するか:
//   (1) 名指しの不在: MP 固有の識別子が indicator_controller.js に 1 つも無い（源泉での固定）。
//   (2) 振る舞い: 未登録のアクター駆動指標は共有の no-op へ落ち、例外にも MP 経路にもならない。
//   (3) 計算量: 未登録の解決を何度繰り返しても新しいオブジェクトを作らない（発行 − 使用 = 0）。
//
// 構造: Arrange-Act-Assert（AAA）。

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

import { IndicatorController } from '../js/adapter/front/indicator_controller.js';
import { MarketProfileController } from '../js/adapter/front/market_profile_controller.js';
import { get } from '../js/usecase/catalog.js';

const noop = () => {};

const SRC = readFileSync(
  fileURLToPath(new URL('../js/adapter/front/indicator_controller.js', import.meta.url)),
  'utf8',
);

// MP 固有の名指し（協働子・その依存・その computeId・委譲面）。
//
//   ここに **入れていない**もの と その理由:
//     - `_isMarketProfile` / `_applyMarketProfile` / `_toggleMarketProfileVisible` /
//       `_removeMarketProfile` / `_onGearMarketProfile`: 中身は台帳駆動の汎用ディスパッチで、
//       名前だけが MP 時代のもの。subclass（ReplayIndicatorController）の override / inherited 呼出と
//       既存テストの呼び口を温存するため名前を残している（改名は別の変更）。
//     - `_mpParams` / `_deriveMode` / `_deriveResmode`: host 契約が要求する host 側の面であり、
//       協働子の持ち物ではない（host が満たすべきもの＝ここに在るのが正しい）。
const MP_SPECIFIC_TOKENS = Object.freeze([
  ['MarketProfileController', /MarketProfileController/],
  ['MARKET_PROFILE_HOST_CONTRACT', /MARKET_PROFILE_HOST_CONTRACT/],
  ['MarketProfileHost', /MarketProfileHost/],
  ['_marketProfile', /_marketProfile/],
  ['_mpModeResolver', /_mpModeResolver/],
  ['_mpGrowthResolver', /_mpGrowthResolver/],
  ["'market_profile'", /'market_profile'/],
  ['this._mp', /this\._mp(?![A-Za-z0-9_])/],
  ['reapplyMarketProfileMode', /reapplyMarketProfileMode/],
  ['_applyMpGrowth', /_applyMpGrowth/],
  ['ctor 引数（marketProfile / mpModeResolver / mpGrowthResolver）', /\b(?:marketProfile|mpModeResolver|mpGrowthResolver)\b\s*=/],
]);

function mpNameSites(source) {
  const out = [];
  source.split('\n').forEach((line, i) => {
    for (const [label, re] of MP_SPECIFIC_TOKENS) {
      if (re.test(line)) {
        out.push(`${i + 1}: ${label} — ${line.trim().slice(0, 90)}`);
      }
    }
  });
  return out;
}

function makeController() {
  return new IndicatorController({
    catalog: { listIndicators: () => [], get },
    compute: { compute: async () => ({ ok: true, generation: 0, series: [] }) },
    persistence: {
      loadApplied: () => [], saveApplied: noop, loadFavorites: () => [], saveFavorites: noop,
      loadUiState: () => ({}), saveUiState: noop, nextSeq: () => 1,
    },
    renderer: {
      renderLine: noop, renderHorizontal: noop, renderHistogram: noop, setData: noop,
      setVisible: noop, remove: noop, setCandles: noop,
    },
    document: null,
  });
}

// ---- (1) 名指しの不在 ----

test('S3: indicator_controller.js は MP 固有の識別子を 1 つも持たない', () => {
  // Arrange / Act
  const sites = mpNameSites(SRC);
  // Assert
  assert.deepEqual(sites, [],
    'IndicatorController に MP 固有の名指しが残っています（アクター駆動指標の追加が'
    + `「台帳 1 行 + 登録 1 行」で完結しません）:\n  ${sites.join('\n  ')}`);
});

test('検出器そのものの検定（空振りしていない）', () => {
  // 走査が常に空を返す壊れ方（正規表現の書き損じ）を塞ぐ。
  assert.equal(mpNameSites('this._mp = new MarketProfileController(x);').length, 2);
  assert.equal(mpNameSites('this._mpParams(p);').length, 0, '契約面 _mpParams を誤検出している');
  assert.equal(mpNameSites('return isActorDriven(def);').length, 0);
});

// ---- (2) 振る舞い: 未登録は共有 no-op へ落ちる ----

test('S3: アクター駆動指標が未登録なら解決先は no-op（MP 経路へ誤配送しない）', () => {
  // Arrange: 何も登録していない controller。
  const c = makeController();
  const def = { compute: { computeId: 'market_profile' } };
  // Act
  const actor = c._actorControllerFor(def);
  // Assert: 何かは返る（呼び出し側に null 分岐を作らせない）。
  assert.ok(actor, '未登録の解決結果が空（呼び出し側で例外になる）');
  // **誤配送しない**: 未登録の解決先が MP のコントローラであってはならない。
  //   これが旧実装の実害だった——台帳にあるが未結線の指標は、MP のオーケストレーションを
  //   そのまま受け取っていた（2 つ目のアクター駆動指標を足した日に静かに壊れる形）。
  assert.ok(!(actor instanceof MarketProfileController),
    '未登録のアクター駆動指標が MP コントローラへ誤配送されている');
  // 未知の computeId も同じ扱い（MP だけを特別扱いしない）。
  assert.equal(c._actorControllerFor({ compute: { computeId: 'unknown_actor' } }), actor);
  // 全メソッドが no-op（副作用も例外も無い）。
  assert.doesNotThrow(() => actor.applyMarketProfile(def, null, {}));
  assert.doesNotThrow(() => actor.toggleVisible({}));
  assert.doesNotThrow(() => actor.removeInstance({}));
  assert.doesNotThrow(() => actor.onGear({}, def));
  assert.doesNotThrow(() => actor.applyMpGrowth());
});

test('S3: 登録した controller が解決先になる（登録 1 行で結線が完了する）', () => {
  // Arrange
  const c = makeController();
  const registered = { applyMarketProfile: () => 'mine' };
  c.registerActorController('market_profile', registered);
  // Act / Assert
  assert.equal(c._actorControllerFor({ compute: { computeId: 'market_profile' } }), registered);
});

// ---- (3) 計算量: フォールバックは共有実体（解決のたびに作らない） ----

test('計算量: 未登録の解決を繰り返しても新しいオブジェクトを作らない（発行 − 使用 = 0）', () => {
  // なぜ在るか（絶対命令 2026-08-28）: フォールバックを `=> ({ ...no-op })` で書くと、出力は
  //   正しいまま解決のたびにオブジェクトが増える。状態検証では原理的に落ちない浪費である。
  //   固定するのは回数そのものではなく **無駄の不在**（同一実体が使い回されること）。
  const c = makeController();
  const defs = ['market_profile', 'tickvol_bands', 'unknown_actor'].map(
    (id) => ({ compute: { computeId: id } }),
  );
  const seen = new Set();
  for (let i = 0; i < 3; i += 1) {
    for (const def of defs) {
      seen.add(c._actorControllerFor(def));
    }
  }
  assert.equal(seen.size, 1, `未登録の解決ごとに実体が増えている: ${seen.size} 個`);
});

for (const round of [1, 8]) {
  test(`計算量: 解決回数を ${round} 回に増やしても実体は増えない（オーダーの表明）`, () => {
    const c = makeController();
    const def = { compute: { computeId: 'market_profile' } };
    const seen = new Set();
    for (let i = 0; i < round; i += 1) {
      seen.add(c._actorControllerFor(def));
    }
    assert.equal(seen.size, 1);
  });
}
