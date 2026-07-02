// market_profile_actor.js — Market Profile の薄い制御アクター（取得→primitive 反映・トグル状態）。
//
// 設計入力: 依頼「プロファイルを取得して primitive に反映する薄い制御・ES Modules・既存層構造」。
//   trade_markers_renderer.js と同層（adapter/front）。client（取得）と primitive（描画）を注入し、
//   本アクターは「トグル状態の保持」と「有効時の取得→反映」の編成のみを担う（SRP）。
//   依存はすべて抽象（duck-typing）に向け、composition root が具象を注入する（DIP）。
//
// 非破壊方針: primitive は初回有効化まで mainSeries へ attach しない（OFF 時はチャートに一切触れない）。
//   取得失敗（client が null）時も既存描画へ干渉せず、前回 profile を保持する。

// 増分2 定数（試作 prototype_260630-01 と一致）。
const ROLL_BARS = 60; // ローリング窓の本数（from = T - ROLL_BARS*bar_sec）。
// timeframe → 足の秒長（from の窓幅算出用）。未知/None は 1D 相当（backend _TF_BAR_SEC と対応）。
const TF_BAR_SEC = {
  '1m': 60, '5m': 300, '15m': 900, '30m': 1800,
  '1h': 3600, '4h': 14400, '1D': 86400, '1W': 604800, '1M': 2592000,
};

export class MarketProfileActor {
  // client: fetchProfile(context)->profile|null。primitive: setProfile/setVisible。
  // mainSeries: attachPrimitive（v5・非提供時は attach を skip＝後方互換）。
  // getContext: ()->{datasetRef,timeframe,limit,...}（取得時点の現在チャート状態を遅延読み取り）。
  // replayBar: リプレイスライダバー（任意注入・setVisible/setCandles/mode/isSnapshot）。未注入時は no-op。
  // getCandles: ()->candles（バーの min/max・index→time の元。renderer.getCandles を配線）。未注入時は空。
  // renderer: 増分2 スナップショットのローソクトリム源（setCandleTrim(time|null)）。未注入時はトリムしない。
  constructor({ client, primitive, mainSeries, getContext, replayBar, getCandles, renderer } = {}) {
    this._client = client;
    this._primitive = primitive;
    this._mainSeries = mainSeries;
    this._replayBar = replayBar ?? null;
    this._renderer = renderer ?? null;
    this._getCandles = typeof getCandles === 'function' ? getCandles : () => [];
    this._getContext = typeof getContext === 'function' ? getContext : () => ({});
    this._enabled = false;
    this._attached = false;
    // 取得パラメータ（bins/va/src/range）。setParams で更新し refresh 時に getContext へ重畳する。
    //   未設定時は空＝getContext のみ（サーバ既定・後方互換）。
    this._params = {};
    // リプレイ（増分1）状態。replay=ON でバー表示・T スクラブで to 付き再取得（coalesce）。
    this._replay = false;
    this._replayTo = null;      // 現在の T（UNIX 秒）。null=最新（全期間）。
    this._scrubRunning = false;  // in-flight フラグ（scrubProfile coalesce・移植元 prototype_260630-01）。
    this._scrubQueued = null;    // in-flight 中に来た最後の T（末尾実行用）。
  }

  // 増分2: リプレイ取得の追加コンテキスト（from/today）を現在のモード/スナップショット状態から組む。
  //   - ローリングモード: from = T - ROLL_BARS*bar_sec（T 直前 60 本の窓）。アンカーは from を載せない。
  //   - スナップショット ON: today=true（当日強調用の today[]/today_max を要求）。OFF は載せない。
  //   移植元 prototype_260630-01 params()（asofmode/asoftrim）。replayBar 未注入時は空（後方互換）。
  _replayExtra(time) {
    const extra = {};
    if (!this._replayBar) {
      return extra;
    }
    const mode = typeof this._replayBar.mode === 'function' ? this._replayBar.mode() : 'anchor';
    if (mode === 'rolling' && time != null) {
      const tf = this._getContext().timeframe;
      const barSec = TF_BAR_SEC[tf] ?? 86400;
      extra.from = time - ROLL_BARS * barSec;
    }
    if (typeof this._replayBar.isSnapshot === 'function' && this._replayBar.isSnapshot()) {
      extra.today = true;
    }
    return extra;
  }

