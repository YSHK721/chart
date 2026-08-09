// replay_ui の wiring 検定で共有するテストダブル（ISSUE-315）。
//
// 5 本の replay_*_wiring 系スイートが同一の fake（DOM 要素・document・chart・controller）を
// 手書きで複製していた。ファイル内のコメントにも「replay_mp_wiring.test.js と同型」と
// 書かれており、複製であることは認識されていた。同型なら 1 つで足りる。
//
// 各スイートが持ち続けるもの: そのスイート固有の spy（何を観測したいか）と、
// fakeDoc に載せる要素の初期値（検証したいモードなど）。

/** 最小の DOM 要素ダブル（auto-vivify）。onclick/addEventListener/classList/value/options を提供する。 */
export function fakeEl(extra = {}) {
  return {
    _l: {}, value: '', min: 0, max: 0, textContent: '', title: '', hidden: false, disabled: false,
    style: {}, dataset: {}, options: [], innerHTML: '',
    classList: { toggle() {}, add() {}, remove() {} },
    appendChild() {}, removeChild() {},
    addEventListener(ev, fn) { (this._l[ev] ||= []).push(fn); },
    set onclick(fn) { this._onclick = fn; }, get onclick() { return this._onclick; },
    set oninput(fn) { this._oninput = fn; }, get oninput() { return this._oninput; },
    ...extra,
  };
}

/** document ダブル。`mode` は #rp-mode の初期値（スイートごとに検証したい再生モード）。 */
export function fakeDoc(mode = 'real_ticks') {
  const els = {
    'rp-speed': fakeEl({ value: '1' }),
    'rp-mode': fakeEl({ value: mode }),
    'rp-prev': fakeEl(),
  };
  return {
    getElementById: (id) => (els[id] || (els[id] = fakeEl())),
    querySelectorAll: () => [],
    createElement: () => fakeEl(),
    addEventListener() {},
    _els: els,
  };
}

/** lightweight-charts の chart ダブル（timeScale/panes/chartElement のみ）。 */
export function fakeChart() {
  const ts = { fitContent() {}, setVisibleLogicalRange() {}, getVisibleLogicalRange() { return null; } };
  return { timeScale: () => ts, panes: () => [], chartElement: () => null };
}

/** IndicatorController ダブル（再計算フックのみ）。 */
export function fakeController() {
  return {
    _timeframe: '1D', _recentBars: 0,
    setUntilTime() {}, isRecomputing() { return false; },
    async recomputeAllApplied({ preRender } = {}) { if (preRender) preRender(); },
    async recomputeFormingLatest() {},
  };
}
