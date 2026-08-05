// LiveTickPlayer（adapter/front/live_tick_player.js）— 12 秒固定遅延のなめらか tick 再生
//   （ジッターバッファ・served=B方式のみ）。ISSUE-049。
//
// 参照実装: prototype_260707-01/web/index.html の poll/playback 機構（依頼者実機確認済み）。
//   present 統合分（現在 tf の形成中バーへの累積・/forming_bar シード）を加えるが、再生機構
//   （固定遅延・100ms 粒度適用・カーソル増分・clockOffset）は参照実装に忠実に保つ。
//
// 設計: FormingBarUpdater（5 秒・/forming_bar を都度取得して置換）とは別系統。こちらは backend の
//   LiveTickBuffer（5 秒周期で増分ポーリングし直近 30 分を保持）から /live_ticks で tick 列を増分取得し、
//   「serverNow-12000 以前の tick」を 100ms 粒度で形成中バーへ累積して価格を滑らかに描く。
//   価格の唯一の書き手にするため、composition root は稼働時に LiveUpdater/FormingBarUpdater へ
//   suppressPriceUpdate=true を渡す（12 秒より古いデータでの巻き戻しを排除）。
//
// **全時間足で同一設計**（ISSUE-253）: 本プレイヤーは「この tick はどのバーに属するか」を
//   **一切計算しない**。バー time は /live_ticks の応答（barTimes / nowBarTime）としてサーバの
//   唯一源（marketdata.tf_meta.bar_time_unix）から届き、プレイヤーはそれを比較するだけ。
//   かつては floor(秒 / tf秒) で周期を再計算しており、暦周期（1W/1M）を表せないため 1W/1M だけが
//   tick 再生から脱落し、更新粒度が時間足で変わっていた（諸悪の根源＝規則の第 2 定義）。
//   リプレイが `cd.time`（ローソク自身の time）でバーを識別しているのと同じ設計に揃える。
//
// 隔離・注入方針（DOM/ネット/タイマー非依存・FormingBarUpdater と同型の全注入）:
//   - fetchLiveTicks / loadFormingBar / renderer / getTimeframe / setInterval / clearInterval / now を注入。
//   - series.update を呼ぶのは ChartRenderer のみ（renderer.updateLastCandle 経由・隔離維持）。

