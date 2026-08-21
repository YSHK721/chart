// bottom_pane_view.js — 統合ページ下部ペイン（版面の縦 2 分割）の器を生成し所有する View。
//
// なぜ在るか（裁定 2026-08-21・依頼者指示「シミュレーションのボタンをクリックしたとき
//   下部ペインに独立して表示しろ」）:
//   旧構成は「チャート API を持たない core のモードでは `.chart-wrap` を `display:none` にし、
//   空いた領域をそのモードの表示層へ丸ごと渡す」（承認 H-C）だった。つまり画面は**排他**で、
//   sim を押すとチャートが消える。参照実装である MT5 はそうなっていない: ストラテジーテスター
//   は下部のドックペインに出て、**チャートは上に残る**（実測: `ss20260821234659.jpg` 系の
//   MT5 実機画面 4 枚）。本 View はその版面を作る。
//
// 責務（SRP）: 「下部ペインという領域を作り、分割線で高さを変えられるようにする」ことだけ。
//   - 中に何を出すかは知らない（sim 表示層は `host()` を受け取って自分の器をそこへ挿す）。
//   - 出す・畳むの判定も知らない（CSS の `body:not(.um-chart-api)` が持つ＝index.html。
//     モード名を書かない反転記法は #replay-bar / #live-follow-toggle と同流儀）。
//   - 見た目（色・寸法）は index.html の統合層 CSS が持つ。ここは DOM と高さの数値だけを扱う。
//
// 依存の向き: doc と「高さを測る関数」だけを注入で受ける（DIP）。lwc・fetch・モード表に触らない。
//   チャートの追随は不要: live core の chart は `autoSize: true` で生成されており
//   （`indigators/indicator_ui/web/js/adapter/front/chart_bootstrap.js:68`）、
//   `.chart-wrap` の高さが変われば ResizeObserver 経由で自分で追随する。

/** 分割線の id（統合ページ側で本 View が所有する 2 要素のうちの 1 つ）。 */
export const BOTTOM_SPLITTER_ID = "um-bottom-splitter";
/** 下部ペインの id（表示層はこの中へ器を挿す）。 */
export const BOTTOM_PANE_ID = "um-bottom-pane";

/** ペインの下限（px）。これ未満にすると中身が読めない。 */
export const MIN_PANE_PX = 120;
/** 上側（チャート）の下限（px）。ペインを引き上げても上は残す（排他に戻さない）。 */
export const MIN_ABOVE_PX = 120;

/**
 * ペイン高さを可動域へ収める（純関数）。
 *
 * 可動域は [MIN_PANE_PX, 配分予算 − MIN_ABOVE_PX]。**配分予算は「上の要素＋ペイン」の実測高**
 * であり、版面（#app）の高さではない——版面にはツールバーやリプレイバーなど**分割に与らない
 * 兄弟**が含まれるので、版面高から引くと上側の下限が実際より狭くなる（実測 2026-08-21:
 * 版面 1000・ツールバー 42 のとき、版面基準の clamp では引き切ったチャートが 78px＝下限 120
 * を割った）。予算が測れない（0・NaN・上の要素が無い）ときは下限だけを掛ける——上限を 0 と
 * 誤解してペインを潰さない。
 *
 * @param {number} desiredPx 望みの高さ
 * @param {number} budgetPx  上の要素とペインが分け合う高さ（＝両者の実測高の和）
 * @returns {number} 可動域内の高さ
 */
export function clampPaneHeight(desiredPx, budgetPx, {
  minPanePx = MIN_PANE_PX,
  minAbovePx = MIN_ABOVE_PX,
} = {}) {
  const want = Number.isFinite(desiredPx) ? desiredPx : minPanePx;
  const lower = Math.max(want, minPanePx);
  if (!Number.isFinite(budgetPx) || budgetPx <= 0) {
    return lower;
  }
  // 上限が下限を割る（版面が極端に低い）ときは下限を優先する。ペイン側を 0 にすると
  //   「押しても何も出ない」という無音の失敗になり、原因が画面から読めない。
  const upper = Math.max(minPanePx, budgetPx - minAbovePx);
  return Math.min(lower, upper);
}

/** 既定の高さ測定（実 DOM）。テストは自前の関数を注入して時計・描画に依存させない。 */
function measureByRect(el) {
  if (!el || typeof el.getBoundingClientRect !== "function") {
    return 0;
  }
  const rect = el.getBoundingClientRect();
  return rect && Number.isFinite(rect.height) ? rect.height : 0;
}

/**
 * 下部ペイン（分割線 ＋ ペイン）を生成・破棄する View を返す。
 *
 * @param {Document} doc           統合ページの DOM
 * @param {function} measureHeight 要素の高さ（px）を返す関数（注入点＝実 DOM 非依存）
 */
