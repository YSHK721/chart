// FormingBarUpdater（adapter/front/forming_bar_updater.js）— 最新足（形成中バー）の
//   ティック由来ライブ更新（served=B方式のみ・既定 5 秒）。
//
// 設計: LiveUpdater（60 秒・/candles 全件再取得）とは**別系統**で、最新足を高頻度に差分反映する。
//   /forming_bar?datasetRef=&timeframe= から選択 tf の「現在期間の形成中バー（mid OHLCV・1 本）」を
//   取得し、(1) renderer.updateLastCandle で価格の最新足を反映する（LiveTickPlayer が価格の書き手に
//   なる固定周期 tf では suppressPriceUpdate で抑止＝実質 1W/1M 専用）、(2) bar.time の前進＝
//   直前バー確定を検知して full 再計算を要求する（第 2 経路）。bar=null（対象外 tf / 期間内ティック
//   無し）は完全 no-op。
//
// ISSUE-250 Phase 1: 足内の指標末尾更新（旧 recomputeAllApplied({mode:'latest'})→
//   requestFormingRecompute）は本 updater から廃止した。tick 粒度の末尾値は /live_ticks 同梱へ移り、
//   LiveTickPlayer が価格更新と同一同期ブロックで描く。
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
    // バー確定検知の第 2 経路（ISSUE-151 追補）: /forming_bar の period（bar.time）前進＝直前バー
    //   確定。LiveTickPlayer（第 1 経路）が死んでいるセッションでも確定 full 再計算を必ず駆動する。
    this._lastFormingTime = null;
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

  // 1 tick: 再計算中ならスキップ。形成中バーを取得し、(1) 価格の最新足を反映（player 非対応 tf のみ）、
  //   (2) バー確定（bar.time 前進）を検知して full 再計算を要求する。bar=null（更新材料なし）は
  //   完全 no-op。1 tick の失敗（取得/描画）は握りつぶしてログ化する（5 秒周期＝
  //   unhandledRejection を毎回出さず、次 tick で回復）。
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
      //   suppressPriceUpdate は boolean または関数（() => boolean）。関数のときは tick ごとに評価し、
      //   tf に応じて抑止可否を切り替える（1W/1M は player 非対応＝ここが価格の書き手＝抑止しない）。
      const suppress = typeof this._suppressPriceUpdate === 'function'
        ? this._suppressPriceUpdate()
        : this._suppressPriceUpdate;
      if (!suppress) {
        this._renderer.updateLastCandle(bar);
      }
      // バー確定検知（第 2 経路・ISSUE-151 追補）: forming bar の period 前進＝直前バー確定。
      //   requestFullRecompute は coalesce/pending 必達＝タイミングに依らず取り落とさない。
      //   第 1 経路（LiveTickPlayer.onBarClose）と重複しても coalesce で無害。
      if (this._lastFormingTime !== null && bar.time > this._lastFormingTime
          && typeof this._controller.requestFullRecompute === 'function') {
        this._controller.requestFullRecompute();
      }
      this._lastFormingTime = bar.time;
      // ISSUE-250 Phase 1: 足内の指標末尾更新要求はここから廃止した。tick 粒度の末尾値は
      //   /live_ticks へ同梱され LiveTickPlayer が updateLastCandle と同一同期ブロックで描く。
      //   5 秒周期の独立要求を残すと、tick と無関係な回数の指標更新が混ざり
      //   「指標更新回数 == ローソク更新回数」が成立しない。本 updater に残る責務は
      //   (1) 価格の最新足反映（player 非対応 tf のみ）(2) バー確定検知（full 要求・第 2 経路）。
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('FormingBarUpdater: tick 失敗（次 tick で回復）:', err && err.message);
      }
    }
  }
}
