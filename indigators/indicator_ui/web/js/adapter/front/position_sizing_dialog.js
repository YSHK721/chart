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

// 書式は共有モジュールの**単一ソース**から取る（協働子ではなく純粋な値変換のみ）。
//   自前で持つと、同じ規則がピッカー側と 2 か所に割れる（実 UI 実測 2026-08-20 で
//   ゴーストに生の浮動小数が残った）。
import {
  priceInTable, percent1, percent2, signedFixed3, decimal2, decimal3, yen, lotAmount,
} from './price_format.js';

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

// 分割本数 K の下限・上限（参照実装 `renderCustomInputs` の `Math.max(1,Math.min(10,...))`）。
//   本モジュールは何も import できない（TC-SW02）ため、domain/split_entry_plan.js の
//   MIN_SPLITS / MAX_SPLITS と同値をここに置く。食い違うと権威側が例外で弾く
//   （＝食い違いは静かに残らない）。
const MIN_SPLITS = 1;
const MAX_SPLITS = 10;

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


// ---- 警告・評価の文言（参照実装が正解を定義する）--------------------------------
//
// 内部識別子（`stop_invalid` 等）を画面に出さない。フラグ → 文言の対応表を**ここ 1 か所**に置く
// （散らすと、フラグを足したときに文言だけ抜ける）。文言は参照実装
// integrated_position_sizing_calculator.html の該当式をそのまま写す（推測で足さない）。

const WARNING_TEXT = Object.freeze({
  // 参照 `:1051`（方向で「下／上」が入れ替わる）
  stop_invalid: (ctx) => '⚠ ストップ価格が不正。'
    + `${ctx.long ? 'ロングでは建玉より下' : 'ショートでは建玉より上'}にストップ価格を置く必要がある`
    + '（このモードの最寄り建玉より損失側）。数値は暫定表示。',
  // 参照 `:1052` の roundZeroed 節
  round_zeroed: () => '整数モード：各建玉を切り捨て。一部の建玉が0単位に丸められている（ロット過小）。',
  // 参照 `:1084` の marginBinds 節
  margin_binds: (ctx) => `⚠ ${ctx.capBasis === 'lc' ? 'ロスカット価格制約' : '証拠金制約'}でロット制限。`
    + 'f が要求する単位数は建てられない。f を下げるのが根本対処。',
});

// ロスカット評価（参照 `:1077-1081` の**3 分岐**）。移植で 1 通りに縮んでいた（B-2）。
//   条件・順序は参照実装のまま（`!immediateLC && lcPrice<0` を最優先で安全側に読む）。
function losscutAssessmentText(plan) {
  if (plan.immediate_lc !== true && plan.losscut_price < 0) {
    return 'ロスカット価格が負（0未満）＝価格によるロスカットは発生しない。'
      + '価格が0まで下げても有効証拠金が必要証拠金を上回るため、実質的にロスカットは損切りより'
      + '遥か遠く安全側（「実質ロスカットなし」と読む）。週末ギャップ等の急変リスクは別途注意。';
  }
  if (plan.lc_before_stop === true) {
    return '⚠ ロスカットが損切りより手前。証拠金が先に尽き、σ̂ベースの損切りが機能する前に'
      + '成行で強制決済される。ロット（f）を下げるか、ストップを狭めるか、証拠金を増やす必要がある。';
  }
  return '✓ 損切りがロスカットより先に発動（意図通り）。'
    + 'ただしギャップ耐性のため使用率は低いほど安全。';
}

// ---- 表示書式（参照実装が正解を定義する）--------------------------------------
//
// 実 UI 実測（2026-08-20）で `EV=0.42120000000000013` `f*=0.1537226277372263` のように
//   **生の float** が出ていた。参照実装 integrated_position_sizing_calculator.html は
//   項目ごとに書式を定義しており、それが正解である（推測で決めない・足さない）。
//
// ここにあるのは**整形だけ**で、値は 1 つも作らない（§3 UC-04 Presenter の責務）。
//   計算値は ViewModel のまま（第 2 実装を作らない）。
//
// 本モジュールは何も import できない（TC-SW02 が「モーダルは import しない」を施行）ため、
//   書式表はこのファイル内に置く。

