// position_sizing_dialog.js — ポジションサイズ計算機のモーダル DOM（両アプリ共有）。
//
// 設計入力（唯一の仕様源）: .doc/POSITION_SIZING_CHART_INTEGRATION_DESIGN.md
//   裁定記録 TBD-1/5（建値も価格の単一ソース＝チャートに一本化。**gap モード・間隔指定は撤廃**。
//     順張り／逆張りの 2 カード比較は参照実装 :1098 の明示により同一結果＝表示しない）、
//   TBD-3（図 3＝資産推移パスは含めない）、TBD-4（3 トグルは 3 つとも残す）、
//   「追加要件裁定 R-P1」（各価格欄の「チャートで指定」がアーム式ピッカーの受け口）、
//   §3 UC-04（表示文字列は Presenter が生成する）。
//
// 参照実装（同型元）: color_theme_dialogs.js（背景＋パネル＋ヘッダ＋本文＋フッタの殻を open で
//   組み立て、close で parentNode から外す。背景クリックでは閉じない＝誤操作防止）。
//
// 責務（SRP）: DOM 構築・イベント配線・**渡された ViewModel の表示**のみ。
//   - 計算は 1 つも持たない。表示値は usecase（position_sizing_plan.js）の ViewModel をそのまま出す
//     （第 2 実装を作らない＝LAYERING_CONVENTIONS の単一ソース規律）。
//   - 協働子（usecase・ChartRenderer・ピッカー）は import しない。すべて注入コールバック（DIP）。
//   - 「チャートで指定」は**アーム要求を呼ぶだけ**。ピッカー本体はスライス 8-d の責務。
//
// 単位の扱い（Presenter の変換）: 参照実装と同じく p・破産水準・α・証拠金率は **%** で入力させ、
//   境界（onChangeParams へ渡す時）で比へ写す。ここにあるのは表示単位の変換だけで、式ではない。

// 入力の定義表（手書きの DOM を並べない＝行を足すのは表への 1 行）。
//   key      : ViewModel / usecase のパラメータ名（そのまま onChangeParams のキーになる）
//   label    : 表示名（参照実装の文言）
//   unit     : '%' なら比へ写して渡す（表示は %）
//   step/min : 数値入力の刻み・下限
const STEP1_FIELDS = [
  {
    key: 'winRate', label: '勝率 p', unit: '%', value: 38, step: 0.1, min: 0,
  },
  {
    key: 'payoffRatio', label: 'ペイオフ比 R', unit: '', value: 2.74, step: 0.01, min: 0,
  },
  {
    key: 'ruinLevel', label: '破産水準', unit: '%', value: 50, step: 1, min: 0,
  },
  {
    key: 'alpha', label: '許容破産確率 α', unit: '%', value: 1, step: 0.1, min: 0,
  },
  {
    key: 'horizon', label: '試行回数 T', unit: '', value: 250, step: 1, min: 1,
  },
  {
    key: 'splitCount', label: '連敗参照 N', unit: '', value: 20, step: 1, min: 1,
  },
  {
    key: 'sims', label: '試行数 sims', unit: '', value: 4000, step: 100, min: 1,
  },
];

const STEP3_FIELDS = [
  {
    key: 'balance', label: '口座残高 E', unit: '', value: 172000, step: 1000, min: 0,
  },
  {
    key: 'pointValue', label: '1 単位あたり価値 V', unit: '', value: 1, step: 0.01, min: 0,
  },
  {
    key: 'marginRate', label: '証拠金率 mr', unit: '%', value: 10, step: 0.1, min: 0,
  },
  {
    key: 'splits', label: '分割本数 K', unit: '', value: 3, step: 1, min: 1,
  },
];

// 択一トグル（TBD-4: 3 つとも残す）と方向・重み。値は参照実装の識別子をそのまま使う。
//   def: 既定値（省略時は options の先頭）。参照実装 :578 の `S` 初期値に合わせる
//   （wpattern:'linear' / lotmode:'int' / ltmode:'lc' / exit:'bracket' / dir:'long'）。
const SELECTS = [
  { key: 'direction', label: '方向', options: [['long', 'ロング'], ['short', 'ショート']] },
  {
    key: 'weightPattern',
    label: '重み',
    options: [['equal', '均等'], ['linear', '線形'], ['double', '倍々'], ['custom', 'カスタム']],
    def: 'linear',
  },
  { key: 'lotMode', label: 'ロット単位', options: [['int', '整数'], ['dec', '小数']] },
  { key: 'exitMode', label: '決済', options: [['bracket', 'ブラケット'], ['time', '時間']] },
  {
    key: 'capBasis',
    label: '建て制約',
    options: [['margin', '証拠金 100%'], ['lc', 'ロスカット基準']],
    def: 'lc',
  },
];

