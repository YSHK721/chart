// PaneLegendView（adapter/front/pane_legend_view.js）— ペインごとの凡例（ISSUE-276）。
//
// 解決する問題（実測 2026-08-06・指標 11 件・チャート高 858px）:
//   左上に 4 つの表示系統が独立に積み上がり、チャート高の 67%（439×571px）を覆っていた。
//     1. 現在値（大きい数字）        2. クロスヘア読み取り欄（OHLC ＋ overlay 各系列の値・229px）
//     3. 凡例（インスタンス行・295px）4. ペイン左上のウォーターマーク（系列値）
//   同じ指標の「名前」と「値」が 2〜3 系統に重複して出るため、指標を増やすほど各系統が伸び、
//   互いに重なって判読不能になっていた（凡例 DOM がウォーターマークの上に載る）。
//
// 抜本策: 系統を **ペインごとの凡例 1 つ**へ統合する。
//   - 行は「その指標が描かれているペイン」の左上に置く → 縦の積み上がりがペインへ分散し、
//     価格ペインには overlay 指標だけが残る（オシレーターは自分のペインへ移る）。
//   - 1 行 = 指標名 ＋ 系列値 ＋ 操作（目/歯車/×）→ 名前と値の重複表示が消える。
//   - 値はクロスヘア位置、クロスヘアが無ければ最新値（TradingView と同じ規約）。
//   - 既定は折りたたみ（`∿ N` のチップ）。**クリックで開閉**する＝「必要なときに表示」。
//     ホバー展開は採らない: 値の更新（毎クロスヘア）で DOM を作り直すため、ホバー由来の開閉と
//     クリック由来の開閉が同じトグルを奪い合い、開いた直後に畳まれる（実測 2026-08-06）。
//     開閉の主語をクリックだけにすると、再描画が何回起きても状態が一意に決まる。
//
// 責務分割:
//   - 幾何（どのペインがどこにあるか）と値は ChartRenderer が DTO で供給する（upstream 隔離を維持）。
//   - 行のラベルと操作（目/歯車/×）は IndicatorController が供給する。
//   - 本 View は 2 つの入力を合成して DOM を組むだけ。lightweight-charts には一切触れない。
//
// ホスト要素の所有（2026-08-06 是正）: 描画先の器は **本 View が所有し自分で生成する**
//   （overlay_host.ensureOverlayHost）。index.html には一切書かない。旧構成は配信 3 ページの
//   HTML へ `<div id="pane-legends">` を手書き複製する前提で、実際に unified_ui（:8000＝実配信）
//   の取り残しにより凡例が全滅していた。ページに要求するのは版面 .chart-wrap ただ 1 つ。
//
// DOM 非依存: document は注入。DTO 未着でも安全（no-op）。ただし DOM がある環境で版面が
//   無い場合は例外（フェイルクローズ）＝契約違反を無症状にしない。

import { fmtValue } from './format.js';
import { ensureOverlayHost } from './overlay_host.js';

// 折りたたみチップの見出し記号（ツールバーの「インジケーター」と同じ字形＝同一概念に別記号を作らない）。
const CHIP_ICON = '∿';

// 本 View が所有するホスト要素のクラス名（＝所有者名）。CSS もこのクラスで当てる。
const HOST_CLASS = 'pane-legends';

// 並べ替え協働子が未注入のときの既定（Null Object）。「並べ替えが無い」ことを **契約を満たす
//   完全な実装**で表す。呼び出し側で `typeof x.isDragging === 'function'` と分岐して表すと、
//   3 か所に散った分岐が協働子の契約を暗黙に再定義してしまい、1 メソッドだけ欠けた部分実装が
//   「並べ替えが効かない」という無症状の不具合として通る（LSP: 置換可能でない実装を受け入れる /
//   ISP: 必要なメソッド集合がどこにも明示されない）。既定を Null Object に固定することで、
//   本 View は常に 3 メソッドを持つ相手だけを相手にし、部分実装は即座に落ちる（フェイルクローズ）。
const NO_REORDER = Object.freeze({
  isDragging: () => false,
  sync: () => {},
  consumeClickSuppression: (_handle) => false,
});

