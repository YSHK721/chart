// market_profile_actor.js — Market Profile の薄い制御アクター（取得→primitive 反映・トグル状態）。
//
// 設計入力: 依頼「プロファイルを取得して primitive に反映する薄い制御・ES Modules・既存層構造」。
//   trade_markers_renderer.js と同層（adapter/front）。client（取得）と primitive（描画）を注入し、
//   本アクターは「トグル状態の保持」と「有効時の取得→反映」の編成のみを担う（SRP）。
//   依存はすべて抽象（duck-typing）に向け、composition root が具象を注入する（DIP）。
//
// 非破壊方針: primitive は初回有効化まで mainSeries へ attach しない（OFF 時はチャートに一切触れない）。
//   取得失敗（client が null）時も既存描画へ干渉せず、前回 profile を保持する。

// sessions の 'YYYY-MM-DD' → UNIX 秒（UTC 深夜）。candle.time との突合に使う（primitive dateToUnix と同一規則）。
function _sessionDateToUnix(dateStr) {
  const parts = String(dateStr).split('-');
  const y = Number(parts[0]);
  const m = Number(parts[1]);
  const d = Number(parts[2]);
  return (y > 0 && m > 0 && d > 0) ? Date.UTC(y, m - 1, d) / 1000 : NaN;
}

// sessions 応答を表示用に組み立てる純変換（SRP: actor は制御に留め、変換は本関数へ）。
//   ① 各セッションへ当日 candle の OHLC を date→time 突合で付与（列内 OHLC 描画用・透明化しても実値は残る）。
//   ② 当日 MP（POC/VAH/VAL）の time→mp Map を作る。VA/POC は backend が _value_area 単一定義で算出済み
//      （poc/va_low/va_high）＝frontend は表示に写すだけ（DRY・VA 定義は backend に一元化）。
//   戻り値 { list, mp }。list=OHLC 付与済みセッション配列、mp=time→{poc,vah,val} の Map。
function _buildSessionView(list, candles) {
  const byTime = new Map((candles || []).map((c) => [c.time, c]));
  const out = [];
  const mp = new Map();
  for (const s of list) {
    const t = _sessionDateToUnix(s.date);
    const c = byTime.get(t);
    out.push(c ? { ...s, open: c.open, high: c.high, low: c.low, close: c.close } : s);
    if (s.poc != null && s.va_low != null && s.va_high != null && Number.isFinite(t)) {
      mp.set(t, { poc: s.poc, vah: s.va_high, val: s.va_low });
    }
  }
  return { list: out, mp };
}

// MP-05: ticklive base=1 応答が DwellAccumulator.init を NaN 汚染せず駆動できるかの presence ガード。
//   無ローソク等で空 profile が返ると priceMin/priceMax/nBins/gridW が欠損し、init の binw/kw0 が NaN と
//   なり snapshot が NaN 価格を出す。必須フィールド（レンジ/グリッド/base 配列）がすべて有限/配列のときだけ
//   true を返し、欠損時は呼び出し側で null 扱い（増分に入らず前回描画を保持＝既存 fetch null と同じ非破壊）。
//   baseKmin は init が priceMin/gridW から導出フォールバックするため必須に含めない。
//   注意: JSON の明示 null は Number(null)===0（有限）で誤通過するため、各必須数値は `!= null`（null/
//   undefined 双方を除外）を先に課してから有限性を判定する（欠損 = 増分に入れない）。
function _finiteNum(x) {
  return x != null && Number.isFinite(Number(x));
}
function _hasBaseFields(f) {
  return !!f
    && _finiteNum(f.priceMin)
    && _finiteNum(f.priceMax)
    && _finiteNum(f.nBins) && Number(f.nBins) > 0
    && _finiteNum(f.gridW) && Number(f.gridW) > 0
    && Array.isArray(f.baseFine);
}

