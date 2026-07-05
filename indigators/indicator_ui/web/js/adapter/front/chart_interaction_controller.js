// ChartInteractionController（adapter/front/chart_interaction_controller.js）—
//   チャートコンテナのポインタ/ホイール操作（振る舞い）を composition root から分離したアダプター。
//
// 設計入力: ISSUE-040(a)。Composition Root は配線専用（new して install するだけ）へ縮小し、
//   DI ルートに混入していたチャート操作（スワイプスクラブ・縦価格パン・wheel 価格ズーム・dblclick reset）を
//   本コントローラへ移設する。移設は挙動 byte 不変（分岐/座標計算/イベント配線/登録順を完全保持）。
//
// 隔離・注入方針（upstream lwc 非依存・DIP）:
//   - lightweight-charts 直呼びはしない。座標→価格変換・スクロール/ズーム制御はすべて renderer 経由。
//   - 依存（container / renderer / replayBar / getController / updatePaneHeight）は constructor 注入。
//   - getController は controller を遅延参照する（composition root では controller 代入前に install するため、
//     () => controller のクロージャで呼出時点の controller を読む＝旧実装の外側 let クロージャと同一挙動）。
//   - container 不在/pointer 非対応（SSR/テスト）では install が no-op（防御）。

export class ChartInteractionController {
  constructor({ container, renderer, replayBar, getController, updatePaneHeight }) {
    this._container = container;
    this._renderer = renderer;
    this._replayBar = replayBar;
    this._getController = getController;
    this._updatePaneHeight = updatePaneHeight;
  }

  // リプレイ ON 判定（controller._marketProfile.isReplay() の遅延参照）。旧実装 isReplayOn/isReplayOn2 と同一。
  _isReplayOn() {
    const controller = this._getController();
    return !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function' && controller._marketProfile.isReplay());
  }

