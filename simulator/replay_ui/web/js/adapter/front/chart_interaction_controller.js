// ChartInteractionController（adapter/front/chart_interaction_controller.js）—
//   チャートコンテナのポインタ/ホイール操作（振る舞い）を composition root から分離したアダプター。
//
// 参照実装（正・byte 不変移植元）:
//   indigators/indicator_ui/web/js/adapter/front/chart_interaction_controller.js
//   - L103-137: wheel 価格ズーム＋dblclick 自動スケール復帰
//       （座標は clientX/Y − コンテナ矩形、capture:true+passive:false、handled 時 preventDefault+stopPropagation）
//   - L139-176: 本体縦ドラッグの価格パン（isPriceZoomed 中のみ・価格軸上除外・リプレイ中除外 isReplayOn2 ゲート）
//
// 移植範囲と除外:
//   ・上記 wheel/dblclick ブロックと本体縦パンブロックのみを分岐/境界/座標計算/イベント登録順/
//     オプション（passive:false / capture:true）を一切足さず・削らず忠実に移植する。
//   ・参照実装 L39-101 の **リプレイ横スワイプスクラブ**（pointer swipe による T スクラブ）ブロックは
//     indicator_ui 固有機能・simulator/replay_ui のスコープ外のため移植しない。
//   ・上記スワイプ除外に伴い、そのブロック専用依存であった replayBar を constructor から落とした
//     （逸脱: 参照実装 constructor は { container, renderer, replayBar, getController, updatePaneHeight }。
//      本実装は replayBar を受け取らない）。renderer/getController/updatePaneHeight/container は不変。
//
// 隔離・注入方針（upstream lwc 非依存・DIP）:
//   - lightweight-charts 直呼びはしない。座標→価格変換・スクロール/ズーム制御はすべて renderer 経由。
//   - 依存（container / renderer / getController / updatePaneHeight）は constructor 注入。
//   - getController は controller を遅延参照する（composition root では controller 代入後に install するが、
//     () => controller のクロージャで呼出時点の controller を読む＝参照実装と同一挙動）。
//   - container 不在/pointer 非対応（SSR/テスト）では install が no-op（防御）。

export class ChartInteractionController {
  constructor({ container, renderer, getController, updatePaneHeight }) {
    this._container = container;
    this._renderer = renderer;
    this._getController = getController;
    this._updatePaneHeight = updatePaneHeight;
  }

  // リプレイ ON 判定（controller._marketProfile.isReplay() の遅延参照）。参照実装 isReplayOn2 と同一。
  _isReplayOn() {
    const controller = this._getController();
    return !!(controller && controller._marketProfile
      && typeof controller._marketProfile.isReplay === 'function' && controller._marketProfile.isReplay());
  }

  // container の wheel・dblclick・pointerdown/move/up/leave を配線する。
  //   参照実装 L103-176（wheel+dblclick+本体縦パン）を登録順そのままに再現する。
  install() {
    const container = this._container;
    const renderer = this._renderer;
    const updatePaneHeight = this._updatePaneHeight;

    // 価格軸ホイールズームの配線（wheel / dblclick）。既存操作（本体ホイール=時間軸ズーム・軸ドラッグ）は不変。
    //   wheel: 価格軸領域上（renderer が x>=timeScale().width() で判定）のときだけ価格ズームし preventDefault。
    //     handlePriceWheel が false（本体領域・データ無し）なら preventDefault せず時間軸ズームへ委ねる。
    //     passive:false で登録（preventDefault を有効化）。
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
