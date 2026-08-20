// position_sizing_dialog.js（ポジションサイズ計算機のモーダル DOM アダプター）のテスト。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   裁定記録 TBD-1/5（建値も価格の単一ソース＝チャートに一本化。**gap モード・間隔指定は撤廃**。
//     順張り／逆張り 2 カード比較は参照実装 :1098 の明示により同一結果＝表示しない）、
//   TBD-3（図 3＝資産推移パスは含めない）、TBD-4（3 トグルは 3 つとも残す）、
//   スライス 6（`color_theme_dialogs.js` と同型・コールバック注入・遅延参照・DOM は自分で生成）、
//   「追加要件裁定 R-P1」（各価格欄の「チャートで指定」がアーム式ピッカーの受け口）、
//   §3 UC-04（表示文字列は Presenter が生成する。**計算は usecase の ViewModel が持つ**）。
//
// 固定する規約:
//   - 表示値は `usecase/position_sizing_plan.js` の ViewModel をそのまま出す（第 2 実装を作らない）。
//   - 協働子（usecase・renderer・ピッカー）は import しない＝すべて注入コールバック。
//   - 「チャートで指定」はアーム要求を**呼ぶだけ**（ピッカー本体はスライス 8-d）。
// 構造: Arrange-Act-Assert（AAA）。jsdom を避けた最小 DOM スタブ（color_theme_dialogs と同作法）。

import { test } from 'node:test';
import assert from 'node:assert/strict';

import { PositionSizingDialog, defaultParams, defaultLevels } from '../js/adapter/front/position_sizing_dialog.js';

// ---- 最小 DOM スタブ（新規依存を追加しない）--------------------------------
class El {
  constructor(tag = 'div') {
    this.tag = tag;
    this.children = [];
    this.dataset = {};
    this.textContent = '';
    this.type = '';
    this.id = '';
    this.title = '';
    this.value = '';
    this.checked = false;
    this.disabled = false;
    this.min = '';
    this.max = '';
    this.step = '';
    this.parentNode = null;
    this._cls = new Set();
    this._handlers = {};
  }

  get className() { return [...this._cls].join(' '); }

  set className(v) { this._cls = new Set(String(v).split(/\s+/).filter(Boolean)); }

  get classList() {
    const s = this._cls;
    return {
      add: (c) => s.add(c),
      remove: (c) => s.delete(c),
      contains: (c) => s.has(c),
      toggle: (c, on) => {
        const next = on === undefined ? !s.has(c) : on;
        if (next) { s.add(c); } else { s.delete(c); }
      },
    };
  }

  get innerHTML() { return ''; }

  set innerHTML(v) {
    if (v === '') {
      for (const k of this.children) { k.parentNode = null; }
      this.children = [];
    }
  }

  append(...kids) {
    for (const k of kids) {
      if (k && typeof k === 'object') { k.parentNode = this; this.children.push(k); }
    }
  }

  appendChild(k) { this.append(k); return k; }

  removeChild(k) {
    this.children = this.children.filter((c) => c !== k);
    if (k) { k.parentNode = null; }
    return k;
  }

  setAttribute(k, v) { this.dataset[`attr_${k}`] = v; }

  focus() {}

  addEventListener(ev, fn) { (this._handlers[ev] ??= []).push(fn); }

  fire(ev, arg = {}) { for (const fn of this._handlers[ev] ?? []) { fn(arg); } }
}

function fakeDoc() {
  const body = new El('body');
  return { body, createElement: (t) => new El(t) };
}

function flatten(el, out = []) {
  for (const kid of el.children ?? []) { out.push(kid); flatten(kid, out); }
  return out;
}

const textOf = (el) => [el, ...flatten(el)].map((e) => e.textContent ?? '').join(' ');

const byData = (root, key, value) => flatten(root).find((e) => e.dataset && e.dataset[key] === value) ?? null;

const allData = (root, key) => flatten(root).filter((e) => e.dataset && e.dataset[key] !== undefined);

// usecase（position_sizing_plan.js）の ViewModel と同じ形（snake_case の plan・camelCase の派生）。
const VM = {
  derived: {
    lossRate: 0.62, expectedValue: 0.4212, kellyFraction: 0.1537, halfKellyFraction: 0.0769,
  },
  edge: {
    kellyFraction: 0.1537, halfKellyFraction: 0.0769, constrainedFraction: 0.0912,
    rorAtConstrained: 0.0098, rorAtKelly: 0.31, growthAtKelly: 0.0541, growthAtConstrained: 0.0402,
  },
  fraction: 0.0912,
  fractionChoice: 'safe',
  plan: {
    lots: [2, 3, 5], total_lot: 10, avg_price: 58650, required_margin: 58650,
    margin_use: 0.341, losscut_price: 58020, losscut_distance: 630, rr: 2.4, breakeven: 0.294,
    ev_yen: 12345.6, buildable_lot: 10, total_risk: 15000, effective_risk: 15000,
    stop_invalid: false, round_zeroed: false, immediate_lc: false, margin_binds: false,
    lc_before_stop: false,
  },
  violations: [],
  levelLines: {
    direction: 'long', entryPrices: [58700, 58600, 58500], stopPrice: 58340, takePrice: 59200,
    losscutPrice: 58020,
  },
};

