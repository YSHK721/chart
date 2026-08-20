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
const SELECTS = [
  { key: 'direction', label: '方向', options: [['long', 'ロング'], ['short', 'ショート']] },
  {
    key: 'weightPattern',
    label: '重み',
    options: [['equal', '均等'], ['linear', '線形'], ['double', '倍々'], ['custom', 'カスタム']],
  },
  { key: 'lotMode', label: 'ロット単位', options: [['int', '整数'], ['dec', '小数']] },
  { key: 'exitMode', label: '決済', options: [['bracket', 'ブラケット'], ['time', '時間']] },
  {
    key: 'capBasis',
    label: '建て制約',
    options: [['margin', '証拠金 100%'], ['lc', 'ロスカット基準']],
  },
];

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
      ['rr', 'RR'], ['breakeven', '損益分岐勝率'], ['evYen', '期待値（円）'],
      ['requiredMargin', '必要証拠金'], ['marginUse', '証拠金使用率'],
      ['losscutPrice', 'ロスカット価格'], ['buildableLot', '実建可能ロット'],
      ['warnings', '警告'],
    ]) {
      sec.append(this._outRow(key, label));
    }
    return sec;
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

  _selectRow({ key, label, options }) {
    const doc = this._doc;
    const row = doc.createElement('label');
    row.className = 'ps-row';
    const name = doc.createElement('span');
    name.className = 'ps-row-label';
    name.textContent = label;
    const select = doc.createElement('select');
    select.dataset.psField = key;
    select.value = options[0][0];
    for (const [value, text] of options) {
      const opt = doc.createElement('option');
      opt.value = value;
      opt.textContent = text;
      select.append(opt);
    }
    select.addEventListener('change', () => {
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