// 固定遅延（ms）: 実測から poll 間隔 5s + feed 側 lag 最大 5.5s + fetch 最大 1.2s + 余裕 ≒ 12s。
//   これ未満だと feed のまとめ配信（3.8〜5.5s）で枯渇する（prototype 実測 25 polls）。
const DELAY_MS = 12000;
const POLL_MS = 2500;      // フロント → served /live_ticks のポーリング間隔。
const PLAYBACK_MS = 100;   // 「元の時間間隔どおり」に適用する再生粒度。
// poll 1 本の打ち切り時間（ISSUE-263）。in-flight ガード（同時要求数 1）と組み合わさると、
//   返らない要求が 1 本あるだけで poll が恒久停止するため上限を設ける。健全時の実測は
//   5〜141ms・劣化時でも数百 ms なので、poll 間隔の 4 倍を上限とし、再生遅延（12 秒）より
//   短く保って「打ち切って次で回復」が表示の穴にならないようにする。
const FETCH_TIMEOUT_MS = 10000;

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
    fetchTimeoutMs = FETCH_TIMEOUT_MS,
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
    this._fetchTimeoutMs = fetchTimeoutMs;
    this._getComputeSpecs = (typeof getComputeSpecs === 'function') ? getComputeSpecs : null;
    this._getLimit = (typeof getLimit === 'function') ? getLimit : null;
    this._applyFormingTails = (typeof applyFormingTails === 'function') ? applyFormingTails : null;
    this._onBarClose = (typeof onBarClose === 'function') ? onBarClose : null;

    // 再生状態。
    // 未適用 tick [(ms, mid, barTime, tails, tf)] 昇順。barTime は**サーバが返した**当該 tick の
    //   所属バー time（唯一源）。tails は当該 tick 時点の指標末尾値 {instanceId: {系列名: 値}}
    //   （未同梱は null）。tf は要求時の時間足（poll から適用までの 12 秒間に足が変われば捨てる）。
    this._queue = [];
    this._cursor = 0;         // /live_ticks の since カーソル（ms）。
    this._clockOffset = 0;    // serverNowMs - now()（遅延判定をサーバ時計基準に）。
    this._tf = null;          // シード済み tf（getTimeframe 変化で再シード）。
    this._nowBarTime = null;  // サーバが返した「現在のバー time」（履歴後退ガードの材料）。
    this._bar = null;         // 形成中バー {time(sec), open, high, low, close, volume}。
    this._seeding = false;    // /forming_bar シード await 中フラグ（true の間は自己シードを抑止＝🟡4 保持）。
    this._applied = 0;
    this._tailsApplied = 0;   // 指標末尾値を適用した tick 数（HUD・回数一致の監視用）。
    this._lastTickMs = 0;

    this._pollId = null;
    this._playbackId = null;
    // poll の in-flight ガード（ISSUE-257）。poll は setInterval で等間隔に起きるが、応答が
    //   間隔より遅いと**前回の完了を待たずに**次が飛び、同一内容の要求が無制限に積み上がる
    //   （実測: サーバのスレッドが 1,392 本・22,885 本生成／19 時間・全応答が数百 ms へ悪化）。
    //   未完了が 1 本でもある間は新しい要求を出さない＝同時要求数を構成上 1 に固定する。
    //   取りこぼしは起きない: since カーソルは応答時にだけ進むため、次の poll が同じ範囲を続けて取る。
    this._polling = false;
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
  //   応答には (1) 各 tick の所属バー time（barTimes・全時間足で唯一源）と (2) 適用中インスタンスを
  //   申告した場合の指標末尾値（tails）が同梱される。どちらもフロントでは再計算しない。
  //   1 回の失敗は握りつぶしてログ化する（次 poll で回復・unhandledRejection を出さない）。
  async _poll() {
    if (this._polling) {
      return;   // 未完了の要求がある間は出さない（同時要求数を 1 に固定・ISSUE-257）。
    }
    this._polling = true;
    try {
      // 時間足は常に申告する（barTimes の解決に必要＝指標を 1 つも適用していなくても要る）。
      //   適用までの 12 秒間に足が変わったデータは捨てるため、要求時の tf を控えてキューへ持たせる。
      const tf = this._getTimeframe();
      const specs = this._getComputeSpecs ? this._getComputeSpecs() : null;
      const res = await this._fetchLiveTicks(this._cursor, {
        specs: (specs && specs.length) ? specs : null,
        datasetRef: this._datasetRef,
        timeframe: tf,
        limit: this._getLimit ? this._getLimit() : undefined,
        // 末尾値が要る区間（ISSUE-257）。_playback は `serverNow - delayMs` 以前の tick を
        //   1 同期ループで一気に適用するため、その区間の末尾値は最後の 1 点しか画面に出ない。
        //   個別に描かれるのは「地平より新しい tick」＝ delayMs ＋ 次 poll までの猶予（pollMs）。
        //   この区間長を知っているのは再生を持つ本 player だけなので、ここから申告する。
        tailsWithinMs: this._delayMs + this._pollMs,
        // 返らない要求で poll を止めない（ISSUE-263）。中断は失敗扱いで次 poll が回復する。
        timeoutMs: this._fetchTimeoutMs,
      });
      if (!res || res.ok !== true) {
        return;
      }
      if (typeof res.serverNowMs === 'number') {
        this._clockOffset = res.serverNowMs - this._now();
      }
      if (typeof res.nowBarTime === 'number' && tf === this._tf) {
        this._nowBarTime = res.nowBarTime;
      }
      const ticks = res.ticks || [];
      if (ticks.length) {
        // barTimes / tails は ticks と同数・同順（サーバ側 controller の契約）。
        //   保険として tails は tickMs 一致も確認し、ずれていれば当該 tick の tails を落とす。
        const barTimes = Array.isArray(res.barTimes) ? res.barTimes : null;
        const tails = Array.isArray(res.tails) ? res.tails : null;
        for (let i = 0; i < ticks.length; i += 1) {
          const tk = ticks[i];
          const entry = tails && tails[i];
          const values = (entry && entry.tickMs === tk[0]) ? (entry.tails || null) : null;
          const barTime = barTimes && typeof barTimes[i] === 'number' ? barTimes[i] : null;
          this._queue.push([tk[0], tk[1], barTime, values, tf]);
        }
        this._cursor = ticks[ticks.length - 1][0];
      }
    } catch (err) {
      if (typeof console !== 'undefined' && console.warn) {
        console.warn('LiveTickPlayer: poll 失敗（次 poll で回復）:', err && err.message);
      }
    } finally {
      this._polling = false;   // 成功・失敗・早期 return のいずれでも必ず解放する。
    }
  }

  // playback: tf 変化ならシード → serverNow-DELAY 以前の tick を順に形成中バーへ適用。
  async _playback() {
    const tf = this._getTimeframe();
    if (tf !== this._tf) {
      await this._seed(tf);
    }
    const serverNow = this._now() + this._clockOffset;
    const playUntil = serverNow - this._delayMs; // この時刻以前の tick を適用してよい。
    while (this._queue.length && this._queue[0][0] <= playUntil) {
      const [ms, mid, barTime, tails, tickTf] = this._queue.shift();
      this._applyTick(ms, mid, barTime, tails, tickTf);
    }
  }

  // tf 切替・起動時のシード: /forming_bar で形成中バーの初期値を取得し、それをベースに以降の tick を累積。
  //   **全時間足で同一手順**（/forming_bar はロールアップ方式で 1W/1M も供給する）。bar=null
  //   （期間内ティック無し等）は _bar=null のまま、以降の tick から自己シードする。
  //   注記: 初回部分バーの高安は、シード（/forming_bar・最大 60 秒粒度の集約）＋ 12 秒遅延の tick で
  //   構成されるため、シード〜適用開始の隙間分だけ粗い近似になりうる（volume も適用 tick 数の近似）。
  async _seed(tf) {
    this._tf = tf;
    this._nowBarTime = null;   // 新しい足の「現在のバー」は次の poll 応答で確定する。
    // シード確定まで _bar=null かつ _seeding=true に倒す。await（loadFormingBar）中に再入した
    //   _playback は _seeding=true を見て自己シードせず（🟡4＝「新足 × 旧 bar」誤描画の防止）。
    this._bar = null;
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

  // 1 tick を形成中バーへ適用する（**全時間足で同一経路**）。過去バー（履歴＝/candles 済）へは
  //   後退させない。バー識別は引数 barTime（サーバの唯一源）だけで行い、ここで時刻から周期を
  //   計算しない＝日中足・1D・1W/1M の区別がコード上に存在しない。
  //   tails（当該 tick 時点の指標末尾値）は updateLastCandle と**同一同期ブロック**で適用する
  //   （ISSUE-250 Phase 1: 価格と指標が同じ tick で同時に動く＝回数一致の構成的保証）。
  _applyTick(ms, mid, barTime = null, tails = null, tickTf = null) {
    if (typeof barTime !== 'number' || tickTf !== this._tf) {
      return; // バー帰属が未解決 / 足が変わった後に届いた tick → 描かない。
    }
    if (this._bar === null) {
      // 自己シード（参照実装復帰）: /forming_bar seed が null でも、現在のバーの tick から起こす。
      //   _seeding 中（seed await 未確定）は抑止（🟡4）。現在より前のバーの tick は自己シードしない
      //   （/candles 済履歴を後退させない＝既存の後退ガードと同一意図）。確定後は非 null 経路へ移る。
      if (this._seeding) {
        return;
      }
      if (this._nowBarTime !== null && barTime < this._nowBarTime) {
        return; // 現在のバーより前 → 履歴側。自己シードしない。
      }
      this._bar = { time: barTime, open: mid, high: mid, low: mid, close: mid, volume: 1 };
      this._renderer.updateLastCandle(this._bar);
      this._applyTails(tails);
      this._applied += 1;
      this._lastTickMs = ms;
      return;
    }
    if (barTime < this._bar.time) {
      return; // シード済みバーより前の tick は無視（履歴を後退させない）。
    }
    if (barTime === this._bar.time) {
      this._bar.high = Math.max(this._bar.high, mid);
      this._bar.low = Math.min(this._bar.low, mid);
      this._bar.close = mid;
      this._bar.volume += 1; // volume は適用 tick 数の近似（シード値＋適用数）。
    } else {
      // 新しいバー（open=mid）。直前バーはこの瞬間に確定＝バー確定イベントを通知する
      //   （ISSUE-151: 全指標の full 再計算をバー確定駆動にする。coalesce/pending は controller 側）。
      this._bar = { time: barTime, open: mid, high: mid, low: mid, close: mid, volume: 1 };
      if (this._onBarClose) {
        this._onBarClose();
      }
    }
    this._renderer.updateLastCandle(this._bar);
    this._applyTails(tails);
    this._applied += 1;
    this._lastTickMs = ms;
  }

  // 当該 tick 時点の指標末尾値を、直前の updateLastCandle と同一同期ブロックで描く。
  //   バー time は形成中バー（描いたばかりのローソク）の time＝価格と指標が必ず同じ点に載る。
  //   足の切替で無効になった tick は _applyTick 側で既に弾かれている。
  _applyTails(tails) {
    if (!this._applyFormingTails || !tails || this._bar === null) {
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
