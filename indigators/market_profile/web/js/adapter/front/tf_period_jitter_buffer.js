// tf_period_jitter_buffer.js — 時間足毎profile列の「ローリング窓＋ジッターバッファ（先読み＋LRUキャッシュ）」。
//
// 設計: 時間軸を固定幅チャンク（windowSec）へ分割し、チャンク単位で /tf_period_profile を取得してキャッシュ。
//   ensure(可視レンジ)で、可視チャンク＋前後 prefetch 分を背後で取得（先読み）、遠方は LRU で破棄。
//   過去周期は不変（実証済み＝.doc/TICK_IMMUTABILITY_VERIFICATION.md）ゆえキャッシュ無効化は不要
//   （＝スクロールで到達する頃には既に手元にある＝待ち時間ゼロ）。純粋寄り: client/now は注入（DOM/実時間非依存）。
//
// tf 変更でキャッシュ全消去（列の意味が変わる）。列は時刻キーで重複排除して返す。
//
// 取得パラメータ（ISSUE-260）: src（集計方式）と va（バリューエリア比率）は**列の内容を変える**ため
//   キャッシュ同一性の一部である。両者は 1 つの「取得パラメータ」オブジェクトとして受け、正規化・
//   キー比較・URL 引数化を各 1 箇所に閉じる。かつては src だけがスカラ引数で 5 箇所（保持・比較・
//   ensure の spread・refreshAt の spread・stale 照合）に散っており、2 つ目（va）を同じ形で足すと
//   同型コードの複製になる＝取り残しが必ず出る（ISSUE-054 と同型の「一部にしか効かない」事故）。

//: 取得パラメータの正規化（未指定は null）。ここが受理キーの単一情報源。
function _normalizeQuery(q) {
  return { src: q?.src ?? null, va: q?.va ?? null };
}

//: キャッシュ同一性の比較キー（値の組を 1 つの文字列へ）。
function _queryKey(q) {
  return `${q.src ?? ''}|${q.va ?? ''}`;
}

//: client.fetchWindow へ渡す引数（null は載せない＝従来 URL byte 不変）。
function _queryArgs(q) {
  return {
    ...(q.src != null ? { src: q.src } : {}),
    ...(q.va != null ? { va: q.va } : {}),
  };
}

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
    // 取得パラメータ（src=集計方式 / va=バリューエリア比率）。変更でキャッシュ破棄。
    this._query = _normalizeQuery(null);
    this._unit = null;
    this._chunks = new Map();   // chunkStart(sec) -> { state:'loading'|'ready', columns:[] }
    this._lru = [];             // chunkStart の使用順（末尾=最新）。
    this._refreshing = new Set(); // refreshAt 進行中の chunkStart（ISSUE-083: 二重再取得防止）。
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

  // tf または取得パラメータ（src/va）変更でキャッシュを破棄する（列の意味が変わるため再利用不可）。
  //   併せて実効チャンク幅 this._windowSec を tf から再導出する（tf 連動窓）。初回（null→tf）でも走る。
  //   未指定（undefined）は null に正規化＝従来呼び出し（ensure(tf, from, to)）の挙動不変。
  _resetIfKeyChanged(tf, query = null) {
    const q = _normalizeQuery(query);
    if (tf !== this._tf || _queryKey(q) !== _queryKey(this._query)) {
      this._tf = tf;
      this._query = q;
      this._windowSec = this._resolveWindowSec(tf);
      this._unit = null;
      this._chunks.clear();
      this._lru = [];
      this._refreshing.clear(); // 進行中 refreshAt の応答は取得後の tf/取得パラメータ照合で破棄される。
    }
  }

  async _fetchChunk(tf, cs, query = null) {
    if (this._chunks.has(cs)) return; // 取得済み/取得中は再取得しない。
    const q = _normalizeQuery(query);
    this._chunks.set(cs, { state: 'loading', columns: [] });
    this._touch(cs);
    const res = await this._client.fetchWindow({
      datasetRef: this._datasetRef, timeframe: tf, from: cs, to: cs + this._windowSec,
      ..._queryArgs(q),
    });
    // tf/取得パラメータが取得中に変わっていたら破棄（stale）。
    if (tf !== this._tf || _queryKey(q) !== _queryKey(this._query)) return;
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
  //   query（ISSUE-260）: 取得パラメータ ``{src, va}``（省略・null は「サーバ既定に委ねる」）。
  ensure(tf, from, to, query = null) {
    this._resetIfKeyChanged(tf, query);
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
        this._fetchChunk(tf, cs, query).catch(() => {});
      }
    }
    this._evict();
    return targets;
  }

  // ライブ育成（ISSUE-083）: time を含むチャンクを stale-while-revalidate で再取得する。
  //   当日（未完了セッション）は backend がキャッシュせず都度計算するため、再取得のたびに経過分まで
  //   育った列が返る。旧列は応答が届くまで保持（非破壊）し、成功時に差し替えて onReady を発火する。
  //   対象は ready チャンクのみ（未取得は ensure 経路・loading は進行中取得に委ねる）。進行中の
  //   再取得がある間の連打は no-op（二重 fetch しない）。戻り値: 差し替えたら true。
  async refreshAt(time) {
    if (this._tf == null) {
      return false;
    }
    const cs = this._chunkStart(time);
    const c = this._chunks.get(cs);
    if (!c || c.state !== 'ready' || this._refreshing.has(cs)) {
      return false;
    }
    this._refreshing.add(cs);
    const tf = this._tf;
    const q = this._query;
    try {
      const res = await this._client.fetchWindow({
        datasetRef: this._datasetRef, timeframe: tf, from: cs, to: cs + this._windowSec,
        ..._queryArgs(q),
      });
      // tf/取得パラメータが取得中に変わっていたら破棄（stale・キャッシュは既に消えている）。
      if (tf !== this._tf || _queryKey(q) !== _queryKey(this._query)) {
        return false;
      }
      if (!res) {
        return false; // 失敗は旧列を保持（非破壊・次のライブ tick で再試行）。
      }
      if (Number.isFinite(res.unit)) {
        this._unit = res.unit;
      }
      this._chunks.set(cs, { state: 'ready', columns: res.columns || [] });
      this._touch(cs);
      if (this._onReady) {
        this._onReady();
      }
      return true;
    } finally {
      // ISSUE-088 🔵-2: 取得中に tf/取得パラメータが変わった場合、_refreshing は _resetIfKeyChanged が
      //   clear 済み＝新世代の同一 chunkStart エントリを誤削除しない（世代照合）。
      if (tf === this._tf && _queryKey(q) === _queryKey(this._query)) {
        this._refreshing.delete(cs);
      }
    }
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
