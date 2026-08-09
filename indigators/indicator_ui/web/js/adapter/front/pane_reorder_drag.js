// PaneReorderDrag（adapter/front/pane_reorder_drag.js）— 指標ペインの上下並べ替え操作
//   （ドラッグ&ドロップ・ユーザー指示 2026-08-09）。
//
// 解決する問題:
//   ペインの並びは「指標を適用した順」でしか決まらず、後から変える手段が無かった。並べ替えるには
//   指標を削除して適用し直すしかなく、設定（パラメータ・スタイル・価格軸の手動レンジ）を失う。
//
// 責務（SRP）: **ポインタ操作を「どのペインを何番へ動かすか」へ翻訳することだけ**を行う。
//   - lightweight-charts には一切触れない（並べ替えの実行は注入された movePane ポート＝
//     ChartRenderer.movePane が担う。upstream 隔離は不変）。
//   - 凡例の DOM は作らない（作るのは PaneLegendView）。本クラスは渡された要素へ操作を結び、
//     ドラッグ中の見た目（掴んだ箱の追従・落下位置の線）だけを触る。
//   - どのペインが動かせるかを自分で決めない（movable は renderer の DTO が運ぶ単一情報源）。
//
// なぜドラッグ中は凡例の再描画を止めるのか（isDragging）:
//   凡例はクロスヘアが動くたびに DOM を作り直す。ドラッグ中はポインタがチャート上を動くため、
//   掴んでいる要素が毎フレーム作り直されて掴みが外れる。ドラッグ中だけ再構築を止め、
//   ドロップ時に movePane → 凡例 DTO 再発行という通常経路で描き直す（描画経路を二重化しない）。
//
// 停止は「終了が保証された寿命」に紐づける（ポインタ捕捉・2026-08-09 是正）:
//   再描画の停止を解除する条件が「pointerup が届くこと」だけだと、届かない経路が 1 つでもあれば
//   凡例が恒久的に固まる（値の更新も指標の追加削除の反映も全停止＝フェイルオープン）。
//   そこで掴んだ時点で掴み手へ **ポインタを捕捉**する（setPointerCapture）。捕捉した要素には
//   以後のポインタ事象が必ず配送され、捕捉が失われるときは lostpointercapture が必ず届く。
//   停止の寿命＝捕捉の寿命にすることで、終了イベントの取りこぼしを構造的に無くす。
//   捕捉を提供しない環境（Fake・古い実装）は no-op へ縮退し、従来どおり document 購読で追う。
//
// DOM 非依存: document は注入。要素・document が無い環境（SSR・純ロジックテスト）は no-op。

// クリックとドラッグを分ける移動量（px）。これ未満はクリック（チップの開閉）として扱う。
const DRAG_THRESHOLD_PX = 4;

// ドラッグ中の見た目に付けるクラス（CSS の契約名）。
const DRAGGING_CLASS = 'is-dragging';
const INDICATOR_CLASS = 'pane-drop-indicator';

// className 文字列だけで付け外しする（classList を持たない注入要素でも同じ 1 経路で動く）。
function addClass(el, name) {
  const cur = String(el.className ?? '');
  if (!cur.split(' ').includes(name)) {
    el.className = cur ? `${cur} ${name}` : name;
  }
}

function removeClass(el, name) {
  const cur = String(el.className ?? '');
  el.className = cur.split(' ').filter((c) => c && c !== name).join(' ');
}

export class PaneReorderDrag {
  /**
   * @param {object} deps
   * @param {object} deps.document      DOM 実装（注入）。落下位置の線の生成にだけ使う。
   * @param {Function} deps.movePane    (fromIndex, toIndex) => boolean の並べ替えポート。
   */
  constructor({ document, movePane } = {}) {
    this._document = document ?? null;
    this._movePane = typeof movePane === 'function' ? movePane : () => false;
    // 直近の描画で受け取った器と各ペインの幾何（落下先の判定に使う）。
    this._root = null;
    this._groups = [];
    // ドラッグ状態。null＝非ドラッグ。{ group, startY, moved, target }
    this._drag = null;
    this._indicator = null;
    // 捕捉中の掴み手。null＝捕捉なし。{ handle, pointerId }
    this._captured = null;
    // ドラッグ直後に発火するチップの click を 1 回だけ握りつぶすための印。
    this._suppressClick = false;
    this._onPointerMove = (ev) => this._handleMove(ev);
    this._onPointerUp = (ev) => this._handleUp(ev);
    this._onLostCapture = (ev) => this._handleUp(ev);
  }

