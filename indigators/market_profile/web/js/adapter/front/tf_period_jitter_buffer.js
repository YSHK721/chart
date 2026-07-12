// tf_period_jitter_buffer.js — 時間足毎profile列の「ローリング窓＋ジッターバッファ（先読み＋LRUキャッシュ）」。
//
// 設計: 時間軸を固定幅チャンク（windowSec）へ分割し、チャンク単位で /tf_period_profile を取得してキャッシュ。
//   ensure(可視レンジ)で、可視チャンク＋前後 prefetch 分を背後で取得（先読み）、遠方は LRU で破棄。
//   過去周期は不変（実証済み＝.doc/TICK_IMMUTABILITY_VERIFICATION.md）ゆえキャッシュ無効化は不要
//   （＝スクロールで到達する頃には既に手元にある＝待ち時間ゼロ）。純粋寄り: client/now は注入（DOM/実時間非依存）。
//
// tf 変更でキャッシュ全消去（列の意味が変わる）。列は時刻キーで重複排除して返す。

export class TfPeriodJitterBuffer {
  constructor({
    client,
    datasetRef,
    windowSec = 6 * 3600,   // 1 チャンクの時間幅（既定 6h・windowSecForTf 未注入時の固定値）。
    windowSecForTf = null,  // tf→チャンク幅(秒) の関数（注入時は tf 連動＝ISSUE-055 fan-out 抑制）。
                            //   1D は数十日窓へ拡大し 6h 過密（274本・81%空）を数本へ。未注入は windowSec 固定
                            //   （＝既存テスト不変）。tf 変更時に this._windowSec を再導出する。
    prefetch = 1,           // 可視チャンクの前後に先読みするチャンク数。
    cacheMax = 12,          // LRU 上限チャンク数（メモリ有界）。可視 chunk 数を下回ると可視列が破棄され
                            //   ローリングでフラッシュするため、comp root は tf 連動窓に見合う値を注入する。
    onReady = null,         // チャンク ready 化（先読み完了）時に呼ぶフック（actor が再描画に使う）。
  }) {
    this._client = client;
    this._datasetRef = datasetRef;
    this._fixedWindowSec = windowSec;   // windowSecForTf 未注入時に用いる固定幅（後方互換）。
    this._windowSecForTf = typeof windowSecForTf === 'function' ? windowSecForTf : null;
    this._windowSec = windowSec;        // 現在の実効チャンク幅（tf 連動時は _resetIfTfChanged で再導出）。
    this._prefetch = prefetch;
    this._cacheMax = cacheMax;
    this._onReady = typeof onReady === 'function' ? onReady : null;
    this._tf = null;
    this._src = null;           // 集計方式（null=従来 min-unit カウント / 'zp'=超過占有）。変更で破棄。
    this._unit = null;
    this._chunks = new Map();   // chunkStart(sec) -> { state:'loading'|'ready', columns:[] }
    this._lru = [];             // chunkStart の使用順（末尾=最新）。
  }

  // 現在 tf の実効チャンク幅（秒）。windowSecForTf 注入時はそれを、未注入時は固定 windowSec を用いる。
  //   0/非有限を返した場合は固定値へフォールバックする（防御）。
  _resolveWindowSec(tf) {
    if (!this._windowSecForTf) {
      return this._fixedWindowSec;
    }
    const w = Number(this._windowSecForTf(tf));
    return Number.isFinite(w) && w > 0 ? w : this._fixedWindowSec;
  }

  unit() { return this._unit; }

  _chunkStart(t) { return Math.floor(t / this._windowSec) * this._windowSec; }

  _touch(cs) {
    const i = this._lru.indexOf(cs);
    if (i >= 0) this._lru.splice(i, 1);
    this._lru.push(cs);
  }

  _evict() {
    while (this._lru.length > this._cacheMax) {
      const old = this._lru.shift();
      this._chunks.delete(old);
    }
  }

  // tf または src 変更でキャッシュを破棄する（列の意味が変わるため再利用不可）。
  //   併せて実効チャンク幅 this._windowSec を tf から再導出する（tf 連動窓）。初回（null→tf）でも走る。
  //   src 未指定（undefined）は null に正規化＝従来呼び出し（ensure(tf, from, to)）の挙動不変。
  _resetIfKeyChanged(tf, src = null) {
    const s = src ?? null;
    if (tf !== this._tf || s !== this._src) {
      this._tf = tf;
      this._src = s;
      this._windowSec = this._resolveWindowSec(tf);
      this._unit = null;
      this._chunks.clear();
      this._lru = [];
    }
  }

  async _fetchChunk(tf, cs, src = null) {
    if (this._chunks.has(cs)) return; // 取得済み/取得中は再取得しない。
    this._chunks.set(cs, { state: 'loading', columns: [] });
    this._touch(cs);
    const res = await this._client.fetchWindow({
      datasetRef: this._datasetRef, timeframe: tf, from: cs, to: cs + this._windowSec,
      ...(src != null ? { src } : {}),
    });
    // tf/src が取得中に変わっていたら破棄（stale）。
    if (tf !== this._tf || (src ?? null) !== this._src) return;
    if (res) {
      if (this._unit == null && Number.isFinite(res.unit)) this._unit = res.unit;
      this._chunks.set(cs, { state: 'ready', columns: res.columns || [] });
      if (this._onReady) this._onReady(); // 先読み完了 → actor へ再描画を促す（スクロール前に埋まる）。
    } else {
      this._chunks.delete(cs); // 失敗は消して次回再試行可能に。
      const i = this._lru.indexOf(cs);
      if (i >= 0) this._lru.splice(i, 1);
    }
  }

  // 可視レンジ [from, to] を満たすチャンクを確保し（先読み含む）、ready チャンクの取得を発火する。
  //   返り値は「発火した/確保対象の chunkStart 昇順配列」（テスト・監視用）。実データは getColumns で取る。
  ensure(tf, from, to, src = null) {
    this._resetIfKeyChanged(tf, src);
    const first = this._chunkStart(from);
    const last = this._chunkStart(to);
    const targets = [];
    for (let cs = first - this._prefetch * this._windowSec;
         cs <= last + this._prefetch * this._windowSec;
         cs += this._windowSec) {
      targets.push(cs);
    }
    for (const cs of targets) {
      if (this._chunks.has(cs)) {
        this._touch(cs);
      } else {
        // 背後で取得（await しない＝先読み・スクロール前に埋める）。失敗は握りつぶす。
        this._fetchChunk(tf, cs, src ?? null).catch(() => {});
      }
    }
    this._evict();
    return targets;
  }

  // 可視レンジ [from, to] を覆う全チャンクが ready か（先読み分は判定に含めない＝表示に必要な分のみ）。
  //   ISSUE-069: actor が「揃ってから一括表示」の完了判定に使う。1 つでも未 ready/未取得なら false。
  allReady(from, to) {
    const first = this._chunkStart(from);
    const last = this._chunkStart(to);
    for (let cs = first; cs <= last; cs += this._windowSec) {
      const c = this._chunks.get(cs);
      if (!c || c.state !== 'ready') {
        return false;
      }
    }
    return true;
  }

  // 現在キャッシュ済み（ready）の列のうち [from, to] に入る列を time 昇順・重複排除で返す。
  getColumns(from, to) {
    const out = new Map(); // time -> column（重複排除）。
    for (const { state, columns } of this._chunks.values()) {
      if (state !== 'ready') continue;
      for (const c of columns) {
        if (c.time >= from && c.time <= to) out.set(c.time, c);
      }
    }
    return [...out.values()].sort((a, b) => a.time - b.time);
  }
}