// 既定の採用 f（参照実装 :578 `chosen:'safe'`）。
const DEFAULT_FRACTION_CHOICE = 'safe';

/**
 * usecase の初期 params（**モーダルの定義表から導出する単一ソース**）。
 *
 * 合成根が自前で既定値を書くと、画面の初期表示と計算の初期値が食い違う（画面は 38% なのに
 * 計算は別の値、という取り違え）。数値欄は % を比へ写す規則も入力時と同一にする。
 */
export function defaultParams() {
  const out = {};
  for (const f of [...STEP1_FIELDS, ...STEP3_FIELDS]) {
    if (f.key === 'splits') {
      continue;   // K は建値の本数＝水準側（levels）が持つ。params ではない。
    }
    out[f.key] = f.unit === '%' ? f.value / 100 : f.value;
  }
  for (const s of SELECTS) {
    if (s.key === 'direction' || s.key === 'exitMode') {
      continue;   // 方向は水準（E-02）が持ち、決済方式は表示だけの関心（usecase へ渡さない）。
    }
    out[s.key] = selectDefault(s);
  }
  out.fractionChoice = DEFAULT_FRACTION_CHOICE;
  return out;
}

/**
 * 初期水準（**まだ価格を入れていない**状態）。K 本の空欄と損切り・利確の空欄を表す。
 * 価格は「チャートが単一ソース」であり、初期値を勝手に置かない（TBD-1）。
 */
export function defaultLevels() {
  const splits = STEP3_FIELDS.find((f) => f.key === 'splits');
  const direction = SELECTS.find((s) => s.key === 'direction');
  return {
    direction: selectDefault(direction),
    entryPrices: new Array(splits ? splits.value : 1).fill(null),
    stopPrice: null,
    takePrice: null,
  };
}

function selectDefault(spec) {
  return spec.def ?? spec.options[0][0];
}

export const FRACTION_CHOICES = [
  ['safe', '安全（破産確率制約）'],
  ['half', 'ハーフケリー'],
  ['full', 'フルケリー'],
];

export class PositionSizingDialog {
  /**
   * @param {object} opts
   * @param {object} opts.document DOM 実装（注入。null 可＝no-op）。
   * @param {?function} [opts.onChangeParams] (patch) => void。入力変更（単位は比へ写した後）。
   * @param {?function} [opts.onChangeLevels] ({direction,entryPrices,stopPrice,takePrice}) => void。
   * @param {?function} [opts.onRun] () => void。「計算する」＝ MC 実行要求。
   * @param {?function} [opts.onRequestPick] (target) => void。「チャートで指定」＝アーム要求
   *   （target は 'entry:0' / 'stop' / 'take'）。ピッカー本体はスライス 8-d。
   */
  constructor({
    document: doc = null, onChangeParams = null, onChangeLevels = null,
    onRun = null, onRequestPick = null,
  } = {}) {
    this._doc = doc;
    this._onChangeParams = typeof onChangeParams === 'function' ? onChangeParams : null;
    this._onChangeLevels = typeof onChangeLevels === 'function' ? onChangeLevels : null;
    this._onRun = typeof onRun === 'function' ? onRun : null;
    this._onRequestPick = typeof onRequestPick === 'function' ? onRequestPick : null;
    this._root = null;
    this._outs = new Map();      // data-ps-out キー -> 表示要素
    this._choiceEls = new Map(); // 採用 f の 3 択
    this._fields = new Map();    // data-ps-field キー -> 入力要素
    this._prices = new Map();    // 'entry:i' / 'stop' / 'take' -> 入力要素
    this._priceBox = null;       // 価格欄のコンテナ（K の変更で作り直す）
    this._exitGroups = new Map();  // 決済方式で出し分ける表示群（bracket / time）
    this._exitMode = 'bracket';    // 参照実装 :578 の初期値
  }

  _usable() {
    const doc = this._doc;
    return !!(doc && typeof doc.createElement === 'function' && doc.body && typeof doc.body.append === 'function');
  }

  isOpen() {
    return this._root !== null;
  }

