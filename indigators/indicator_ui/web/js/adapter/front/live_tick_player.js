// LiveTickPlayer（adapter/front/live_tick_player.js）— 12 秒固定遅延のなめらか tick 再生
//   （ジッターバッファ・served=B方式のみ）。ISSUE-049。
//
// 参照実装: prototype_260707-01/web/index.html の poll/playback 機構（依頼者実機確認済み）。
//   present 統合分（現在 tf の形成中バーへの累積・/forming_bar シード）を加えるが、再生機構
//   （固定遅延・100ms 粒度適用・カーソル増分・clockOffset）は参照実装に忠実に保つ。
//
// 設計: FormingBarUpdater（5 秒・/forming_bar を都度取得して置換）とは別系統。こちらは backend の
//   LiveTickBuffer（5 秒周期で増分ポーリングし直近 30 分を保持）から /live_ticks で tick 列を増分取得し、
//   「serverNow-12000 以前の tick」を 100ms 粒度で現在 tf の形成中バーへ累積して価格を滑らかに描く。
//   価格の唯一の書き手にするため、composition root は稼働時に LiveUpdater/FormingBarUpdater へ
//   suppressPriceUpdate=true を渡す（12 秒より古いデータでの巻き戻しを排除）。
//
// 隔離・注入方針（DOM/ネット/タイマー非依存・FormingBarUpdater と同型の全注入）:
//   - fetchLiveTicks / loadFormingBar / renderer / getTimeframe / setInterval / clearInterval / now を注入。
//   - series.update を呼ぶのは ChartRenderer のみ（renderer.updateLastCandle 経由・隔離維持）。

// セッション日境界（ISSUE-078）: 1D の期間・バー time はセッション日（NY17:00 ET 基準）で解決する。
import { sessionBarTime } from '../../domain/session_day.js';

// 固定周期 tf（floor 可能・1m..1D）と秒長は domain/tf_meta.js（単一情報源・ISSUE-087 🔴-2）を参照。
import { TF_BAR_SEC, isFloorTimeframe } from '../../domain/tf_meta.js';

// プレイヤー（floor ベースの tick 累積）が扱える固定周期 tf か。1W/1M・未知は false
//   （＝カレンダー周期でありプレイヤーでは扱えず、/forming_bar ポーリング＝FormingBarUpdater へ委譲する側）。
//   composition root が「1W/1M のとき FormingBarUpdater を価格の書き手にする」配線判定に用いる。
export function isPlayerTimeframe(tf) {
  return isFloorTimeframe(tf);
}

// 固定遅延（ms）: 実測から poll 間隔 5s + feed 側 lag 最大 5.5s + fetch 最大 1.2s + 余裕 ≒ 12s。
//   これ未満だと feed のまとめ配信（3.8〜5.5s）で枯渇する（prototype 実測 25 polls）。
const DELAY_MS = 12000;
const POLL_MS = 2500;      // フロント → served /live_ticks のポーリング間隔。
const PLAYBACK_MS = 100;   // 「元の時間間隔どおり」に適用する再生粒度。