// 書式の実体は price_format.js（単一ソース）。ここでは対応表の可読性のため別名だけ与える。
const pct2 = percent2;
const pct1 = percent1;
const fix0 = priceInTable;
const signed3 = signedFixed3;
const fmtLot = lotAmount;

// 出力キー → 書式。**参照実装に定義がある項目だけ**を載せる（無い項目は素の値のまま）。
const OUT_FORMAT = Object.freeze({
  // Step 1 派生カード（参照 `updateDerived()`）
  lossRate: decimal3,                                   // 参照 `q.toFixed(3)`
  expectedValue: signed3,                               // `${ev>=0?'+':''}${ev.toFixed(3)}`
  kellyFraction: pct2,                                  // `(f*100).toFixed(2)+'%'`
  halfKellyFraction: pct2,                              // `(Math.max(f,0)/2*100).toFixed(2)+'%'`
  // Step 1 MC 結果カード
  constrainedFraction: pct2,                            // `(fSafe*100).toFixed(2)+'%'`（c_safe）
  // **注記（外挿）**: 参照実装は rorAtSafe を数値として表示しない（c_safe_sub は α を出す）。
  //   同種の量である rorAtKelly の書式 `(rorAtKelly*100).toFixed(1)+'%'` を当てている。
  //   参照実装が直接定義した規則ではない旨をここに残す（要確認事項として報告済み）。
  rorAtConstrained: pct1,
  // Step 2（参照 `updateChosen()`）
  fraction: pct2,                                       // `(f*100).toFixed(2)+'%'`
  // Step 3（参照 `renderSplit()` の kv 行）
  totalLot: (v, c) => `${fmtLot(v, c.lotMode)}単位`,    // `${fmtLot(r.totalLot)}単位`
  avgPrice: fix0,                                       // `r.avgP.toFixed(0)`
  totalRisk: yen,                                       // `¥${Math.round(r.totalRisk).toLocaleString()}`
  rr: (v) => `${decimal2(v)} : 1`,                      // 参照 `${r.rr.toFixed(2)} : 1`
  breakeven: pct1,                                      // `(r.breakeven*100).toFixed(1)+'%'`
  winRate: pct1,                                        // `(r.pEmp*100).toFixed(1)+'%'`
  excess: (v) => `${v >= 0 ? '+' : ''}${pct1(v)}`,      // `${r.excess>=0?'+':''}${(r.excess*100).toFixed(1)}%`
  evYen: (v) => `${v >= 0 ? '+' : '−'}${yen(Math.abs(v))}`,
  evMultiple: signed3,                                  // 時間決済側の EV（`...toFixed(3)`）
  requiredMargin: yen,                                  // `¥${Math.round(r.reqMargin).toLocaleString()}`
  marginUse: pct1,                                      // `(r.marginUse*100).toFixed(1)+'%'`
  // `r.immediateLC?'即時（証拠金不足）':(r.lcPrice<0?r.lcPrice.toFixed(0)+'（0未満・到達不能）':r.lcPrice.toFixed(0))`
  losscutPrice: (v, c) => {
    if (c.plan.immediate_lc === true) {
      return '即時（証拠金不足）';
    }
    return v < 0 ? `${fix0(v)}（0未満・到達不能）` : fix0(v);
  },
  buildableLot: (v, c) => `${fmtLot(v, c.lotMode)}単位`, // `${fmtLot(r.totalLotBuild)}単位`
});

export const FRACTION_CHOICES = [
  ['safe', '安全（破産確率制約）'],
  ['half', 'ハーフケリー'],
  ['full', 'フルケリー'],
];

/**
 * 価格欄の対象名（`entry:i` / `stop` / `take` → 表示名）。
 * 欄のラベルとアーム中バーの文言が**同じ表**から出る（片方だけ直る取り残しを作らない）。
 */
