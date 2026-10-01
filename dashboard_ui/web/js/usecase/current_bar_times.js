// current_bar_times（usecase/current_bar_times.js）— 足ごとの現在バーの time の台帳。
//
// 設計入力（水準到達シート基本設計書 §3.5.3）: MP の期間水準の当期は「列の time ＝その足の
//   現在バーの time」。現在バーの time はライブの参照実装と同じ出所から得る。
//   - 参照実装 tf_period_profile_actor.js の onLiveTick は、チャートが持つローソクの末尾の time
//     （`renderer.getCandles()` の最後）を現在周期とする。そのローソクは /candles の全置換
//     （setCandles）と、ティックの形成中バー（updateLastCandle）の 2 本で更新される。
//   - 本台帳も同じ 2 本で更新する: /candles の応答は末尾の time で置き換え（休場中・起動直後でも
//     当期が決まる）、形成中バーは末尾より古い time を捨てる（参照実装 candle_feed.js の
//     updateLastCandle の後退ガード ISSUE-096 と同じ）。
//
// 計算量: 既に届いた応答と形成中バーの time を控えるだけで、取得は発行しない。

/**
 * 足ごとの現在バーの time の台帳を作る。
 *
 * @returns {{fromCandles: Function, fromBar: Function, of: Function, clear: Function}}
 */
export function createCurrentBarTimes() {
  /** 足 → 現在バーの time（UNIX 秒）。 */
  const times = new Map();

  return {
    /**
     * /candles の応答で置き換える（参照実装の setCandles と同じく全置換）。
     *
     * @param {string} timeframe 足
     * @param {Array}  candles   /candles の応答（time 昇順）
     */
    fromCandles(timeframe, candles) {
      if (!Array.isArray(candles) || candles.length === 0) {
        return;
      }
      const time = Number(candles[candles.length - 1].time);
      if (Number.isFinite(time)) {
        times.set(timeframe, time);
      }
    },

    /**
     * 形成中バーで進める（末尾より古い time は捨てる・参照実装の後退ガード）。
     *
     * @param {string} timeframe 足
     * @param {object} bar       形成中バー（time を持つ）
     */
    fromBar(timeframe, bar) {
      const time = Number(bar && bar.time);
      if (!Number.isFinite(time)) {
        return;
      }
      const known = times.get(timeframe);
      if (known !== undefined && time < known) {
        return;
      }
      times.set(timeframe, time);
    },

    /** その足の現在バーの time（未着は null）。 */
    of(timeframe) {
      return times.get(timeframe) ?? null;
    },

    /** 控えを捨てる（モードを出るとき）。 */
    clear() {
      times.clear();
    },
  };
}
