// dashboard 表示層の検定で共有する DOM のテストダブル。
//
// jsdom / happy-dom は導入しない（ライブラリ追加は承認事項・既存 4 スイートも node:test のみで
//   動いている）。ここで固定したいのは「View がどの DOM を作り、どう畳むか」であって実
//   ブラウザの描画結果ではない（実 UI の実測は e2e の責務）。
//
// 参照した同型実装: simulator/sim_ui/web/tests/_fakes.js（同じ理由で同じ形にしてある）。

/** 最小の DOM 要素ダブル。 */
export function fakeEl(tag = 'div') {
  // `textContent` は実 DOM と同じ意味にする（**単なる文字列プロパティにしない**）。
  //   get: 子孫のテキストを連結して返す
  //   set: **子をすべて捨てて**そのテキストだけにする
  // ここを手抜きすると、「子要素を足した後に textContent を代入する」コードが検定では
  //   通り、実ブラウザでだけ子が消える（背景の 3 分割が実 UI でだけ出ない）という、
  //   検定が原理的に見つけられない欠陥を作る。
  let ownText = '';
  const el = {
    tagName: String(tag).toUpperCase(),
    id: '',
    className: '',
    title: '',
    // 実 DOM の CSSStyleDeclaration は**未設定のプロパティに空文字**を返す（undefined ではない）。
    //   素の {} にすると「色を置かない」ことの検定が undefined と '' の食い違いで落ち、
    //   逆に落ちない書き方をすると実ブラウザとの差が検定から見えなくなる。
    style: new Proxy({}, {
      get: (target, prop) => (prop in target ? target[prop] : (typeof prop === 'string' ? '' : undefined)),
    }),
    dataset: {},
    children: [],
    parentNode: null,
    _listeners: {},
    classList: {
      add(c) { el._classes().add(c); el.className = [...el._classes()].join(' '); },
      remove(c) { el._classes().delete(c); el.className = [...el._classes()].join(' '); },
      contains(c) { return el._classes().has(c); },
    },
    _classes() {
      if (!el.__classSet) el.__classSet = new Set(String(el.className || '').split(/\s+/).filter(Boolean));
      return el.__classSet;
    },
    appendChild(child) { child.parentNode = el; el.children.push(child); return child; },
    removeChild(child) {
      const i = el.children.indexOf(child);
      if (i >= 0) { el.children.splice(i, 1); child.parentNode = null; }
      return child;
    },
    remove() { if (el.parentNode) el.parentNode.removeChild(el); },
    addEventListener(ev, fn) { (el._listeners[ev] ||= []).push(fn); },
    querySelector(sel) { return querySelectorIn(el, sel); },
    querySelectorAll(sel) { return querySelectorAllIn(el, sel); },
  };
  Object.defineProperty(el, 'textContent', {
    enumerable: true,
    get() {
      return ownText + el.children.map((c) => c.textContent).join('');
    },
    set(value) {
      for (const child of el.children) child.parentNode = null;
      el.children.length = 0;
      ownText = value === null || value === undefined ? '' : String(value);
    },
  });
  return el;
}

/** `.class` / `#id` / `tag` だけを解する最小の選択子照合。 */
function matches(el, selector) {
  const sel = String(selector).trim();
  if (sel.startsWith('.')) return el.classList.contains(sel.slice(1));
  if (sel.startsWith('#')) return el.id === sel.slice(1);
  return el.tagName === sel.toUpperCase();
}

function descendants(root) {
  const out = [];
  const walk = (el) => { for (const c of el.children || []) { out.push(c); walk(c); } };
  walk(root);
  return out;
}

function querySelectorIn(root, selector) {
  return descendants(root).find((el) => matches(el, selector)) || null;
}

function querySelectorAllIn(root, selector) {
  return descendants(root).filter((el) => matches(el, selector));
}

/** 最小の document ダブル。 */
export function fakeDoc() {
  const head = fakeEl('head');
  const body = fakeEl('body');
  return {
    head,
    body,
    createElement: (tag) => fakeEl(tag),
    querySelector: (sel) => querySelectorIn(body, sel) || querySelectorIn(head, sel),
    querySelectorAll: (sel) => [...querySelectorAllIn(body, sel), ...querySelectorAllIn(head, sel)],
  };
}

/** 部分木の全要素（走査ヘルパ）。 */
export function flatten(root) {
  return root ? [root, ...descendants(root)] : [];
}

/** 部分木のテキスト（版面の中身を 1 本の文字列として見る）。 */
export function textOf(root) {
  return root ? root.textContent : '';
}

/** arch-spec §9 の応答をそのまま組む（検定ごとに形を発明しないための素材）。 */
export function sheetResponse(overrides = {}) {
  return {
    ok: true,
    current_price: 65756.0,
    current_index: 2,
    rows: [],
    cells: [],
    degradations: [],
    ...overrides,
  };
}

/** 第 1 表の 1 行（LadderRow の直列化形・dashboard_ui/usecase/sheet_models.py と同名）。 */
export function ladderRow(overrides = {}) {
  return {
    price: 65803.4,
    timeframe: '5m',
    label: 'cvfe 外側上 2σ',
    distance: 47.4,
    gap_to_previous: 16.2,
    horizon_marks: [],
    reach: { reached: false, since_time: null, truncated: false },
    horizon_p: { short: 0.058, medium: 0.077, long: 0.128 },
    // なめらか再生の宣言（依頼者指示 2026-08-31: 距離・価格・差もライブチャート粒度）。
    instance_key: ['cvfe', 'default', '{}', '5m'],
    series: 'cvfe_u2',
    ...overrides,
  };
}

/** 第 2 表の 1 セル（OscCell の直列化形）。 */
export function oscCell(overrides = {}) {
  return {
    indicator_id: 'ma_marod',
    timeframe: '1m',
    value: 0.8,
    p: 0.31,
    tail_unscaled: false,
    reach: null,
    unavailable_reason: null,
    level_prices: { q_high: null, q_low: null },   // 各側は {price, level} | null
    // なめらか再生の宣言（依頼者指示 2026-08-31）。instance_key の第 3 要素（params_key）は
    //   JSON として復元できる（サーバの json.dumps と同じ契約）。
    instance_key: ['ma_marod', 'default', '{"length": 50}', '1m'],
    value_series: 'ma_marod',
    ...overrides,
  };
}