function build(opts = {}) {
  const doc = fakeDoc();
  const dialog = new PositionSizingDialog({ document: doc, ...opts });
  dialog.open();
  return { doc, dialog, root: doc.body.children[0] };
}

// ---------------------------------------------------------------------------
// 殻（同型元 color_theme_dialogs の open/close 規約）
// ---------------------------------------------------------------------------

test('TC-PD01 open() で body へ 1 枚だけ生成し close() で除去する（二重 open で増えない）', () => {
  // Arrange / Act
  const { doc, dialog } = build();
  // Assert
  assert.equal(doc.body.children.length, 1);
  dialog.open();
  assert.equal(doc.body.children.length, 1, '同時に 2 枚開かない（後勝ち）');
  dialog.close();
  assert.equal(doc.body.children.length, 0);
  assert.doesNotThrow(() => dialog.close(), 'close の重ねがけは冪等');
});

// ---------------------------------------------------------------------------
// Step 1（エッジと破産確率）
// ---------------------------------------------------------------------------

test('TC-PD02 Step 1 の入力 7 件が在席し sims の既定は 4000（裁定済み）', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  for (const f of ['winRate', 'payoffRatio', 'ruinLevel', 'alpha', 'horizon', 'splitCount', 'sims']) {
    assert.ok(byData(root, 'psField', f), `Step 1 の入力 ${f} が無い`);
  }
  assert.equal(byData(root, 'psField', 'sims').value, '4000');
});

test('TC-PD03 Step 1 に「計算する」ボタンがある（MC は押したときだけ走る）', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  const run = byData(root, 'psAction', 'run');
  assert.ok(run, '「計算する」が無い');
  assert.ok(textOf(run).includes('計算'), `文言に「計算」を含む（実際: ${textOf(run)}）`);
});

// ---------------------------------------------------------------------------
// Step 2（採用する f を選ぶ）
// ---------------------------------------------------------------------------

test('TC-PD04 Step 2 は safe / half / full の 3 択・既定は safe（参照実装 :337-346）', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  assert.deepEqual(allData(root, 'psChoice').map((e) => e.dataset.psChoice), ['safe', 'half', 'full']);
  assert.equal(byData(root, 'psChoice', 'safe').classList.contains('is-active'), true);
});

// ---------------------------------------------------------------------------
// Step 3（分割エントリー）
// ---------------------------------------------------------------------------

test('TC-PD05 Step 3 の入力（E・V・mr・K・重み）と方向が在席する', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  for (const f of ['balance', 'pointValue', 'marginRate', 'splits', 'weightPattern', 'direction']) {
    assert.ok(byData(root, 'psField', f), `Step 3 の入力 ${f} が無い`);
  }
});

test('TC-PD06 3 トグル（ロット単位・決済・建て制約）が 3 つとも在席する（TBD-4）', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  assert.ok(byData(root, 'psField', 'lotMode'), 'ロット単位（整数/小数）が無い');
  assert.ok(byData(root, 'psField', 'exitMode'), '決済（ブラケット/時間）が無い');
  assert.ok(byData(root, 'psField', 'capBasis'), '建て制約（証拠金100%/ロスカット基準）が無い');
});

test('TC-PD07 撤廃項目は載せない: gap 系（pmode・g・gapmode）・順張り/逆張り 2 カード・図 3（TBD-1/3）', () => {
  // Arrange / Act
  const { root } = build();
  const fields = allData(root, 'psField').map((e) => e.dataset.psField);
  const text = textOf(root);
  // Assert
  for (const gone of ['pmode', 'priceMode', 'gap', 'gapMode', 'gapmode']) {
    assert.equal(fields.includes(gone), false, `撤廃したはずの入力 ${gone} が載っている（TBD-1）`);
  }
  assert.equal(/順張り|逆張り/.test(text), false, '順張り／逆張りの 2 カードは direct では同一結果＝表示しない');
  assert.equal(/資産推移/.test(text), false, '図 3（資産推移パス）は含めない（TBD-3）');
});

// ---------------------------------------------------------------------------
// 価格欄とアーム式ピッカーの受け口（R-P1）
// ---------------------------------------------------------------------------

test('TC-PD08 価格欄は 建値 K 本＋損切り＋利確。各欄に「チャートで指定」がある（R-P1）', () => {
  // Arrange / Act
  const { root } = build();
  const prices = allData(root, 'psPrice').map((e) => e.dataset.psPrice);
  const picks = allData(root, 'psPick').map((e) => e.dataset.psPick);
  // Assert
  assert.deepEqual(prices, ['entry:0', 'entry:1', 'entry:2', 'stop', 'take'], '既定 K=3 の建値 3 本＋損切り＋利確');
  assert.deepEqual(picks, prices, '価格欄と「チャートで指定」は 1 対 1');
  assert.ok(textOf(byData(root, 'psPick', 'stop')).includes('チャートで指定'));
});