  /**
   * 凡例が描き直されるたびに、その時点の器・幾何・掴み手を受け取る（PaneLegendView が呼ぶ）。
   *
   * @param {object} root      凡例の器（.pane-legends）。座標の原点に使う。
   * @param {Array} groups     [{ paneIndex, movable, top, height, box, handle }]。
   *                           box=ペインの凡例グループ要素／handle=掴み手（チップ）。
   */
  sync(root, groups) {
    this._root = root ?? null;
    this._groups = (groups ?? []).filter((g) => g && Number.isFinite(g.paneIndex));
    for (const g of this._groups) {
      if (!g.movable || !g.handle || typeof g.handle.addEventListener !== 'function') {
        continue;
      }
      // 要素は描画のたびに作り直されるため、購読も毎回この新しい要素へ結ぶ（残留しない）。
      g.handle.addEventListener('pointerdown', (ev) => this._handleDown(ev, g));
    }
  }

  // ドラッグ中か（PaneLegendView が再描画を止める判断に使う）。
  isDragging() {
    return this._drag !== null;
  }

  // 直前のドラッグ由来の click を握りつぶすべきか（1 回だけ true を返す）。
  //   掴み手はチップ（開閉ボタン）と同じ要素なので、動かした後の click で畳まれないようにする。
  consumeClickSuppression() {
    const suppress = this._suppressClick;
    this._suppressClick = false;
    return suppress;
  }

  _handleDown(ev, group) {
    if (this._drag) {
      return;
    }
    if (ev && ev.button != null && ev.button !== 0) {
      return;   // 左ボタン以外（右クリック・中クリック）は掴まない。
    }
    // 新しい操作の始まりでは前回の握りつぶし印を捨てる（click が飛ばずに残った場合の持ち越し防止）。
    this._suppressClick = false;
    this._drag = { group, startY: this._clientY(ev), moved: false, target: group.paneIndex };
    this._capturePointer(group.handle, ev);
    this._bindWindow(true);
    if (ev && typeof ev.preventDefault === 'function') {
      ev.preventDefault();   // テキスト選択・チャート側のドラッグ開始を巻き込まない。
    }
  }

  _handleMove(ev) {
    const drag = this._drag;
    if (!drag) {
      return;
    }
    const dy = this._clientY(ev) - drag.startY;
    if (!drag.moved) {
      if (Math.abs(dy) < DRAG_THRESHOLD_PX) {
        return;   // まだクリックと区別できない＝何も動かさない。
      }
      drag.moved = true;
      addClass(drag.group.box, DRAGGING_CLASS);
    }
    // 掴んだ箱をポインタへ追従させる（掴んでいる実感＝どれを動かしているかの明示）。
    drag.group.box.style.transform = `translateY(${Math.round(dy)}px)`;
    drag.target = this._targetPaneIndex(this._clientY(ev), drag.group.paneIndex);
    this._showIndicator(drag);
  }

  _handleUp() {
    const drag = this._drag;
    if (!drag) {
      return;
    }
    // 先に掴みを解く: movePane が凡例 DTO を再発行する（＝再描画が走る）ため、その前に
    //   ドラッグ状態を畳んでおかないと再描画が止められたままになる。
    //   また _releasePointer は lostpointercapture を誘発しうるので、その再入が上の
    //   `if (!drag) return` で弾かれるよう、状態を畳んでから捕捉を解く。
    this._bindWindow(false);
    this._drag = null;
    this._releasePointer();
    drag.group.box.style.transform = '';
    removeClass(drag.group.box, DRAGGING_CLASS);
    this._hideIndicator();
    if (!drag.moved) {
      return;   // 動かしていない＝チップのクリック（開閉）としてそのまま通す。
    }
    this._suppressClick = true;
    if (drag.target !== drag.group.paneIndex) {
      this._movePane(drag.group.paneIndex, drag.target);
    }
  }