  // 増分2: スナップショット状態を反映する（ローソクトリム＋primitive の減光/today 描画）。
  //   snapshot ON: ローソクを T までトリム（renderer.setCandleTrim(T)）・primitive.setSnapshot(true)。
  //   snapshot OFF: トリム解除（setCandleTrim(null)）・primitive.setSnapshot(false)。
  //   renderer/primitive の該当メソッド非提供時は skip（後方互換）。
  _applySnapshot(time) {
    const on = !!(this._replayBar && typeof this._replayBar.isSnapshot === 'function'
      && this._replayBar.isSnapshot());
    if (this._renderer && typeof this._renderer.setCandleTrim === 'function') {
      this._renderer.setCandleTrim(on && time != null ? time : null);
    }
    if (this._primitive && typeof this._primitive.setSnapshot === 'function') {
      this._primitive.setSnapshot(on);
    }
  }

  isEnabled() {
    return this._enabled;
  }

  // 取得パラメータ（bins/va/src/range）を設定する。null/undefined のキーは無視する
  //   （getContext の値やサーバ既定を潰さない）。次回 refresh から反映される。
  //   range（レンジpt）は client.buildMarketProfileUrl が barw へ写像する（'auto' は付与しない）。
  setParams(params = {}) {
    const next = {};
    for (const key of ['bins', 'va', 'src', 'range', 'resmode']) {
      if (params[key] != null) {
        next[key] = params[key];
      }
    }
    this._params = next;
    // replay トグル（増分1）。明示指定時のみ反映する（undefined は現状維持）。
    if (params.replay != null) {
      this._setReplay(!!params.replay);
    }
  }

  // replay ON/OFF を反映する。ON: バー表示（candles は composition root が別途 setCandles 済み）。
  //   OFF: バー非表示・T 縦線消去・T をリセット（全期間へ復帰）。移植元 prototype_260630-01。
  _setReplay(on) {
    this._replay = on;
    if (this._replayBar) {
      // ON 時に最新 candles をバーへ供給（min/max・index→time の元）。timeframe 切替後も現在足に追従。
      if (on && typeof this._replayBar.setCandles === 'function') {
        this._replayBar.setCandles(this._getCandles());
      }
      if (typeof this._replayBar.setVisible === 'function') {
        this._replayBar.setVisible(on);
      }
    }
    if (!on) {
      // OFF: 当時カーソルを解除し、T 縦線を消す（primitive.setCursorTime(null)）。
      this._replayTo = null;
      this._scrubQueued = null;
      if (this._primitive && typeof this._primitive.setCursorTime === 'function') {
        this._primitive.setCursorTime(null);
      }
      // 増分2: スナップショットのローソクトリムを解除し（全ローソク復元）、primitive の減光を消す。
      if (this._renderer && typeof this._renderer.setCandleTrim === 'function') {
        this._renderer.setCandleTrim(null);
      }
      if (this._primitive && typeof this._primitive.setSnapshot === 'function') {
        this._primitive.setSnapshot(false);
      }
      // 防御: スワイプ捕捉中（setUserInteraction(false)）のまま gear で OFF にされても
      // チャート操作を必ず復元する（冪等・未捕捉時も無害）。
      if (this._renderer && typeof this._renderer.setUserInteraction === 'function') {
        this._renderer.setUserInteraction(true);
      }
    }
  }