test('TC-PD09 「チャートで指定」はアーム要求を対象識別子つきで呼ぶだけ（ピッカー本体は 8-d）', () => {
  // Arrange
  const armed = [];
  const { root } = build({ onRequestPick: (target) => armed.push(target) });
  // Act
  byData(root, 'psPick', 'stop').fire('click');
  byData(root, 'psPick', 'entry:1').fire('click');
  byData(root, 'psPick', 'take').fire('click');
  // Assert
  assert.deepEqual(armed, ['stop', 'entry:1', 'take']);
});

test('TC-PD10 分割本数 K を変えると建値欄が増減する（K は建値の本数そのもの）', () => {
  // Arrange
  const { root, dialog } = build();
  const splits = byData(root, 'psField', 'splits');
  // Act
  splits.value = '5';
  splits.fire('input');
  // Assert
  const prices = allData(dialog._root, 'psPrice').map((e) => e.dataset.psPrice);
  assert.deepEqual(prices, ['entry:0', 'entry:1', 'entry:2', 'entry:3', 'entry:4', 'stop', 'take']);
});

// ---------------------------------------------------------------------------
// 入力の通知（DIP: 判定も計算もせず、そのまま渡す）
// ---------------------------------------------------------------------------

test('TC-PD11 数値入力の変更を通知する。% の欄は比へ写して渡す（表示単位の変換だけ）', () => {
  // Arrange
  const patches = [];
  const { root } = build({ onChangeParams: (p) => patches.push(p) });
  // Act
  const p = byData(root, 'psField', 'winRate');
  p.value = '42';
  p.fire('input');
  const r = byData(root, 'psField', 'payoffRatio');
  r.value = '3.1';
  r.fire('input');
  // Assert
  assert.deepEqual(patches, [{ winRate: 0.42 }, { payoffRatio: 3.1 }]);
});

test('TC-PD12 択一（方向・重み・3 トグル）の変更を識別子のまま通知する', () => {
  // Arrange
  const patches = [];
  const { root } = build({ onChangeParams: (p) => patches.push(p) });
  // Act
  const sel = byData(root, 'psField', 'capBasis');
  sel.value = 'lc';
  sel.fire('change');
  // Assert
  assert.deepEqual(patches, [{ capBasis: 'lc' }]);
});

test('TC-PD13 採用 f の 3 択は選択状態を移し、識別子を通知する（Step 2）', () => {
  // Arrange
  const patches = [];
  const { root } = build({ onChangeParams: (p) => patches.push(p) });
  // Act
  byData(root, 'psChoice', 'half').fire('click');
  // Assert
  assert.deepEqual(patches, [{ fractionChoice: 'half' }]);
  assert.equal(byData(root, 'psChoice', 'half').classList.contains('is-active'), true);
  assert.equal(byData(root, 'psChoice', 'safe').classList.contains('is-active'), false);
});

test('TC-PD14 価格欄の入力は水準として通知する（価格の単一ソースは水準側・TBD-1）', () => {
  // Arrange
  const levels = [];
  const { root } = build({ onChangeLevels: (l) => levels.push(l) });
  // Act
  byData(root, 'psPrice', 'entry:0').value = '58700';
  byData(root, 'psPrice', 'entry:1').value = '58600';
  byData(root, 'psPrice', 'entry:2').value = '58500';
  const stop = byData(root, 'psPrice', 'stop');
  stop.value = '58340';
  stop.fire('input');
  // Assert
  assert.equal(levels.length, 1);
  assert.deepEqual(levels[0].entryPrices, [58700, 58600, 58500]);
  assert.equal(levels[0].stopPrice, 58340);
  assert.equal(levels[0].takePrice, null, '未入力の利確は null（0 円の利確にしない）');
  assert.equal(levels[0].direction, 'long');
});

test('TC-PD15 setPrice(target, price) で外（ピッカー・水準線 drag）から価格を書き戻せる', () => {
  // Arrange
  const levels = [];
  const { root, dialog } = build({ onChangeLevels: (l) => levels.push(l) });
  // Act
  dialog.setPrice('stop', 58340);
  // Assert
  assert.equal(byData(root, 'psPrice', 'stop').value, '58340');
  assert.equal(levels.length, 1, '書き戻しも入力と同じ経路で通知する（片方向にしない）');
  assert.equal(levels[0].stopPrice, 58340);
});

// ---------------------------------------------------------------------------
// 表示（usecase の ViewModel をそのまま出す＝第 2 実装を作らない）
// ---------------------------------------------------------------------------

test('TC-PD16 render(vm) は ViewModel の値を表示する（式を 1 つも持たない）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render(VM);
  // Assert: 合計ロット・平均建値・ロスカット価格・RR は VM の値がそのまま出る。
  // 書式は参照実装 renderSplit の `${fmtLot(r.totalLot)}単位`（書式の権威は TC-PD39）。
  assert.equal(byData(root, 'psOut', 'totalLot').textContent, '10単位');
  assert.equal(byData(root, 'psOut', 'avgPrice').textContent, '58650');
  assert.equal(byData(root, 'psOut', 'losscutPrice').textContent, '58020');
  assert.equal(byData(root, 'psOut', 'rr').textContent, '2.40 : 1');
  // Step 1 の派生（MC 非依存）と MC 結果も VM 由来。
  assert.equal(byData(root, 'psOut', 'kellyFraction').textContent, '15.37%');
  assert.equal(byData(root, 'psOut', 'constrainedFraction').textContent, '9.12%');
  assert.equal(byData(root, 'psOut', 'fraction').textContent, '9.12%');
});

