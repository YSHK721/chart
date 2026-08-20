// price_level_drag_controller.js — 水準線を掴んで動かすアダプター（ISSUE-368 スライス 4）。
//
// 設計入力: 設計書 §6「Adapter: PriceLevelDragController」／§4-C。
//
// 責務: container のポインタ操作を「どの水準を・いくらへ」へ翻訳し、PriceLevels（domain・E-02）の
//   非破壊更新を呼び出し側へ渡す。価格の計算式・配置の不変条件は持たない（domain の責務）。
//
// 掴み判定の座標源は **primitive が保持する最新 y 表**（`primitive.handleAt`）。ここで
//   価格→座標を計算し直すと、描画と掴みで座標源が 2 つになりスケール変更時にズレる。
//
// drag 中に縦パンを止める手段が **2 つとも必要**な理由（§4-C の実測）:
//   1. `renderer.setUserInteraction(false)` は lwc の handleScroll/handleScale を落とすだけ。
//   2. アプリ自前の縦価格パンは `scale_controller` が `priceScale.setVisibleRange` を
//      **直叩き**しており lwc オプションを見ない。こちらは縦パンブロッカーでしか止まらない。
//   片方だけだと「線を掴んだ瞬間にチャートが縦にずれて掴めない」。
//
// 発火順への依存を作らない設計:
//   共有配線（ChartInteractionController）は drag より **先に** install される。実 DOM では
//   capture 段階が bubble 段階より先に走るため pointerdown を capture で登録するが、
//   それだけに頼らず「掴める線の上をホバーしている間もブロッカーを真にする」ことで、
//   pointerdown の発火順に関係なく縦パンが始まらないようにする。
//
// 依存: renderer（priceAtCoordinate / setUserInteraction）・primitive（handleAt）・
//   PriceLevels 実体（withEntry / withStop / withTake）。lwc も DOM API も直に触らない
//   （container のイベントと矩形だけ）。

// 掴み許容（px）。既定は線の視認幅より広く取る（細い線をピクセル単位で狙わせない）。
const DEFAULT_GRAB_TOLERANCE_PX = 6;

export class PriceLevelDragController {
  constructor({
    container, renderer, primitive,
    getLevels = () => null,
    onLevelsChange = () => {},
    registerVerticalPanBlocker = null,
    isGrabBlocked = null,
    onGrabBlocked = null,
    grabTolerancePx = DEFAULT_GRAB_TOLERANCE_PX,
  } = {}) {
    this._container = container;
    this._renderer = renderer;
    this._primitive = primitive;
    this._getLevels = getLevels;
    this._onLevelsChange = onLevelsChange;
    this._registerVerticalPanBlocker = registerVerticalPanBlocker;
    // 掴んではいけない状態を外から注入する（ピッカーがアーム中など）。未注入は従来どおり常に掴める。
    this._isGrabBlocked = typeof isGrabBlocked === 'function' ? isGrabBlocked : () => false;
    // 掴めなかったことの**告知**（工程 5 🟡-2）。設計「フェイルセーフ」は「確定しない」だけでなく
    //   「理由を出す」ことまで求めるが、drag 経路には告知が 1 行も無く、線が動かない理由を
    //   利用者が「掴めない」のか「バグ」なのか区別できなかった。
    //   **任意注入・既定 no-op**（何を告知するか＝文言と条件は結線側の責務。本 class は
    //   トーストも文言も知らない＝DIP）。判定そのもの（`isGrabBlocked`）は変えない。
    this._onGrabBlocked = typeof onGrabBlocked === 'function' ? onGrabBlocked : () => {};
    this._releaseInteraction = null;
    this._tolerance = grabTolerancePx;
    this._dragging = null;      // 掴んでいる handle（{kind,index}）または null
    this._hovered = null;       // ホバー中の handle または null
    this._unregisterBlocker = null;
  }

  // ドラッグ中か（縦パンブロッカーと検定が読む）。
  isDragging() {
    return this._dragging != null;
  }

  // ホバー中の掴み対象（無ければ null）。
  hoveredHandle() {
    return this._hovered;
  }

