// mp_borrow（adapter/front/mp_borrow.js）— MP 列の「ライブから借りる」系統ひとまとまり。
//
// 設計入力（依頼者承認 2026-09-06「MP 列＝ライブ MP の借用」・裁定 6/7、契機は裁定 a
//   「ライブと完全同期」）: URL の組み立てと設定の写像はライブの唯一源
//   （buildMarketProfileUrl / MpFetchParams）を**公開面から借りる**。写すとライブが 1
//   パラメータ足した瞬間にラダーだけ古い URL を投げ、メモの共有も仕様の同期も無言で壊れる。
//   公開面が読めない環境（単体起動・live 停止）では借用そのものを始めない＝発行も 0
//   （取得だけして捨てる経路を作らない・絶対命令 §4.1）。
//
// なぜ合成根から出したのか（ISSUE-502 段階 4C・F-3）:
//   合成根には「公開面の名前の検査」「失敗時の文言」「client / context / poller の組み立て」
//   「契機の通し方」が直に書かれており、MP の仕様が動くたびに**結線のファイル**が書き換わって
//   いた。借用は 1 つの系統であり、その所有者を 1 モジュールにする。合成根に残るのは
//   「借用系統を 1 つ作って、結果の受け取り口を渡す」ことだけになる。
//
// 版面への反映方針（描くか・世代を進めるか）は呼び手が持つ——本モジュールは
//   「何が起きたか」だけを 1 つの口（onBorrowed）で伝える。文言は**そのまま**流す
//   （組み立てるのは失敗を観測した mp_profile_client と、公開面を読めなかったここだけ）。
//
// 計算量: 発行するかは mp_poller が決める（契機を渡すだけ）。tick 1 回につき発行は高々 1 本。

import { createMpProfileClient } from './mp_profile_client.js';
import { createMpFetchContext } from './mp_fetch_context.js';
import { createMpPoller } from '../../usecase/mp_poller.js';

/**
 * MP 借用の系統を作る。
 *
 * @param {object}   opts
 * @param {?Function} opts.transport      fetch 実装（null＝通信できない環境。借用しない）
 * @param {string}   opts.apiPrefix       借用先の prefix（live core）
 * @param {string}   opts.datasetRef      素材
 * @param {string}   opts.timeframe       チャート足（窓の判定にも使う）
 * @param {number}   opts.barMs           チャート足の 1 バーの長さ（枠の判定）
 * @param {Function} opts.now             時計 ms
 * @param {Function} opts.loadLiveMpApi   live 公開面の読み込み（検定は fake を注入）
 * @param {Function} opts.getLatestCandle 最新 1m 形成中バー（第 2 の取得口を作らない）
 * @param {Function} opts.readBundle      instance 束を読む（束の記録の形は呼び手が知る）
 * @param {Function} opts.isActive        いま有効か（モードを出た後の着弾を捨てる札）
 * @param {Function} opts.onBorrowed      ({note, profile, changed}) => void
 * @returns {{start: Function, tick: Function, stop: Function}}
 */
export function createMpBorrow({
  transport, apiPrefix, datasetRef, timeframe, barMs, now,
  loadLiveMpApi, getLatestCandle, readBundle, isActive, onBorrowed,
}) {
  let client = null;
  let context = null;
  let poller = null;

  /** MP を 1 本借りて結果を伝える（発行するかは mp_poller が決める）。 */
  async function issue() {
    const result = await client.fetchProfile(context.context());
    if (!isActive()) {
      return result;   // モードを出た後の遅延着弾は捨てる（合成根の present と同じ 1 箇所ガード）。
    }
    // 無言縮退の禁止: 列が空のとき「密度が無い相場」と区別が付く形で理由を掲示する。
    onBorrowed(result.ok
      ? { note: null, profile: result.profile, changed: true }
      : { note: result.error.message, profile: null, changed: true });
    return result;
  }

  /**
   * 借用の契機を 1 つ通す（契機は 3 つ——1m バー枠の進み・有効化直後の初回・
   * テンプレートの MP 設定の変化）。どれを発行に変えるかは mp_poller が決める。
   *
   * @param {?object} [bundle] 既に読んだ instance 束（無ければここで読む）
   */
  function tick(bundle = null) {
    if (!isActive() || !poller || !context) {
      return;
    }
    const read = bundle ?? readBundle();
    if (!read.ok) {
      return;   // 束が組めない理由は版面が既に掲示している（二重に出さない）。
    }
    // どの instance の設定を借りるか（裁定 7）は mp_fetch_context が持つ——束の記録の形を
    //   知るのは設定を写す役であって、結線ではない。
    context.setFromBundle(read);
    poller.tick({ paramsKey: context.settingsKey() });
  }

  return {
    /**
     * 公開面を読み、読めたら借用を結線して初回の契機を通す。
     *
     * 面はあるが名前が無い場合に黙って返すと、MP 列がただ空になり、借用が始まってすら
     * いないことが版面からも読めない（無言縮退の禁止）。したがって throw して掲示へ回す。
     */
    start() {
      if (!transport) {
        return;
      }
      loadLiveMpApi().then((mod) => {
        if (!isActive() || poller) {
          return;   // モードを出た後の着弾・二重結線は静かに捨てる（異常ではない）。
        }
        if (!mod || typeof mod.buildMarketProfileUrl !== 'function'
            || typeof mod.MpFetchParams !== 'function') {
          // 公開面の再輸出が消えた / live 側の配置換え。
          throw new TypeError('ライブの公開面に MP の借用口がありません');
        }
        client = createMpProfileClient({
          fetch: transport, apiPrefix, buildUrl: mod.buildMarketProfileUrl,
        });
        context = createMpFetchContext({
          MpFetchParams: mod.MpFetchParams,
          datasetRef,
          timeframe,
          // ライブの src 既定（テンプレートに MP が無いときだけ使う）。'zp' のリテラルを
          //   dashboard 側に持たない——ライブが既定を変えたら黙ってずれる。
          defaultSource: mod.MP_DEFAULT_SOURCE,
          // 最新 1m 足は既存の LiveTickPlayer 供給を流用する（第 2 の取得口を作らない）。
          getLatestCandle,
          nowSec: () => Math.floor(now() / 1000),
        });
        poller = createMpPoller({
          issue,
          now,
          // 枠の判定はチャート足のバー周期（candle_poller と同じ表・同じ式）。
          barMs,
        });
        tick();   // 有効化直後の初回（3 契機のうちの 1 つ）。
      }).catch((err) => {
        // 公開面を読めない（live 停止・単体起動・配置換え・再輸出の削除）。借用は始まらない
        //   ので発行は 0 のままだが、**なぜ MP 列が空なのか**は版面に出す。
        //   世代は進めない——プロファイルは 1 度も入れ替わっていない。
        if (!isActive()) {
          return;
        }
        onBorrowed({
          note: `MP を借用できません: ${err && err.message ? err.message : err}`,
          profile: null,
          changed: false,
        });
      });
    },

    tick,

    /** 借用を止めて結線を捨てる（モードを出るとき）。 */
    stop() {
      if (poller) {
        poller.stop();
        poller = null;
      }
      client = null;
      context = null;
    },
  };
}