test('TC-PD17 MC 未実行（edge=null）の欄は「—」のまま（0 と偽らない）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render({ ...VM, edge: null, fraction: 0 });
  // Assert
  assert.equal(byData(root, 'psOut', 'constrainedFraction').textContent, '—');
  assert.equal(byData(root, 'psOut', 'kellyFraction').textContent, '15.37%', '派生カードは MC 非依存で出る');
});

test('TC-PD18 違反・警告は VM の判定をそのまま出す（判定を作らない）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render({
    ...VM,
    violations: ['stop_invalid'],
    plan: { ...VM.plan, immediate_lc: true, margin_binds: true },
  });
  // Assert
  const warn = byData(root, 'psOut', 'warnings').textContent;
  assert.match(warn, /stop_invalid/);
  assert.match(warn, /immediate_lc/);
  assert.match(warn, /margin_binds/);
});

test('TC-PD19 閉じているときの render は no-op（例外にしない）', () => {
  // Arrange
  const { dialog } = build();
  dialog.close();
  // Act / Assert
  assert.doesNotThrow(() => dialog.render(VM));
});

test('TC-PD20 「計算する」は MC 実行要求を呼ぶだけ（計算は usecase・実行場所は Worker）', () => {
  // Arrange
  const calls = [];
  const { root } = build({ onRun: () => calls.push('run') });
  // Act
  byData(root, 'psAction', 'run').fire('click');
  // Assert
  assert.deepEqual(calls, ['run']);
});

test('TC-PD21 K の変更で価格欄を作り直しても、同じ欄の入力値は残る（入力を捨てない）', () => {
  // Arrange: 建値 1 と損切りを入れてから K を増やす。
  const { root, dialog } = build();
  byData(root, 'psPrice', 'entry:0').value = '58700';
  byData(root, 'psPrice', 'stop').value = '58340';
  // Act
  const splits = byData(root, 'psField', 'splits');
  splits.value = '4';
  splits.fire('input');
  // Assert
  assert.equal(byData(dialog._root, 'psPrice', 'entry:0').value, '58700');
  assert.equal(byData(dialog._root, 'psPrice', 'stop').value, '58340');
  assert.equal(byData(dialog._root, 'psPrice', 'entry:3').value, '', '増えた欄は空から始まる');
});

// ---------------------------------------------------------------------------
// 決済方式（exit）の表示出し分け（ISSUE-368 乖離記録 1 の解消・スライス 7）
//
// 参照実装の定義（**読んで確認した事実**・推測ではない）:
//   `S.exit` の出現箇所は :387-389（DOM）/ :578（初期値）/ :799-800（トグル→renderSplit）/
//   :1064（renderSplit の表示分岐）/ :1115・:1136（テキスト出力）だけで、
//   **build()（:956-1031）には 1 箇所も出てこない**＝計算には効かず、表示だけを切り替える。
//   - bracket: 損益分岐到達確率 / 実測勝率 p / 超過勝率 / 期待値（実測 p ベース）の 4 行
//   - time   : 代わりに「期待値 EV（①実測, R マルチプル）＝ Rp−q」1 行と注記
// ---------------------------------------------------------------------------

test('TC-PD22 既定（ブラケット）は 2 値評価の 4 行を出し、時間決済の行は出さない（:1064）', () => {
  // Arrange / Act
  const { root, dialog } = build();
  dialog.render(VM);
  // Assert
  for (const key of ['breakeven', 'winRate', 'excess', 'evYen']) {
    assert.ok(byData(root, 'psOut', key), `ブラケット決済の行 ${key} が無い`);
  }
  assert.equal(byData(root, 'psGroup', 'bracket').classList.contains('is-hidden'), false);
  assert.equal(byData(root, 'psGroup', 'time').classList.contains('is-hidden'), true);
});

test('TC-PD23 時間決済へ切り替えると 2 値評価を隠し、EV（R マルチプル）を出す（:1064 の else 側）', () => {
  // Arrange
  const { root, dialog } = build();
  dialog.render(VM);
  // Act
  const sel = byData(root, 'psField', 'exitMode');
  sel.value = 'time';
  sel.fire('change');
  // Assert
  assert.equal(byData(root, 'psGroup', 'bracket').classList.contains('is-hidden'), true);
  assert.equal(byData(root, 'psGroup', 'time').classList.contains('is-hidden'), false);
  assert.equal(
    byData(root, 'psOut', 'evMultiple').textContent,
    '+0.421',
    'EV は ViewModel の derived.expectedValue（Rp−q）を参照実装の書式で出す＝式を持たない',
  );
});

test('TC-PD24 exit は usecase へ渡さない（参照実装で build() に効かない＝表示だけの関心）', () => {
  // Arrange
  const patches = [];
  const { root } = build({ onChangeParams: (p) => patches.push(p) });
  // Act
  const sel = byData(root, 'psField', 'exitMode');
  sel.value = 'time';
  sel.fire('change');
  // Assert
  assert.deepEqual(patches, [], '計算に効かない値を usecase へ流すと「効いているつもり」の偽配線になる');
});

