// next_target_glow（domain/next_target_glow.js）— 「次のターゲット」印の移動を検出し、
//   行の残光を**いつ・どの行へ**乗せるかを決める予定表（スケジューラ）。
//
// 設計入力（依頼者承認 2026-08-31 →「移動した価格帯の**行全体**に色を乗せてフェードアウト
//   せよ」）: 印（horizon_marks）の持ち主行が前回応答から変わったとき、移動先の行全体へ
//   色を乗せ、一定秒かけてフェードアウトする。
//
// なぜ View から出したのか（ISSUE-502 段階 4C・F-2）:
//   これは**予定表**（いつ・何を・一度だけ）であって描画ではない。View に置くと、
//   「表示時刻に達したか」「賞味期限が切れたか」「二重に適用していないか」という時間の
//   規則を、DOM を組んでからでないと検証できない。時間の規則と版面の構造は変更要求の
//   出所が別なので分ける（SRP）。視覚のフェードそのものは CSS（dash-row-glow）が持ち、
//   ここは「効果がもう終わった印を再適用しない」ための賞味期限だけを持つ。
//
// 計算量: track は行数に比例（1 巡）、due は予約数に比例。時計を持たず、発行も予約 API も
//   呼ばない——時刻は呼び手が注入する（View は時計を持たない規約と対称）。

import { rowKeyOf } from './ladder_row.js';

/** 残光の賞味期限（秒）。視覚のフェードの実体は CSS（dash-row-glow・同じ 8s）。 */
export const NEXT_MOVE_FADE_SECONDS = 8;

/** 発光の適用を遅らせる秒数。**時間基準の統一**（依頼者指示 2026-08-31・v0.9.44）で
 *  サーバのシート計算自体が表示と同じ遅延スナップショットになったため、印の移動は
 *  検出された時点で既に表示時刻＝**遅延は 0**（二重に遅らせると逆に遅れる）。 */
export const NEXT_MOVE_DELAY_SECONDS = 0;

/**
 * 印（`horizon:side`）→ 持ち主行の対応表。
 *
 * 側は距離の符号で決まる（版面の buildMarks と同じ定義）。
 *
 * @param {Array<object>} rows `/reach_sheet` の全行
 * @returns {Map<string,string>} markKey → rowKey
 */
export function markOwnersOf(rows) {
  const owners = new Map();
  for (const row of rows) {
    const side = Number(row.distance) >= 0 ? 'up' : 'down';
    for (const key of (Array.isArray(row.horizon_marks) ? row.horizon_marks : [])) {
      owners.set(`${key}:${side}`, rowKeyOf(row));
    }
  }
  return owners;
}

/**
 * 残光の予定表を作る。
 *
 * @param {object} [opts]
 * @param {number} [opts.fadeSeconds]  賞味期限（秒）
 * @param {number} [opts.delaySeconds] 検出から表示までの遅延（秒）
 * @returns {{track: Function, due: Function, markApplied: Function, size: Function, clear: Function}}
 */
export function createNextTargetGlow({
  fadeSeconds = NEXT_MOVE_FADE_SECONDS,
  delaySeconds = NEXT_MOVE_DELAY_SECONDS,
} = {}) {
  /** markKey → {at: 発光を**表示する**時刻（unix 秒）, owner: 移動先 rowKey, applied: 適用済みか}。 */
  const moved = new Map();
  /** 前回の持ち主（null＝初回）。 */
  let lastOwners = null;

  return {
    /**
     * 応答 1 件から移動を検出して予約する。
     *
     * 全行（窓の外も含む）で突合する——移動先が窓の外なら何も光らないだけで、記録の意味は
     * 変わらない。時計が無い環境では効果ごと捨てる（経過を測れないまま光らせると、
     * 消えない残光を発明することになる）。
     *
     * 表を作り直すと発光のクラスも消えるので、表示中の予約は毎回「未適用」へ戻す
     * （負の delay で残り時間から続く＝再点滅にはならない）。
     *
     * @param {Array<object>} rows   全行
     * @param {?number}       nowSec 検出時刻（unix 秒）。null＝時計なし
     */
    track(rows, nowSec) {
      const owners = markOwnersOf(rows);
      if (nowSec !== null && nowSec !== undefined && lastOwners !== null) {
        for (const [mark, owner] of owners) {
          const before = lastOwners.get(mark);
          if (before !== undefined && before !== owner) {
            moved.set(mark, { at: nowSec + delaySeconds, owner, applied: false });
          }
        }
      }
      lastOwners = owners;
      for (const entry of moved.values()) {
        entry.applied = false;
      }
      if (nowSec === null || nowSec === undefined) {
        moved.clear();
      }
    },

    /**
     * 表示時刻に達した予約を返し、賞味期限が切れた予約を捨てる。
     *
     * 返すだけで「適用済み」にはしない——光らせる先（可視行）が無いこともあり、そのときは
     * 記録を寿命まで保って次の描画で拾う。適用の宣言は `markApplied` で明示的に行う。
     *
     * @param {number} nowSec 現在時刻（unix 秒）
     * @returns {Array<{mark: string, owner: string, elapsed: number}>} 経過秒つきの予約
     */
    due(nowSec) {
      const out = [];
      for (const [mark, entry] of moved) {
        if (nowSec - entry.at >= fadeSeconds) {
          moved.delete(mark);   // 終わった効果は再適用しない（賞味期限）。
          continue;
        }
        if (entry.applied || nowSec < entry.at) {
          continue;   // 適用済み・またはまだ表示時刻に達していない。
        }
        out.push({ mark, owner: entry.owner, elapsed: nowSec - entry.at });
      }
      return out;
    },

    /** 予約 1 件を「この版面へ適用済み」にする。 */
    markApplied(mark) {
      const entry = moved.get(mark);
      if (entry) {
        entry.applied = true;
      }
    },

    /** 予約の件数（0 なら呼び手は走査そのものを省ける）。 */
    size() {
      return moved.size;
    },

    /** 予約と前回の持ち主を捨てる（版面を畳むとき）。 */
    clear() {
      moved.clear();
      lastOwners = null;
    },
  };
}
