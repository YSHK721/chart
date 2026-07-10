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
    windowSec = 6 * 3600,   // 1 チャンクの時間幅（既定 6h）。可視域はこの倍数チャンクで満たす。
    prefetch = 1,           // 可視チャンクの前後に先読みするチャンク数。
    cacheMax = 12,          // LRU 上限チャンク数（メモリ有界）。
    onReady = null,         // チャンク ready 化（先読み完了）時に呼ぶフック（actor が再描画に使う）。
  }) {
    this._client = client;
    this._datasetRef = datasetRef;
    this._windowSec = windowSec;
    this._prefetch = prefetch;
    this._cacheMax = cacheMax;
    this._onReady = typeof onReady === 'function' ? onReady : null;
    this._tf = null;
    this._unit = null;
    this._chunks = new Map();   // chunkStart(sec) -> { state:'loading'|'ready', columns:[] }
    this._lru = [];             // chunkStart の使用順（末尾=最新）。
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

  // tf 変更でキャッシュを破棄する（列の意味が変わるため再利用不可）。
  _resetIfTfChanged(tf) {
    if (tf !== this._tf) {
      this._tf = tf;
      this._unit = null;
      this._chunks.clear();
      this._lru = [];
    }
  }

  async _fetchChunk(tf, cs) {
    if (this._chunks.has(cs)) return; // 取得済み/取得中は再取得しない。
    this._chunks.set(cs, { state: 'loading', columns: [] });
    this._touch(cs);
    const res = await this._client.fetchWindow({
      datasetRef: this._datasetRef, timeframe: tf, from: cs, to: cs + this._windowSec,
    });
    // tf が取得中に変わっていたら破棄（stale）。
    if (tf !== this._tf) return;
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
  ensure(tf, from, to) {
    this._resetIfTfChanged(tf);
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
        this._fetchChunk(tf, cs).catch(() => {});
      }
    }
    this._evict();
    return targets;
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