test('TC-PD25 時間決済には参照実装どおりの注記を出す（2 値評価は不適用）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  const sel = byData(root, 'psField', 'exitMode');
  sel.value = 'time';
  sel.fire('change');
  dialog.render(VM);
  // Assert
  assert.match(textOf(byData(root, 'psGroup', 'time')), /2 値評価は不適用|2値評価は不適用/);
});

test('TC-PD26 addEntryPrice(price) は建値を 1 本増やして書き込む（右クリック「建値に追加」の受け口）', () => {
  // Arrange: 既定 K=3。
  const levels = [];
  const { root, dialog } = build({ onChangeLevels: (l) => levels.push(l) });
  byData(root, 'psPrice', 'entry:0').value = '58700';
  // Act
  dialog.addEntryPrice(58500);
  // Assert
  assert.equal(byData(dialog._root, 'psField', 'splits').value, '4', 'K が 1 本増える（K＝建値の本数）');
  assert.equal(byData(dialog._root, 'psPrice', 'entry:3').value, '58500', '増えた欄へ書き込む');
  assert.equal(byData(dialog._root, 'psPrice', 'entry:0').value, '58700', '既存の入力は残る');
  assert.equal(levels.length, 1, '追加も入力と同じ経路で通知する');
  assert.equal(levels[0].entryPrices.length, 4);
});

test('TC-PD27 syncPrices(levelLines) は通知せずに価格欄へ書き戻す（水準線 drag の反映・エコーしない）', () => {
  // Arrange: drag は水準そのものを更新する。モーダルは「表示を合わせる」だけでよく、
  //   ここで通知すると drag → モーダル → drag の往復（エコー）になる。
  const levels = [];
  const { root, dialog } = build({ onChangeLevels: (l) => levels.push(l) });
  // Act
  dialog.syncPrices({
    direction: 'long', entryPrices: [58700, 58600], stopPrice: 58340, takePrice: 59200,
  });
  // Assert
  assert.equal(byData(dialog._root, 'psField', 'splits').value, '2', 'K は建値の本数に合わせる');
  assert.equal(byData(dialog._root, 'psPrice', 'entry:1').value, '58600');
  assert.equal(byData(dialog._root, 'psPrice', 'stop').value, '58340');
  assert.equal(byData(dialog._root, 'psPrice', 'take').value, '59200');
  assert.deepEqual(levels, [], '書き戻しでは通知しない（エコー防止）');
  assert.equal(byData(root, 'psField', 'direction').value, 'long');
});

test('TC-PD28 syncPrices は利確 null を空欄にする（0 円の利確にしない）', () => {
  // Arrange
  const { dialog } = build();
  dialog.syncPrices({
    direction: 'long', entryPrices: [58700], stopPrice: 58340, takePrice: 59200,
  });
  // Act
  dialog.syncPrices({
    direction: 'long', entryPrices: [58700], stopPrice: 58340, takePrice: null,
  });
  // Assert
  assert.equal(byData(dialog._root, 'psPrice', 'take').value, '');
});

// ---------------------------------------------------------------------------
// 既定値の単一ソース（ISSUE-368 スライス 7）
//   合成根が usecase の初期 params / levels を組み立てるとき、モーダルの初期表示と食い違うと
//   「画面には 38% と出ているのに計算は別の値」という取り違えが起きる。既定はモーダルの
//   定義表から導出し、合成根はそれを使う（2 か所に書かない）。
// ---------------------------------------------------------------------------

test('TC-PD29 defaultParams() はモーダルの初期表示と同じ値を返す（% は比へ）', () => {
  // Arrange / Act
  const { root } = build();
  const params = defaultParams();
  // Assert
  assert.equal(params.winRate, 0.38);
  assert.equal(byData(root, 'psField', 'winRate').value, '38', '画面は % 表示・計算は比');
  assert.equal(params.payoffRatio, 2.74);
  assert.equal(params.marginRate, 0.1);
  assert.equal(params.sims, 4000);
  assert.equal(params.balance, 172000);
  assert.equal(params.fractionChoice, 'safe');
});

test('TC-PD30 重みの既定は参照実装 :578（S.wpattern=linear）に合わせる', () => {
  // Arrange / Act
  const { root } = build();
  // Assert
  assert.equal(byData(root, 'psField', 'weightPattern').value, 'linear');
  assert.equal(defaultParams().weightPattern, 'linear');
  assert.equal(defaultParams().lotMode, 'int', 'ロット単位の既定は整数（:578 S.lotmode）');
  assert.equal(defaultParams().capBasis, 'lc', '建て制約の既定はロスカット基準（:578 S.ltmode）');
});

test('TC-PD31 defaultLevels() は「まだ価格を入れていない」状態（K 本の空欄）を返す', () => {
  // Arrange / Act
  const levels = defaultLevels();
  // Assert
  assert.equal(levels.direction, 'long');
  assert.deepEqual(levels.entryPrices, [null, null, null], '既定 K=3 の空欄');
  assert.equal(levels.stopPrice, null);
  assert.equal(levels.takePrice, null);
});