  // 増分2: リプレイバーのモード（アンカー/ローリング）・スナップショット変更を受け、現在 T で再取得する。
  //   replayBar.onChange から配線する。無効時（replay OFF / disabled / T 未設定）は no-op。
  async onReplayControlsChange() {
    if (!this._enabled || !this._replay || this._replayTo == null) {
      return;
    }
    await this.setReplayCursor(this._replayTo);
  }

  isReplay() {
    return this._replay;
  }

  // リプレイ T スクラブ: T（対応足の time・UNIX 秒）を当時カーソルに設定し、to=T で当時プロファイルを
  //   再取得して primitive へ反映する。連続スクラブは coalesce（in-flight 中は最後の T だけ末尾実行＝
  //   移植元 prototype_260630-01 scrubProfile）。無効時（replay OFF / disabled）は no-op。
  async setReplayCursor(time) {
    if (!this._enabled || !this._replay) {
      return;
    }
    this._replayTo = time;
    // T 縦線は即時反映（fetch 完了を待たずカーソルを動かす＝プロト applyAsofView 相当）。
    if (this._primitive && typeof this._primitive.setCursorTime === 'function') {
      this._primitive.setCursorTime(time);
    }
    // 増分2: スナップショットのローソクトリム/減光を即時反映（fetch を待たず＝プロト applyAsofView）。
    this._applySnapshot(time);
    // coalesce: in-flight 中は最後の要求だけを queue し、完了後に末尾実行する。
    if (this._scrubRunning) {
      this._scrubQueued = time;
      return;
    }
    this._scrubRunning = true;
    try {
      await this._fetchAt(time);
    } finally {
      this._scrubRunning = false;
    }
    if (this._scrubQueued != null) {
      const last = this._scrubQueued;
      this._scrubQueued = null;
      await this.setReplayCursor(last); // 末尾実行（最後の T のみ）。
    }
  }

  // to=T（＋増分2 の from/today）を重畳して 1 回取得し、profile を反映する（null は前回描画保持）。
  async _fetchAt(time) {
    const profile = await this._client.fetchProfile({
      ...this._getContext(), ...this._params, to: time, ...this._replayExtra(time),
    });
    if (profile) {
      this._primitive.setProfile(profile);
    }
  }

  // トグル。ON: 初回のみ attach → 取得して反映 → 表示。OFF: 非表示（取得しない）。
  async setEnabled(enabled) {
    this._enabled = !!enabled;
    if (this._enabled) {
      this._ensureAttached();
      await this.refresh();
      this._primitive.setVisible(true);
    } else {
      this._primitive.setVisible(false);
    }
  }

  // 現在のコンテキストで再取得し反映する（有効時のみ）。null は反映しない（前回描画保持）。
  async refresh() {
    if (!this._enabled) {
      return;
    }
    // getContext（datasetRef/timeframe/…）へ setParams の bins/va/src/range を重畳して取得する。
    //   getContext が limit(recentBars) を含んでも client.buildMarketProfileUrl が破棄する（全期間集計）。
    const profile = await this._client.fetchProfile({ ...this._getContext(), ...this._params });
    if (profile) {
      this._primitive.setProfile(profile);
    }
  }

  // primitive を mainSeries へ一度だけ attach する（attachPrimitive 非提供時は skip）。
  _ensureAttached() {
    if (this._attached) {
      return;
    }
    if (this._mainSeries && typeof this._mainSeries.attachPrimitive === 'function') {
      this._mainSeries.attachPrimitive(this._primitive);
      this._attached = true;
    }
  }

  // primitive を mainSeries から取り外す（detachPrimitive 非提供時は skip＝後方互換）。
  //   凡例からの削除（close）で呼び、次回有効化で再 attach できるよう _attached を戻す。
  detach() {
    if (this._attached && this._mainSeries && typeof this._mainSeries.detachPrimitive === 'function') {
      this._mainSeries.detachPrimitive(this._primitive);
    }
    this._attached = false;
  }
}
