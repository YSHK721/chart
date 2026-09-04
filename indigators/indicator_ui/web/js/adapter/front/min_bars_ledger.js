// MinBarsLedger（adapter/front/min_bars_ledger.js）— 最小バー数の学習台帳（ISSUE-283）。
//   （ISSUE-479 Wave2 J-1 SRP: indicator_controller.js から 1:1 抽出）。
//
// 担う関心は 1 つ:「その指標が計算に何本必要かを（サーバの申告から）学習し、いまの計算窓で
//   満たせるかを答える」。
//
// なぜ要るか（ISSUE-283 の実測）: リプレイは計算窓を limit=bar+1 に絞るため、長い履歴を要する
//   指標は要件に届かない間**必ず失敗する**。結果が捨てられると分かっている要求は発行しない
//   （回数を間引くのではなく、要求そのものを消す）。窓が要件に達すれば自動的に再開し、
//   params/variant 変更時は忘れる（別の要件になりうる）。
//
// 状態の所有（ISSUE-181「状態も一緒に移す」）: 学習内容（instanceId -> 必要バー数）は
//   **本クラスが所有する**。IndicatorController 側は Map を持たず、公開面
//   （_knownMinBars / _forgetMinBars / _computeWindowBars）だけを薄い委譲で温存する。
//
// ISP（ISSUE-099 🟡-4 / ISSUE-255）: host の広い公開面ではなく MIN_BARS_HOST_CONTRACT の射影
//   （createHostView）だけを受け取る。契約外へ触れると実行時に例外になる。

/**
 * MinBarsLedger（最小バー数台帳ロール）が host に要求する最小契約。
 *
 * @typedef {object} MinBarsHost
 * @property {function} computeLimit  /compute へ送る表示範囲（直近 N 本）を返す。
 */

// MinBarsHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const MIN_BARS_HOST_CONTRACT = Object.freeze({
  role: 'MinBarsHost',
  methods: Object.freeze(['computeLimit']),
  fields: Object.freeze([]),
  optionalFields: Object.freeze([]),
});

export class MinBarsLedger {
  /**
   * @param {MinBarsHost} host 最小バー数台帳ロール契約を満たす host 射影。
   */
  constructor(host) {
    this._host = host;
    // instanceId -> その指標が計算に要する最小バー数（サーバが violations で申告）。
    this._minBars = new Map();
  }

  // 学習済みの最小バー数（未学習は null＝知識なし）。
  known(instanceId) {
    return this._minBars.get(instanceId) ?? null;
  }

  // サーバの申告を学習する（以後、窓が満たすまで発行しない）。
  learn(instanceId, requiredBars) {
    this._minBars.set(instanceId, requiredBars);
  }

  // 学習を忘れる（計算成功・params/variant 変更）。未学習でも安全に no-op。
  forget(instanceId) {
    this._minBars.delete(instanceId);
  }

  // 次に送る計算窓の本数（不明なら null＝スキップ判定に使わない）。
  windowBars() {
    const n = this._host.computeLimit();
    return Number.isFinite(n) ? n : null;
  }

  /**
   * この instance の要求を見送るべきか。
   *
   * 要件（最小バー数）が分かっており、現在の窓がそれに満たないときだけ見送る。知識が無い
   * （未学習・窓が不明）なら**必ず送る**＝安全側（誤ってスキップしない）。
   *
   * @returns {?{requiredBars: number, windowBars: number}} 見送るなら理由、送るなら null。
   */
  shouldDefer(instanceId) {
    const need = this.known(instanceId);
    const windowBars = this.windowBars();
    if (need != null && windowBars != null && windowBars < need) {
      return { requiredBars: need, windowBars };
    }
    return null;
  }
}
