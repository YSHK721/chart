// CandleFeed（adapter/front/candle_feed.js）— ローソクデータ所有と更新の協働クラス
//   （SOLID 是正 🔴-2: chart_renderer.js から 1:1 抽出）。
//
// ChartRenderer（ファサード）の内部協働子。共有状態（_chart / _mainSeries / _baseCandles /
//   _lastBar / _lastTrimIdx / _replayViewSpan / _profileMarginFraction / _lastRightOffsetBars /
//   _onCandlesChanged）は ChartRenderer が所有し続け、本クラスはコンストラクタで注入された
//   host 参照経由で読み書きする（協働子間の直接依存は作らない）。公開面（ChartRenderer の
//   public メソッド・export）は不変で、実体だけが本ファイルへ移動した。

// ISSUE-114/115: チャート右端の常設余白（チャート幅比率）。最新足の右側に常時 幅×この比率 の
//   空きを px 基準で確保する。lwc rightOffset は論理バー単位のため、バー数固定では全体表示
//   （微小 barSpacing）で余白が消える（ISSUE-115）＝_syncRightOffset が width×frac÷barSpacing を
//   都度再計算して適用する。MP プロファイル右マージン（setRightMarginFraction）とは比率の max 合成。
const BASE_RIGHT_MARGIN_FRACTION = 0.05;

export class CandleFeed {
  // host: ChartRenderer インスタンス（共有状態の所有者）。
  constructor(host) {
    this._h = host;
  }

  // 時間足切替: メインローソク系列のデータを差し替え、可視範囲を全体へ合わせる。
  setCandles(candles) {
    const arr = candles ?? [];
    this._h._mainSeries.setData(arr);
    // v6: 基準 candles を全置換で更新（per-bar 減光/復元の元集合）。
    this._h._baseCandles = arr;
    // 増分2: 基準 candles が入れ替わったのでトリム位置キャッシュをリセット（次の setCandleTrim で再 set）。
    this._h._lastTrimIdx = null;
    // 価格スケールの手動状態（軸ドラッグ/ホイールズーム）には本メソッドでは触れない。setCandles は
    //   replay_ui の足リビールで毎バー呼ばれるため、ここで解除するとバー境界のたびにズームが消える
    //   （旧実装の不具合）。解除点は「価格軸 dblclick（resetPriceZoom）」と「時間足切替
    //   （TimeframeController.setTimeframe が resetPriceZoom を呼ぶ・ISSUE-113 ユーザー裁定）」の 2 点で、
    //   いずれも呼び出し側の責務。手動スケールは lwc 内部状態なので setData 全置換でも lwc 自身が保持する。
    this._h._replayViewSpan = null; // スクラブ span キャッシュのみリセット（価格ズームとは別物）。
    // ISSUE-114: fitContent は全データを幅いっぱいへフィットし右余白を潰すため、直後に
    //   scrollToRealTime で常設 rightOffset を反映する（barSpacing は fitContent の結果を維持）。
    //   scrollToRealTime 非提供（後方互換 Fake）は no-op（下の呼出し済みガードに委ねる）。
    // 読み取り欄の最新足の単一源を更新（配列末尾の足）。空配列なら null。
    this._h._lastBar = arr.length > 0 ? arr[arr.length - 1] : null;
    const ts = this._h._chart.timeScale();
    ts.fitContent();
    // fitContent で barSpacing が確定した直後に余白バー数を再計算してから右端へスクロール
    //   （ISSUE-115: px 基準余白の反映順）。
    this._syncRightOffset({ force: true });
    if (typeof ts.scrollToRealTime === 'function') {
      ts.scrollToRealTime();
    }
    // v6: candle 変更を observer へ通知（ChartRenderer 起点同期＝hover 中なら highlight 解除へ）。
    this._h._onCandlesChanged();
  }

  // 右端余白の単一権威（ISSUE-115）: 実効比率 = max(常設 5%, MP プロファイル余白率) を px 換算し、
  //   rightOffset バー数 = width×frac ÷ barSpacing（小数のまま＝px 幅一定）で適用する。
  //   ISSUE-164（ユーザー裁定）: ズーム（可視範囲変化）からは呼ばれない（購読は撤去済み・
  //   ビューを動かしてよいのは明示イベントのみ）。呼出点は初期表示・setCandles（時間足切替）・
  //   MP 余白率変更の 3 点。同値（±0.01 バー）スキップは再適用ループ防止として温存。
  //   width 未確定（レイアウト前）は何もしない。
  _syncRightOffset({ force = false } = {}) {
    const ts = typeof this._h._chart.timeScale === 'function' ? this._h._chart.timeScale() : null;
    if (!ts || typeof ts.applyOptions !== 'function') {
      return;
    }
    // ISSUE-148: 過去閲覧中（右端から離れている）は rightOffset を再適用しない。lwc では
    //   rightOffset の適用が「最新足基準へのスクロール」として働くため、ズーム（barSpacing 変化）
    //   由来の再同期が『過去へ遡って拡大すると最新足へ戻る』ジャンプになる。右端復帰
    //   （スクロール/FOLLOW）で可視範囲購読が再発火し、その時点で余白は再同期される。
    //   scrollPosition()= 最新足から右端までの距離（バー・過去閲覧中は負）。非提供 Fake は従来どおり。
    if (typeof ts.scrollPosition === 'function' && ts.scrollPosition() < -0.5) {
      return;
    }
    const w = typeof ts.width === 'function' ? ts.width() : 0;
    const bs = (typeof ts.options === 'function' && ts.options() && ts.options().barSpacing) || 6;
    if (!(w > 0) || !(bs > 0)) {
      return;
    }
    const frac = Math.max(BASE_RIGHT_MARGIN_FRACTION, this._h._profileMarginFraction || 0);
    const bars = (w * frac) / bs;
    if (!force && this._h._lastRightOffsetBars !== null
        && Math.abs(bars - this._h._lastRightOffsetBars) < 0.01) {
      return;
    }
    this._h._lastRightOffsetBars = bars;
    ts.applyOptions({ rightOffset: bars });
  }

