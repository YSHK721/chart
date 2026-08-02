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
  //   メンバー名・挙動不変のまま構造的に本契約を満たす。ISSUE-181 で時間足ロールの状態
  //   （_timeframe/_recentBars/_loadCandles/observer）と競合ガードを host から移送したため、
  //   依存面は field: _datasetRef/_state/_renderer、method: recomputeAllApplied/_persistAll/
  //   recomputeGate、optional: _el のみになった（host のフィールドへは一切代入しない）。
  /**
   * @param {import('./indicator_controller.js').TimeframeHost} host 時間足ロール契約を満たすホスト。
   * @param {{timeframe?: string, recentBars?: ?number, loadCandles?: ?function}} [state]
   *   時間足ロールが所有する状態の初期値（ISSUE-181: 状態も一緒に移す）。
   */
  constructor(host, { timeframe = '1D', recentBars = null, loadCandles = null } = {}) {
    this._host = host;
    // ISSUE-181: 時間足ロールの状態は本協働子が所有する（host のフィールドではない）。
    //   現在の表示時間足（1 分足原子から resample・compute/candles に伝搬する）。
    this._timeframe = timeframe;
    // 直近表示本数（§配信設計: リサンプル＋直近 N 本）。compute の limit に伝搬する。null=制限なし。
    this._recentBars = recentBars;
    // 時間足切替時に candles を再取得するローダ (datasetRef, timeframe) → Promise<candles|null>。
    //   B方式のみ注入される（A方式は SAMPLE_DATA・再集計不可のため null）。
    this._loadCandles = loadCandles;
    // 時間足変更の購読者（任意・1 個）。setTimeframe 適用後に新時間足を通知する。
    this._observer = null;
    // 時間足切替の反映役（任意・1 個。null=既定＝ライブ経路）。ISSUE-231。
    //   (timeframe) => Promise<void>。「candles 取得 → メイン系列差替え → 全指標再計算」の実行主体だけを
    //   差し替える seam。リプレイ層が登録すると、切替はリプレイの単一経路（同期一括描画）で行われる。
    this._applier = null;
  }

  // ---- 所有状態のアクセサ（host は本協働子へ委譲するだけでフィールドを持たない）----
  current() { return this._timeframe; }

  // 副作用なしの現在値差し替え（restore が保存済み時間足を確定する経路で使う）。
  setCurrent(timeframe) { this._timeframe = timeframe; }

  recentBars() { return this._recentBars; }

  setRecentBars(value) { this._recentBars = value; }

  loader() { return this._loadCandles; }

  observer() { return this._observer; }

  setObserver(observer) { this._observer = observer; }

  // 反映役の登録／解除（null=既定のライブ経路へ戻す）。ISSUE-231。
  setApplier(applier) { this._applier = typeof applier === 'function' ? applier : null; }

  applier() { return this._applier; }

  // 時間足切替（§チャート表示時間選択・1 分足原子から resample）。
  //   1) candles を新時間足で再取得しメイン系列を差し替え（B方式のみ・直近 recentBars 本）。
  //   2) 適用済み全指標を新時間足で再計算・再描画（candles と時間軸を揃える）。
  //   3) uiState に時間足を永続化（restore で復元）。
  //   A方式（loadCandles 無し・SAMPLE_DATA）では candles 再取得を行わない（再集計不可）。
  async setTimeframe(timeframe) {
    const host = this._host;
    if (!timeframe || timeframe === this._timeframe) {
      return;
    }
    this._timeframe = timeframe;
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
    //   ISSUE-181: 深さカウンタ・開始時刻は host のフィールドではなく RecomputeGate が所有する
    //   （enter が開始時刻の記録も行う。未記録だと時限ゲートが即座に開き、candles fetch await 中の
    //   tick スキップ保証が壊れる）。
    const gate = host.recomputeGate();
    gate.enter();
    try {
      // ISSUE-231（リプレイの非同時描画・二重実行の恒久解消）: 反映役が登録されているときは、
      //   以下のライブ反映（candles 取得 → メイン系列差替え → 全指標再計算）を**行わず**反映役へ委譲する。
      //   ライブの反映は ISSUE-196 の裁定でローソク先行（指標は compute 完了後）だが、リプレイは
      //   「その時点のリビール」が不変条件であり、ローソクだけ先に新足へ替わる中間状態は仕様違反
      //   （リビール範囲外の足が指標なしで露出する）。リプレイ層は自身の render（preRender で
      //   ローソク＋指標を await を挟まず同期一括描画）へ一本化する。
      //   未登録（ライブ）は下の従来経路がそのまま走る＝byte 挙動不変。
      await (this._applier ? this._applier(timeframe) : this._applyLive(timeframe));
    } finally {
      gate.exit();
    }
    host._state.uiState = { ...host._state.uiState, timeframe };
    host._persistAll();
    // 時間足購読者へ新時間足を通知する（売買マーカーの該当時間足フィルタ等）。
    this._observer?.(this._timeframe);
  }

  // ライブ既定の反映（candles 取得 → メイン系列差替え → 全指標再計算）。setTimeframe から呼ばれる
  //   （競合ガードは呼び出し側が保持している）。反映役が登録されているときは呼ばれない。
  async _applyLive(timeframe) {
    const host = this._host;
    // candles を新時間足で再取得（取得のみ・描画は下のバッチへ遅延）。
    let candles = null;
    if (typeof this._loadCandles === 'function') {
      candles = await this._loadCandles(host._datasetRef, timeframe);
    }
    // ISSUE-196（抜本対策・2026-07-29 実測に基づく設計変更）:
    //   旧: メイン系列差し替え（setCandles）を「全指標 compute 完了後」の同期バッチへ入れて
    //       全要素を同時更新していた（ISSUE-023）。この順序は 2 つの実測不具合の原因だった。
    //     1) 切替の所要が「最も遅い指標 compute」に律速される。/candles は 1.0 秒で届いているのに
    //        チャートが変わるのは 5.63 秒後（指標 5 件・market_profile 単発 5.4 秒）。
    //     2) その setData 時点で指標系列が旧足の time を保持しているため lwc が `Value is null` を
    //        throw し、例外がバッチを中断して指標が旧足のまま固着する（以後の再計算も同じ throw で
    //        失敗し続ける）。
    //   新: 「旧足の指標系列を空にする」→「新足ローソクへ差し替える」を **await を挟まない
    //       同一同期ブロック** で実行する。これで
    //     - 時間軸に載る time は常にローソク系列に存在する（不変条件を構造的に保証＝1) の原因が消える）
    //     - ローソク・時間軸は candles 取得直後（実測 約 1 秒）に切り替わる（指標構成に非依存）
    //   指標は後続の recomputeAllApplied フェーズ2 が同期一括で描く（指標同士の同時更新は不変）。
    //   切替直後の短時間は指標が空になる（compute 完了まで）。これは「5 秒以上チャート全体が
    //   旧足のまま」だった従来挙動に対する意図的な変更（UI 挙動の変更点）。
    if (candles && candles.length > 0) {
      if (typeof host._renderer?.clearInstanceData === 'function') {
        for (const inst of host._state?.applied ?? []) {
          host._renderer.clearInstanceData(inst.instanceId);
        }
      }
      host._renderer.setCandles(candles);
    }
    // 適用済み全指標を新時間足で再計算（params 据え置き・generation+1・gateway が timeframe 注入）。
    //   再計算ループは recomputeAllApplied に集約（ライブ更新と共通の単一入口・挙動/順序/generation 採否不変）。
    //   preRender は渡さない（メイン系列は上で差し替え済み）。
    await host.recomputeAllApplied({ preRender: null });
  }

  // 時間足セレクタの active 表示を現在値へ同期する（DOM 在席時のみ）。
  //   ISSUE-117: ドロップダウン項目も [data-timeframe] を持つため同ループで同期される。
  //   トリガーラベル（timeframeMenuLabel）は現在足要素の表記（例「日」）へ更新する。
  syncButtons() {
    const host = this._host;
    let currentLabel = null;
    for (const b of host._el?.timeframeBtns ?? []) {
      const active = b.dataset.timeframe === this._timeframe;
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
  //   'chart'/未指定はグローバル時間足（本協働子が所有）に追従、特定足（1h 等）は当該足で計算（MTF）。
  effectiveTimeframe(tfParam) {
    return tfParam && tfParam !== 'chart' ? tfParam : this._timeframe;
  }

  // 直近表示本数（compute の limit・§配信設計: リサンプル＋直近 N 本）。null=制限なし（undefined 送出）。
  limit() {
    return this._recentBars ?? undefined;
  }
}
