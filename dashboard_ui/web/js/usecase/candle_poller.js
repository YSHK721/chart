// candle_poller（usecase/candle_poller.js）— ローソク再取得の**発行判定だけ**を持つ純ロジック。
//
// 設計入力: ISSUE-452 内容 2（各時間足のチャート一覧）。水準と現在値は /reach_sheet が毎秒
//   運ぶため、チャート側で増えるのは**その時間足の確定足**だけである。確定足はその時間足の
//   1 バー周期でしか増えない。したがってローソクの再取得は「その時間足のバー枠が進んだとき」
//   だけ発行する——枠の内側で取り直しても応答は同じであり、丸ごと浪費になる。
//
// なぜ DOM / HTTP から切り離すか（CLAUDE.md 絶対命令 §4.1・sheet_poller.js と同じ理由）:
//   「作ってから捨てる」欠陥は出力が正しいまま残るので、チャートの見た目を検査しても
//   原理的に落ちない。発行判定を純ロジックへ出せば Test Spy で発行そのものを数えられる。
//
// 本モジュールが固定する不変条件（回数そのものは焼き込まない・固定するのは**無駄の不在**）:
//   - 同じバー枠の内側では、同じ時間足を 2 回発行しない
//   - 契機（tick 呼び出し）を増やしても発行が増えない（発行は枠の進みだけで決まる）
//   - 応答が返る前に同じ時間足の次を重ねない（ISSUE-257 と同型の積み上げ禁止）
//   - `stop()` 後は 1 本も発行しない（モードを出た後に叩き続けない）
//
// 依存は注入だけ（`issue` / `now` / 時間足と周期の表）。fetch も DOM も timer も import しない。

/**
 * ローソク発行判定器を作る。
 *
 * @param {object}   deps
 * @param {(timeframe: string) => Promise<object>} deps.issue 発行そのもの（HTTP を知る側が渡す）
 * @param {() => number} deps.now         現在時刻 ms（注入＝実時間に依存させない）
 * @param {string[]} deps.timeframes      対象の時間足（表示の唯一源 DASHBOARD_TIMEFRAMES を渡す）
 * @param {Object<string, number>} deps.refreshMs 時間足 → バー周期 ms（TIMEFRAME_REFRESH_MS を渡す）
 * @returns {{tick: Function, stop: Function, isRunning: Function}}
 */
export function createCandlePoller({ issue, now, timeframes, refreshMs } = {}) {
  if (typeof issue !== 'function') {
    throw new TypeError('createCandlePoller: issue（発行）の注入は必須');
  }
  if (typeof now !== 'function') {
    throw new TypeError('createCandlePoller: now（時計）の注入は必須');
  }
  if (!Array.isArray(timeframes) || timeframes.length === 0) {
    throw new TypeError('createCandlePoller: timeframes の注入は必須');
  }
  const periods = timeframes.map((tf) => {
    const ms = refreshMs ? refreshMs[tf] : undefined;
    if (!Number.isFinite(ms) || ms <= 0) {
      // 周期が引けない時間足を黙って毎 tick 発行に倒すと、この検定の守る不変条件が
      //   その 1 本だけ無効になる（無言の縮退）。結線時に落とす。
      throw new TypeError(`createCandlePoller: 時間足 ${tf} の周期が refreshMs にありません`);
    }
    return { tf, ms };
  });

  let stopped = false;
  /** 時間足 → 直近に発行したバー枠番号（floor(now / 周期)）。 */
  const lastSlot = new Map();
  /** 発行中の時間足（応答が返る前に次を重ねない）。 */
  const inFlight = new Set();

  /**
   * 契機を 1 つ通す。枠が進んだ時間足だけ発行し、それ以外は何もしない。
   *
   * @returns {Promise<object>[]} 発行した Promise の一覧（発行しなければ空配列）
   */
  function tick() {
    if (stopped) {
      return [];
    }
    const at = now();
    const issued = [];
    for (const { tf, ms } of periods) {
      const slot = Math.floor(at / ms);
      if (inFlight.has(tf) || lastSlot.get(tf) === slot) {
        continue;
      }
      inFlight.add(tf);
      lastSlot.set(tf, slot);
      const settled = () => { inFlight.delete(tf); };
      issued.push(Promise.resolve(issue(tf)).then(
        (result) => { settled(); return result; },
        (err) => { settled(); throw err; },
      ));
    }
    return issued;
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
