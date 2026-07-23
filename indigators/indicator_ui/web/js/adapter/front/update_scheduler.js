// UpdateScheduler（adapter/front/update_scheduler.js）— クロック駆動更新スケジューラ。
//
// ISSUE-157（クロック駆動設計・indicator_controller.js から抽出＝SOLID 是正 🔴-1）:
//   指標更新は「要求フラグ＋クロック」で駆動する。イベント（tick・バー確定）はフラグを
//   立てて _drive() を呼ぶだけで、実行中要求の完了を一切待たない（await をゲートにしない）。
//   呼び出し元のポーラー（LiveTickPlayer 2.5s/100ms・FormingBarUpdater 5s・LiveUpdater 60s）が
//   自走クロックとなり、ハングした試行は STALL_DEADLINE_MS 経過後のクロックで単に無視される
//   ＝「凍結」という吸収状態が存在しない（ローソク側ポーラーと同一の自己回復構造・
//   全時間足/ライブ/リプレイ共通）。遅延して届いた古い応答は per-instance generation
//   （recompute の latest-wins）が破棄する。
//
// 依存注入（DIP・テスト容易性）:
//   - runForming: () => Promise  末尾差分再計算の実体（controller.recomputeFormingTails）
//   - runFull:    () => Promise  バー確定 full 再計算の実体（controller.recomputeAllApplied({mode:'full'})）
//   - isBlocked:  () => boolean  外部バッチ（params 変更・setTimeframe 等）実行中の述語
//                                （controller.isRecomputing・時限式のため恒久閉鎖はない）
//   - now:        () => number   現在時刻 ms（既定 Date.now・テストで注入可能）

// ISSUE-157（クロック駆動設計）: 進行中試行を「健全」とみなす上限（ms）。これを超えた試行は
//   ハング（応答が返らない要求等）と推定し、以後のクロックはその完了を待たずに新しい試行を発行する
//   （遅延して届いた古い応答は per-instance generation の latest-wins が破棄する＝競合安全）。
//   実測の full バッチ最大 ~4s（サーバ飽和時）に十分な余裕を持つ値。正常系では到達しない。
export const STALL_DEADLINE_MS = 10000;

export class UpdateScheduler {
  constructor({ runForming, runFull, isBlocked = () => false, now = Date.now }) {
    this._runForming = runForming;
    this._runFull = runFull;
    this._isBlocked = isBlocked;
    this._now = now;
    this.wantForming = false;   // 末尾差分の要求（latest-wins・試行開始時に消費）
    this.wantFull = false;      // バー確定 full の要求（必達・失敗時は再度立てて次クロックで再試行）
    this.attemptSeq = 0;        // 試行 id 発番（古い試行の finally が新しい試行の状態を壊さない）
    this.attemptActiveSeq = null; // 進行中試行の id（null=なし）
    this.attemptStartedMs = 0;  // 進行中試行の開始時刻（STALL_DEADLINE_MS 判定）
  }

  // tick 粒度の末尾差分要求。フラグを立ててクロックを 1 回駆動するだけ（実行中要求の完了を
  //   待たない・ISSUE-157 クロック駆動設計）。ライブ（LiveTickPlayer の tick 適用・
  //   FormingBarUpdater）から毎 tick 呼んでよい（連発は _drive が 1 試行に畳む＝latest-wins）。
  requestForming() {
    this.wantForming = true;
    this._drive();
  }

  // バー確定時の full 再計算要求（ISSUE-151: 必達）。ライブのバー確定イベント
  //   （LiveTickPlayer の期間ロールオーバー・FormingBarUpdater の bar.time 前進・LiveUpdater の
  //   新確定足検知）から呼ぶ。フラグは full 試行の成功まで再セットされ続ける（取り落とすと
  //   非登録指標＝帯系がバー境界で止まる）。
  requestFull() {
    this.wantFull = true;
    this._drive();
  }

  // ISSUE-157 クロック駆動の中枢。要求フラグがあれば新しい試行を 1 つ発行する。
  //   - 進行中試行が健全（開始から STALL_DEADLINE_MS 以内）なら何もしない（coalesce）。
  //     完了時の finally が再度 _drive() するため、積み残した要求は必ず消化される。
  //   - 進行中試行が STALL_DEADLINE_MS を超えていればハングとみなし、完了を待たずに新試行を
  //     発行する（古い試行の遅延応答は per-instance generation の latest-wins が破棄）。
  //   - 全体の生存はこの関数の再入呼び出し（各ポーラー＝自走クロック）にのみ依存し、
  //     どの await の完了にも依存しない＝凍結という吸収状態が構造的に存在しない。
  _drive() {
    if (!this.wantFull && !this.wantForming) {
      return;
    }
    const now = this._now();
    if (this.attemptActiveSeq !== null && (now - this.attemptStartedMs) <= STALL_DEADLINE_MS) {
      return;   // 健全な試行が進行中（完了時 finally の _drive が要求を消化する）
    }
    if (this._isBlocked()) {
      return;   // 外部バッチ（params 変更・setTimeframe 等）優先。時限式のため恒久閉鎖はない
    }
    // full 優先（末尾差分を包含する）。フラグはここで消費する（latest-wins）。
    const isFull = this.wantFull;
    this.wantFull = false;
    this.wantForming = false;
    const seq = ++this.attemptSeq;
    this.attemptActiveSeq = seq;
    this.attemptStartedMs = now;
    (async () => {
      let ok = true;
      try {
        if (isFull) {
          await this._runFull();
        } else {
          await this._runForming();
        }
      } catch (err) {
        ok = false;
        if (typeof console !== 'undefined' && console.warn) {
          console.warn(`${isFull ? 'full' : 'forming'} 再計算失敗（次クロックで再試行）:`, err && err.message);
        }
      } finally {
        // 古い（ハング判定後に完了した）試行が新しい試行の進行中状態を壊さない（seq 照合）。
        if (this.attemptActiveSeq === seq) {
          this.attemptActiveSeq = null;
        }
        if (isFull && !ok) {
          // バー確定 full は必達（ISSUE-151 追補2）: 要求を立て直し、次のクロック（次 tick・
          //   約 2〜5 秒後）で再試行する。即時 _drive はしない（恒久障害時のタイトループ防止）。
          this.wantFull = true;
        } else {
          // 成功時: 実行中に積まれた要求（新バー確定・新 tick）を即時消化する。
          this._drive();
        }
      }
    })();
  }
}