export class PaneLegendView {
  /**
   * @param {object} deps
   * @param {object} deps.document       DOM 実装（注入）。
   * @param {object} [deps.anchor]       版面要素の直接注入（既定は document から .chart-wrap を引く）。
   * @param {boolean} [deps.collapsed]   既定の折りたたみ状態（既定 false＝開いた状態で表示する）。
   * @param {object} [deps.reorder]      ペイン並べ替えのドラッグ協働子（PaneReorderDrag）。
   *                                     契約は { isDragging(), sync(root, groups), consumeClickSuppression(handle) }。
   *                                     未注入なら NO_REORDER（並べ替え無し・本 View は操作を一切知らない）。
   */
  constructor({ document, anchor = null, collapsed = false, overlayId = 'chart-overlay-tl', reorder = null } = {}) {
    this._document = document ?? null;
    this._anchor = anchor ?? null;
    // 自分で生成したホスト要素の保持（再描画のたびに引き直さない）。
    this._host = null;
    // 価格ペイン（0 番）の凡例は、同じ左上に居る現在値・読み取り欄の**下**へずらす。
    //   両者は別の器（#chart-overlay-tl）で、高さは表示中の行数で変わるため定数では避けられない。
    this._overlayId = overlayId;
    // ペイン単位の展開状態。ユーザーが開いたペインは、値の更新（毎クロスヘア）で畳まない。
    //   鍵は **paneKey（位置に依らないペインの同一性）**（ISSUE-341）。並べ替えを入れた結果
    //   paneIndex は「今どこに居るか」しか表さなくなり、位置を鍵にすると畳んだ状態が動かした
    //   ペインではなく「その位置」に residue として残る（実測 2026-08-09: RSI を畳んで下へ
    //   動かすと、畳んだ表示は元の位置の別指標に残り RSI 側が開いた）。
    this._expanded = new Set();
    // 依頼者指示（2026-08-08）: 基本はオープン。畳むのは利用者がチップを押したときだけ。
    this._defaultCollapsed = collapsed === true;
    // controller 由来の行メタ（instanceId -> { label, visible, onEye, onGear, onClose }）。
    this._rowMeta = new Map();
    // renderer 由来の幾何＋値（{ groups: [{ paneIndex, top, height, movable, rows }] }）。
    this._model = null;
    // ペイン並べ替え（ドラッグ&ドロップ）。操作の意味づけは協働子が持ち、本 View は
    //   「掴み手はこの要素」「幾何はこれ」を渡すだけ（描画と操作の責務を混ぜない）。
    this._reorder = reorder ?? NO_REORDER;
  }

  // 描画先（本 View 所有のホスト要素）。無ければ版面 .chart-wrap の直下に生成する。
  //   版面から切り離された（モード切替でサブツリーごと差し替わった）場合は作り直す。
  _root() {
    if (this._host && this._host.isConnected !== false) {
      return this._host;
    }
    this._host = ensureOverlayHost(this._document, { className: HOST_CLASS, anchor: this._anchor });
    return this._host;
  }

  // controller から: 適用中インスタンスのラベルと操作を差し替える（適用/削除/可視切替のたび）。
  setInstances(rows) {
    this._rowMeta = new Map();
    for (const r of rows ?? []) {
      if (r && r.instanceId) {
        this._rowMeta.set(r.instanceId, r);
      }
    }
    this.render();
  }

  // renderer から: ペイン幾何と系列値を差し替える（クロスヘア移動・再描画のたび）。
  update(model) {
    this._model = model ?? null;
    this.render();
  }

  // 展開状態の切替（チップのクリック／行の閉じるボタン）。鍵は paneKey＝ペインの同一性。
  toggle(paneKey) {
    if (this._expanded.has(paneKey)) {
      this._expanded.delete(paneKey);
    } else {
      this._expanded.add(paneKey);
    }
    this.render();
  }

  _isExpanded(paneKey) {
    return this._defaultCollapsed ? this._expanded.has(paneKey) : !this._expanded.has(paneKey);
  }

