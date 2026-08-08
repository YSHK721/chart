// replay_cursor.js — 再生カーソル（対象データと現在位置）を所有するロール（ISSUE-256・SRP）。
//
// 設計入力（ISSUE-256）: `setupReplay` は 847 行の単一関数で、再生対象（ローソク列・現在バー・
//   リプレイ開始位置・時間足・世代）が**素の局所変数**として関数全体に露出していた。誰でもどこでも
//   書き換えられるため、「今どこを再生しているか」の正しさが関数全体に散らばっていた。
//
// 本クラスが所有する状態（旧 setupReplay の局所変数をそのまま移送）:
//   candles / bar / replayStart / timeframe / generation / activeSecs / activePeriodBars
//
// 併せて**対象データの取得**（/candles・/available_days）も持つ。「どの窓を見ているか」と
//   「その窓をどう取ってくるか」は同じ変更要求（再生対象の決定）に属するため。
//
// 純ロジック（clamp・時刻→index）は `replay/state.js` が唯一源。ここでは状態と HTTP だけを担う。
//
// 依存は注入する: fetchImpl / datasetRef / recentBars / preBars（カレンダー起点の前方バー数）。
import { clampBar, idxForTime } from './state.js';

export class ReplayCursor {
  constructor({ fetchImpl, datasetRef, recentBars, preBars, timeframe = null }) {
    this._fetch = fetchImpl;
    this._datasetRef = datasetRef;
    this._recentBars = recentBars;
    this._preBars = preBars;

    this._candles = [];
    this._bar = 0;
    this._replayStart = 0;
    this._timeframe = timeframe;
    this._generation = 0;
    this._activeSecs = null;         // 選択中の期間プリセット（秒）。null=未選択（全期間）
    this._activePeriodBars = null;   // 期間プリセットに対応する表示本数。null=既定
  }

  // ---- 読み取り ----

  candles() { return this._candles; }

  bar() { return this._bar; }

  replayStart() { return this._replayStart; }

  timeframe() { return this._timeframe; }

  activeSecs() { return this._activeSecs; }

  activePeriodBars() { return this._activePeriodBars; }

  // 現在バーのローソク（未確定は undefined）。
  current() { return this._candles[this._bar]; }

  // 末尾（未来足なし）か＝再生不可の判定材料。
  atEnd() { return !this._candles.length || this._bar >= this._candles.length - 1; }

  // ---- 遷移（名前のある操作だけを公開する＝どこからでも書き換えられる状態をなくす） ----

  setCandles(list) { this._candles = Array.isArray(list) ? list : []; }

  // 位置は必ず範囲内へ丸める（旧 `bar = clampBar(target, candles.length)`）。
  setBar(target) {
    this._bar = clampBar(target, this._candles.length);
    return this._bar;
  }

  setReplayStart(idx) { this._replayStart = idx; }

  // 時刻から開始位置を決める（カレンダー選択）。旧 `replayStart = idxForTime(candles, startUnix)`。
  setReplayStartAtTime(unixSec) {
    this._replayStart = idxForTime(this._candles, unixSec);
    return this._replayStart;
  }

  setTimeframe(tf) { this._timeframe = tf; }

  setActivePeriod({ secs = null, bars = null } = {}) {
    this._activeSecs = secs;
    this._activePeriodBars = bars;
  }

  // 期間選択を解除する（時間足切替・日付ジャンプ・ライブ復帰で使う）。
  clearActivePeriod() { this.setActivePeriod({ secs: null, bars: null }); }

  // ---- 世代（in-flight の描画・計算を supersede する） ----

  bumpGeneration() { this._generation += 1; return this._generation; }

  generation() { return this._generation; }

  // ---- 対象データの取得 ----

  async fetchCandles(tf, startUnix = null) {
    let url = `/candles?datasetRef=${encodeURIComponent(this._datasetRef)}`
      + `&timeframe=${encodeURIComponent(tf)}&limit=${this._recentBars}`;
    if (startUnix != null) url += `&from=${startUnix}&pre=${this._preBars}`;
    const payload = await (await this._fetch(url)).json();
    return (payload && payload.ok) ? payload.candles : [];
  }

  // カレンダーの選択可能日（足が 1 本以上ある UTC 日・"YYYY-MM-DD" 昇順）。
  async fetchDays(tf) {
    const url = `/available_days?datasetRef=${encodeURIComponent(this._datasetRef)}`
      + `&timeframe=${encodeURIComponent(tf)}`;
    const payload = await (await this._fetch(url)).json();
    return (payload && payload.ok && Array.isArray(payload.days)) ? payload.days : [];
  }
}