// ---------------------------------------------------------------------------
// MC 進捗の表示（設計スライス 5 NFR-09「MC 実行中もチャート操作が固まらない／**進捗が進む**」）
//
//   進捗の観測点は MC ループの内側にしかなく、domain → Worker → gateway → usecase の
//   4 段はすでに通っていたが、**表示先が無かった**（62 回の postMessage が空撃ちだった）。
//   計算は数秒かかるため、表示が無いと「押しても何も起きない／固まった」と区別できない。
//   本モーダルは**表示するだけ**（比→%の書式は Presenter の責務・計算は 1 つも持たない）。
// ---------------------------------------------------------------------------

test('TC-PD32 setProgress(ratio) は進捗を表示し、null で消える（完了後に残さない）', () => {
  // Arrange
  const { dialog, root } = build();
  const progress = () => byData(root, 'psOut', 'progress');
  assert.equal(progress().textContent, '', '開いた直後は進捗を出さない（走っていないため）');
  // Act / Assert: 実行中。
  dialog.setProgress(0.42);
  assert.equal(progress().textContent, '計算中 42%');
  dialog.setProgress(1);
  assert.equal(progress().textContent, '計算中 100%');
  // Act / Assert: 完了・失敗で消す（古い進捗を画面に残さない）。
  dialog.setProgress(null);
  assert.equal(progress().textContent, '');
});

test('TC-PD33 閉じているときの setProgress は no-op（例外にしない）', () => {
  // Arrange
  const { dialog } = build();
  dialog.close();
  // Act / Assert
  assert.doesNotThrow(() => dialog.setProgress(0.5));
});

// ---------------------------------------------------------------------------
// select の既定値（実 UI 実測 2026-08-20・参照実装 :578 が正解を定義する）
//
//   実 UI 実測: 重み=equal / 建て制約=margin。参照実装 :578 は
//   `wpattern:'linear'` / `ltmode:'lc'`。SELECTS 表の `def` は既に linear / lc なので
//   「既定が 2 か所にある」のではなく、**`select.value` を option 追加より前に設定していた**
//   のが原因である（HTML 仕様: 一致する option が無い value 代入は選択に反映されず、
//   その後 option を足すと非 multiple の select は先頭 option が選択状態になる）。
//
//   実測 5 件はすべて `options[0][0]` と一致し、`def` とは 2 件で食い違う:
//     direction  options[0]=long    def なし → 実測 long    （両説一致）
//     weight     options[0]=equal   def=linear → 実測 equal  （def 説と矛盾）
//     lotMode    options[0]=int     def なし → 実測 int      （両説一致）
//     exitMode   options[0]=bracket def なし → 実測 bracket  （両説一致）
//     capBasis   options[0]=margin  def=lc   → 実測 margin   （def 説と矛盾）
//   ＝「先頭 option が勝っている」で 5/5 説明でき、「def が使われている」では 3/5 しか
//   説明できない。よって原因は代入順序と確定する。
//
//   上の El は value を素のプロパティとして持つため順序差を再現できず、既存検定は
//   全緑ですり抜けていた。ここでは仕様どおりの select を使う（fake の穴を塞ぐ）。
// ---------------------------------------------------------------------------

// HTMLSelectElement の value セマンティクスを再現する最小 fake。
class SpecSelectEl extends El {
  constructor() {
    super('select');
  }

  // 基底 El の constructor が this.value='' を代入するため（サブクラスのフィールド初期化より
  //   前に走る）、option 一覧は参照時に遅延生成する。
  get _opts() { return (this._optionValues ??= []); }

  get value() {
    // 選択が無い非 multiple の select は先頭 option の値を返す（仕様どおり）。
    if (this._value) { return this._value; }
    return this._opts[0] ?? '';
  }

  set value(v) {
    // 一致する option が無ければ選択されない（＝代入は捨てられる）。
    this._value = this._opts.includes(v) ? v : '';
  }

  append(...kids) {
    for (const k of kids) {
      if (k && k.tag === 'option') { this._opts.push(k.value); }
    }
    super.append(...kids);
  }
}

function specDoc() {
  const body = new El('body');
  return {
    body,
    createElement: (t) => (t === 'select' ? new SpecSelectEl() : new El(t)),
  };
}

test('TC-PD34 select の初期表示が参照実装 :578 の既定と一致する（重み linear・建て制約 lc）', () => {
  // Arrange: 仕様どおりの select を持つ DOM で開く。
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc });
  dialog.open();
  const root = doc.body.children[0];
  const valueOf = (key) => byData(root, 'psField', key).value;
  // Act / Assert: 参照実装 :578 の S 初期値（dir/exit/wpattern/lotmode/ltmode）。
  assert.equal(valueOf('direction'), 'long');
  assert.equal(valueOf('weightPattern'), 'linear', '参照実装 :578 は wpattern:"linear"');
  assert.equal(valueOf('lotMode'), 'int');
  assert.equal(valueOf('exitMode'), 'bracket');
  assert.equal(valueOf('capBasis'), 'lc', '参照実装 :578 は ltmode:"lc"');
});