  close() {
    if (this._root && this._root.parentNode) {
      this._root.parentNode.removeChild(this._root);
    }
    this._root = null;
    this._outs = new Map();
    this._choiceEls = new Map();
    this._fields = new Map();
    this._prices = new Map();
    this._priceBox = null;
    this._exitGroups = new Map();
  }

  open() {
    if (!this._usable()) {
      return;   // DOM 不在（SSR・テスト最小 fake）は no-op。
    }
    this.close();   // 同時に 2 枚開かない（後勝ち・同型元と同じ規約）。
    const doc = this._doc;
    const root = doc.createElement('div');
    root.className = 'ps-dialog-backdrop is-open';
    root.dataset.psDialog = 'plan';

    const panel = doc.createElement('div');
    panel.className = 'ps-dialog';
    if (typeof panel.setAttribute === 'function') {
      panel.setAttribute('role', 'dialog');
    }

    const head = doc.createElement('div');
    head.className = 'ps-dialog-head';
    const title = doc.createElement('span');
    title.className = 'ps-dialog-title';
    title.textContent = 'ポジションサイズ計算機';
    const closeBtn = this._button('×', 'cancel', 'ps-dialog-close');
    closeBtn.addEventListener('click', () => this.close());
    head.append(title, closeBtn);

    const body = doc.createElement('div');
    body.className = 'ps-dialog-body';
    body.append(this._step1(), this._step2(), this._step3());

    panel.append(head, body);
    root.append(panel);
    // 背景クリックでは閉じない（誤操作防止・color_theme_dialogs と同方針）。
    root.addEventListener('mousedown', (ev) => {
      if (ev && ev.target === root && typeof ev.stopPropagation === 'function') {
        ev.stopPropagation();
      }
    });
    doc.body.append(root);
    this._root = root;
  }

  // ---- Step 1: エッジと破産確率 ---------------------------------------------
  _step1() {
    const sec = this._section('Step 1 エッジと破産確率');
    for (const f of STEP1_FIELDS) {
      sec.append(this._numberRow(f));
    }
    for (const [key, label] of [
      ['lossRate', '負け率 q'], ['expectedValue', '期待値 EV'],
      ['kellyFraction', 'ケリー f*'], ['halfKellyFraction', 'ハーフ'],
      ['constrainedFraction', '制約 f'], ['rorAtConstrained', '制約 f の破産確率'],
    ]) {
      sec.append(this._outRow(key, label));
    }
    const run = this._button('計算する', 'run', 'ps-dialog-run');
    run.addEventListener('click', () => this._onRun?.());
    sec.append(run);
    return sec;
  }

  // ---- Step 2: 採用する f を選ぶ ---------------------------------------------
  _step2() {
    const sec = this._section('Step 2 採用する f を選ぶ');
    for (const [value, label] of FRACTION_CHOICES) {
      const btn = this._doc.createElement('button');
      btn.type = 'button';
      btn.className = value === 'safe' ? 'ps-choice is-active' : 'ps-choice';
      btn.dataset.psChoice = value;
      btn.textContent = label;
      btn.addEventListener('click', () => {
        this._selectChoice(value);
        this._onChangeParams?.({ fractionChoice: value });
      });
      this._choiceEls.set(value, btn);
      sec.append(btn);
    }
    sec.append(this._outRow('fraction', '採用 f'));
    return sec;
  }

  // ---- Step 3: 分割エントリー ------------------------------------------------
  _step3() {
    const sec = this._section('Step 3 分割エントリー');
    for (const f of STEP3_FIELDS) {
      sec.append(this._numberRow(f));
    }
    for (const s of SELECTS) {
      sec.append(this._selectRow(s));
    }
    // 価格欄（建値 K 本＋損切り＋利確）。K の変更で作り直すためコンテナを保持する。
    const box = this._doc.createElement('div');
    box.className = 'ps-prices';
    sec.append(box);
    this._priceBox = box;
    this._renderPriceRows();
    for (const [key, label] of [
      ['totalLot', '合計ロット'], ['avgPrice', '平均建値'], ['totalRisk', '合計リスク'],
      ['rr', 'RR'],
    ]) {
      sec.append(this._outRow(key, label));
    }
    // 決済方式による表示の出し分け（参照実装 :1064）。**計算には効かない**（build() に exit は
    //   1 箇所も出てこない）ため、切り替えるのは出す行だけで、値はどちらも同じ ViewModel から来る。
    sec.append(this._bracketGroup(), this._timeGroup());
    for (const [key, label] of [
      ['requiredMargin', '必要証拠金'], ['marginUse', '証拠金使用率'],
      ['losscutPrice', 'ロスカット価格'], ['buildableLot', '実建可能ロット'],
      ['warnings', '警告'],
    ]) {
      sec.append(this._outRow(key, label));
    }
    this._applyExitMode('bracket');   // 既定は参照実装 :578 と同じ bracket。
    return sec;
  }