  // ライブ更新: 最新足を差分反映する（series.update を呼ぶのは本所のみ・upstream 隔離維持）。
  //   既存 time なら上書き、新しい time なら追加（lightweight-charts の update 仕様）。
  updateLastCandle(candle) {
    // ★スナップショット（トリム）中は series へ現在足を入れない。トリム系列（過去 T 時点まで）へ
    //   ライブの現在足（time=now・現在価格）を append すると、トリム範囲外の不可解な位置にバーが
    //   プロットされる（放置でライブ更新が発火し発生・実機バグの修正）。基準 _baseCandles は更新し、
    //   トリム解除後に最新足へ正しく復帰できるようにする（読み取り欄は T 時点のまま維持）。
    if (this._h._lastTrimIdx !== null) {
      this._mergeBaseCandle(candle);
      return;
    }
    // 後退ガード（ISSUE-096）: 時間足切替（setCandles で系列＋_lastBar が新周期へ差替）直後に、
    //   インフライトの旧周期ライブ tick が実系列末尾より古い time で来ると、lightweight-charts の
    //   series.update が "Cannot update oldest data" を投げる（player 内 _bar 基準の後退ガードでは
    //   系列側が差し替わったケースを捕捉できない）。実系列末尾（_lastBar）より古い time のライブ足は
    //   skip する（旧周期の stale tick は新周期 base へ混ぜない）。同/新 time は従来どおり反映する。
    if (this._h._lastBar != null && typeof candle.time === 'number'
        && typeof this._h._lastBar.time === 'number' && candle.time < this._h._lastBar.time) {
      return;
    }
    this._h._mainSeries.update(candle);
    // 最新足の単一源を更新し、hover していない読み取り表示が古くならないよう DTO を再発火する。
    this._h._lastBar = candle;
    // v6: 基準 candles の末尾を差分反映（同 time は置換・新 time は追加）。減光の元集合を同期。
    this._mergeBaseCandle(candle);
    this._h._emitReadout(null);
    // v6: candle 変更を observer へ通知（live tick でも hover 中なら highlight 解除させる）。
    this._h._onCandlesChanged();
  }

  // ライブ欠落補完（ISSUE-106）: タブ休止（PC スリープ・バックグラウンドタイマー抑制）や更新停止で
  //   足境界を 2 本以上またぐと、差分経路（updateLastCandle＝末尾 1 本前提）では途中の確定足を挿入
  //   できず（lwc の series.update は末尾より古い time を受け付けない）恒久的な歯抜けになる。
  //   fetched（サーバー正の /candles 全件・time 昇順）に「実系列末尾より新しい足が 2 本以上」または
  //   「既知範囲内の未保持 time（穴）」を検出したときのみ setData 全置換で再同期する。
  //   通常運転（差分 0〜1 本・全 time 既知）は何もせず false（従来差分経路のまま挙動不変）。
  //   fitContent は呼ばない（ユーザーのズーム・スクロール位置を保持）。
  //   現在足の後退防止（ISSUE-049 系）: 置換前末尾（LiveTickPlayer が書いた最新値）の time が
  //   新データ末尾以上なら置換後に復元する（最大 60 秒古いサーバー値で価格を巻き戻さない）。
  //   スナップショット（トリム）中は不介入（updateLastCandle と同方針・解除後の tick で再同期される）。
  resyncMissedCandles(candles) {
    const arr = Array.isArray(candles) ? candles : [];
    if (arr.length === 0 || this._h._lastTrimIdx !== null) {
      return false;
    }
    const base = this._h._baseCandles;
    if (!base || base.length === 0 || this._h._lastBar == null) {
      return false; // 初期ロード前は setCandles（全置換）の責務。
    }
    const lastTime = this._h._lastBar.time;
    const known = new Set(base.map((b) => b.time));
    let newer = 0;
    let hole = false;
    for (const c of arr) {
      if (c.time > lastTime) {
        newer += 1;
      } else if (!known.has(c.time)) {
        hole = true;
      }
    }
    if (newer < 2 && !hole) {
      return false;
    }
    const prevLast = this._h._lastBar;
    const newest = arr[arr.length - 1];
    this._h._mainSeries.setData(arr);
    this._h._baseCandles = arr;
    this._h._lastBar = newest;
    if (typeof prevLast.time === 'number' && typeof newest.time === 'number'
        && prevLast.time >= newest.time) {
      this._h._mainSeries.update(prevLast);
      this._h._lastBar = prevLast;
      this._mergeBaseCandle(prevLast);
    }
    this._h._emitReadout(null);
    this._h._onCandlesChanged();
    return true;
  }

  // v6: 基準 candles の末尾足を差分マージする（updateLastCandle 用）。基準未保持なら単一要素配列。
  _mergeBaseCandle(candle) {
    if (!candle) {
      return;
    }
    const base = this._h._baseCandles ? this._h._baseCandles.slice() : [];
    if (base.length > 0 && base[base.length - 1].time === candle.time) {
      base[base.length - 1] = candle;
    } else {
      base.push(candle);
    }
    this._h._baseCandles = base;
  }
}
