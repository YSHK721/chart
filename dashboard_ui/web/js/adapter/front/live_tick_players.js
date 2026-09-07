// live_tick_players（adapter/front/live_tick_players.js）— なめらか tick 再生の台を並べる係。
//
// 設計入力（依頼者指示 2026-08-31: 各時間足のチャートもライブモードと同じティック粒度）:
//   実装は live の `LiveTickPlayer` **そのもの**（12 秒固定遅延・100ms 粒度・全ティック適用）。
//   再生の規約は写さない——公開面から借りる。時間足ごとに 1 台で、主（＝チャート足）が
//   ラダー・第 2 表・自分のタイルを、他の 7 台が各タイルを駆動する。
//   tails の申告は**主 player だけ**が行う（同じ末尾値を 8 回計算させるのは使わない計算の
//   発行・絶対命令 §4.1）。
//
// なぜ合成根から出したのか（ISSUE-502 段階 4C・F-3）:
//   合成根には 8 台ぶんの生成・タイマのラップ・renderer の結線・tails の受け口が直に
//   書かれており（約 90 行）、再生の仕様が動くたびに**結線のファイル**が書き換わっていた。
//   ここが持つのは「何台をどう並べるか」だけで、値の行き先（どの View のどのメソッド）は
//   呼び手がコールバックで与える。
//
// 計算量: 台数は時間足の数で固定（1 足 1 台）。同じ足に 2 台立てると同一ストリームの
//   二重取得になるため、主の足は必ず除く。

import { createLiveTicksFeed } from './live_ticks_client.js';

/**
 * tick 再生の台を並べる係を作る。
 *
 * @param {object}   opts
 * @param {?Function} opts.transport        fetch 実装（null＝再生しない）
 * @param {string}   opts.apiPrefix         `/live_ticks` の供給元（live core）
 * @param {string}   opts.datasetRef        素材
 * @param {readonly string[]} opts.timeframes 台を立てる時間足（主を含む）
 * @param {string}   opts.chartTimeframe    主の足（ラダー・第 2 表・tails を駆動する）
 * @param {number}   opts.tailsLimit        tails の窓長（付けないとサーバ 1 ステップが全件に比例）
 * @param {Function} opts.loadLiveTickPlayer 参照実装の読み込み（検定は fake を注入）
 * @param {Function} opts.getComputeSpecs   tails の申告（主だけが使う）
 * @param {Function} opts.onPrimaryBar      (bar) => void  主の足の形成中バー
 * @param {Function} opts.onBar             (timeframe, bar) => void  他の足の形成中バー
 * @param {Function} opts.onTails           (tails) => void  同一同期ブロックの末尾値
 * @returns {{start: Function, stop: Function}}
 */
export function createLiveTickPlayers({
  transport, apiPrefix, datasetRef, timeframes, chartTimeframe, tailsLimit,
  loadLiveTickPlayer, getComputeSpecs, onPrimaryBar, onBar, onTails,
}) {
  let players = [];
  /** 稼働を望んでいるか（非同期 import が stop 後に着弾したら捨てるための札）。 */
  let wanted = false;

  return {
    /**
     * 台を並べて再生を始める。
     *
     * import 失敗（単体テスト・live 停止）は握りつぶし＝従来表示のまま（縮退表示）。
     */
    start() {
      wanted = true;
      if (!transport) {
        return;
      }
      loadLiveTickPlayer().then((mod) => {
        if (!wanted || players.length > 0
            || !mod || typeof mod.LiveTickPlayer !== 'function') {
          return;
        }
        const feed = createLiveTicksFeed({ fetch: transport, apiPrefix });
        // タイマのラップ必須の理由: player は `this._setInterval(...)` とメソッド形で呼ぶため、
        //   素の globalThis.setInterval を渡すと this が Window でなくなり "Illegal invocation"
        //   で start が黙って死ぬ（実測 2026-08-31。live 側は bootstrap がバインド済みを注入）。
        const timers = {
          setInterval: (...args) => globalThis.setInterval(...args),
          clearInterval: (...args) => globalThis.clearInterval(...args),
        };
        const common = {
          fetchLiveTicks: feed.fetchLiveTicks,
          loadFormingBar: feed.loadFormingBar,
          datasetRef,
          ...timers,
        };
        players.push(new mod.LiveTickPlayer({
          renderer: { updateLastCandle: (bar) => { if (bar) onPrimaryBar(bar); } },
          getTimeframe: () => chartTimeframe,
          // 第 2 表・第 1 表のなめらか再生（ISSUE-250 Phase 1 の同梱経路そのもの）:
          //   poll で instance を申告し、各 tick 時点の末尾値（サーバ計算）を tick 適用と
          //   同一同期ブロックで流す。フロントは数値を再計算しない（唯一源はサーバの tails）。
          getComputeSpecs,
          getLimit: () => tailsLimit,
          applyFormingTails: (tails) => { if (tails) onTails(tails); },
          ...common,
        }));
        // 残りの足はタイル駆動だけ（tails の申告は主のみ）。
        for (const timeframe of timeframes) {
          if (timeframe === chartTimeframe) {
            continue;
          }
          players.push(new mod.LiveTickPlayer({
            renderer: { updateLastCandle: (bar) => { if (bar) onBar(timeframe, bar); } },
            getTimeframe: () => timeframe,
            ...common,
          }));
        }
        players.forEach((player) => player.start());
      }).catch(() => {});
    },

    /** 再生を止めて台を捨てる（モードを出るとき）。 */
    stop() {
      wanted = false;
      players.forEach((player) => player.stop());
      players = [];
    },
  };
}