  // ブラケット決済（:1064 の then 側）: 2 値評価の 4 行。
  _bracketGroup() {
    const box = this._doc.createElement('div');
    box.className = 'ps-exit-group';
    box.dataset.psGroup = 'bracket';
    for (const [key, label] of [
      ['breakeven', '損益分岐到達確率（無ドリフト）'],
      ['winRate', '実測勝率 p（入力）'],
      ['excess', '超過勝率（p−分岐点）'],
      ['evYen', '期待値（実測 p ベース）'],
    ]) {
      box.append(this._outRow(key, label));
    }
    this._exitGroups.set('bracket', box);
    return box;
  }

  // 時間決済（:1064 の else 側）: EV（① 実測・R マルチプル）1 行と注記。
  //   EV＝Rp−q は ViewModel の derived.expectedValue と同一の値であり、ここで計算し直さない。
  _timeGroup() {
    const box = this._doc.createElement('div');
    box.className = 'ps-exit-group';
    box.dataset.psGroup = 'time';
    box.append(this._outRow('evMultiple', '期待値 EV（① 実測・R マルチプル）'));
    const note = this._doc.createElement('div');
    note.className = 'ps-exit-note';
    note.textContent = '時間決済：多くが途中決済のため利確/損切りの 2 値評価は不適用。'
      + '期待値は ① の実測 p・R（EV=Rp−q）で見る。';
    box.append(note);
    this._exitGroups.set('time', box);
    return box;
  }

  // 出す行の切り替え（表示だけ・値には触れない）。
  _applyExitMode(mode) {
    this._exitMode = mode;
    for (const [key, box] of this._exitGroups) {
      if (box.classList && typeof box.classList.toggle === 'function') {
        box.classList.toggle('is-hidden', key !== mode);
      }
    }
  }

  // ---- 部品 ------------------------------------------------------------------
  _section(title) {
    const el = this._doc.createElement('div');
    el.className = 'ps-dialog-section';
    const head = this._doc.createElement('div');
    head.className = 'ps-dialog-section-title';
    head.textContent = title;
    el.append(head);
    return el;
  }

  _button(label, action, className) {
    const btn = this._doc.createElement('button');
    btn.type = 'button';
    btn.className = className;
    btn.dataset.psAction = action;
    btn.textContent = label;
    return btn;
  }

  _numberRow({
    key, label, unit, value, step, min,
  }) {
    const doc = this._doc;
    const row = doc.createElement('label');
    row.className = 'ps-row';
    const name = doc.createElement('span');
    name.className = 'ps-row-label';
    name.textContent = unit ? `${label}（${unit}）` : label;
    const input = doc.createElement('input');
    input.type = 'number';
    input.dataset.psField = key;
    input.step = String(step);
    input.min = String(min);
    input.value = String(value);
    input.addEventListener('input', () => {
      const raw = Number(input.value);
      if (key === 'splits') {
        // K は「建値の本数」そのもの（別のパラメータではない）。欄を作り直して水準を通知する。
        this._renderPriceRows();
        this._emitLevels();
        return;
      }
      if (!Number.isFinite(raw)) {
        return;   // 入力途中（空・'-'）は通知しない（NaN を usecase へ流さない）。
      }
      this._onChangeParams?.({ [key]: unit === '%' ? raw / 100 : raw });
    });
    this._fields.set(key, input);
    row.append(name, input);
    return row;
  }

  // 価格欄の作り直し（K 本の建値＋損切り＋利確）。既存の入力値は同じ target 名で引き継ぐ。
  _renderPriceRows() {
    const box = this._priceBox;
    if (!box) {
      return;
    }
    const previous = new Map([...this._prices].map(([target, el]) => [target, el.value]));
    box.innerHTML = '';
    this._prices = new Map();
    const splits = this._splitCount();
    const targets = [];
    for (let i = 0; i < splits; i += 1) {
      targets.push([`entry:${i}`, `建値 ${i + 1}`]);
    }
    targets.push(['stop', '損切り'], ['take', '利確']);
    for (const [target, label] of targets) {
      box.append(this._priceRow(target, label, previous.get(target) ?? ''));
    }
  }