  // 状態の鍵。DTO が paneKey を運んでいればそれを使う。運んでいない（DTO 未着・幾何なしの
  //   縮退＝renderer が panes() を持たない Fake/SSR）場合は従来どおり paneIndex を文字列化した
  //   ものへ退避する。並べ替えが起きない環境では位置と同一性が一致するため、これで従来挙動のまま。
  _keyOf(group) {
    const key = group && group.paneKey;
    return (typeof key === 'string' && key.length > 0) ? key : String(group ? group.paneIndex : 0);
  }

  // 価格ペインだけ、左上オーバーレイ（現在値＋読み取り欄）の高さぶん下げる。他ペインは 0。
  _topOffsetFor(paneIndex) {
    if (paneIndex !== 0) {
      return 0;
    }
    const doc = this._document;
    const el = (doc && typeof doc.getElementById === 'function') ? doc.getElementById(this._overlayId) : null;
    const h = el && typeof el.getBoundingClientRect === 'function' ? el.getBoundingClientRect().height : 0;
    return Number.isFinite(h) && h > 0 ? h + 14 : 0;   // 14px はオーバーレイ上端の余白ぶん。
  }

  // 行の在席権威は **controller の適用一覧（_rowMeta）**。renderer のモデルは「その行をどこへ
  //   置くか（paneIndex・ペイン幾何）と何を表示するか（系列値）」だけを供給する。
  //   renderer のスロット集合を在席権威にすると、系列を持たない**アクター駆動型指標**
  //   （market_profile / tickvol_bands＝自前プリミティブで描く）が凡例に現れず、目/歯車/× を
  //   失って適用後に操作不能になる（旧 #legend 撤去後は代替手段が無い）。実測 2026-08-06:
  //   ライブ診断で market_profile は「スロットなし（未描画）」＝モデルに出ない。
  render() {
    const doc = this._document;
    // ドラッグ中は作り直さない。凡例はクロスヘアが動くたびに DOM を捨てて作り直すため、
    //   ペインを掴んで動かしている最中に再構築すると掴んでいる要素そのものが消える。
    //   並べ替え確定後は movePane が凡例 DTO を再発行する＝通常経路で描き直される。
    if (this._reorder.isDragging()) {
      return;
    }
    const root = this._root();
    if (!doc || !root) {
      return;
    }
    root.innerHTML = '';
    const { placement, geometry } = this._indexModel();
    // paneIndex -> 行（適用順を保つ）。幾何が無い指標は価格ペイン（0）＝自前プリミティブは
    //   価格ペインに描かれるため、そこに行を置くのが描画と一致する。
    const byPane = new Map();
    for (const instanceId of this._rowMeta.keys()) {
      const place = placement.get(instanceId);
      const paneIndex = place ? place.paneIndex : 0;
      if (!byPane.has(paneIndex)) {
        byPane.set(paneIndex, []);
      }
      byPane.get(paneIndex).push({ instanceId, values: place ? place.values : [] });
    }
    const placed = [];
    for (const paneIndex of [...byPane.keys()].sort((a, b) => a - b)) {
      const geom = geometry.get(paneIndex) ?? { paneIndex, top: 0, height: 0, movable: false };
      const { box, handle } = this._buildGroup(doc, geom, byPane.get(paneIndex));
      root.appendChild(box);
      placed.push({ ...geom, box, handle });
    }
    // 並べ替え協働子へ、この描画で作った掴み手と幾何を渡す（要素は毎回作り直されるため毎回渡す）。
    this._reorder.sync(root, placed);
  }

  // renderer モデルを instanceId / paneIndex で引ける形へ落とす（描画の都合は View が持つ）。
  _indexModel() {
    const placement = new Map();
    const geometry = new Map();
    for (const g of (this._model && this._model.groups) || []) {
      geometry.set(g.paneIndex, {
        paneIndex: g.paneIndex, top: g.top ?? 0, height: g.height ?? 0,
        // 位置に依らないペインの同一性。折りたたみ状態の鍵に使う（位置の情報とは別に運ぶ）。
        paneKey: g.paneKey ?? null,
        // 掴めるかは renderer の判定（価格ペインは対象外・指標ペインが 1 つなら不可）をそのまま運ぶ。
        movable: g.movable === true,
      });
      for (const r of g.rows ?? []) {
        placement.set(r.instanceId, { paneIndex: g.paneIndex, values: r.values ?? [] });
      }
    }
    return { placement, geometry };
  }