test('TC-PD35 画面の初期表示と usecase の初期 params が一致する（既定は単一ソース）', () => {
  // 画面が equal を出しているのに計算が linear で回る、という取り違えを構造で止める。
  // Arrange
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc });
  dialog.open();
  const root = doc.body.children[0];
  const params = defaultParams();
  // Act / Assert: params が持つ択一キーは、画面の select と同じ値でなければならない。
  for (const key of ['weightPattern', 'lotMode', 'capBasis']) {
    assert.equal(
      byData(root, 'psField', key).value,
      params[key],
      `${key}: 画面の初期表示と計算の初期値が食い違っている`,
    );
  }
});

// ---------------------------------------------------------------------------
// 表示書式（参照実装が正解を定義する・実 UI 実測 2026-08-20 の是正）
//
//   実 UI 実測: EV=0.42120000000000013 / f*=0.1537226277372263 のように**生の float**が出ていた。
//   参照実装 `updateDerived()` は q→toFixed(3)、EV→符号付き toFixed(3)、f 系→% 表示（toFixed(2)）と
//   定義している。整形は View（Presenter）の責務で、計算値は 1 つも変えない（§3 UC-04）。
//   参照実装に表示定義が無い項目へ勝手な整形規則を足さないこと（下の rorAtConstrained の注記参照）。
// ---------------------------------------------------------------------------

test('TC-PD36 Step 1 の派生は参照実装 updateDerived の書式で出る（q・EV・f*・ハーフ）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render(VM);
  // Assert: q→`q.toFixed(3)` / EV→`(ev>=0?'+':'')+ev.toFixed(3)` /
  //   f*→`(f*100).toFixed(2)+'%'` / ハーフ→`(max(f,0)/2*100).toFixed(2)+'%'`
  assert.equal(byData(root, 'psOut', 'lossRate').textContent, '0.620');
  assert.equal(byData(root, 'psOut', 'expectedValue').textContent, '+0.421');
  assert.equal(byData(root, 'psOut', 'kellyFraction').textContent, '15.37%');
  assert.equal(byData(root, 'psOut', 'halfKellyFraction').textContent, '7.69%');
});

test('TC-PD37 EV が負なら符号を付けない（参照実装は正のときだけ + を足す）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render({ ...VM, derived: { ...VM.derived, expectedValue: -0.1234 } });
  // Assert: `${ev>=0?'+':''}${ev.toFixed(3)}` → 負は toFixed が付ける '−' のみ。
  assert.equal(byData(root, 'psOut', 'expectedValue').textContent, '-0.123');
});

test('TC-PD38 f 系（制約 f・採用 f）は % 表示（参照実装 c_safe / chosenF）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render(VM);
  // Assert: `(fSafe*100).toFixed(2)+'%'` / `(f*100).toFixed(2)+'%'`
  assert.equal(byData(root, 'psOut', 'constrainedFraction').textContent, '9.12%');
  assert.equal(byData(root, 'psOut', 'fraction').textContent, '9.12%');
});

test('TC-PD39 Step 3 は参照実装 renderSplit の書式で出る（ロット・金額・比・％）', () => {
  // Arrange
  const { root, dialog } = build();
  // Act
  dialog.render(VM);
  const out = (k) => byData(root, 'psOut', k).textContent;
  // Assert: 参照実装の該当式どおり。
  assert.equal(out('totalLot'), '10単位', '`fmtLot(r.totalLot)+"単位"`');
  assert.equal(out('avgPrice'), '58650', '`r.avgP.toFixed(0)`');
  assert.equal(out('totalRisk'), '¥15,000', '`¥${Math.round(r.totalRisk).toLocaleString()}`');
  assert.equal(out('rr'), '2.40 : 1', '`${r.rr.toFixed(2)} : 1`');
  assert.equal(out('breakeven'), '29.4%', '`(r.breakeven*100).toFixed(1)+"%"`');
  assert.equal(out('requiredMargin'), '¥58,650', '`¥${Math.round(r.reqMargin).toLocaleString()}`');
  assert.equal(out('marginUse'), '34.1%', '`(r.marginUse*100).toFixed(1)+"%"`');
  assert.equal(out('losscutPrice'), '58020', '`r.lcPrice.toFixed(0)`');
  assert.equal(out('buildableLot'), '10単位', '`fmtLot(r.totalLotBuild)+"単位"`');
  assert.equal(out('evYen'), '+¥12,346', '`${x>=0?"+":"−"}¥${Math.abs(Math.round(x)).toLocaleString()}`');
});

test('TC-PD40 ロスカット価格の分岐は参照実装どおり（即時／0 未満／通常）', () => {
  // Arrange
  const { root, dialog } = build();
  const out = () => byData(root, 'psOut', 'losscutPrice').textContent;
  // Act / Assert: `r.immediateLC?'即時（証拠金不足）':(r.lcPrice<0?...+'（0未満・到達不能）':...)`
  dialog.render({ ...VM, plan: { ...VM.plan, immediate_lc: true } });
  assert.equal(out(), '即時（証拠金不足）');
  dialog.render({ ...VM, plan: { ...VM.plan, losscut_price: -120.4 } });
  assert.equal(out(), '-120（0未満・到達不能）');
  dialog.render(VM);
  assert.equal(out(), '58020');
});