export class LiveTickPlayer {
  constructor({
    renderer,
    fetchLiveTicks,
    loadFormingBar,
    datasetRef,
    getTimeframe,
    setInterval: setIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.setInterval : undefined),
    clearInterval: clearIntervalImpl = (typeof globalThis !== 'undefined' ? globalThis.clearInterval : undefined),
    now = (typeof Date !== 'undefined' ? () => Date.now() : () => 0),
    delayMs = DELAY_MS,
    pollMs = POLL_MS,
    playbackMs = PLAYBACK_MS,
    // ---- 指標末尾値の同梱経路（ISSUE-250 Phase 1）--------------------------------------
    // 旧設計は tick 適用のたびに /compute へ HTTP 往復を要求していた（onFormingUpdate →
    //   UpdateScheduler → recomputeFormingTails）。scheduler は in-flight 1 本へ coalesce するため
    //   「指標更新回数 == ローソク更新回数」が構成上成立しない（ISSUE-157 で確定した「1 往復 1 回」）。
    //   本経路は poll 時に「適用中インスタンスの申告（specs）」を /live_ticks へ添え、サーバが
    //   各ティック時点の末尾値を一括算出して同梱する。フロントは tick 適用と**同一同期ブロック**で
    //   描くため、tick 路から往復が消えて回数一致が構成上の保証になる。
    //   - getComputeSpecs: () => [{instanceId, indicatorId, variant, params}]（controller が申告）
    //   - getLimit:        () => number|undefined（/compute と同一の表示範囲＝窓長）
    //   - applyFormingTails: (tailsMap, barTimeSec) => void（controller が末尾点を描く）
    //   いずれも null は従来挙動（価格のみ・申告なし＝サーバ応答も従来 byte）。
    getComputeSpecs = null,
    getLimit = null,
    applyFormingTails = null,
    // バー確定フック（ISSUE-151）。tick が新しい期間へロールオーバーした瞬間＝直前バーの確定を
    //   通知する（composition root が controller.requestFullRecompute を注入）。60 秒タイマー
    //   （LiveUpdater）だけに頼ると衝突スキップで確定イベントを取り落とし、非登録指標（帯系）が
    //   バー境界で停止する（1 分足で実測）。null は従来挙動。
    onBarClose = null,
  }) {
    this._renderer = renderer;
    this._fetchLiveTicks = fetchLiveTicks;
    this._loadFormingBar = loadFormingBar;
    this._datasetRef = datasetRef;
    this._getTimeframe = getTimeframe;
    this._setInterval = setIntervalImpl;
    this._clearInterval = clearIntervalImpl;
    this._now = now;
    this._delayMs = delayMs;
    this._pollMs = pollMs;
    this._playbackMs = playbackMs;
    this._getComputeSpecs = (typeof getComputeSpecs === 'function') ? getComputeSpecs : null;
    this._getLimit = (typeof getLimit === 'function') ? getLimit : null;
    this._applyFormingTails = (typeof applyFormingTails === 'function') ? applyFormingTails : null;
    this._onBarClose = (typeof onBarClose === 'function') ? onBarClose : null;

    // 再生状態。
    // 未適用 tick [(ms, mid, tails, tailsTf)] 昇順。tails は当該 tick 時点の指標末尾値
    //   {instanceId: {系列名: 値}}（未同梱は null）。tailsTf は tails を要求したときの tf
    //   （poll から適用までの 12 秒間に tf が変わった tails は適用しない）。
    this._queue = [];
    this._cursor = 0;         // /live_ticks の since カーソル（ms）。
    this._clockOffset = 0;    // serverNowMs - now()（遅延判定をサーバ時計基準に）。
    this._tf = null;          // シード済み tf（getTimeframe 変化で再シード）。
    this._tfSec = null;       // 現 tf の期間秒（非対応 tf は null＝no-op）。
    this._bar = null;         // 形成中バー {time(sec), open, high, low, close, volume}。
    this._seeding = false;    // /forming_bar シード await 中フラグ（true の間は自己シードを抑止＝🟡4 保持）。
    this._applied = 0;
    this._tailsApplied = 0;   // 指標末尾値を適用した tick 数（HUD・回数一致の監視用）。
    this._lastTickMs = 0;

    this._pollId = null;
    this._playbackId = null;
  }

  // 再生を開始する（冪等・稼働中の再 start は無視）。poll と playback の 2 タイマーを登録する。
  start() {
    if (this._pollId !== null || this._playbackId !== null) {
      return;
    }
    this._pollId = this._setInterval(() => this._poll(), this._pollMs);
    this._playbackId = this._setInterval(() => this._playback(), this._playbackMs);
  }

  // 停止する（両タイマーを clear）。冪等。
  stop() {
    if (this._pollId !== null) {
      this._clearInterval(this._pollId);
      this._pollId = null;
    }
    if (this._playbackId !== null) {
      this._clearInterval(this._playbackId);
      this._playbackId = null;
    }
  }

  // poll: /live_ticks を since カーソル付き取得しキューへ。serverNowMs で clockOffset を維持。
  //   ISSUE-250 Phase 1: 適用中インスタンスを申告し、各ティック時点の指標末尾値（tails）を
  //   同梱させる。申告なし（未注入・適用 0 件）は従来クエリ＝従来応答（byte 不変）。
  //   1 回の失敗は握りつぶしてログ化する（次 poll で回復・unhandledRejection を出さない）。
  async _poll() {
    try {
      // 申告に用いる tf は「これから tick を積む tf」＝現在の選択足。適用までの 12 秒間に
      //   足が変わった tails は捨てるため、要求時の tf を控えてキューへ持たせる。
      const tf = this._getTimeframe();
      const specs = this._getComputeSpecs ? this._getComputeSpecs() : null;
      const tailReq = (specs && specs.length)
        ? {
          specs,
          datasetRef: this._datasetRef,
          timeframe: tf,
          limit: this._getLimit ? this._getLimit() : undefined,
        }
        : null;
      const res = await this._fetchLiveTicks(this._cursor, tailReq);
      if (!res || res.ok !== true) {
        return;
      }
      if (typeof res.serverNowMs === 'number') {
        this._clockOffset = res.serverNowMs - this._now();
      }
      const ticks = res.ticks || [];
      if (ticks.length) {
        // tails は ticks と同数・同順（usecase.serve_live_tick_tails.tails_for_ticks の契約）。
        //   保険として tickMs 一致も確認し、ずれていれば当該 tick の tails を落とす。
        const tails = Array.isArray(res.tails) ? res.tails : null;
        for (let i = 0; i < ticks.length; i += 1) {
          const tk = ticks[i];
          const entry = tails && tails[i];
          const values = (entry && entry.tickMs === tk[0]) ? (entry.tails || null) : null;
          this._queue.push([tk[0], tk[1], values, values ? tf : null]);
        }
        this._cursor = ticks[ticks.length - 1][0];
      }
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('LiveTickPlayer: poll 失敗（次 poll で回復）:', err && err.message);
      }
    }
  }

  // playback: tf 変化ならシード → serverNow-DELAY 以前の tick を順に現在 tf の形成中バーへ適用。
  async _playback() {
    const tf = this._getTimeframe();
    if (tf !== this._tf) {
      await this._seed(tf);
    }
    const serverNow = this._now() + this._clockOffset;
    const playUntil = serverNow - this._delayMs; // この時刻以前の tick を適用してよい。
    while (this._queue.length && this._queue[0][0] <= playUntil) {
      const [ms, mid, tails, tailsTf] = this._queue.shift();
      this._applyTick(ms, mid, tails, tailsTf);
    }
  }

  // tf 切替・起動時のシード: /forming_bar で形成中バーの初期値を取得し、それをベースに以降の tick を累積。
  //   bar=null（1W/1M・非対応 tf / 期間内ティック無し）または非固定周期 tf は _bar=null＝当該 tf で
  //   何も描かない（既存挙動維持）。
  //   注記: 初回部分バーの高安は、シード（/forming_bar・最大 60 秒粒度の集約）＋ 12 秒遅延の tick で
  //   構成されるため、シード〜適用開始の隙間分だけ粗い近似になりうる（volume も適用 tick 数の近似）。
  async _seed(tf) {
    this._tf = tf;
    this._tfSec = isFloorTimeframe(tf) ? TF_BAR_SEC[tf] : null;
    // シード確定まで _bar=null かつ _seeding=true に倒す。await（loadFormingBar）中に再入した
    //   _playback は _seeding=true を見て自己シードせず（🟡4＝「新 tfSec × 旧 bar」誤描画の防止）。
    this._bar = null;
    if (this._tfSec === null) {
      this._seeding = false;
      return; // 非対応 tf（1W/1M/未知）→ no-op。
    }
    this._seeding = true;
    let bar = null;
    try {
      bar = await this._loadFormingBar(this._datasetRef, tf);
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('LiveTickPlayer: seed 失敗:', err && err.message);
      }
      bar = null;
    }
    // tf が seed 中に再度変わっていたら破棄（直近の getTimeframe を優先。_seeding は新 seed が所有）。
    if (this._tf !== tf) {
      return;
    }
    // seed 確定。非 null なら正確な形成中バーを採用。null（短周期で当日 parquet 窓が空・非対応）でも
    //   _bar=null のまま _seeding を解除し、以降 _applyTick が現周期 live tick から自己シードする
    //   （参照実装 prototype_260707-01 の !bar→新バー挙動へ復帰＝固着させない）。
    this._bar = bar
      ? { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume }
      : null;
    this._seeding = false;
  }

  // tick 時刻 → 現在 tf の期間キー（バー time）。ISSUE-078: '1D' はセッション日の 1D バー規約
  //   （セッション日ラベルの UTC 深夜＝backend rollup/forming と同一）。日中足は UTC floor（不変）。
  //   旧 UTC floor のままだと日曜夜 UTC（月曜セッション）の tick が「過去期間」と誤判定され、
  //   1D ライブバーが毎日 21:00-24:00 UTC の間フリーズしていた。
  _periodOf(sec) {
    if (this._tf === '1D') {
      return sessionBarTime(sec);
    }
    return Math.floor(sec / this._tfSec) * this._tfSec;
  }

  // 1 tick を現在 tf の形成中バーへ適用する。過去期間（履歴＝/candles 済）へは後退させない。
  //   tails（当該 tick 時点の指標末尾値）は updateLastCandle と**同一同期ブロック**で適用する
  //   （ISSUE-250 Phase 1: 価格と指標が同じ tick で同時に動く＝回数一致の構成的保証）。
  _applyTick(ms, mid, tails = null, tailsTf = null) {
    if (this._tfSec === null) {
      return; // 非対応 tf（1W/1M/未知）→ 何もしない。
    }
    const periodSec = this._periodOf(ms / 1000);
    if (this._bar === null) {
      // 自己シード（参照実装復帰）: /forming_bar seed が null でも、現周期の tick からバーを起こす。
      //   _seeding 中（seed await 未確定）は抑止（🟡4）。現 live 周期より前の tick は自己シードしない
      //   （/candles 済履歴を後退させない＝既存の後退ガードと同一意図）。確定後は非 null 経路へ移る。
      if (this._seeding) {
        return;
      }
      const nowPeriod = this._periodOf((this._now() + this._clockOffset) / 1000);
      if (periodSec < nowPeriod) {
        return; // 現周期より前 → 履歴側。自己シードしない。
      }
      this._bar = { time: periodSec, open: mid, high: mid, low: mid, close: mid, volume: 1 };
      this._renderer.updateLastCandle(this._bar);
      this._applyTails(tails, tailsTf);
      this._applied += 1;
      this._lastTickMs = ms;
      return;
    }
    if (periodSec < this._bar.time) {
      return; // シード期間より前の tick は無視（履歴を後退させない）。
    }
    if (periodSec === this._bar.time) {
      this._bar.high = Math.max(this._bar.high, mid);
      this._bar.low = Math.min(this._bar.low, mid);
      this._bar.close = mid;
      this._bar.volume += 1; // volume は適用 tick 数の近似（シード値＋適用数）。
    } else {
      // 新しい期間 → 新バー（open=mid）。直前バーはこの瞬間に確定＝バー確定イベントを通知する
      //   （ISSUE-151: 全指標の full 再計算をバー確定駆動にする。coalesce/pending は controller 側）。
      this._bar = { time: periodSec, open: mid, high: mid, low: mid, close: mid, volume: 1 };
      if (this._onBarClose) {
        this._onBarClose();
      }
    }
    this._renderer.updateLastCandle(this._bar);
    this._applyTails(tails, tailsTf);
    this._applied += 1;
    this._lastTickMs = ms;
  }

  // 当該 tick 時点の指標末尾値を、直前の updateLastCandle と同一同期ブロックで描く。
  //   要求時と現在で tf が違う tails は捨てる（12 秒遅延中に足を切り替えた場合＝旧足の値）。
  //   バー time は形成中バー（描いたばかりのローソク）の time＝価格と指標が必ず同じ点に載る。
  _applyTails(tails, tailsTf) {
    if (!this._applyFormingTails || !tails || tailsTf !== this._tf || this._bar === null) {
      return;
    }
    this._applyFormingTails(tails, this._bar.time);
    this._tailsApplied += 1;
  }

  // メトリクス（HUD・監視用）。
  stats() {
    return {
      applied: this._applied,
      tailsApplied: this._tailsApplied,
      queued: this._queue.length,
      cursor: this._cursor,
      clockOffset: this._clockOffset,
      tf: this._tf,
      lastTickMs: this._lastTickMs,
    };
  }
}