  // 1 行: [ラベル] [価格入力] [チャートで指定]。
  //   「チャートで指定」は**アーム要求を呼ぶだけ**（ピッカー本体はスライス 8-d の責務）。
  _priceRow(target, label, value) {
    const doc = this._doc;
    const row = doc.createElement('label');
    row.className = 'ps-row ps-price-row';
    const name = doc.createElement('span');
    name.className = 'ps-row-label';
    name.textContent = label;
    const input = doc.createElement('input');
    input.type = 'number';
    input.dataset.psPrice = target;
    input.step = 'any';
    input.value = value;
    input.addEventListener('input', () => this._emitLevels());
    const pick = doc.createElement('button');
    pick.type = 'button';
    pick.className = 'ps-pick';
    pick.dataset.psPick = target;
    pick.textContent = 'チャートで指定';
    pick.addEventListener('click', () => this._onRequestPick?.(target));
    this._prices.set(target, input);
    row.append(name, input, pick);
    return row;
  }

  /**
   * 外（アーム式ピッカー・水準線 drag）から価格を書き戻す。
   * 入力と同じ経路で通知するため、書き戻しと手入力で水準の更新経路が割れない。
   * @param {string} target 'entry:i' / 'stop' / 'take'
   * @param {number} price
   */
  setPrice(target, price) {
    const input = this._prices.get(target);
    if (!input) {
      return;
    }
    input.value = String(price);
    this._emitLevels();
  }

  /**
   * 水準（ViewModel の levelLines）を価格欄へ**通知せずに**書き戻す（水準線 drag の反映）。
   *
   * 通知しない理由: drag は水準そのものを更新しており、モーダルは表示を合わせるだけでよい。
   * ここで `onChangeLevels` を出すと drag → モーダル → 水準更新 → drag と往復する（エコー）。
   * 逆に手入力・ピッカー・右クリックは**モーダルが起点**なので通知する（`setPrice` 側）。
   *
   * @param {{direction:string,entryPrices:Array<number>,stopPrice:number,takePrice:(number|null)}} levelLines
   */
  syncPrices(levelLines) {
    if (!levelLines || !this._root) {
      return;
    }
    const entries = Array.isArray(levelLines.entryPrices) ? levelLines.entryPrices : [];
    const splits = this._fields.get('splits');
    if (splits && entries.length > 0 && String(entries.length) !== splits.value) {
      splits.value = String(entries.length);
      this._renderPriceRows();
    }
    const write = (target, value) => {
      const input = this._prices.get(target);
      if (input) {
        input.value = (value === null || value === undefined) ? '' : String(value);
      }
    };
    entries.forEach((price, i) => write(`entry:${i}`, price));
    write('stop', levelLines.stopPrice);
    write('take', levelLines.takePrice);
    const direction = this._fields.get('direction');
    if (direction && levelLines.direction) {
      direction.value = levelLines.direction;
    }
  }

  /**
   * 建値を 1 本増やして価格を書き込む（右クリック「この価格を建値に追加」の受け口・R-P3）。
   * K（分割本数）は建値の本数そのものなので、欄を増やすことは K を増やすことと同義である。
   * @param {number} price
   */
  addEntryPrice(price) {
    const splits = this._fields.get('splits');
    if (!splits) {
      return;
    }
    const next = this._splitCount() + 1;
    splits.value = String(next);
    this._renderPriceRows();          // 既存の入力値は target 名で引き継がれる。
    this.setPrice(`entry:${next - 1}`, price);
  }

  // 価格欄の現在値を水準（E-02 の入力）として通知する。判定・派生距離は持たない（domain の責務）。
  _emitLevels() {
    if (!this._onChangeLevels) {
      return;
    }
    const num = (target) => {
      const el = this._prices.get(target);
      const v = el ? Number(el.value) : NaN;
      return Number.isFinite(v) && el.value !== '' ? v : null;
    };
    const entryPrices = [];
    for (let i = 0; i < this._splitCount(); i += 1) {
      entryPrices.push(num(`entry:${i}`));
    }
    this._onChangeLevels({
      direction: this._fields.get('direction')?.value ?? 'long',
      entryPrices,
      stopPrice: num('stop'),
      takePrice: num('take'),   // 未入力は null（0 円の利確にしない）
    });
  }

