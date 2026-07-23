// timeframe_controller.js — 時間足取得・切替（A3）の関心事を担う協働子。
//
// ISSUE-094 🔴-4: indicator_controller.js（A6 指標管理 UI）へ混在していた時間足（A3）の関心事
//   ——setTimeframe（candles 再取得・メイン系列差替＋全指標再計算）・時間足ボタン同期・
//   _gatewayAdapter の timeframe/limit 注入——を本協働子へ外出しする。
//
// 設計上の制約（byte 挙動不変）: 共有ベース IndicatorController は replay_ui の subclass に継承され
//   （symlink 単一ソース・触れない）、restore()/bind() は this._syncTimeframeButtons() を、composition
//   root/テストは controller.setTimeframe() を呼ぶ。よって base は本協働子への薄い委譲を保持し、本協働子は
//   host（IndicatorController インスタンス）を受け取り host.* を直接操作する（挙動は抽出前と byte 等価）。
//   再計算入口（recomputeAllApplied）はライブ入口として controller 側に温存し、host 経由で呼ぶ。

export class TimeframeController {
  // 依存契約（ISP・ISSUE-099 🟡-3）: 本協働子は host（IndicatorController）の広い公開面ではなく、
  //   時間足ロール専用の狭い契約 TimeframeHost にのみ依存する。契約の単一ソースは
  //   indicator_controller.js（@typedef TimeframeHost ＋ TIMEFRAME_HOST_CONTRACT）で明文化し、
  //   IndicatorController（present）/ ReplayIndicatorController（replay・symlink 継承）が
  //   メンバー名・挙動不変のまま構造的に本契約を満たす（依存面 = getter/setter: _timeframe/
  //   _recomputeDepth/_datasetRef/_recentBars/_state/_renderer/_loadCandles/_timeframeObserver、
  //   method: recomputeAllApplied/_persistAll、optional: _el）。
  /**
   * @param {import('./indicator_controller.js').TimeframeHost} host 時間足ロール契約を満たすホスト。
   */
  constructor(host) {
    this._host = host;
  }

  // 時間足切替（§チャート表示時間選択・1 分足原子から resample）。
  //   1) candles を新時間足で再取得しメイン系列を差し替え（B方式のみ・直近 recentBars 本）。
  //   2) 適用済み全指標を新時間足で再計算・再描画（candles と時間軸を揃える）。
  //   3) uiState に時間足を永続化（restore で復元）。
  //   A方式（loadCandles 無し・SAMPLE_DATA）では candles 再取得を行わない（再集計不可）。
  async setTimeframe(timeframe) {
    const host = this._host;
    if (!timeframe || timeframe === host._timeframe) {
      return;
    }
    host._timeframe = timeframe;
    this.syncButtons();
    // ISSUE-113（ユーザー裁定）: 時間足切替では手動価格スケール（軸ドラッグ/ホイール拡大/縦パン）を
    //   自動スケールへリセットする（前の足の拡大レンジを持ち越さない）。手動スケールの解除点は
    //   「価格軸 dblclick または時間足切替」となる。renderer 非対応（後方互換 Fake/SSR）は no-op。
    if (typeof host._renderer?.resetPriceZoom === 'function') {
      host._renderer.resetPriceZoom();
    }
    // ISSUE-163: pane（オシレータ）価格軸も同裁定を適用。ISSUE-150 の手動スケール保持（keepPane
    //   退避/復元）は同一時間足の再計算のみを守り、切替では破棄して自動スケールへ戻す
    //   （旧レンジ持ち越し＝新値域のクリップ・全高ブロック化を防ぐ。実 UI 再現済み 2026-07-23）。
    if (typeof host._renderer?.resetPaneScales === 'function') {
      host._renderer.resetPaneScales();
    }
    // バッチ全体（candles 取得 await＋全指標再計算）を競合ガードで包む。これがないと
    //   _loadCandles の await 中は isRecomputing()=false となり、その隙にライブ tick が
    //   割り込んで二重 compute する（🟡-2）。最外で increment し finally で確実に解除する。
    host._recomputeDepth += 1;
    // isRecomputing() の時限判定（ISSUE-157）: バッチ開始時刻を記録する（未記録だと
    //   時限ゲートが即座に開き、candles fetch await 中の tick スキップ保証が壊れる）。
    host._recomputeLastStartMs = Date.now();
    try {
      // candles を新時間足で再取得（取得のみ・描画は下のバッチへ遅延）。
      let candles = null;
      if (typeof host._loadCandles === 'function') {
        candles = await host._loadCandles(host._datasetRef, timeframe);
      }
      // メイン系列差し替えを指標の再描画と同じ同期バッチへ含め、全要素を同時更新する（ISSUE-023）。
      //   取得失敗・A方式（candles 無し）は preRender=null でメイン系列を据え置く。
      const preRender = candles && candles.length > 0
        ? () => host._renderer.setCandles(candles)
        : null;
      // 適用済み全指標を新時間足で再計算（params 据え置き・generation+1・gateway が timeframe 注入）。
      //   再計算ループは recomputeAllApplied に集約（ライブ更新と共通の単一入口・挙動/順序/generation 採否不変）。
      await host.recomputeAllApplied({ preRender });
    } finally {
      host._recomputeDepth -= 1;
    }
    host._state.uiState = { ...host._state.uiState, timeframe };
    host._persistAll();
    // 時間足購読者へ新時間足を通知する（売買マーカーの該当時間足フィルタ等）。
    host._timeframeObserver?.(host._timeframe);
  }

  // 時間足セレクタの active 表示を現在値へ同期する（DOM 在席時のみ）。
  //   ISSUE-117: ドロップダウン項目も [data-timeframe] を持つため同ループで同期される。
  //   トリガーラベル（timeframeMenuLabel）は現在足要素の表記（例「日」）へ更新する。
  syncButtons() {
    const host = this._host;
    let currentLabel = null;
    for (const b of host._el?.timeframeBtns ?? []) {
      const active = b.dataset.timeframe === host._timeframe;
      b.classList.toggle('is-active', active);
      if (active && currentLabel === null && typeof b.textContent === 'string') {
        currentLabel = b.textContent;
      }
    }
    const label = host._el?.timeframeMenuLabel;
    if (label && currentLabel !== null) {
      label.textContent = currentLabel;
    }
  }

  // 計算.時間足（params.timeframe）の per-indicator override を解決する（_gatewayAdapter が注入に使う）。
  //   'chart'/未指定はグローバル時間足（host._timeframe）に追従、特定足（1h 等）は当該足で計算（MTF）。
  effectiveTimeframe(tfParam) {
    return tfParam && tfParam !== 'chart' ? tfParam : this._host._timeframe;
  }

  // 直近表示本数（compute の limit・§配信設計: リサンプル＋直近 N 本）。null=制限なし（undefined 送出）。
  limit() {
    return this._host._recentBars ?? undefined;
  }
}
