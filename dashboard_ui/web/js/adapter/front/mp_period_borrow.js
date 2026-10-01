// mp_period_borrow（adapter/front/mp_period_borrow.js）— MP の期間水準（日・週・月の POC・VAH・VAL）を
//   ライブから借りる系統。
//
// 設計入力（依頼者指示 2026-09-29「「日」「週」「月」のMFの「POT」「VAH」「VAL」を追加しろ」・
//   水準到達シート基本設計書 §3.5.2〜§3.5.4）:
//   - 値は live core の `/tf_period_profile` の当期の列の poc / va_high / va_low（dashboard は MP を
//     計算しない）。URL はライブの buildTfPeriodUrl、src は mpTfPeriodSrc で写した MP の src、va は
//     MP の va（ライブの getQuery と同じ組）。
//   - どの instance の設定を借りるかは MP 列と**同じ取得文脈**（mp_fetch_context の setFromBundle）を
//     読む。規則を 2 か所に書かないため、文脈は mp_borrow から受け取る（ここでは選ばない）。
//   - 当期＝その足の現在バーの time（参照実装と同じくローソクの末尾＝/candles の末尾と形成中バー・
//     usecase/current_bar_times.js）に一致する列。窓は [time, time + 1)。
//   - 借りた値は `/reach_sheet` の要求の欄 `mp_levels` へ載る（合流はサーバ）。現在バーが未着・
//     当期の列が無い足は行を出さず、理由を掲示する（無言の縮退の禁止）。
//
// 計算量（CLAUDE.md 絶対命令 §4.1・設計書 §3.5.3「取得の回数: 時間足ごとに 1 回」）:
//   取りに行くかは MP 列と同じ判定器（mp_poller: 1m バー枠の進み・初回・設定の変化）が足ごとに決め、
//   設定の鍵にその足の現在バーの time を足す。行ごと・足の本数ごと・ティックごとには取りに行かない。
//   足ごとに判定器を分けるのは、ある足の現在バーが進んだときに他の足まで取り直さないためである。

import { createTfPeriodClient } from './tf_period_client.js';
import { createMpPoller } from '../../usecase/mp_poller.js';
import {
  MP_PERIOD_TIMEFRAMES, currentColumnOf, levelsOfColumn, periodWindowOf, pocLabelOf,
} from '../../domain/mp_period_levels.js';

/** live の公開面に要る名前（設計書 §3.5.2 の加法再輸出）。 */
const REQUIRED_NAMES = Object.freeze(['buildTfPeriodUrl', 'mpTfPeriodSrc', 'mpSourceCapability']);

/**
 * MP の期間水準の借用系統を作る。
 *
 * @param {object}   opts
 * @param {?Function} opts.transport        fetch 実装（null＝通信できない環境。借用しない）
 * @param {string}   opts.apiPrefix         借用先の prefix（live core）
 * @param {string}   opts.datasetRef        素材
 * @param {number}   opts.barMs             1m バー枠の長さ（MP 列と同じ判定器の枠）
 * @param {Function} opts.now               時計 ms
 * @param {Function} opts.currentBarTimeOf  (timeframe) => その足の現在バーの time（未着は null）
 * @param {Function} opts.isActive          いま有効か（モードを出た後の着弾を捨てる札）
 * @param {Function} opts.onChange          () => void  借用の結果か掲示が変わった
 * @returns {{start: Function, tick: Function, levels: Function, note: Function, stop: Function}}
 */
