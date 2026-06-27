// FormingBarUpdater（adapter/front/forming_bar_updater.js）— 最新足（形成中バー）の
//   ティック由来ライブ更新（served=B方式のみ・既定 5 秒）。
//
// 設計: LiveUpdater（60 秒・全インジ再計算）とは**別系統**で、価格の最新足だけを高頻度に差分反映する。
//   /forming_bar?datasetRef=&timeframe= から選択 tf の「現在期間の形成中バー（mid OHLCV・1 本）」を取得し
//   renderer.updateLastCandle で反映する。インジ再計算はしない（負荷分離＝5 秒間隔でも全指標を再計算
//   しない）。bar=null（対象外 tf / 期間内ティック無し）は無視（更新なし）。
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
  }) {
    this._controller = controller;
    this._renderer = renderer;
    this._loadFormingBar = loadFormingBar;
    this._datasetRef = datasetRef;
    this._getTimeframe = getTimeframe;
    this._setInterval = setIntervalImpl;
    this._clearInterval = clearIntervalImpl;
    this._intervalMs = intervalMs;
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

  // 1 tick: 再計算中ならスキップ。そうでなければ形成中バーを取得し最新足へ差分反映（インジ再計算なし）。
  async _tick() {
    if (this._controller.isRecomputing()) {
      return;
    }
    const bar = await this._loadFormingBar(this._datasetRef, this._getTimeframe());
    if (bar) {
      this._renderer.updateLastCandle(bar);
    }
  }
}
