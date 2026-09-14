// mp_poller（usecase/mp_poller.js）— 借用 MP の**発行判定だけ**を持つ純ロジック。
//
// 設計入力（依頼者裁定 2026-09-06 案 a「更新契機はライブと完全同期」）:
//   ライブの MP 再計算の入口（recomputeAllApplied → onLiveTick）を実際に呼ぶのは**バー確定**
//   （update_scheduler.requestFull・必達）と時間足変更だけである。足内ティック駆動は
//   ISSUE-250 で廃止済み。したがってラダー側も畳む契機は 3 つに閉じる:
//     1. チャート足（1m）の**バー枠が進んだとき**（判定は candle_poller と同じ floor(now/周期)）
//     2. モードを有効化した直後の 1 回
//     3. テンプレートの MP 設定が変わったとき（＝借用 URL が変わるとき）
//   枠の内側で取り直しても zp の応答は同じであり、丸ごと浪費になる。
//
// なぜ DOM / HTTP から切り離すか（CLAUDE.md 絶対命令 §4.1・candle_poller.js と同じ理由）:
//   「作ってから捨てる」欠陥は出力が正しいまま残るので、MP 列の見た目を検査しても原理的に
//   落ちない。発行判定を純ロジックへ出せば Test Spy で発行そのものを数えられる。
//
// 本モジュールが固定する不変条件（回数そのものは焼き込まない・固定するのは**無駄の不在**）:
//   - 同じバー枠の内側で、同じ設定なら 2 回発行しない
//   - 契機（tick 呼び出し）を増やしても発行が増えない
//   - 応答が返る前に次を重ねない（ISSUE-257 と同型の積み上げ禁止）
//   - `stop()` 後は 1 本も発行しない
//   - 発行したものは 1 本残らず呼び出し側へ渡る（発行 − 使用 = 0）
//
// 依存は注入だけ（`issue` / `now` / バー周期）。fetch も DOM も timer も import しない。

/**
 * 借用 MP の発行判定器を作る。
 *
 * @param {object}   deps
 * @param {Function} deps.issue 発行そのもの（HTTP を知る側が渡す）
 * @param {Function} deps.now   現在時刻 ms（注入＝実時間に依存させない）
 * @param {number}   deps.barMs チャート足のバー周期 ms（枠の判定に使う）
 * @returns {{tick: Function, stop: Function, isRunning: Function}}
 */
export function createMpPoller({ issue, now, barMs } = {}) {
  if (typeof issue !== 'function') {
    throw new TypeError('createMpPoller: issue（発行）の注入は必須');
  }
  if (typeof now !== 'function') {
    throw new TypeError('createMpPoller: now（時計）の注入は必須');
  }
  if (!Number.isFinite(barMs) || barMs <= 0) {
    // 周期が引けないまま毎 tick 発行へ倒すと、本モジュールの守る不変条件が無言で無効になる。
    //   結線時に落とす（candle_poller.js:44-47 と同じ規律）。
    throw new TypeError('createMpPoller: barMs（バー周期）の注入は必須');
  }

  let stopped = false;
  /** 直近に発行したバー枠番号（floor(now / 周期)）。null＝まだ 1 度も発行していない。 */
  let lastSlot = null;
  /** 直近に発行した設定の鍵。 */
  let lastParamsKey = null;
  /** 発行中か（応答が返る前に次を重ねない）。 */
  let inFlight = false;

  /**
   * 契機を 1 つ通す。枠が進んだ・設定が変わった・初回のいずれかだけ発行する。
   *
   * @param {object} opts
   * @param {string} opts.paramsKey 現在の設定の鍵（借用 URL が変われば変わる値）
   * @returns {Promise<object>[]} 発行した Promise の一覧（発行しなければ空配列）
   */
  function tick({ paramsKey = null } = {}) {
    if (stopped || inFlight) {
      return [];
    }
    const slot = Math.floor(now() / barMs);
    const first = lastSlot === null;
    if (!first && slot === lastSlot && paramsKey === lastParamsKey) {
      return [];
    }
    lastSlot = slot;
    lastParamsKey = paramsKey;
    inFlight = true;
    // 失敗しても札を必ず戻す（1 度の切断で発行が永久に止まらないように）。
    const settled = () => { inFlight = false; };
    return [Promise.resolve(issue({ paramsKey })).then(
      (result) => { settled(); return result; },
      (err) => { settled(); throw err; },
    )];
  }

  /** 発行を止める（`disable()` から呼ぶ。以後 1 本も発行しない）。 */
  function stop() {
    stopped = true;
  }

  /** 停止済みか（診断用）。 */
  function isRunning() {
    return !stopped;
  }

  return { tick, stop, isRunning };
}