test('TC-PD41 ロット表示は lotMode に従う（参照実装 fmtLot: int は切り捨て・dec は小数 2 桁）', () => {
  // Arrange: 既定は int。
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc });
  dialog.open();
  const root = doc.body.children[0];
  const out = () => byData(root, 'psOut', 'totalLot').textContent;
  // Act / Assert: `S.lotmode==='int'?Math.floor(x+1e-9).toLocaleString():x.toFixed(2)`
  dialog.render({ ...VM, plan: { ...VM.plan, total_lot: 1234.7 } });
  assert.equal(out(), '1,234単位', 'int は切り捨て＋桁区切り');
  byData(root, 'psField', 'lotMode').value = 'dec';
  dialog.render({ ...VM, plan: { ...VM.plan, total_lot: 1234.7 } });
  assert.equal(out(), '1234.70単位', 'dec は小数 2 桁');
});

// ---------------------------------------------------------------------------
// 重み「カスタム」（工程 5 レビュー 🔴-3・node で再現）
//
//   再現: 重みで「カスタム」を選ぶと
//   `weight_pattern='custom' には custom_weights が必要です` が throw され計算が止まる。
//   選択肢だけ移植して入力欄（参照実装 `ensureCustomLen` / `renderCustomInputs`）を
//   落としたのが原因＝**移植で条件を削った**状態。参照実装どおり移植する:
//     - 既定シードは `[1,2,…,K]`（`while(S.customW.length<K)S.customW.push(S.customW.length+1)`）
//     - K を変えたら長さを追随（伸長はシード・短縮は slice）
//     - 入力は `step 0.5 / min 0`、値は `parseFloat(v)||0`
// ---------------------------------------------------------------------------

test('TC-PD42 重みカスタムを選ぶと既定シード [1..K] が計算へ渡る（例外で止まらない）', () => {
  // Arrange
  const patches = [];
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc, onChangeParams: (p) => patches.push(p) });
  dialog.open();
  const root = doc.body.children[0];
  const sel = byData(root, 'psField', 'weightPattern');
  // Act
  sel.value = 'custom';
  sel.fire('change');
  // Assert: パターンと重みが**同時に**渡る（別々だと custom_weights 不在の瞬間に throw する）。
  const last = patches.at(-1);
  assert.equal(last.weightPattern, 'custom');
  assert.deepEqual(last.customWeights, [1, 2, 3], '参照実装 ensureCustomLen の既定シード（K=3）');
});

test('TC-PD43 カスタム欄は K 本描かれ、値の変更が計画へ渡る（参照 renderCustomInputs）', () => {
  // Arrange
  const patches = [];
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc, onChangeParams: (p) => patches.push(p) });
  dialog.open();
  const root = doc.body.children[0];
  const sel = byData(root, 'psField', 'weightPattern');
  sel.value = 'custom';
  sel.fire('change');
  // Act
  const inputs = allData(root, 'psCustomWeight');
  assert.equal(inputs.length, 3, 'K=3 本の入力欄が出ていない');
  assert.equal(inputs[0].step, '0.5', '参照実装は step 0.5');
  assert.equal(inputs[0].min, '0', '参照実装は min 0');
  inputs[1].value = '4.5';
  inputs[1].fire('input');
  // Assert
  assert.deepEqual(patches.at(-1).customWeights, [1, 4.5, 3]);
});

test('TC-PD44 K を変えるとカスタム欄の長さが追随する（伸長はシード・短縮は切り詰め）', () => {
  // Arrange
  const patches = [];
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc, onChangeParams: (p) => patches.push(p) });
  dialog.open();
  const root = doc.body.children[0];
  const sel = byData(root, 'psField', 'weightPattern');
  sel.value = 'custom';
  sel.fire('change');
  const splits = byData(root, 'psField', 'splits');
  // Act: K=5 へ伸ばす。
  splits.value = '5';
  splits.fire('input');
  // Assert
  assert.equal(allData(root, 'psCustomWeight').length, 5);
  assert.deepEqual(patches.at(-1).customWeights, [1, 2, 3, 4, 5], '伸長分は参照実装のシード');
  // Act: K=2 へ縮める。
  splits.value = '2';
  splits.fire('input');
  // Assert
  assert.equal(allData(root, 'psCustomWeight').length, 2);
  assert.deepEqual(patches.at(-1).customWeights, [1, 2], '短縮は slice');
});

test('TC-PD45 カスタム以外を選ぶと入力欄は出ない（参照実装は custom のときだけ表示）', () => {
  // Arrange
  const doc = specDoc();
  const dialog = new PositionSizingDialog({ document: doc });
  dialog.open();
  const root = doc.body.children[0];
  const sel = byData(root, 'psField', 'weightPattern');
  // Act / Assert: 既定（linear）では出ない。
  assert.equal(allData(root, 'psCustomWeight').length, 0);
  sel.value = 'custom';
  sel.fire('change');
  assert.equal(allData(root, 'psCustomWeight').length, 3);
  sel.value = 'equal';
  sel.fire('change');
  assert.equal(allData(root, 'psCustomWeight').length, 0, 'custom を外しても欄が残っている');
});
