// recompute_gate.js — 再計算バッチの競合ガード（並行制御）ロール（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: IndicatorController に同居していた 5 アクターの 1 つが
//   「再計算バッチの並行制御」だった（旧 indicator_controller.js:211-216 isRecomputing ＋
//   :528-529/:567 の深さカウンタ増減）。さらに TimeframeController が host の private フィールド
//   （host._recomputeDepth / host._recomputeLastStartMs）へ直接代入しており（旧
//   timeframe_controller.js:55,58,74）、「クラスは分割されたが状態所有は host のまま」という
//   分割不全の実例になっていた。
//
// 状態も一緒に移す（ISSUE-181 対応方針）: 深さカウンタ（_depth）と最終開始時刻（_lastStartMs）を
//   本クラスが所有する。IndicatorController / TimeframeController はいずれも本ゲートの
//   enter/exit/isBusy を呼ぶだけになり、カウンタへの直接代入は消える。
//
// 不変条件（挙動不変・抽出前と等価）:
//   - depth<=0 なら非ビジー。
//   - depth>0 でも「最終 enter から STALL_DEADLINE_MS を超過」したバッチはハングとみなし非ビジー
//     （ISSUE-157: await ハングで finally が走らず、ゲートが恒久閉鎖するのを防ぐ時限式）。
//   - enter は必ず最終開始時刻を更新する（ネスト時も最内の開始時刻で時限を測る＝抽出前と同一）。

import { STALL_DEADLINE_MS } from './update_scheduler.js';

export class RecomputeGate {
  constructor() {
    // 再計算実行中の深さ（競合ガードの単一権威）。bool ではなく深さカウンタにするのは、
    //   setTimeframe（candles 取得 await＋全指標再計算）が内側の recomputeInstance を
    //   ネスト呼びするため。bool だと内側 finally がバッチ途中で解除し、その隙に tick が
    //   割り込む（torn なバッチ）。カウンタなら最外バッチ終了まで busy を維持する。
    this._depth = 0;
    // isBusy() の時限判定（ISSUE-157）に使うバッチ開始時刻。
    this._lastStartMs = 0;
  }

  // バッチ開始（必ず try/finally の try 直前で呼び、finally で exit する）。
  enter() {
    this._depth += 1;
    this._lastStartMs = Date.now();
  }

  // バッチ終了。
  exit() {
    this._depth -= 1;
  }

  // 実行中（かつハングしていない）か。ライブ更新の tick が先頭で参照しスキップ判定する。
  isBusy() {
    if (this._depth <= 0) {
      return false;
    }
    return (Date.now() - this._lastStartMs) <= STALL_DEADLINE_MS;
  }

  // ---- 互換アクセサ（host の _recomputeDepth / _recomputeLastStartMs 面を保つための最小面）----
  //   既存テストが host 経由で任意の深さ・開始時刻を直接注入して時限挙動を検証するため、
  //   読み書き両方を提供する（値の所有者は本クラス＝host はフィールドを持たない）。
  depth() { return this._depth; }

  setDepth(value) { this._depth = value; }

  lastStartMs() { return this._lastStartMs; }

  setLastStartMs(value) { this._lastStartMs = value; }
}