  // container の pointerdown/move/up/leave・wheel・dblclick を配線する。
  //   旧 composition root の 2 ブロック（swipe / wheel+dblclick+本体縦パン）を登録順そのままに再現する。
  install() {
    const container = this._container;
    const renderer = this._renderer;
    const replayBar = this._replayBar;
    const updatePaneHeight = this._updatePaneHeight;

    // 増分2 スワイプ: チャートコンテナの横ドラッグで T をスクラブする（replay ON 中のみ）。
    //   ★プロト準拠の**相対デルタ方式**（prototype_260630-01/js/app.js L442-457）:
    //     pointerdown で開始 x（startX）と開始 index（startIdx）を記録するだけ（＝クリック位置へ飛ばさない）。
    //     pointermove で dIdx=round((x - startX)/pixelsPerBar) を startIdx に足して T を更新する。
    //     pixelsPerBar は renderer 側で barSpacing を返しつつ極小時は 8px を下限にする（ズームアウト時に
    //     わずかなマウス移動でスライダが暴走する不具合の修正）。旧実装は coordinateToLogical の絶対
    //     マッピング（下限なし）で過敏だった。lwc 座標 API は renderer に隔離済み。
    //   container/doc 不在（SSR/テスト）や pointer 非対応は no-op（防御）。
    //   ★リプレイ中も**縦成分は価格パン**する（要望「拡大しても上下も移動」）。横=スクラブ・縦=価格パンの
    //     2D 操作にする。純横（dy=0）は価格を触らず T 追従を維持、純縦は index 不変でスクラブせず価格のみ動く。
    if (container && typeof container.addEventListener === 'function') {
      let swiping = false;
      let swipeStartX = 0;
      let swipeStartIdx = 0;
      let lastScrubIdx = 0; // 冗長スクラブ回避（縦のみドラッグで同 index を再取得しない）。
      let lastSwipeY = 0;   // 縦パンの前フレーム y（価格パンの dy 算出）。
      const isReplayOn = () => this._isReplayOn();
      const rectLeft = () => (typeof container.getBoundingClientRect === 'function'
        ? container.getBoundingClientRect().left : 0);
      container.addEventListener('pointerdown', (e) => {
        if (!isReplayOn() || e.button !== 0) {
          return; // 左ボタンのみ（右クリック等でスワイプ開始しない・2Dパン側と整合）。
        }
        swiping = true;
        renderer.setUserInteraction(false); // 通常スクロール/ズームを停止（スワイプ捕捉）。
        // 開始点のみ記録（クリック位置へは飛ばさない＝プロト mousedown 相当）。
        swipeStartX = e.clientX - rectLeft();
        swipeStartIdx = typeof replayBar.currentIndex === 'function' ? replayBar.currentIndex() : 0;
        lastScrubIdx = swipeStartIdx;
        lastSwipeY = e.clientY;
      });
      container.addEventListener('pointermove', (e) => {
        if (!swiping || !isReplayOn()) {
          return;
        }
        // 横成分: T スクラブ（相対デルタ・index 変化時のみ再取得＝縦のみドラッグで無駄打ちしない）。
        const x = e.clientX - rectLeft();
        const px = renderer.pixelsPerBar(); // barSpacing（極小時は 8px 下限＝プロト準拠）。
        const idx = swipeStartIdx + Math.round((x - swipeStartX) / px); // 左ドラッグ=過去へ。
        if (idx !== lastScrubIdx) {
          lastScrubIdx = idx;
          replayBar.scrubToLogical(idx); // clamp は scrubToLogical 内で実施。
        }
        // 縦成分: 価格パン。**価格ズーム中（isPriceZoomed）のみ**（非リプレイ側と統一）。全体表示で
        //   縦パンすると override が張られ空白露出＝撤去した不具合をリプレイ中に再現するため、ゲートする。
        const dy = e.clientY - lastSwipeY;
        lastSwipeY = e.clientY;
        if (typeof renderer.isPriceZoomed === 'function' && renderer.isPriceZoomed()) {
          updatePaneHeight(); // autoSize 追随。
          renderer.panPriceByPixels(dy);
        }
      });
      const endSwipe = () => {
        if (!swiping) {
          return;
        }
        swiping = false;
        renderer.setUserInteraction(true); // 通常操作を復元。
      };

      container.addEventListener('pointerup', endSwipe);
      container.addEventListener('pointerleave', endSwipe);
    }

    // 価格軸ホイールズームの配線（wheel / dblclick）。既存操作（本体ホイール=時間軸ズーム・軸ドラッグ）は不変。
    //   wheel: 価格軸領域上（renderer が x>=timeScale().width() で判定）のときだけ価格ズームし preventDefault。
    //     handlePriceWheel が false（本体領域・データ無し）なら preventDefault せず時間軸ズームへ委ねる。
    //     passive:false で登録（preventDefault を有効化）。リプレイの pointer swipe とは別イベントで非干渉。
    //   dblclick: 価格軸領域なら自動スケールへ復帰（resetPriceZoom）。
    //   座標は **clientX/Y - コンテナ矩形**（コンテナ左上基準）で計算する。offsetX はイベント target
    //   （lwc の内部 canvas＝価格軸 canvas 等）基準になり、軸上では小さい値→本体領域と誤判定して
    //   価格ズームが発火しない（実機で確認したバグの修正）。lwc 座標/priceScale API は renderer に隔離済み。
    if (container && typeof container.addEventListener === 'function') {
      const containerXY = (e) => {
        const r = typeof container.getBoundingClientRect === 'function'
          ? container.getBoundingClientRect() : { left: 0, top: 0 };
        return { x: e.clientX - r.left, y: e.clientY - r.top };
      };
      container.addEventListener('wheel', (e) => {
        // リサイズ追随: 価格変換前に pane 高を再計算する（autoSize で container 高が変わるため）。
        updatePaneHeight();
        const { x, y } = containerXY(e);
        const handled = renderer.handlePriceWheel(x, y, e.deltaY);
        if (handled) {
          // lwc は自前の wheel リスナーで defaultPrevented を尊重せず時間軸ズームも実行してしまう。
          // capture 段階（本リスナー）で stopPropagation し、lwc へ届く前に止める（実機で確認したバグの修正）。
          if (typeof e.preventDefault === 'function') {
            e.preventDefault();
          }
          if (typeof e.stopPropagation === 'function') {
            e.stopPropagation();
          }
        }
      }, { passive: false, capture: true });
      container.addEventListener('dblclick', (e) => {
        if (renderer.isOverPriceAxis(containerXY(e).x)) {
          renderer.resetPriceZoom();
        }
      });

      // 本体ドラッグの縦成分で価格パン（上下移動）を **価格ズーム中（override 有効時）に限り** 行う。
      //   ・全体表示（自動スケール）では縦パンしない＝空白が出て拡大縮小に見える不具合を出さない（撤去理由）。
      //   ・価格軸ホイールズーム後（renderer.isPriceZoomed()）は縦パンを許可＝拡大した価格帯の外も辿れる
      //     （ユーザFB「その価格帯以外確認できないのは問題」への対応）。横は lwc の時間パンに委ねる。
      //   価格軸上・リプレイ中は対象外（リプレイは横スワイプが占有・軸は lwc ネイティブ）。
      let vpanActive = false;
      let lastVpanY = 0;
      const isReplayOn2 = () => this._isReplayOn();
      container.addEventListener('pointerdown', (e) => {
        if (isReplayOn2() || e.button !== 0) {
          return;
        }
        if (renderer.isOverPriceAxis(containerXY(e).x)) {
          return; // 価格軸上は lwc ネイティブのスケールに委ねる。
        }
        vpanActive = true;
        lastVpanY = e.clientY;
      });
      container.addEventListener('pointermove', (e) => {
        if (!vpanActive) {
          return;
        }
        if ((e.buttons & 1) === 0) {
          vpanActive = false;
          return;
        }
        const dy = e.clientY - lastVpanY;
        lastVpanY = e.clientY;
        // ★価格ズーム中のみ縦パン（全体表示では価格を触らず自動スケール維持）。
        if (typeof renderer.isPriceZoomed === 'function' && renderer.isPriceZoomed()) {
          updatePaneHeight();
          renderer.panPriceByPixels(dy);
        }
      });
      const endVpan = () => { vpanActive = false; };
      container.addEventListener('pointerup', endVpan);
      container.addEventListener('pointerleave', endVpan);
    }
  }
}