  _buildGroup(doc, group, rows) {
    const box = doc.createElement('div');
    box.className = 'pane-legend';
    box.dataset.paneIndex = String(group.paneIndex);
    // ペインの上端へ絶対配置する。高さは内容なり（ペイン高で切らない＝はみ出しても読める）。
    box.style.top = `${Math.max(0, Math.round(group.top + this._topOffsetFor(group.paneIndex)))}px`;

    const paneKey = this._keyOf(group);
    const expanded = this._isExpanded(paneKey);
    const chip = doc.createElement('button');
    chip.type = 'button';
    // チップはこのペインの掴み手も兼ねる（畳んでいても必ず在る唯一の要素＝並べ替えが常にできる）。
    chip.className = 'pane-legend-chip' + (expanded ? ' is-open' : '') + (group.movable ? ' is-movable' : '');
    chip.title = (expanded ? '指標を畳む' : '指標を表示する')
      + (group.movable ? '（上下にドラッグでペイン移動）' : '');
    chip.textContent = `${CHIP_ICON} ${rows.length}`;
    chip.addEventListener('click', () => {
      // 掴んで動かした直後の click は開閉ではない（同じ要素が掴み手のため）。判定は**この要素**が
      //   掴み手だったかどうかで行う（真偽値だと、click が飛ばずに残った印を無関係なチップが食う）。
      if (this._reorder.consumeClickSuppression(chip)) {
        return;
      }
      this.toggle(paneKey);
    });
    box.appendChild(chip);

    if (!expanded) {
      return { box, handle: chip };
    }
    const list = doc.createElement('div');
    list.className = 'pane-legend-rows';
    for (const r of rows) {
      list.appendChild(this._buildRow(doc, r));
    }
    box.appendChild(list);
    return { box, handle: chip };
  }

  _buildRow(doc, row) {
    const meta = this._rowMeta.get(row.instanceId);
    const el = doc.createElement('div');
    el.className = 'pane-legend-row' + (meta.visible === false ? ' is-hidden' : '');

    const name = doc.createElement('span');
    name.className = 'pane-legend-name';
    name.textContent = meta.label;
    el.appendChild(name);

    // 依頼者指示（2026-08-08）: 並びは「指標名 → 設定（操作）→ 値」。値は可変長で伸び縮みするため
    //   最後に置き、操作（目/歯車/×）の位置が値の桁数で動かないようにする。
    const vals = doc.createElement('span');
    vals.className = 'pane-legend-values';
    for (const v of row.values ?? []) {
      const text = fmtValue(v.value);
      if (!text) {
        continue;   // 値未着（材料不足）は欄を作らない＝空欄が並ばない。
      }
      const chip = doc.createElement('span');
      chip.className = 'pane-legend-value';
      chip.title = v.name;
      if (v.color) {
        chip.style.color = v.color;
      }
      chip.textContent = text;
      vals.appendChild(chip);
    }
    // 表示/非表示トグル（依頼者指示 2026-08-08: 絵文字の「目」は写実的すぎるため図形へ）。
    //   ●＝表示中／○＝非表示。他の操作（⚙ / ✕）と同じ単色の記号系で揃え、状態は
    //   「塗り／抜き」だけで表す（色は CSS が持つ＝ここで色を決めない）。
    const eye = doc.createElement('button');
    eye.type = 'button';
    eye.className = 'pane-legend-visibility';
    eye.title = meta.visible ? '非表示にする' : '表示する';
    eye.textContent = meta.visible ? '●' : '○';
    eye.addEventListener('click', () => meta.onEye && meta.onEye());

    const gear = doc.createElement('button');
    gear.type = 'button';
    gear.className = 'pane-legend-gear';
    gear.title = '設定';
    gear.textContent = '⚙';
    gear.addEventListener('click', () => meta.onGear && meta.onGear());

    const close = doc.createElement('button');
    close.type = 'button';
    close.className = 'pane-legend-remove';
    close.title = '削除';
    close.textContent = '✕';
    close.addEventListener('click', () => meta.onClose && meta.onClose());

    el.append(eye, gear, close, vals);
    return el;
  }
}