  install() {
    const container = this._container;
    if (!container || typeof container.addEventListener !== 'function') {
      return;   // SSR/テスト防御（共有配線の他アダプターと同一規約）。
    }
    if (typeof this._registerVerticalPanBlocker === 'function') {
      this._unregisterBlocker = this._registerVerticalPanBlocker(
        () => this.isDragging() || this.hoveredHandle() != null,
      );
    }

    // capture 段階で登録する（実 DOM では共有配線の bubble リスナーより先に走る）。
    container.addEventListener('pointerdown', (e) => {
      if (e.button !== 0) {
        return;
      }
      if (this._isGrabBlocked()) {
        // ピッカーのアーム中など（入力先は常に一意＝R-P1。別の水準を動かさない）。
        //   **掴む対象の上を押したときだけ**告知する: 線から離れた場所は通常のチャート操作で
        //   あって「掴めなかった」ではなく、そこで鳴らすと操作のたびに鳴る。
        if (this._handleAt(e)) {
          this._onGrabBlocked();
        }
        return;
      }
      const handle = this._handleAt(e);
      if (!handle) {
        return;   // 線から離れている＝通常のチャート操作に委ねる（何も奪わない）。
      }
      this._dragging = handle;
      this._suppressInteraction();
    }, { capture: true });

    container.addEventListener('pointermove', (e) => {
      if (!this._dragging) {
        // 掴めない状態（ピッカーのアーム中）ではホバー扱いにもしない
        //   ＝縦パンブロッカーを無用に真にしない。
        this._hovered = this._isGrabBlocked() ? null : this._handleAt(e);
        return;
      }
      if ((e.buttons & 1) === 0) {
        this._end();                          // ボタンが離れていた（container 外での pointerup 等）。
        return;
      }
      this._applyPrice(this._dragging, this._priceAt(e));
    }, { capture: true });

    const end = () => { this._end(); };
    container.addEventListener('pointerup', end);
    container.addEventListener('pointerleave', () => { this._hovered = null; end(); });
    container.addEventListener('pointercancel', end);
  }

  // ---- 内部 ----

  _end() {
    if (!this._dragging) {
      return;
    }
    this._dragging = null;
    this._releaseInteraction?.();
    this._releaseInteraction = null;
  }

  // コンテナ左上基準の y（offsetY は lwc の内部 canvas 基準になるため使わない
  //   ＝chart_interaction_controller.js が既に踏んだ罠と同じ理由）。
  _containerY(e) {
    const rect = typeof this._container.getBoundingClientRect === 'function'
      ? this._container.getBoundingClientRect() : { top: 0 };
    return e.clientY - rect.top;
  }

  _handleAt(e) {
    if (!this._primitive || typeof this._primitive.handleAt !== 'function') {
      return null;
    }
    return this._primitive.handleAt(this._containerY(e), this._tolerance);
  }

  _priceAt(e) {
    if (!this._renderer || typeof this._renderer.priceAtCoordinate !== 'function') {
      return null;
    }
    return this._renderer.priceAtCoordinate(this._containerY(e));
  }

  // lwc 操作の抑止を**登録**する（単数スロットの奪い合いを避ける＝ChartRenderer.suppressInteraction）。
  //   解除は自分が受け取ったトークンだけを外すので、同時に抑止しているピッカーを巻き添えにしない。
  _suppressInteraction() {
    const renderer = this._renderer;
    if (this._releaseInteraction || !renderer || typeof renderer.suppressInteraction !== 'function') {
      return;
    }
    this._releaseInteraction = renderer.suppressInteraction();
  }

  // 掴んでいる水準へ価格を反映する（非破壊更新は PriceLevels 側の責務）。
  //   価格が取れない（可視範囲外）ときは**更新しない**。0 や直前値へ倒すと、画面外へ
  //   引っ張った瞬間に水準が飛ぶ。
  _applyPrice(handle, price) {
    if (price == null || !Number.isFinite(price)) {
      return;
    }
    const levels = this._getLevels();
    if (!levels) {
      return;
    }
    let next = null;
    if (handle.kind === 'entry') {
      next = levels.withEntry(handle.index, price);
    } else if (handle.kind === 'stop') {
      next = levels.withStop(price);
    } else if (handle.kind === 'take') {
      next = levels.withTake(price);
    }
    if (next) {
      this._onLevelsChange(next);
    }
  }
}
