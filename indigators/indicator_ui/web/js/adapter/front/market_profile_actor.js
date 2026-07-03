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
// MP 表示中の右マージン（プロファイル専用領域＝試作 PROFILE_FRAC。バーとローソクの重なり回避）。
const PROFILE_MARGIN_FRACTION = 0.30;
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
    // sessions（日別プロファイル分割）ON/OFF。既定 false（通常の累積プロファイル・後方互換）。
    this._sessions = false;
    // 単日フォーカス（列クリックで拡大）。null=一覧／date=その 1 日を全幅表示。sessions OFF・
    //   setEnabled(false)・detach で必ず null へ戻す（拡大状態を安全に解除・後方互換）。
    this._sessionFocus = null;
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
    // sessions（日別プロファイル分割）トグル。明示指定時のみ反映する（undefined は現状維持）。
    //   true で refresh 時に context へ sessions:true を載せ、応答の profile.sessions を primitive/renderer へ
    //   反映する。false で通常モードへ復帰（primitive.setSessions(null)・ローソク透明化解除）。
    if (params.sessions != null) {
      this._sessions = !!params.sessions;
    }
    // replay トグル（増分1）。明示指定時のみ反映する（undefined は現状維持）。
    if (params.replay != null) {
      this._setReplay(!!params.replay);
    }
  }

  // 単日フォーカス（列クリックで拡大）を primitive へ伝播する。date=拡大／null=一覧。
  //   sessions 表示中でないとき（this._sessions=false）は状態だけ保持し描画には影響しない
  //   （primitive は sessions non-null のときのみ focus を描く）。composition の click 配線から呼ぶ。
  //   本タスク: date 指定時は day 付きで単発再取得し、応答の day_path を primitive へ渡す（左70%＝
  //   その日のティック推移）。取得失敗/day_path 無し（非 tick ref 等）は path 無しフォールバック
  //   （現行の全幅ヒストグラム）。解除（null）で path をクリアする。await 不要（即時 focus は同期反映）。
  async setSessionFocus(date) {
    this._sessionFocus = date == null ? null : date;
    // 即時反映: focus 状態（path はまだ無し）・価格軸ズーム・時間軸ロックは fetch を待たずに適用する。
    if (this._primitive && typeof this._primitive.setSessionFocus === 'function') {
      this._primitive.setSessionFocus(this._sessionFocus, null);
    }
    // 価格軸をフォーカス日の価格帯へ自動ズーム（全期間レンジのままだと形が潰れて検証できない）。
    //   primitive.sessionPriceRange（tpo>0 の bin 価格 min/max±半ビン）を 4% パディングして
    //   renderer.setPriceAutoscaleOverride へ。解除（null）で既定オートスケールへ復帰。
    this._applyFocusPriceZoom(this._sessionFocus);
    // 単日拡大中は時間軸（下部ルーラ）の操作をロックする（横パン/時間軸ズーム停止・価格軸は残す）。
    //   focus=date でロック、focus=null で lwc 既定へ復元。renderer 非提供時は no-op。
    this._applyTimeScaleLock(this._sessionFocus != null);
    // 解除（null）は path 無しで確定＝ここで終了（fetch しない）。
    if (this._sessionFocus == null) {
      return;
    }
    // day 付きで単発再取得（sessions コンテキスト＋day・coalesce 不要）→ day_path を primitive へ伝播。
    await this._fetchDayPath(this._sessionFocus);
  }

  // 単日拡大の左70%パス（day_path）を day 付き単発取得して primitive へ渡す（本タスク）。
  //   sessions コンテキストを維持したまま day=<date> を重畳して 1 回だけ fetch する。取得中に focus が
  //   変わった/解除された場合は stale 反映を避ける（応答時点の focus と date が一致するときだけ適用）。
  //   取得失敗（null）・day_path 無しは path 無しフォールバック（primitive は全幅ヒストグラムへ）。
  async _fetchDayPath(date) {
    let profile = null;
    if (this._client && typeof this._client.fetchProfile === 'function') {
      profile = await this._client.fetchProfile({
        ...this._getContext(), ...this._params, ...this._sessionsExtra(), day: date,
      });
    }
    if (this._sessionFocus !== date) {
      return; // focus が変わった/解除された（stale 応答は捨てる）。
    }
    const dayPath = profile && Array.isArray(profile.day_path) ? profile.day_path : null;
    if (this._primitive && typeof this._primitive.setSessionFocus === 'function') {
      this._primitive.setSessionFocus(date, dayPath);
    }
  }

  // 単日拡大中の時間軸ロックを renderer へ委譲する（on=true でロック／false で復元）。
  //   renderer.setTimeScaleLock 非提供時は no-op（後方互換）。冪等。
  _applyTimeScaleLock(on) {
    if (this._renderer && typeof this._renderer.setTimeScaleLock === 'function') {
      this._renderer.setTimeScaleLock(!!on);
    }
  }

  // MP 表示中の右マージン（プロファイル専用領域）を renderer へ委譲する。
  //   on=true で PROFILE_MARGIN_FRACTION（=0.30）ぶんローソクを左へ寄せ、false で復元。
  //   renderer.setRightMarginFraction 非提供時は no-op（後方互換）。冪等。
  _applyProfileMargin(on) {
    if (this._renderer && typeof this._renderer.setRightMarginFraction === 'function') {
      this._renderer.setRightMarginFraction(on ? PROFILE_MARGIN_FRACTION : null);
    }
  }

  // フォーカス日の価格帯へ価格軸をズーム（date=null で解除）。renderer/primitive 非提供時は no-op。
  _applyFocusPriceZoom(date) {
    if (!this._renderer || typeof this._renderer.setPriceAutoscaleOverride !== 'function') {
      return;
    }
    let range = null;
    if (date != null && this._primitive && typeof this._primitive.sessionPriceRange === 'function') {
      const r = this._primitive.sessionPriceRange(date);
      if (r) {
        const pad = (r.max - r.min) * 0.04;
        range = { min: r.min - pad, max: r.max + pad };
      }
    }
    this._renderer.setPriceAutoscaleOverride(range);
  }

  // 現在の単日フォーカス状態（date|null）を返す。click 配線が「focus 中→一覧／一覧中→拡大」を分岐する。
  sessionFocus() {
    return this._sessionFocus;
  }

  // sessions（日別プロファイル分割）を表示中か。click 配線が「sessions 中のみクリック拡大を有効化」する。
  //   MP 有効かつ sessions トグル ON のときだけ true（OFF/通常モードでは既存クリック挙動を変えない）。
  isSessions() {
    return this._enabled && !!this._sessions;
  }

  // ヒットテスト委譲: xRatio（クリックx / コンテナ CSS 幅・0..1）→ 一覧の列 date（範囲外/非表示は null）。
  //   primitive が直近描画の一覧レイアウトで index→date を解く（DPR 非依存）。composition から呼ぶ。
  sessionDateAt(xRatio) {
    if (this._primitive && typeof this._primitive.sessionDateAt === 'function') {
      return this._primitive.sessionDateAt(xRatio);
    }
    return null;
  }

  // 単日フォーカスを解除する（null 伝播）。sessions OFF・setEnabled(false)・detach から呼ぶ共通処理。
  //   primitive 非提供/未フォーカス時も無害（冪等）。
  _clearSessionFocus() {
    if (this._sessionFocus == null) {
      return;
    }
    this._sessionFocus = null;
    if (this._primitive && typeof this._primitive.setSessionFocus === 'function') {
      this._primitive.setSessionFocus(null, null); // path もクリア（左70%パスを残さない）。
    }
    this._applyFocusPriceZoom(null); // 価格軸ズームも解除（既定オートスケールへ復帰）。
    this._applyTimeScaleLock(false); // 時間軸ロックも必ず解除（既定へ復元）。
  }

  // sessions（日別プロファイル分割）を primitive/renderer へ反映する。
  //   on: primitive.setSessions(profile.sessions)・renderer.setCandleTransparency(true)（ローソク透明化）。
  //   off: primitive.setSessions(null)（通常モード）・renderer.setCandleTransparency(false)（復元）。
  //   該当メソッド非提供時は skip（後方互換）。移植元 prototype_260630-01 drawSessions。
  _applySessions(profile) {
    const on = !!this._sessions;
    // sessions OFF 時は単日フォーカスを必ず解除する（拡大状態を残さない）。
    if (!on) {
      this._clearSessionFocus();
    }
    if (this._primitive && typeof this._primitive.setSessions === 'function') {
      const list = on && profile && Array.isArray(profile.sessions) ? profile.sessions : null;
      // sessions_total（キャップ前の実日数）を第 2 引数で渡す（注記「直近N/全M日」の M・修正1）。
      //   未提供時は primitive 側で受信長へフォールバック（後方互換）。
      const total = on && profile && profile.sessions_total != null ? profile.sessions_total : null;
      this._primitive.setSessions(list, total);
    }
    if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(on);
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

  // to=T（＋増分2 の from/today／sessions）を重畳して 1 回取得し、profile を反映する（null は前回描画保持）。
  async _fetchAt(time) {
    const profile = await this._client.fetchProfile({
      ...this._getContext(), ...this._params, to: time,
      ...this._replayExtra(time), ...this._sessionsExtra(),
    });
    if (profile) {
      this._primitive.setProfile(profile);
      this._applySessions(profile);
    }
  }

  // sessions ON 時のみ context へ sessions:true を載せる（client が &sessions=1 を付与）。OFF は載せない（後方互換）。
  _sessionsExtra() {
    return this._sessions ? { sessions: true } : {};
  }

  // トグル。ON: 初回のみ attach → 取得して反映 → 表示。OFF: 非表示（取得しない）。
  async setEnabled(enabled) {
    this._enabled = !!enabled;
    if (this._enabled) {
      this._ensureAttached();
      // ローソクを左へ寄せ右側をプロファイル専用領域に（試作 PROFILE_FRAC＝重なり回避・実機FB）。
      this._applyProfileMargin(true);
      await this.refresh();
      this._primitive.setVisible(true);
    } else {
      this._primitive.setVisible(false);
      this._applyProfileMargin(false); // 右マージン復元（ローソクを従来位置へ）。
      // 単日フォーカスを解除する（MP OFF で拡大状態を残さない・安全に一覧へ）。
      this._clearSessionFocus();
      // sessions のローソク透明化・分割描画を必ず復元する（MP OFF で従来のローソク/累積へ戻す）。
      if (this._primitive && typeof this._primitive.setSessions === 'function') {
        this._primitive.setSessions(null);
      }
      if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
        this._renderer.setCandleTransparency(false);
      }
    }
  }

  // 現在のコンテキストで再取得し反映する（有効時のみ）。null は反映しない（前回描画保持）。
  async refresh() {
    if (!this._enabled) {
      return;
    }
    // getContext（datasetRef/timeframe/…）へ setParams の bins/va/src/range を重畳して取得する。
    //   getContext が limit(recentBars) を含んでも client.buildMarketProfileUrl が破棄する（全期間集計）。
    const profile = await this._client.fetchProfile({
      ...this._getContext(), ...this._params, ...this._sessionsExtra(),
    });
    if (profile) {
      this._primitive.setProfile(profile);
    }
    // sessions ON/OFF を反映する（profile が null でも OFF 復元は必要＝独立に呼ぶ）。
    this._applySessions(profile);
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
    this._applyProfileMargin(false); // 右マージン復元（MP 削除で取り残さない）。
    // 単日フォーカスを解除する（MP 削除で拡大状態を残さない）。
    this._clearSessionFocus();
    // sessions のローソク透明化を必ず復元する（MP 削除でローソクを不透明へ戻す＝取り残さない）。
    if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(false);
    }
  }
}