  _splitCount() {
    const raw = Number(this._fields.get('splits')?.value);
    return Number.isInteger(raw) && raw >= 1 ? raw : 1;
  }

  _selectRow({
    key, label, options, def = null,
  }) {
    const doc = this._doc;
    const row = doc.createElement('label');
    row.className = 'ps-row';
    const name = doc.createElement('span');
    name.className = 'ps-row-label';
    name.textContent = label;
    const select = doc.createElement('select');
    select.dataset.psField = key;
    select.value = selectDefault({ options, def });
    for (const [value, text] of options) {
      const opt = doc.createElement('option');
      opt.value = value;
      opt.textContent = text;
      select.append(opt);
    }
    select.addEventListener('change', () => {
      if (key === 'exitMode') {
        // 参照実装で exit は build() に効かない（表示の出し分けだけ）。usecase へ渡すと
        //   「効いているつもり」の偽配線になるため、ここで表示を切り替えて終える。
        this._applyExitMode(select.value);
        return;
      }
      if (key === 'direction') {
        // 方向は**水準（E-02）が持つ**（ViewModel の levelLines.direction）。params ではない。
        this._emitLevels();
        return;
      }
      this._onChangeParams?.({ [key]: select.value });
    });
    this._fields.set(key, select);
    row.append(name, select);
    return row;
  }

  _outRow(key, label) {
    const doc = this._doc;
    const row = doc.createElement('div');
    row.className = 'ps-out';
    const name = doc.createElement('span');
    name.className = 'ps-out-label';
    name.textContent = label;
    const val = doc.createElement('span');
    val.className = 'ps-out-value';
    val.dataset.psOut = key;
    val.textContent = '—';
    row.append(name, val);
    this._outs.set(key, val);
    return row;
  }

  /**
   * usecase（position_sizing_plan.js）の ViewModel を表示する。
   *
   * **式を 1 つも持たない**: 出すのは VM が持っている数値そのもので、ここで足し引きしない
   *   （第 2 実装を作らない＝単一ソース規律）。値が無いもの（MC 未実行の edge・非数）は
   *   「—」を出す（0 と偽らない）。
   *
   * @param {object} vm PositionSizingPlanUseCase.viewModel() の戻り値。
   */
  render(vm) {
    if (!this._root || !vm) {
      return;   // 閉じているときは no-op。
    }
    const d = vm.derived ?? {};
    const edge = vm.edge ?? {};
    const plan = vm.plan ?? {};
    const values = {
      lossRate: d.lossRate,
      expectedValue: d.expectedValue,
      kellyFraction: d.kellyFraction,
      halfKellyFraction: d.halfKellyFraction,
      constrainedFraction: vm.edge ? edge.constrainedFraction : null,
      rorAtConstrained: vm.edge ? edge.rorAtConstrained : null,
      fraction: vm.fraction,
      totalLot: plan.total_lot,
      avgPrice: plan.avg_price,
      totalRisk: plan.total_risk,
      rr: plan.rr,
      breakeven: plan.breakeven,
      evYen: plan.ev_yen,
      requiredMargin: plan.required_margin,
      marginUse: plan.margin_use,
      losscutPrice: plan.losscut_price,
      buildableLot: plan.buildable_lot,
      excess: plan.excess,
      winRate: plan.win_rate,
      // 時間決済の EV（Rp−q）。derived と同じ値であり、ここで計算し直さない（:1064 else 側）。
      evMultiple: d.expectedValue,
    };
    for (const [key, value] of Object.entries(values)) {
      const el = this._outs.get(key);
      if (el) {
        el.textContent = Number.isFinite(value) ? String(value) : '—';
      }
    }
    this._renderWarnings(vm, plan);
    if (vm.fractionChoice) {
      this._selectChoice(vm.fractionChoice);
    }
  }

  // 警告は **VM の判定をそのまま並べる**（どれが警告かを本モジュールで決め直さない）。
  _renderWarnings(vm, plan) {
    const el = this._outs.get('warnings');
    if (!el) {
      return;
    }
    const flags = ['stop_invalid', 'round_zeroed', 'immediate_lc', 'margin_binds', 'lc_before_stop'];
    const hits = [...(vm.violations ?? []), ...flags.filter((f) => plan[f] === true)];
    el.textContent = hits.length > 0 ? [...new Set(hits)].join(' / ') : '—';
  }

  _selectChoice(value) {
    for (const [key, el] of this._choiceEls) {
      el.classList.toggle('is-active', key === value);
    }
  }
}
