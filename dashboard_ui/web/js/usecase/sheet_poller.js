// sheet_poller（usecase/sheet_poller.js）— 更新の**発行判定だけ**を持つ純ロジック。
//
// 設計入力: 基本設計書 §7「更新経路（2 段）」。
//
//   | 段 | 契機     | mode   | 内容                                                     |
//   |----|----------|--------|----------------------------------------------------------|
//   | 1  | バー確定 | "full" | 水準系列と観測値系列を突合し、水準値と到達時刻を再導出する |
//   | 2  | ティック | "tick" | 観測値と到達状態（第 1 表は価格降順の並びも）を更新する     |
//
// なぜ DOM / HTTP から切り離すか（CLAUDE.md 絶対命令 §4.1）:
//   「作ってから捨てる」欠陥は**出力が正しいまま**なので、表の見た目を検査しても原理的に
//   落ちない（ISSUE-450: 既存テスト 1,233 件が緑のまま 20 日間 破棄率 98.0% を保護した）。
//   発行の判定を純ロジックとして取り出せば、Test Spy で発行そのものを数えられる。
//
// 本モジュールが固定する不変条件（回数そのものは焼き込まない・固定するのは**無駄の不在**）:
//   - 同一周期内に**同一ボディ**を 2 回発行しない
//   - 発行数は**表示量に依存しない**（束が 11 本でも 23 本でも同じ操作列なら同じ発行数）
//   - 応答が返る前に次を重ねない（遅い段 1 の最中に段 1 を積み上げない・ISSUE-257 と同型）
//   - `stop()` 後は 1 本も発行しない（モードを出た後に叩き続けない）
//
// 依存は注入だけ（`issue` / `now`）。fetch も DOM も timer も import しない。

/** 段 1（バー確定）で送る mode。値は /reach_sheet の JSON 契約（arch-spec §9）。 */
const MODE_FULL = 'full';

/** 段 2（ティック）で送る mode。 */
const MODE_TICK = 'tick';

/** 段 2 の既定周期（ms）。ティックの到来より粗く刻み、同一周期の重複発行を畳む。 */
const DEFAULT_TICK_INTERVAL_MS = 1_000;

/**
 * 発行の同一性キー。**ボディそのもの**から作る（順序の揺れを避けるため、束は instance_id で
 * 整列してから畳む）。キーが不安定だと同じ要求を 2 回出しても検査が通る
 * （sheet_models.py の `params_key` が `sort_keys=True` である理由と同じ）。
 *
 * `mode` は**キーに入れない**。mode はボディではなく契機から導かれる状態であり、含めると
 * 「バー確定の直後に、同じ内容の段 2 を撃つ」という無駄が同一性の検査をすり抜ける
 * （実測: 段 1 の直後に段 2 が必ず 1 本余計に出た）。
 */
function bodyKey(body) {
  const instances = [...(body.instances ?? [])]
    .map((i) => [i.instance_id, i.indicator_id, i.variant, JSON.stringify(i.params ?? {}), i.timeframe ?? ''])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
  return JSON.stringify([body.dataset_ref, body.chart_timeframe, instances]);
}

/**
 * 発行判定器を作る。
 *
 * @param {object}   deps
 * @param {(request: object) => Promise<object>} deps.issue  発行そのもの（HTTP を知る側が渡す）
 * @param {() => number} deps.now              現在時刻 ms（注入＝実時間に依存させない）
 * @param {number}   [deps.tickIntervalMs]     段 2 の周期（既定 1000ms）
 * @returns {{tick: Function, stop: Function, isRunning: Function}}
 */
export function createSheetPoller({ issue, now, tickIntervalMs = DEFAULT_TICK_INTERVAL_MS } = {}) {
  if (typeof issue !== 'function') {
    throw new TypeError('createSheetPoller: issue（発行）の注入は必須');
  }
  if (typeof now !== 'function') {
    throw new TypeError('createSheetPoller: now（時計）の注入は必須');
  }

  let stopped = false;
  let inFlight = false;
  /** 直近に発行した要求のキー（同一性の判定に使う）。 */
  let lastKey = null;
  /** 直近に発行した時刻（周期の判定に使う）。 */
  let lastAt = null;
  /** 直近に段 1 を撃ったときのバー確定時刻（バーが進んだかの判定に使う）。 */
  let lastBarCloseTime = null;

  /**
   * 契機を 1 つ通す。発行すべきなら発行し、そうでなければ何もしない。
   *
   * @param {object} opts
   * @param {object} opts.body           送るボディの本体（dataset_ref / chart_timeframe / instances）
   * @param {number} opts.barCloseTime   最新の確定バーの時刻（これが進んだら段 1）
   * @returns {Promise<object>|null}     発行したときはその Promise、しなければ null
   */
  function tick({ body, barCloseTime } = {}) {
    if (stopped || inFlight) {
      return null;
    }
    // 段の決定: バーが進んでいれば段 1、そうでなければ段 2（§7 の表そのもの）。
    const barAdvanced = lastBarCloseTime === null || barCloseTime !== lastBarCloseTime;
    const mode = barAdvanced ? MODE_FULL : MODE_TICK;
    const request = { ...body, mode };
    const key = bodyKey(body);
    const at = now();

    // 同一周期内の**同一要求**は畳む（無駄の不在）。内容が変われば周期の内側でも通す
    //   ——止めると束の変更が無言で落ちるため。
    if (!barAdvanced && key === lastKey && lastAt !== null && at - lastAt < tickIntervalMs) {
      return null;
    }

    inFlight = true;
    lastKey = key;
    lastAt = at;
    lastBarCloseTime = barCloseTime;
    const settled = () => { inFlight = false; };
    return Promise.resolve(issue(request)).then(
      (result) => { settled(); return result; },
      (err) => { settled(); throw err; },
    );
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