// 増分2 定数（試作 prototype_260630-01 と一致）。
const ROLL_BARS = 60; // ローリング窓の本数（from = T - ROLL_BARS*bar_sec）。
// 日別（sessions）初回オートズームの最大遡り期間（直近1年）。ISSUE-055: 全期間（1D で最大3.6年）を初回に
//   映すと tf-period 列が可視域ぶん一括取得され応答肥大（実測 87MB・warm でも数秒）で初回表示が重い。初回は
//   直近1年に限定し（古い範囲はスクロールで＝A案デバウンス＋per-day キャッシュで滑らか）、初回取得量/描画を抑える。
//   データが1年未満（intraday 等）のときは実効的に全期間（下限＝最古足）で不変。
const SESSIONS_INITIAL_SPAN_SEC = 365 * 86400;
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
  constructor({
    client, primitive, mainSeries, getContext, replayBar, getCandles, renderer,
    formingClient, makeAccumulator, sessionsDrawnByTfPeriod,
  } = {}) {
    this._client = client;
    this._primitive = primitive;
    this._mainSeries = mainSeries;
    this._replayBar = replayBar ?? null;
    this._renderer = renderer ?? null;
    // 日別（sessions）モードで、日別プロファイルを tf-period 列（別 actor）が描くか否かの述語（注入）。
    //   true のとき本 actor は日別タイル（_drawSessions 用の setSessions）を描かず、candle 透明化も tf-period
    //   側（列が描けた時点）へ委ねる（初回の「日別(candle)→(tf-period)」ちらつき防止・ISSUE-055）。未注入は
    //   常に false＝従来どおり本 actor がタイル描画＋透明化（tf-period 非配線の A方式・非対応 tf で不変）。
    this._sessionsDrawnByTfPeriod = typeof sessionsDrawnByTfPeriod === 'function'
      ? sessionsDrawnByTfPeriod : () => false;
    // tick 逐次成長（ticklive・増分2 系とは独立の 4 つ目の排他モード）。未注入時は非増分（refresh 委譲）。
    this._formingClient = formingClient ?? null;
    this._makeAccumulator = typeof makeAccumulator === 'function' ? makeAccumulator : null;
    this._ticklive = false;       // ticklive モード ON/OFF（既定 OFF＝非増分・後方互換）。
    // Model A 直交化: 成長状態（growing/static）。成長エンジン（_isIncremental/onLiveTick/_enterTicklive）は
    //   この _growing で駆動する（表示モードと成長状態の分離）。Phase1 は mode='ticklive' が唯一 _growing=true を
    //   立てる互換維持（_ticklive とロックステップ＝挙動不変）。Phase2 で mode 非依存の applyGrowthState が
    //   直接トグルできるようにする（FOLLOW+normal 成長など）。
    this._growing = false;
    this._asOf = null;            // 因果基準秒（as-seen-at-t）の布石。present は常にライブ（now）ゆえ未使用。
    this._accumulator = null;     // 現在の DwellAccumulator（null＝未 enter）。
    this._formingStart = null;    // 現在足の formingStart（rollover 検出用）。
    this._lastSec = null;         // 最後に addTick した tick 秒（base=0 尾部 since）。
    this._getCandles = typeof getCandles === 'function' ? getCandles : () => [];
    this._getContext = typeof getContext === 'function' ? getContext : () => ({});
    this._enabled = false;
    this._attached = false;
    // sessions（日別プロファイル分割）ON/OFF。既定 false（通常の累積プロファイル・後方互換）。
    this._sessions = false;
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
    // mode（表示モード・排他統合）: 旧 replay/sessions の 2 トグルを 1 つの排他 ENUM へ統合。
    //   明示指定時のみ反映する（undefined は現状維持）。mode は legacy replay/sessions に優先する
    //   （競合時は mode を採用＝二重管理を避ける）。normal|replay|sessions のいずれかへ状態遷移する。
    if (params.mode != null) {
      this._applyMode(params.mode);
      return; // mode 指定時は legacy 分岐を評価しない（mode 優先）。
    }
    // legacy 受理（後方互換・mode 未指定時のみ）。旧 replay:true / sessions:true を引き続き受理する。
    //   sessions（日別プロファイル分割）トグル: true で refresh 時に context へ sessions:true を載せ、
    //   応答の profile.sessions を primitive/renderer へ反映。false で通常モードへ復帰。
    if (params.sessions != null) {
      this._sessions = !!params.sessions;
    }
    // replay トグル（増分1）。明示指定時のみ反映する（undefined は現状維持）。
    if (params.replay != null) {
      this._setReplay(!!params.replay);
    }
  }

  // 表示モードの排他遷移。既存の _setReplay / _applySessions 復元経路を再利用する（重複実装しない）。
  //   - 'sessions': replay 一式 OFF（_setReplay(false)＝バー非表示・T 縦線/トリム/スナップショット解除・
  //     カーソル null・チャート操作復元）＋ sessions ON（_sessions=true）。応答反映は後続 refresh で行う。
  //   - 'replay': sessions 一式 OFF（_sessions=false ＋ _applySessions(null)＝setSessions(null)・透明化解除）
  //     ＋ replay ON（_setReplay(true)）。
  //   - 'normal': 両 OFF 一式（_setReplay(false) ＋ _sessions=false ＋ _applySessions(null)）。
  //   排他が構造的に保証される（同時 ON が不可能）。未知の mode は 'normal' 扱い（安全側）。
  _applyMode(mode) {
    if (mode === 'ticklive') {
      // ticklive ON（tick 逐次成長）。replay/sessions 一式を解除して排他化する。
      this._setReplay(false);
      this._sessions = false;
      this._applySessions(null);
      this._ticklive = true;
      this._growing = true;   // ticklive モード＝成長 ON（Phase1 互換: mode が _growing を立てる）。
      return;
    }
    if (mode === 'sessions') {
      this._exitTicklive();     // ticklive 解除（排他）。
      this._setReplay(false);   // replay 一式解除（バー/カーソル/トリム/スナップショット/操作）。
      // 自動ズームは **非 sessions → sessions の新規入場時のみ** pending にする。既に sessions のまま
      //   _applyMode('sessions') が再適用される（FOLLOW/ANALYSIS 遷移時の reapplyMarketProfileMode 等）
      //   ケースで pending を再セットすると、価格更新→自動 FOLLOW 復帰のたびに focus が再発火して
      //   ユーザーの手動ズームが「全体が初期表示」へリセットされる（実機バグ）。再適用では寄せない。
      if (!this._sessions) {
        this._sessionsFocusPending = true;
      }
      this._sessions = true;    // sessions ON（応答の profile.sessions は refresh の _applySessions で反映）。
      return;
    }
    if (mode === 'replay') {
      this._exitTicklive();     // ticklive 解除（排他）。
      this._sessions = false;   // sessions OFF。
      this._applySessions(null); // sessions 一式解除（focus/ズーム/ロック・setSessions(null)・透明化解除）。
      this._setReplay(true);    // replay ON（バー表示）。
      return;
    }
    // 'normal'（および未知値）: 全 OFF 一式。
    this._exitTicklive();       // ticklive 解除（排他）。
    this._setReplay(false);
    this._sessions = false;
    this._applySessions(null);
  }

  // ticklive 表示中か（MP 有効かつ ticklive トグル ON のときだけ true）。
  //   Phase5: 'ticklive' は表示選択肢から撤去済（catalog）だが、内部フラグ/機構は grow 軸の互換維持で残す
  //   （setParams({mode:'ticklive'}) は依然 _applyMode('ticklive') を通り _growing を立てる）。UI からは到達不能。
  isTicklive() {
    return this._enabled && !!this._ticklive;
  }

  // Model A 統一成長（Phase5）: 「push 成長中か」の grow 軸判定。MP 有効かつ growing かつ非 sessions
  //   （normal/replay 表示での足内 push 成長）のとき true。reveal（replay）の push 駆動ゲート
  //   （enterBar/growTo/feedTick・旧 isTicklive() ゲート）を表示モードから成長軸へ移行するための単一判定。
  //   sessions は refresh(to) で育てる（機構A）ため push 対象外（!_sessions）。present の pull 成長
  //   （onLiveTick→forming）は _isIncremental が担い、本判定は replay の push ゲートに用いる。
  isGrowingPush() {
    return this._enabled && !!this._growing && !this._sessions;
  }

  // Model A 直交化: 成長状態を表示モードと独立に設定する単一信号（境界追加・actor ロジックは不変）。
  //   growing=true で成長エンジン（_isIncremental→onLiveTick/_enterTicklive/forming）を有効化する。
  //   これにより mode を維持したまま（例: FOLLOW+normal）成長 ON/OFF を切替えられる（present #2 の直交化）。
  //   asOf は因果基準秒（as-seen-at-t）の布石。present は常にライブ（now）ゆえ未使用（保持のみ）。
  //   growing=false へ遷移する際は成長エンジンの累積器/尾部を破棄する（static 復帰＝_enterTicklive 再入の初期化）。
  applyGrowthState({ growing, asOf } = {}) {
    const next = !!growing;
    if (asOf !== undefined) {
      this._asOf = asOf;
    }
    if (next === !!this._growing) {
      return; // 同状態は no-op（冪等）。
    }
    this._growing = next;
    if (!next) {
      // static 復帰: 累積器/形成足/尾部を破棄（次回 growing=true で _enterTicklive が再取得・再 init）。
      this._accumulator = null;
      this._formingStart = null;
      this._lastSec = null;
    }
  }

  // 増分（forming/accumulator）成長が可能か: growing かつ formingClient・accumulator factory 注入済み、
  //   かつ **sessions モードでない**こと。いずれか欠ければ非増分（onLiveTick は refresh へ委譲＝回帰ゼロ）。
  //   Model A Phase3（成長経路の分岐）: sessions+growing は forming 単一プロファイル（_enterTicklive→
  //   setProfile）を sessions 描画へ被せず、refresh(to=cursor, sessions=1) で backend の因果 sessions 分割
  //   （当日=[session_start,to)・過去日静的）を取得する（review🔵4 の破綻状態を正しく解消）。よって
  //   _sessions 時は非増分＝refresh 経路へ倒す（accumulator は sessions で使わない＝共有グリッド不整合回避）。
  //   normal/replay+growing は従来どおり増分（全期間 base + bar-period forming）。
  //   src=zp（超過占有 z(p)）は per-tick 増分が定義できない（帰無モーメント込みの再計算が必要）ため
  //   非増分＝refresh 委譲へ倒す（onLiveTick はライブ足更新周期＝数秒に 1 回。backend は当日 null を
  //   経過分キーでメモし 0.05〜0.2s 程度で応答する）。
  _isIncremental() {
    return !!this._growing && !this._sessions && this._params.src !== 'zp'
      && !!this._formingClient && !!this._makeAccumulator;
  }

  // 現在選択中の src（未設定は null）。composition root が tf-period 列への src 透過判定に使う。
  srcParam() {
    return this._params.src ?? null;
  }

  // forming 取得の引数（getContext＋params＋base/since）。limit は buildFormingUrl が無視する（全期間 base）。
  //   MP-04 是正: ticklive は dwell（滞在秒 time-at-price）を原子とする機能。base（確定足累積）は backend
  //   controller が src='dwell' を強制し、forming tick から DwellAccumulator が dwell 原子を計算する。
  //   よって actor 境界でも src を 'dwell' に固定し、UI で選択中の src（candle/m1）が base と live 増分の
  //   原子を食い違わせないことを保証する（非 ticklive 表示の原子との不整合を防ぐ）。参照実装 mp_core の
  //   dwell 原子（_session_dwell）に忠実。
  _buildFormingArgs({ base, since }) {
    const args = { ...this._getContext(), ...this._params, src: 'dwell', base, since };
    // present normal ライブ成長（FOLLOW）: base 累積窓を全期間 → 当日（現在セッション）へ絞る。
    //   全期間累積だと現在足 1 本ぶんの成長が数年分に対して極小で視認不能になるため、当日始端を base 下限
    //   （from）にする（古典的セッション Market Profile・ユーザー確定・視認性優先）。growing かつ非 sessions
    //   のときだけ載せる（static=ANALYSIS は _growing=false で forming 経路に入らず refresh 委譲＝全期間・不変／
    //   sessions は refresh(to,sessions) が backend で当日タイルを育てるため forming from を載せない＝不変）。
    //   from の写像規則は domain GrowthWindow.forCurrent('normal',tf,now).from と一致させる（下記 _sessionFrom）。
    //   replay は subclass ReplayMarketProfileActor が _buildFormingArgs を override し、super（本メソッド）呼び
    //   出しの後に自前 from（GrowthWindow(mode,tf,getContext().to)）を必ず再設定する＝本 present 分岐は replay の
    //   from へ波及しない（override 優先・replay 非退行）。
    if (this._growing && !this._sessions) {
      const from = this._sessionFrom();
      if (from != null) {
        args.from = from;
      }
    }
    return args;
  }

  // present normal ライブ成長の base 下限 from（当日始端）を最新ローソク time から写像する。
  //   now = 最新ローソク time（_getCandles 末尾・sessions の getContext().to 源と同一）。空/不明は null
  //   （窓を成さず＝全期間へ縮退＝既存 fetch と同じ非破壊）。
  //   from = min(session_start, forming_start)＝domain GrowthWindow.forCurrent('normal',tf,now).from と同一規則
  //   （日中足は session_start=当日始端／1W/1M は当該バー期間の始端で from<=forming_start 不変条件を保つ）。
  //   NOTE: domain GrowthWindow を import せず本 actor 既存 TF_BAR_SEC で同規則を算出する。build.mjs（present
  //   バンドル）は ES Modules を単一 IIFE スコープへ連結するため、growth_window.js を取り込むと本 actor と
  //   growth_window.js の双方が持つ top-level `const TF_BAR_SEC` が二重宣言衝突を起こす（bundle 破損）。よって
  //   規則を growth_window.js と一致させたまま本 actor 内で直接算出する（直接 session_start・規則同一）。
  _sessionFrom() {
    const candles = this._getCandles();
    const last = Array.isArray(candles) && candles.length ? candles[candles.length - 1] : null;
    const now = last && last.time != null ? Number(last.time) : NaN;
    if (!Number.isFinite(now)) {
      return null;
    }
    const barSec = TF_BAR_SEC[this._getContext().timeframe] ?? 86400;
    const sessionStart = Math.floor(now / 86400) * 86400;
    const formingStart = Math.floor(now / barSec) * barSec;
    return Math.min(sessionStart, formingStart);
  }

  // ライブ tick 契機。増分（ticklive）: 未 enter なら _enterTicklive、以降は base=0 尾部を addTick して
  //   snapshot を反映。formingStart 変化（rollover）で _enterTicklive を再実行。
  //   非増分: this.refresh() へ byte-identical 委譲（ticklive OFF / formingClient 未注入＝回帰ゼロ）。
  async onLiveTick() {
    if (!this._isIncremental()) {
      return this.refresh(); // 非増分＝既存 refresh と同一（後方互換・回帰ゼロ）。
    }
    if (!this._enabled) {
      return undefined;
    }
    if (!this._accumulator) {
      return this._enterTicklive(); // 初回＝UC-01。
    }
    const forming = await this._formingClient.fetchForming(
      this._buildFormingArgs({ base: 0, since: this._lastSec }),
    );
    if (!forming) {
      return undefined; // null は前回描画を保持（非破壊）。
    }
    if (forming.formingStart !== this._formingStart) {
      return this._enterTicklive(); // rollover: base を取り直して reset。
    }
    for (const t of forming.ticks) {
      this._accumulator.addTick(t[0], t[1]);
      this._lastSec = t[0];
    }
    this._primitive.setProfile(this._accumulator.snapshot());
    return undefined;
  }

  // UC-01: base=1 を取得して accumulator を init、forming tick 列を畳み込み、snapshot を描画する。
  async _enterTicklive() {
    if (!this._enabled || !this._isIncremental()) {
      return;
    }
    const forming = await this._formingClient.fetchForming(
      this._buildFormingArgs({ base: 1, since: null }),
    );
    if (!forming) {
      return; // null は前回描画を保持（非破壊）。
    }
    // MP-05 是正: base=1 応答の必須フィールド（レンジ/グリッド/base 配列）が欠損（無ローソク等の空
    //   profile）なら init へ NaN が伝播し snapshot が NaN 価格を出す。presence ガードで欠損時は null と
    //   同じ扱い（増分に入らず前回描画を保持＝既存 fetch null と同じ非破壊挙動）にする。
    if (!_hasBaseFields(forming)) {
      return; // 空 profile（必須フィールド欠損）は前回描画を保持（非破壊・NaN 混入を防ぐ）。
    }
    const acc = this._makeAccumulator();
    acc.init({
      baseFine: forming.baseFine,
      baseKmin: forming.baseKmin,
      activeTable: forming.activeTable,
      priceMin: forming.priceMin,
      priceMax: forming.priceMax,
      nBins: forming.nBins,
      gridW: forming.gridW,
      formingStart: forming.formingStart,
    });
    this._accumulator = acc;
    this._formingStart = forming.formingStart;
    this._lastSec = null;
    for (const t of forming.ticks) {
      acc.addTick(t[0], t[1]);
      this._lastSec = t[0];
    }
    this._primitive.setProfile(acc.snapshot());
  }

  // ticklive を解除する（累積器破棄・通常経路復帰・冪等）。
  _exitTicklive() {
    this._ticklive = false;
    this._growing = false;  // モード離脱＝成長 OFF（Phase1 互換: _ticklive とロックステップ）。
    this._accumulator = null;
    this._formingStart = null;
    this._lastSec = null;
  }

  // MP 表示中の右マージン（プロファイル専用領域）を renderer へ委譲する。
  //   on=true で PROFILE_MARGIN_FRACTION（=0.30）ぶんローソクを左へ寄せ、false で復元。
  //   renderer.setRightMarginFraction 非提供時は no-op（後方互換）。冪等。
  _applyProfileMargin(on) {
    if (this._renderer && typeof this._renderer.setRightMarginFraction === 'function') {
      this._renderer.setRightMarginFraction(on ? PROFILE_MARGIN_FRACTION : null);
    }
  }

  // sessions（日別プロファイル分割）を表示中か。
  //   MP 有効かつ sessions トグル ON のときだけ true（OFF/通常モードでは既存挙動を変えない）。
  isSessions() {
    return this._enabled && !!this._sessions;
  }

  // sessions（日別プロファイル分割）を primitive/renderer へ反映する。
  //   on: primitive.setSessions(profile.sessions)・renderer.setCandleTransparency(true)（ローソク透明化）。
  //   off: primitive.setSessions(null)（通常モード）・renderer.setCandleTransparency(false)（復元）。
  //   該当メソッド非提供時は skip（後方互換）。移植元 prototype_260630-01 drawSessions。
  _applySessions(profile) {
    const on = !!this._sessions;
    // tf-period 列が日別プロファイルを描くモード（player tf の日別）か。true のとき本 actor は日別タイルを
    //   描かず（先に届く sessions 応答での一瞬のタイル描画→tf-period 列への差し替えちらつきを防ぐ）、candle
    //   透明化も tf-period 側（列描画時）へ委ねる（それまで candle 可視＝空白回避）。ISSUE-055。
    const tfDraws = on && this._sessionsDrawnByTfPeriod();
    // 表示用ビュー（OHLC 付与済みリスト＋当日 MP Map）を純変換で一括構築する（SRP）。読取欄は tfDraws でも要る。
    const rawList = on && profile && Array.isArray(profile.sessions) ? profile.sessions : null;
    const view = (rawList && rawList.length)
      ? _buildSessionView(rawList, (typeof this._getCandles === 'function' ? this._getCandles() : []))
      : null;
    if (this._primitive && typeof this._primitive.setSessions === 'function') {
      // tfDraws のときは日別タイルを描かない（tf-period 列が描く）＝setSessions(null)。
      this._primitive.setSessions(tfDraws ? null : (view ? view.list : null));
    }
    // 読み取り欄: クロスヘアが当日を指したとき OHLC に加え当日 MP（POC/VAH/VAL）を出す（sessions のみ・tfDraws でも供給）。
    if (this._renderer && typeof this._renderer.setSessionMP === 'function') {
      this._renderer.setSessionMP(view ? view.mp : null);
    }
    // candle 透明化: tfDraws のときはここで触らず tf-period 側（列描画時に true / 無効化時に false）へ委ねる。
    //   非 tfDraws（通常の日別タイル or OFF）は従来どおり on で透明化/復元する。
    if (!tfDraws && this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(on);
    }
    // sessions を有効化した初回のみ、被覆セッション日の時間レンジへズームを寄せる（時間軸連動タイルが
    //   潰れない／短周期でも日別列が画面内に入るように）。以後は寄せない（手動ズーム/スクロール尊重）。
    //   時間ベース（focusTimeRange）にすることで、1m（1日=1440本）でも「日数」を「バー数」と誤解して
    //   列が画面外に落ちる不具合を解消する。off 遷移でフラグをクリア。
    if (on) {
      if (this._sessionsFocusPending && rawList && rawList.length
          && this._renderer && typeof this._renderer.focusTimeRange === 'function') {
        const candles = (typeof this._getCandles === 'function' ? this._getCandles() : []) || [];
        if (candles.length) {
          this._sessionsFocusPending = false;
          // 被覆日の下限＝最古セッション日始端（ただしロード済み candle の左端より前へは行かない＝空白回避）。
          const sessStart = _sessionDateToUnix(rawList[0].date);
          const oldest = Number.isFinite(sessStart)
            ? Math.max(sessStart, candles[0].time)
            : candles[0].time;
          const to = candles[candles.length - 1].time; // 右端＝最新足（now）。
          // 初回は直近 SESSIONS_INITIAL_SPAN_SEC（1年）に限定する（初回 tf-period 取得量/描画負荷の抑制・
          //   ISSUE-055）。1年未満のデータでは oldest が下限となり実効全期間で不変。
          const from = Math.max(oldest, to - SESSIONS_INITIAL_SPAN_SEC);
          this._renderer.focusTimeRange(from, to);
        }
      }
    } else {
      this._sessionsFocusPending = false;
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
      // 初期カーソルを現在のスライダ位置（既定=右端=最新）に設定して T 縦線を即描画する。
      //   スライダは右端から始まるため、スクラブ前でも線が出る（ユーザFB「スナップショットONで
      //   T 縦線が出ない」の修正）。fetch はしない（線＝setCursorTime のみ）。onReplayControlsChange の
      //   初期化元にもなり、スクラブ前にスナップショットを ON にしても当時 T が確定する。
      if (on && this._replayTo == null && typeof this._replayBar.currentTime === 'function') {
        const t0 = this._replayBar.currentTime();
        if (t0 != null) {
          this._replayTo = t0;
          if (this._primitive && typeof this._primitive.setCursorTime === 'function') {
            this._primitive.setCursorTime(t0);
          }
        }
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
    if (!this._enabled || !this._replay) {
      return;
    }
    // カーソル未設定（スクラブ前）でも、現在のスライダ位置（既定=最新）を当時 T として初期化する。
    //   これによりスクラブせずスナップショットを ON にしても当時プロファイル・T 縦線が反映される。
    let t = this._replayTo;
    if (t == null && this._replayBar && typeof this._replayBar.currentTime === 'function') {
      t = this._replayBar.currentTime();
    }
    if (t == null) {
      return;
    }
    await this.setReplayCursor(t);
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
      // ISSUE-065: 増分経路（present dwell ライブ成長＝growing×非sessions×非zp×forming注入）は初期描画を
      //   当日 forming で**直接**行う。従来は refresh()（全期間 dwell）を 1 回描き、直後の onLiveTick→
      //   _enterTicklive が当日窓（_sessionFrom＝当日始端 from）へ置換するため、全期間バーが一瞬映る
      //   ちらつきがあった。増分時は最初から onLiveTick（accumulator 未生成→_enterTicklive＝当日 base+
      //   forming）を描いて全期間フレームを消す。end-state（当日絞り）は不変。非増分（static/sessions/zp/
      //   非tick）は従来どおり refresh()＝全期間（挙動不変・回帰ゼロ）。
      if (this._isIncremental()) {
        await this.onLiveTick();
      } else {
        await this.refresh();
      }
      this._primitive.setVisible(true);
    } else {
      this._primitive.setVisible(false);
      this._applyProfileMargin(false); // 右マージン復元（ローソクを従来位置へ）。
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
  //   coalesce（保険）: in-flight 中の再入は末尾 1 回に丸める（setReplayCursor と同型）。
  //   src=zp のライブ成長は onLiveTick→refresh 委譲のため、応答遅延中の連続要求を貯めない。
  async refresh() {
    if (!this._enabled) {
      return;
    }
    if (this._refreshRunning) {
      this._refreshQueued = true;
      return;
    }
    this._refreshRunning = true;
    try {
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
    } finally {
      this._refreshRunning = false;
    }
    if (this._refreshQueued) {
      this._refreshQueued = false;
      await this.refresh(); // 末尾実行（最後の 1 回のみ）。
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
    this._applyProfileMargin(false); // 右マージン復元（MP 削除で取り残さない）。
    // sessions のローソク透明化を必ず復元する（MP 削除でローソクを不透明へ戻す＝取り残さない）。
    if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
      this._renderer.setCandleTransparency(false);
    }
  }
}
