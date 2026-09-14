// ChartInteractionController（adapter/front/chart_interaction_controller.js）—
//   チャートコンテナのポインタ/ホイール操作（振る舞い）を composition root から分離したアダプター。
//
// 設計入力: ISSUE-040(a)。Composition Root は配線専用（new して install するだけ）へ縮小し、
//   DI ルートに混入していたチャート操作（縦価格パン・wheel 価格ズーム・dblclick reset）を
//   本コントローラへ移設する。
// ISSUE-082: リプレイモードは present から撤去（replay_ui＝別アプリ専用機能へ）。リプレイ用の
//   スワイプスクラブブロック・replayBar 依存・_isReplayOn 判定を本ファイルから削除した
//   （replay_ui は独立コピー simulator/replay_ui/web/.../chart_interaction_controller.js を保持）。
//
// 隔離・注入方針（upstream lwc 非依存・DIP）:
//   - lightweight-charts 直呼びはしない。座標→価格変換・スクロール/ズーム制御はすべて renderer 経由。
//   - 依存（container / renderer / getController / updatePaneHeight）は constructor 注入。
//   - getController は controller を遅延参照する（composition root では controller 代入前に install するため、
//     () => controller のクロージャで呼出時点の controller を読む＝旧実装の外側 let クロージャと同一挙動）。
//   - isVerticalPanBlocked（任意・ISSUE-123）: 縦パンの開始を外部条件でブロックする述語（() => bool）。
//     replay_ui が「MP リプレイモード中は本体縦パンを開始しない」（旧・独立コピーの _isReplayOn ゲート）を
//     注入するために使う。未注入＝ブロックなし（present は従来どおり）。本オプション化により両アプリが
//     同一実体（symlink 単一ソース）を参照できる＝値渡しコピーの廃止。
//   - container 不在/pointer 非対応（SSR/テスト）では install が no-op（防御）。

export class ChartInteractionController {
  constructor({ container, renderer, getController, updatePaneHeight, isVerticalPanBlocked }) {
    this._container = container;
    this._renderer = renderer;
    this._getController = getController;
    this._updatePaneHeight = updatePaneHeight;
    this._isVerticalPanBlocked = typeof isVerticalPanBlocked === 'function'
      ? isVerticalPanBlocked : () => false;
    // ISSUE-368 スライス 3: 追加ブロッカーの登録先（下記 addVerticalPanBlocker 参照）。
    this._verticalPanBlockers = new Set();
  }

  // ISSUE-368 スライス 3: 縦パンを止める述語を**追加**登録する（解除関数を返す）。
  //
  //   なぜ合成にするか: 止める口は constructor の `isVerticalPanBlocked` 単数スロットしか無く、
  //   リプレイ root が「MP リプレイ表示モード中は縦パンしない」で既に使用中である
  //   （`composition_roots_share_wiring.test.js:98` が固定）。水準線 drag（スライス 4）が
  //   同じ口を要求するため、流用するとリプレイ側の条件を上書きして壊す。単数スロットの奪い合いは
  //   `setCandleObserver` / `setTfPeriodHoverHandler` で既に起きた再発型なので、OR 合成にして潰す。
  //
  //   評価は pointerdown のたびに行う（登録時点の値を焼き付けない）。未登録・未注入は
  //   従来と完全に同一＝常にブロックなし。関数以外は無視する（未定義を真と扱わない）。
  addVerticalPanBlocker(predicate) {
    if (typeof predicate !== 'function') {
      return () => {};   // 呼び出し側に分岐を作らせないため、解除関数は常に返す。
    }
    this._verticalPanBlockers.add(predicate);
    return () => { this._verticalPanBlockers.delete(predicate); };
  }

  // 縦パンを開始してよいか（合成判定）。constructor 注入と登録ブロッカーの OR。
  _verticalPanBlocked() {
    if (this._isVerticalPanBlocked()) {
      return true;
    }
    for (const blocked of this._verticalPanBlockers) {
      if (blocked()) {
        return true;
      }
    }
    return false;
  }

  // container の wheel・dblclick・pointerdown/move/up/leave（本体縦パン）を配線する。
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

      // 本体ドラッグの縦成分で価格パン（上下移動）を **常時** 行う（ISSUE-108・ユーザー裁定）。
      //   ・旧仕様の「価格ズーム中限定」ゲートは、旧 override 実装（provider 差し替え）で全体表示の
      //     縦パンが空白露出を起こした時代の回避策。ネイティブ setVisibleRange 置換（6a61c54）で
      //     発生機構が消滅したため撤去した。全体表示からの縦ドラッグは初回 panPriceByPixels が
      //     手動スケール（autoScale=OFF）へ遷移させるだけで、lwc が表示を戻すことはない。
      //   ・純横ドラッグ（dy=0）は panPriceByPixels 内で no-op＝自動スケール維持。横は lwc の時間パン。
      //   ・自動スケール復帰は価格軸 dblclick（resetPriceZoom）のみ（既存仕様のまま）。
      //   価格軸上は対象外（軸は lwc ネイティブ）。
      let vpanActive = false;
      let lastVpanY = 0;
      container.addEventListener('pointerdown', (e) => {
        if (this._verticalPanBlocked() || e.button !== 0) {
          return; // 外部ブロック述語（replay の MP リプレイ中ゲート等・ISSUE-123）または非左ボタン。
        }
        if (renderer.isOverPriceAxis(containerXY(e).x)) {
          return; // 価格軸上は lwc ネイティブのスケールに委ねる。
        }
        vpanActive = true;
        lastVpanY = e.clientY;
      });
      container.addEventListener('pointermove', (e) => {
        // ペイン区切りのドラッグ追随（ISSUE-440）: 区切りを掴んで動かすとペイン幾何が変わるが、
        //   lwc はそれを通知せず、クロスヘア移動も抑止されるため凡例が古い位置に取り残される
        //   （実測 2026-08-21: 区切りを 100px 上へ引いてもラベルは動かず、ペイン上端 458px に
        //   対しラベル 558px）。幾何が変わっていなければ何も起きない（指紋比較のみ）ので、
        //   通常のマウス移動に余計な再描画は生まれない。
        renderer.syncPaneGeometry();
        if (!vpanActive) {
          return;
        }
        if ((e.buttons & 1) === 0) {
          vpanActive = false;
          return;
        }
        const dy = e.clientY - lastVpanY;
        lastVpanY = e.clientY;
        // ★常時縦パン（ISSUE-108）。dy=0（純横）は価格を触らない＝自動スケール維持。
        if (dy !== 0) {
          updatePaneHeight();
          renderer.panPriceByPixels(dy);
        }
      });
      // 掴んでいた手を離した時点でも幾何を突き合わせる（移動が届かないまま終わる操作の受け皿）。
      const endVpan = () => {
        vpanActive = false;
        renderer.syncPaneGeometry();
      };
      container.addEventListener('pointerup', endVpan);
      container.addEventListener('pointerleave', endVpan);
    }
  }
}