export function createBottomPaneView({ doc, measureHeight = measureByRect } = {}) {
  let app = null;
  let above = null;
  let splitter = null;
  let pane = null;
  // ドラッグ中だけ立つ状態。pointerdown で掴んだ時点の座標とペイン高を覚える。
  let drag = null;

  const applyHeight = (px) => {
    // flex-basis で与える（`flex-grow:0 / flex-shrink:0` は CSS 側が持つ）。既定値
    //   （45%）は CSS が持ち、ドラッグしたときだけ px を上書きする＝自動介入をしない。
    pane.style.flexBasis = `${Math.round(px)}px`;
  };

  /** 上の要素とペインが分け合う高さ（実測）。上を渡されていなければ 0＝測れない。 */
  const budget = () => (above ? measureHeight(above) + measureHeight(pane) : 0);

  const onPointerMove = (ev) => {
    if (!drag) return;
    // 上へ引くほどペインは高くなる（clientY は下方向が正）。
    applyHeight(clampPaneHeight(drag.startHeight + (drag.startY - ev.clientY), drag.budget));
  };

  const onPointerUp = (ev) => {
    if (!drag) return;
    drag = null;
    if (ev && ev.pointerId !== undefined && typeof splitter.releasePointerCapture === "function") {
      splitter.releasePointerCapture(ev.pointerId);
    }
  };

  const onPointerDown = (ev) => {
    if (!pane) return;
    // 予算は掴んだ時点で 1 回だけ測る（移動のたびに測ると、自分が変えた高さを読み直して
    //   予算が揺れる）。
    drag = { startY: ev.clientY, startHeight: measureHeight(pane), budget: budget() };
    // **ポインタを分割線へ捕捉する**。捕捉しないと、カーソルがペイン内の iframe（sim 子文書）
    //   へ入った瞬間に pointermove が子文書側へ配られ、親の購読へ届かなくなる＝下方向へ引くと
    //   ドラッグが無反応になる（実測 2026-08-21: 上方向は動くが下方向だけ効かない状態だった）。
    if (ev.pointerId !== undefined && typeof splitter.setPointerCapture === "function") {
      splitter.setPointerCapture(ev.pointerId);
    }
    // 掴んでいる間はテキスト選択が走ってドラッグが途切れるので既定動作を止める。
    if (typeof ev.preventDefault === "function") ev.preventDefault();
  };

  return {
    isMounted() { return pane !== null; },

    /**
     * 分割線とペインを版面（#app）の末尾へ挿す。二重 mount は無視。
     *
     * @param {Element} target  版面（縦 flex）
     * @param {Element} above   分割線の上で高さを譲る要素（統合ページでは `.chart-wrap`）。
     *                          可動域の上限はこの要素の下限から決まる。
     */
    mount(target, { above: aboveEl = null } = {}) {
      if (pane) return pane;
      app = target;
      above = aboveEl;
      splitter = doc.createElement("div");
      splitter.id = BOTTOM_SPLITTER_ID;
      // 分割線は「操作できる境界」である。見た目だけでなく役割を持たせる
      //   （読み上げ・キーボード操作の入口。値の更新は将来の拡張点）。
      splitter.setAttribute("role", "separator");
      splitter.setAttribute("aria-orientation", "horizontal");
      splitter.title = "ドラッグで下部ペインの高さを変える";
      pane = doc.createElement("div");
      pane.id = BOTTOM_PANE_ID;

      // 購読は 3 つとも**分割線が持つ**（ポインタ捕捉で移動・離しもここへ配られる）。
      //   文書側に置かないので、unmount で器を外せば購読も一緒に消える。
      splitter.addEventListener("pointerdown", onPointerDown);
      splitter.addEventListener("pointermove", onPointerMove);
      splitter.addEventListener("pointerup", onPointerUp);
      // 捕捉が外部要因で切れたとき（別ウィンドウへ移る等）にドラッグ状態を残さない。
      splitter.addEventListener("pointercancel", onPointerUp);

      app.appendChild(splitter);
      app.appendChild(pane);
      return pane;
    },

    /** 表示層が器を挿す先（ペインそのもの）。未 mount なら null。 */
    host() { return pane; },

    /** 分割線（診断・E2E の観測点）。未 mount なら null。 */
    splitterElement() { return splitter; },

    /** 現在のペイン高（px）。未 mount なら 0。 */
    heightPx() { return pane ? measureHeight(pane) : 0; },

    /** ペイン高を明示指定する（可動域へ丸める）。未 mount なら何もしない。 */
    setHeightPx(px) {
      if (!pane) return;
      applyHeight(clampPaneHeight(px, budget()));
    },

    /** 分割線とペインを外す（統合ページへ何も残さない）。二重 unmount は無視。 */
    unmount() {
      if (!pane) return;
      app.removeChild(splitter);
      app.removeChild(pane);
      app = above = splitter = pane = null;
      drag = null;
    },
  };
}
