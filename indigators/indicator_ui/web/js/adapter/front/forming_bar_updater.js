// FormingBarUpdater（adapter/front/forming_bar_updater.js）— 最新足（形成中バー）の
//   ティック由来ライブ更新（served=B方式のみ・既定 5 秒）。
//
// 設計: LiveUpdater（60 秒・/candles 全件再取得＋最新点 latest 再計算）とは**別系統**で、最新足を
//   高頻度に差分反映する。両者の分離の実体は「/candles 再取得(Live) vs /forming_bar(Forming)」「60秒
//   vs 5秒」であり、指標再計算はどちらも mode:'latest'（重い全件 full 計算ではない）。
//   /forming_bar?datasetRef=&timeframe= から選択 tf の「現在期間の形成中バー
//   （mid OHLCV・1 本）」を取得し、(1) renderer.updateLastCandle で価格の最新足を反映し、(2) 指標も
//   recomputeAllApplied({mode:'latest'}) で最新点をティック由来に再計算する（backend が mode=latest 時に
//   形成中バーを最新足として末尾追加して計算する）。bar=null（対象外 tf / 期間内ティック無し）は
//   価格も指標も更新しない（完全 no-op）。指標の重い再計算は確定足の LiveUpdater に委ね、ここは
//   「最新点（latest）」のみ更新する（頻度分離）。
//
// 隔離・注入方針（DOM/ネット/タイマー非依存）:
//   - setInterval / clearInterval / loadFormingBar / getTimeframe は注入（テストでフェイク化）。
//   - 競合ガードは controller.isRecomputing() の単一権威を参照（独自フラグを持たない）。
//   - series.update を呼ぶのは ChartRenderer のみ（renderer.updateLastCandle 経由・隔離維持）。

export class FormingBarUpdater {
  constructor({
    controller,
    renderer,
    loadFormingBar,
    datasetRef,
    getTimeframe,
    setInterval: setIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.setInterval : undefined),
    clearInterval: clearIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.clearInterval : undefined),
    intervalMs = 5000,
    // 価格の最新足更新（updateLastCandle）を抑止する（ISSUE-049）。LiveTickPlayer が価格の唯一の
    //   書き手になるときのみ composition root が true を渡す。既定 false＝従来挙動 byte 不変
    //   （指標の最新点再計算 recomputeAllApplied は抑止対象外・従来どおり実行する）。
    suppressPriceUpdate = false,
  }) {
    this._controller = controller;
    this._renderer = renderer;
    this._loadFormingBar = loadFormingBar;
    this._datasetRef = datasetRef;
    this._getTimeframe = getTimeframe;
    this._setInterval = setIntervalImpl;
    this._clearInterval = clearIntervalImpl;
    this._intervalMs = intervalMs;
    this._suppressPriceUpdate = suppressPriceUpdate;
    // 稼働中の interval ハンドル（null=停止中）。多重 start 防止の判定にも用いる。
    this._timerId = null;
  }

  // ライブ足内更新を開始する。多重 start は無視する（稼働中なら二重に setInterval しない）。
  start() {
    if (this._timerId !== null) {
      return;
    }
    this._timerId = this._setInterval(() => this._tick(), this._intervalMs);
  }

  // 停止する（clearInterval）。冪等。
  stop() {
    if (this._timerId === null) {
      return;
    }
    this._clearInterval(this._timerId);
    this._timerId = null;
  }

  // 1 tick: 再計算中ならスキップ。形成中バーを取得し、(1) 価格の最新足を反映、(2) 指標の最新点を
  //   ティック由来に再計算する。bar=null（更新材料なし）は完全 no-op。1 tick の失敗（取得/再計算/
  //   描画）は握りつぶしてログ化する（5 秒周期＝unhandledRejection を毎回出さず、次 tick で回復）。
  async _tick() {
    try {
      if (this._controller.isRecomputing()) {
        return;
      }
      const bar = await this._loadFormingBar(this._datasetRef, this._getTimeframe());
      if (!bar) {
        return;
      }
      // 価格の最新足反映。suppressPriceUpdate 時は skip（player が唯一の書き手＝巻き戻し防止）。
      if (!this._suppressPriceUpdate) {
        this._renderer.updateLastCandle(bar);
      }
      // 指標も最新点を再計算（mode:'latest'）。backend が形成中バーを最新足として計算へ織り込む。
      await this._controller.recomputeAllApplied({ mode: 'latest' });
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('FormingBarUpdater: tick 失敗（次 tick で回復）:', err && err.message);
      }
    }
  }
}