export function createMpPeriodBorrow({
  transport, apiPrefix, datasetRef, barMs, now, currentBarTimeOf, isActive, onChange,
}) {
  let face = null;
  let context = null;
  let client = null;
  /** 足 → 判定器（足ごとに 1 つ）。 */
  let pollers = null;
  /** 足 → 直近の借用結果 {time, levels, note}。 */
  const results = new Map();
  /** 公開面を使えないときの掲示（足に依らない）。 */
  let faceNote = null;

  function barTimeOf(timeframe) {
    const time = currentBarTimeOf(timeframe);
    return Number.isFinite(time) ? time : null;
  }

  function record(timeframe, entry) {
    results.set(timeframe, entry);
    onChange();
  }

  /** 1 足ぶんを借りる（発行するかは mp_poller が決める）。 */
  async function issue(timeframe) {
    const time = barTimeOf(timeframe);
    if (time === null) {
      record(timeframe, { time: null, levels: [], note: `MP の期間水準（${timeframe}）: 現在バーが未着です` });
      return null;
    }
    const settings = context.fetchSettings();
    const result = await client.fetchColumns({
      datasetRef, timeframe, ...periodWindowOf(time),
      src: face.mpTfPeriodSrc(settings.src), va: settings.va,
    });
    if (!isActive() || !pollers) {
      return result;   // モードを出た後の遅延着弾は捨てる。
    }
    if (!result.ok) {
      record(timeframe, { time, levels: [], note: result.error.message });
      return result;
    }
    const column = currentColumnOf(result.columns, time);
    if (!column) {
      record(timeframe, { time, levels: [], note: `MP の期間水準（${timeframe}）: 当期の列がありません` });
      return result;
    }
    const { levels, missing } = levelsOfColumn({
      timeframe, column, pocLabel: pocLabelOf(face.mpSourceCapability(settings.src)),
    });
    record(timeframe, {
      time,
      levels,
      note: missing.length > 0
        ? `MP の期間水準（${timeframe}）: ${missing.join('・')} の値がありません` : null,
    });
    return result;
  }

  return {
    /**
     * 公開面と取得文脈を受け取り、借用を結線する（mp_borrow が公開面を読めた後に呼ぶ）。
     *
     * @param {object} mod        live の公開面
     * @param {object} mpContext  MP 列と共有する取得文脈（mp_fetch_context）
     */
    start(mod, mpContext) {
      if (!transport || pollers) {
        return;
      }
      const absent = REQUIRED_NAMES.filter((name) => !mod || typeof mod[name] !== 'function');
      if (absent.length > 0) {
        // 再輸出の削除・live 側の配置換え。行がただ消えるだけにしない。
        faceNote = `MP の期間水準を借用できません: ライブの公開面に ${absent.join(' / ')} がありません`;
        onChange();
        return;
      }
      face = mod;
      context = mpContext;
      client = createTfPeriodClient({ fetch: transport, apiPrefix, buildUrl: mod.buildTfPeriodUrl });
      pollers = new Map(MP_PERIOD_TIMEFRAMES.map((timeframe) => [
        timeframe, createMpPoller({ issue: () => issue(timeframe), now, barMs }),
      ]));
    },

    /** 契機を 1 つ通す（足ごとに、設定の鍵＋現在バーの time で判定器へ渡す）。 */
    tick() {
      if (!pollers || !isActive()) {
        return;
      }
      const settingsKey = context.settingsKey();
      for (const [timeframe, poller] of pollers) {
        poller.tick({ paramsKey: `${settingsKey}|${barTimeOf(timeframe) ?? ''}` });
      }
    },

    /**
     * 要求の欄 `mp_levels` に載せる値。いまの現在バーの期間に借りた値だけを返す
     * （現在バーが進んだ直後に、確定済みの期間の値を送らない・§3.5.3）。
     */
    levels() {
      const out = [];
      for (const timeframe of MP_PERIOD_TIMEFRAMES) {
        const entry = results.get(timeframe);
        if (entry && entry.time !== null && entry.time === barTimeOf(timeframe)) {
          out.push(...entry.levels);
        }
      }
      return out;
    },

    /** 掲示文（無ければ null）。 */
    note() {
      const notes = faceNote ? [faceNote] : [];
      for (const timeframe of MP_PERIOD_TIMEFRAMES) {
        const entry = results.get(timeframe);
        if (entry && entry.note) {
          notes.push(entry.note);
        }
      }
      return notes.length > 0 ? notes.join(' / ') : null;
    },

    /** 借用を止めて結線と結果を捨てる（モードを出るとき）。 */
    stop() {
      if (pollers) {
        for (const poller of pollers.values()) {
          poller.stop();
        }
      }
      pollers = null;
      face = null;
      context = null;
      client = null;
      faceNote = null;
      results.clear();
    },
  };
}