  // ポインタ位置が乗っているペインの番号（＝そこへ落とす）。動かせないペイン（価格ペイン）は
  //   落下先にしないため、上下端は最寄りの動かせるペインへ丸める。候補が無ければ現在位置を返す。
  _targetPaneIndex(clientY, fallback) {
    const movable = this._groups
      .filter((g) => g.movable)
      .sort((a, b) => a.top - b.top);
    if (movable.length === 0) {
      return fallback;
    }
    const y = clientY - this._rootTop();
    for (const g of movable) {
      if (y < g.top + g.height) {
        return g.paneIndex;   // 最初に「下端がポインタより下」になるペイン＝乗っているペイン。
      }
    }
    return movable[movable.length - 1].paneIndex;
  }

  // 落下位置の線。下へ動かすなら対象ペインの下端、上へ動かすなら上端に引く（着地位置の予告）。
  _showIndicator(drag) {
    const target = this._groups.find((g) => g.paneIndex === drag.target);
    if (!target || !this._root || !this._document || typeof this._document.createElement !== 'function') {
      return;
    }
    if (!this._indicator) {
      this._indicator = this._document.createElement('div');
      this._indicator.className = INDICATOR_CLASS;
      this._root.appendChild(this._indicator);
    }
    const upward = drag.target <= drag.group.paneIndex;
    this._indicator.style.top = `${Math.round(upward ? target.top : target.top + target.height)}px`;
  }

  _hideIndicator() {
    const el = this._indicator;
    this._indicator = null;
    if (!el || !this._root || typeof this._root.removeChild !== 'function') {
      return;
    }
    this._root.removeChild(el);
  }

  // 器の画面上端（凡例 DTO の top と同じ原点へ揃えるため）。取得できない環境は 0。
  _rootTop() {
    const root = this._root;
    if (!root || typeof root.getBoundingClientRect !== 'function') {
      return 0;
    }
    const rect = root.getBoundingClientRect();
    const top = rect && rect.top;
    return Number.isFinite(top) ? top : 0;
  }

  _clientY(ev) {
    const y = ev && ev.clientY;
    return Number.isFinite(y) ? y : 0;
  }

  // 掴み手へポインタを捕捉し、捕捉の喪失（lostpointercapture）を掴みの終了へ結ぶ。
  //   ドラッグ中は凡例の再描画を止めるため、**終了が必ず届く経路**を 1 本用意しておく必要がある
  //   （pointerup だけに頼ると、届かない経路で凡例が恒久的に固まる）。
  //   捕捉を提供しない環境・捕捉できない pointerId では何もしない（従来どおり document 購読で追う）。
  _capturePointer(handle, ev) {
    const pointerId = ev && ev.pointerId;
    if (!handle || typeof handle.setPointerCapture !== 'function' || !Number.isFinite(pointerId)) {
      return;
    }
    try {
      handle.setPointerCapture(pointerId);
    } catch {
      return;   // 既に無効な pointerId 等。捕捉できないだけで掴みは続けられる。
    }
    this._captured = { handle, pointerId };
    if (typeof handle.addEventListener === 'function') {
      handle.addEventListener('lostpointercapture', this._onLostCapture);
    }
  }

  // 捕捉を解く（掴みの終了で必ず通る）。購読も同時に外し、次の掴みへ持ち越さない。
  _releasePointer() {
    const cap = this._captured;
    this._captured = null;
    if (!cap) {
      return;
    }
    if (typeof cap.handle.removeEventListener === 'function') {
      cap.handle.removeEventListener('lostpointercapture', this._onLostCapture);
    }
    if (typeof cap.handle.releasePointerCapture === 'function') {
      try {
        cap.handle.releasePointerCapture(cap.pointerId);
      } catch { /* 既に捕捉が失われている（要素の消失など）。解除済みとして扱う。 */ }
    }
  }

  // 掴んでいる間だけ document で move/up を拾う（ポインタが凡例の外＝チャート上へ出ても追える）。
  _bindWindow(on) {
    const doc = this._document;
    if (!doc || typeof doc.addEventListener !== 'function' || typeof doc.removeEventListener !== 'function') {
      return;
    }
    if (on) {
      doc.addEventListener('pointermove', this._onPointerMove);
      doc.addEventListener('pointerup', this._onPointerUp);
      doc.addEventListener('pointercancel', this._onPointerUp);
    } else {
      doc.removeEventListener('pointermove', this._onPointerMove);
      doc.removeEventListener('pointerup', this._onPointerUp);
      doc.removeEventListener('pointercancel', this._onPointerUp);
    }
  }
}