function priceTargetLabel(target) {
  if (target === 'stop') {
    return '損切り';
  }
  if (target === 'take') {
    return '利確';
  }
  const m = /^entry:(\d+)$/.exec(String(target ?? ''));
  return m ? `建値 ${Number(m[1]) + 1}` : '';
}

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
    onRun = null, onRequestPick = null, onClose = null, onCancelPick = null,
  } = {}) {
    this._doc = doc;
    this._onChangeParams = typeof onChangeParams === 'function' ? onChangeParams : null;
    this._onChangeLevels = typeof onChangeLevels === 'function' ? onChangeLevels : null;
    this._onRun = typeof onRun === 'function' ? onRun : null;
    this._onRequestPick = typeof onRequestPick === 'function' ? onRequestPick : null;
    this._onClose = typeof onClose === 'function' ? onClose : null;
    this._onCancelPick = typeof onCancelPick === 'function' ? onCancelPick : null;
    this._pickingBar = null;     // アーム中に出す細いバー（パネルは畳む）
    this._reopening = false;     // open() が内部で close() する間だけ真（取消ではない）
    this._root = null;
    this._outs = new Map();      // data-ps-out キー -> 表示要素
    this._choiceEls = new Map(); // 採用 f の 3 択
    this._fields = new Map();    // data-ps-field キー -> 入力要素
    this._prices = new Map();    // 'entry:i' / 'stop' / 'take' -> 入力要素（表示）
    // 価格の**保持先**（Y-4）。DOM は表示に徹し、値はここが持つ。K の打ち直し（空文字を
    //   通過する）で欄を作り直しても、モデル側に在るので消えない。close() でも捨てない
    //   ＝右クリックで開き直したときに直前の入力が戻る。
    this._priceValues = new Map();
    this._priceBox = null;       // 価格欄のコンテナ（K の変更で作り直す）
    this._progress = null;       // MC 進捗の表示欄（open で作り close で捨てる）
    this._customBox = null;      // 重みカスタムの入力欄コンテナ（参照実装 renderCustomInputs）
    this._customWeights = [];    // 各建玉のロット比（参照実装 S.customW）
    this._exitGroups = new Map();  // 決済方式で出し分ける表示群（bracket / time）
    this._exitMode = 'bracket';    // 参照実装 :578 の初期値
    // 価格欄の刻み（ISSUE-368 スライス S-6・丸めの適用点 経路 7）。**表示の関心だけ**を持つ:
    //   ここで値を丸めない（丸めるのは domain の 1 か所）。未解決なら従来どおり step='any'。
    this._tick = null;
  }

  /**
   * 銘柄仕様（呼び値）を受け取る（共有配線が解決済みの値を配る・解決はしない）。
   * @param {{tick:number}|null|undefined} spec 解決できないときは null（step は 'any' のまま）。
   */
  setSymbolSpec(spec) {
    this._tick = spec && Number.isFinite(spec.tick) && spec.tick > 0 ? spec.tick : null;
  }

  _usable() {
    const doc = this._doc;
    return !!(doc && typeof doc.createElement === 'function' && doc.body && typeof doc.body.append === 'function');
  }

  isOpen() {
    return this._root !== null;
  }

  close() {
    // 「利用者が開いていたものを閉じた」ときだけ通知する（R-P1「モーダル側の取消で解除」）。
    //   open() は先頭で close() を呼ぶ（二重表示の防止）。これは**取消ではない**ので、
    //   無条件に通知するとアームが巻き添えで解除される（TC-SW21 が固定）。
    const wasOpen = this._root !== null && !this._reopening;
    if (this._root && this._root.parentNode) {
      this._root.parentNode.removeChild(this._root);
    }
    this._root = null;
    this._outs = new Map();
    this._choiceEls = new Map();
    this._fields = new Map();
    this._prices = new Map();
    this._priceBox = null;
    this._progress = null;
    this._customBox = null;
    this._pickingBar = null;
    this._exitGroups = new Map();
    if (wasOpen) {
      this._onClose?.();
    }
  }

  open() {
    if (!this._usable()) {
      return;   // DOM 不在（SSR・テスト最小 fake）は no-op。
    }
    this._reopening = true;
    this.close();   // 同時に 2 枚開かない（後勝ち・同型元と同じ規約）。
    this._reopening = false;
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
    // アーム中に出す細いバー（パネルの**兄弟**に置く。子にするとパネルを畳んだとき一緒に消える）。
    //   高さを詰め、チャートを極力覆わない（裁定 2026-08-20）。
    const bar = doc.createElement('div');
    bar.className = 'ps-picking-bar';
    bar.dataset.psPickingBar = '';
    const barText = doc.createElement('span');
    barText.className = 'ps-picking-text';
    const barCancel = this._button('取消', 'cancel-pick', 'ps-picking-cancel');
    barCancel.addEventListener('click', () => this._onCancelPick?.());
    bar.append(barText, barCancel);
    this._pickingBar = bar;
    this._pickingText = barText;
    root.append(bar, panel);
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
    // MC の進捗（NFR-09「MC 実行中もチャート操作が固まらない／進捗が進む」）。
    //   MC は数秒かかるため、表示が無いと「押しても何も起きない」と区別できない。
    //   欄は常設で、走っていない間は空文字（「0%」と出すと止まっているように見える）。
    const progress = this._doc.createElement('div');
    progress.className = 'ps-progress';
    progress.dataset.psOut = 'progress';
    progress.textContent = '';
    this._outs.set('progress', progress);
    this._progress = progress;
    sec.append(progress);
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
    // 重みカスタムの入力欄（参照実装 renderCustomInputs）。custom のときだけ中身を持つ。
    const custom = this._doc.createElement('div');
    custom.className = 'ps-custom-weights';
    sec.append(custom);
    this._customBox = custom;
    this._renderCustomInputs();
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
      ['losscutPrice', 'ロスカット価格'], ['losscutAssessment', 'ロスカット評価'],
      ['buildableLot', '実建可能ロット'],
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
        // 重みカスタムの本数も K に追随する（参照実装 ensureCustomLen）。
        this._renderCustomInputs();
        this._emitCustomWeights();
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

  /**
   * 重みの長さを K に合わせる（参照実装 `ensureCustomLen(K)` の写し）。
   *   伸長は `push(length+1)`＝`[1,2,…,K]` のシード、短縮は `slice(0,K)`。
   *   条件を足しも削りもしない（参照実装が正解を定義する）。
   */
  _ensureCustomLen(splits) {
    while (this._customWeights.length < splits) {
      this._customWeights.push(this._customWeights.length + 1);
    }
    if (this._customWeights.length > splits) {
      this._customWeights = this._customWeights.slice(0, splits);
    }
  }

  /**
   * 重みカスタムの入力欄を描く（参照実装 `renderCustomInputs()` の写し）。
   *   custom 以外では中身を空にする（参照実装も `box.innerHTML=''` で消す）。
   *   K は参照実装と同じく 1〜10 に丸める。
   */
  _renderCustomInputs() {
    const box = this._customBox;
    if (!box) {
      return;
    }
    box.innerHTML = '';
    if ((this._fields.get('weightPattern')?.value ?? '') !== 'custom') {
      return;
    }
    const splits = Math.max(MIN_SPLITS, Math.min(MAX_SPLITS, this._splitCount()));
    this._ensureCustomLen(splits);
    const doc = this._doc;
    const note = doc.createElement('div');
    note.className = 'ps-custom-note';
    note.textContent = `各建玉のロット比を直接入力（#1 がストップ最遠側 → #${splits} に向かう並び）：`;
    box.append(note);
    this._customWeights.forEach((weight, index) => {
      const cell = doc.createElement('label');
      cell.className = 'ps-custom-cell';
      const name = doc.createElement('span');
      name.className = 'ps-custom-label';
      name.textContent = `#${index + 1}`;
      const input = doc.createElement('input');
      input.type = 'number';
      input.dataset.psCustomWeight = String(index);
      input.step = '0.5';    // 参照実装 `step="0.5"`
      input.min = '0';       // 参照実装 `min="0"`
      input.value = String(weight);
      input.addEventListener('input', () => {
        // 参照実装 `S.customW[i]=parseFloat(e.target.value)||0`（非数は 0）。
        const raw = Number.parseFloat(input.value);
        this._customWeights[index] = Number.isFinite(raw) ? raw : 0;
        this._emitCustomWeights();
      });
      cell.append(name, input);
      box.append(cell);
    });
  }

  // いまの重み（写しを渡す＝外から配列を書き換えられない）。
  _emitCustomWeights() {
    if ((this._fields.get('weightPattern')?.value ?? '') !== 'custom') {
      return;
    }
    this._onChangeParams?.({ customWeights: [...this._customWeights] });
  }

  // 価格欄の作り直し（K 本の建値＋損切り＋利確）。既存の入力値は同じ target 名で引き継ぐ。
  _renderPriceRows() {
    const box = this._priceBox;
    if (!box) {
      return;
    }
    // 捨てる前に、いま画面に出ている値をモデルへ取り込む（欄の作り直しで編集を失わない）。
    //   モデルは K を跨いで残るので、K の打ち直し（空文字の通過）で建値が消えない（Y-4）。
    for (const [target, el] of this._prices) {
      this._priceValues.set(target, el.value);
    }
    box.innerHTML = '';
    this._prices = new Map();
    const splits = this._splitCount();
    const targets = [];
    for (let i = 0; i < splits; i += 1) {
      targets.push([`entry:${i}`, priceTargetLabel(`entry:${i}`)]);
    }
    targets.push(['stop', priceTargetLabel('stop')], ['take', priceTargetLabel('take')]);
    for (const [target, label] of targets) {
      box.append(this._priceRow(target, label, this._priceValues.get(target) ?? ''));
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
    // 刻みが分かっているなら矢印キー・スピナーが刻みの外へ出ない（経路 7）。
    input.step = this._tick === null ? 'any' : String(this._tick);
    input.value = value;
    input.addEventListener('input', () => {
      this._priceValues.set(target, input.value);   // 保持先はモデル（DOM は表示）。
      this._emitLevels();
    });
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
    this._priceValues.set(target, input.value);
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
      const text = (value === null || value === undefined) ? '' : String(value);
      this._priceValues.set(target, text);
      const input = this._prices.get(target);
      if (input) {
        input.value = text;
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
    // **option を全て足してから value を代入する**（順序が仕様上の意味を持つ・実 UI 実測 2026-08-20）。
    //   HTML 仕様では、一致する option が存在しない value 代入は選択に反映されず捨てられる。
    //   その後 option を足すと非 multiple の select は先頭 option が選択状態になるため、
    //   逆順に書くと `def` が無視され options[0] が既定になる（実 UI で 重み=equal・
    //   建て制約=margin となり、参照実装 :578 の linear / lc と食い違っていた）。
    //   検定の最小 DOM は value を素のプロパティとして持つため順序差を再現できない
    //   ＝この誤りは実ブラウザでしか出ない。TC-PD34 は仕様どおりの select fake で固定する。
    for (const [value, text] of options) {
      const opt = doc.createElement('option');
      opt.value = value;
      opt.textContent = text;
      select.append(opt);
    }
    select.value = selectDefault({ options, def });
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
      if (key === 'weightPattern') {
        // 入力欄を出し入れし、**パターンと重みを同時に**渡す。別々に渡すと
        //   「custom なのに custom_weights が無い」瞬間ができ、権威が例外で止まる（🔴-3）。
        this._renderCustomInputs();
        this._onChangeParams?.({
          weightPattern: select.value,
          customWeights: [...this._customWeights],
        });
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
   * ピッカーのアーム中だけモーダルを**非モーダル化**する（実 UI 実測 2026-08-20 の是正）。
   *
   * 背景（実測）: backdrop は `position:fixed; inset:0`（ビューポート全面）であり、アーム中も
   * そのままだと `elementFromPoint(チャート中央)` がモーダルを返す＝チャートをホバーも
   * クリックもできず、R-P1（クロスヘア追従 → クリック確定）が実 UI で成立しない。
   *
   * ここで持つのは**状態クラスの付け外しとバーの文言だけ**で、畳み方は CSS が決める
   * （`.is-picking` でパネルを隠し、細いバーだけを出す）。
   * DOM を作り直さないため、アーム解除・確定後も全入力値がそのまま残る（必須要件）。
   *
   * 裁定（2026-08-20・実測スクショ根拠）: パネルを細くするのではなく**畳む**。
   * 320px へ狭めた版では入力欄の値が切れ（「38」→「3」）、ラベルが 3 行に折り返して読めなかった。
   *
   * @param {boolean} on アーム中なら true。
   * @param {?string} target 指定中の欄（'entry:i' / 'stop' / 'take'）。バーの文言に使う。
   */
  setPicking(on, target = null) {
    const root = this._root;
    if (!root || !root.classList || typeof root.classList.toggle !== 'function') {
      return;   // 閉じている・DOM 非対応は no-op。
    }
    root.classList.toggle('is-picking', !!on);
    if (this._pickingText) {
      // 対象名は価格欄のラベルと同じ表から引く（片方だけ直る取り残しを作らない）。
      const label = priceTargetLabel(target);
      this._pickingText.textContent = on && label ? `${label}をチャートで指定中…` : '';
    }
  }

  /**
   * MC の進捗を表示する（NFR-09「進捗が進む」）。
   *
   * 表示するだけで、判定も計算も持たない（比 → % の書式は Presenter の責務＝§3 UC-04）。
   * `null`（完了・失敗）で消す。古い進捗を残すと「まだ走っている」ように見える。
   *
   * @param {number|null} ratio 0..1 の進捗比。null で消去。
   */
  setProgress(ratio) {
    const el = this._progress;
    if (!this._root || !el) {
      return;   // 閉じているときは no-op（モーダルを閉じてから完了しても例外にしない）。
    }
    el.textContent = Number.isFinite(ratio) ? `計算中 ${Math.round(ratio * 100)}%` : '';
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
    // 整形の文脈（参照実装が書式の切り替えに使っている状態）。ロット書式は S.lotmode、
    //   ロスカット価格の分岐は r.immediateLC に従う（どちらも参照実装の定義どおり）。
    const context = { plan, lotMode: this._fields.get('lotMode')?.value ?? 'int' };
    for (const [key, value] of Object.entries(values)) {
      const el = this._outs.get(key);
      if (el) {
        const format = OUT_FORMAT[key];
        el.textContent = Number.isFinite(value)
          ? (format ? format(value, context) : String(value))
          : '—';
      }
    }
    this._renderWarnings(vm, plan);
    if (vm.fractionChoice) {
      this._selectChoice(vm.fractionChoice);
    }
  }

  // 警告は **VM の判定をそのまま並べる**（どれが警告かを本モジュールで決め直さない）。
  //   出すのは**参照実装の文言**で、内部識別子は画面に出さない（Y-5）。
  //   ロスカットの前後関係（immediate_lc / lc_before_stop）は警告ではなく
  //   「ロスカット評価」の 3 分岐が担う（参照実装の構成・B-2）。
  _renderWarnings(vm, plan) {
    const assessment = this._outs.get('losscutAssessment');
    if (assessment) {
      assessment.textContent = losscutAssessmentText(plan);
    }
    const el = this._outs.get('warnings');
    if (!el) {
      return;
    }
    const context = {
      long: (vm.levelLines?.direction ?? 'long') === 'long',
      capBasis: this._fields.get('capBasis')?.value ?? 'lc',
    };
    const flags = ['stop_invalid', 'take_invalid', 'round_zeroed', 'margin_binds'];
    const hits = [...new Set([
      ...(vm.violations ?? []),
      ...flags.filter((f) => plan[f] === true),
    ])].filter((f) => flags.includes(f));
    // 文言が定義されていないフラグは識別子のまま出す（黙って消さない＝取り残しが見える）。
    const text = hits.map((f) => (WARNING_TEXT[f] ? WARNING_TEXT[f](context) : f)).join(' / ');
    el.textContent = text.length > 0 ? text : '—';
  }

  _selectChoice(value) {
    for (const [key, el] of this._choiceEls) {
      el.classList.toggle('is-active', key === value);
    }
  }
}
