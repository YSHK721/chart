// market_profile_actor.js — Market Profile の薄い制御アクター（取得→primitive 反映・トグル状態）。
//
// 設計入力: 依頼「プロファイルを取得して primitive に反映する薄い制御・ES Modules・既存層構造」。
//   trade_markers_renderer.js と同層（adapter/front）。client（取得）と primitive（描画）を注入し、
//   本アクターは「トグル状態の保持」と「有効時の取得→反映」の編成のみを担う（SRP）。
//   依存はすべて抽象（duck-typing）に向け、composition root が具象を注入する（DIP）。
//
// 非破壊方針: primitive は初回有効化まで mainSeries へ attach しない（OFF 時はチャートに一切触れない）。
//   取得失敗（client が null）時も既存描画へ干渉せず、前回 profile を保持する。

// セッション日境界（ISSUE-078・NY17:00 ET 基準）。日切り・当日窓・日別集計の唯一の規則源。
import { sessionDayStart } from '../../domain/session_day.js';
// ソース能力記述子（domain 単一情報源）: src 別の増分可否・期間窓・session ブロックを導出する
//   （ISSUE-097 🟡-9・散在した src==='zp' 述語の集約）。
import { mpSourceCapability } from '../../domain/mp_source_capability.js';

// 日別（sessions）表示の純変換（_sessionDateToUnix / _buildSessionView）と初回オートズームの
//   スパン定数（SESSIONS_INITIAL_SPAN_SEC）は mp_session_tiles.js（日別タイル ロール）が所有する
//   （ISSUE-181）。A方式バンドルは ES Modules を単一 IIFE スコープへ連結するため、同名の top-level
//   宣言を両モジュールへ置かない（build.mjs 冒頭の衝突禁止規律）。
// base=1 応答の presence ガード（_hasBaseFields）は mp_tick_growth.js（tick 逐次成長 ロール）が
//   所有する（ISSUE-181）。A方式バンドルは ES Modules を単一 IIFE スコープへ連結するため、同名の
//   top-level 宣言を両モジュールへ置かない（build.mjs 冒頭の衝突禁止規律）。
// 増分2 定数（ROLL_BARS）は mp_replay_scrub.js（リプレイ・スクラブ ロール）が所有する（ISSUE-181）。
//   A方式バンドルは ES Modules を単一 IIFE スコープへ連結するため、同名 top-level const を
//   両モジュールに置くと二重宣言でバンドルが壊れる（build.mjs 冒頭の衝突禁止規律）。
// 右マージン率（PROFILE_MARGIN_FRACTION）は mp_chart_layout.js が所有する（ISSUE-181）。
//   A方式バンドル（単一 IIFE 連結）で同名 top-level const を二重宣言しないため本体からは削除する。
// timeframe → 足の秒長は domain/tf_meta.js（単一情報源・ISSUE-087 🔴-2）から import する。
//   旧: growth_window.js との top-level const 衝突（IIFE 連結）で再宣言していた＝解消済み。
import { TF_BAR_SEC } from '../../domain/tf_meta.js';
// 表示モード遷移ロール（ISSUE-181・SRP で外出し。状態も移送済み）。mpDisplayMode 台帳の参照は
//   本ロールが持つ（host からは import しない＝A方式バンドルでの重複束縛を作らない）。
import { MpModeTransition } from './mp_mode_transition.js';
// リプレイ・スクラブ ロール（ISSUE-181・SRP で外出し。状態も移送済み）。
import { MpReplayScrubController } from './mp_replay_scrub.js';
// チャートレイアウト（attach／右マージン）ロール（ISSUE-181・SRP で外出し。状態も移送済み）。
import { MpChartLayout } from './mp_chart_layout.js';
// 取得パラメータ・URL コンテキスト写像ロール（ISSUE-181・SRP で外出し。状態も移送済み）。
import { MpFetchParams } from './mp_fetch_params.js';
// tick 逐次成長（forming/accumulator の足内 pull 成長）ロール（ISSUE-181・SRP で外出し。状態も移送済み）。
import { MpTickGrowth } from './mp_tick_growth.js';
// 日別（sessions）タイル反映＋初回オートズーム ロール（ISSUE-181・SRP で外出し。状態も移送済み）。
import { MpSessionTiles } from './mp_session_tiles.js';

export class MarketProfileActor {
  // client: fetchProfile(context)->profile|null。primitive: setProfile/setVisible。
  // mainSeries: attachPrimitive（v5・非提供時は attach を skip＝後方互換）。
  // getContext: ()->{datasetRef,timeframe,limit,...}（取得時点の現在チャート状態を遅延読み取り）。
  // replayBar: リプレイスライダバー（任意注入・setVisible/setCandles/mode/isSnapshot）。未注入時は no-op。
  // getCandles: ()->candles（バーの min/max・index→time の元。renderer.getCandles を配線）。未注入時は空。
  // renderer: 増分2 スナップショットのローソクトリム源（setCandleTrim(time|null)）。未注入時はトリムしない。
  constructor({
    client, primitive, mainSeries, getContext, replayBar, getCandles, renderer,
    formingClient, makeAccumulator, sessionsDrawnByTfPeriod, onParamsChanged,
    onSessionsLiveGrow, nowSecFn,
  } = {}) {
    this._client = client;
    // パラメータ変更通知（注入）。setParams 完了時に呼ぶ。composition_root が tf-period 列アクターの
    //   即時再取得（src/mode 変更を可視レンジ変化を待たず反映・ISSUE-066）へ配線する。未注入は no-op。
    this._onParamsChanged = typeof onParamsChanged === 'function' ? onParamsChanged : () => {};
    // ライブ育成通知（ISSUE-083・注入）。日別×tf-period 描画×growing（FOLLOW）の refresh（live tick 経路）
    //   で呼ぶ。composition_root が tf-period 列アクターの当日チャンク再取得（refreshAt）へ配線し、
    //   当日列（zp/dwell とも）を育てる。throttle は tf-period 側の責務。未注入は no-op（後方互換）。
    this._onSessionsLiveGrow = typeof onSessionsLiveGrow === 'function' ? onSessionsLiveGrow : () => {};
    // 現在時刻源（秒・ISSUE-086: 1W/1M ラベルの未来日クランプ用）。テスト注入可・既定 Date.now。
    this._nowSec = typeof nowSecFn === 'function' ? nowSecFn : () => Date.now() / 1000;
    this._primitive = primitive;
    // リプレイ・スクラブ（ISSUE-181・A2）を委譲する協働子。replay ON/OFF・当時カーソル T・
    //   スクラブ coalesce 状態・リプレイバー参照は本協働子が所有する（host はフィールドを持たない）。
    this._replayScrub = new MpReplayScrubController(this, replayBar);
    this._renderer = renderer ?? null;
    // チャートレイアウト（ISSUE-181・A5）を委譲する協働子。attach 済みフラグ・attach 先
    //   （mainSeries）・右マージン設定先（renderer）は本協働子が所有する（host は持たない）。
    this._layout = new MpChartLayout({ primitive, mainSeries, renderer: this._renderer });
    // 日別（sessions）モードで、日別プロファイルを tf-period 列（別 actor）が描くか否かの述語（注入）。
    //   true のとき本 actor は日別タイル（_drawSessions 用の setSessions）を描かず、candle 透明化も tf-period
    //   側（列が描けた時点）へ委ねる（初回の「日別(candle)→(tf-period)」ちらつき防止・ISSUE-055）。未注入は
    //   常に false＝従来どおり本 actor がタイル描画＋透明化（tf-period 非配線の A方式・非対応 tf で不変）。
    this._sessionsDrawnByTfPeriod = typeof sessionsDrawnByTfPeriod === 'function'
      ? sessionsDrawnByTfPeriod : () => false;
    // tick 逐次成長（ISSUE-181・A1）を委譲する協働子。成長フラグ（_growing）・累積器（_accumulator）・
    //   現在足 formingStart・尾部秒（_lastSec）・注入依存（formingClient / makeAccumulator）は本協働子が
    //   所有する（host は own field を持たず、下の prototype アクセサで旧フィールド面のみ維持する）。
    this._growth = new MpTickGrowth(this, { formingClient, makeAccumulator });
    // 表示モード遷移（ISSUE-181・A3）を委譲する協働子。ticklive トグル（_ticklive）と sessions
    //   トグル（_sessions）は本協働子が所有する（host は own field を持たず、下の prototype
    //   アクセサで旧読み取り面のみ維持する）。
    this._mode = new MpModeTransition(this);
    // 日別タイル＋初回オートズーム（ISSUE-181・A4）を委譲する協働子。初回オートズームの pending
    //   （_sessionsFocusPending）は本協働子が所有する（host は own field を持たない）。
    //   ISSUE-164: ビュー介入（focusTimeRange）の呼び出し箇所・ガード・発火順序は抽出前と同一。
    this._tiles = new MpSessionTiles(this);
    this._getCandles = typeof getCandles === 'function' ? getCandles : () => [];
    this._getContext = typeof getContext === 'function' ? getContext : () => ({});
    this._enabled = false;
    // 取得パラメータ（bins/va/src/range 等）は MpFetchParams（A6・ISSUE-181）が所有する。
    //   host は下の読み取り専用アクセサ _params で参照するだけ（フィールドを持たない）。
    this._fetchParams = new MpFetchParams(this);
  }

  // リプレイ・スクラブ関連は MpReplayScrubController（A2）へ外出しした（ISSUE-181）。
  //   以下は subclass（ReplayMarketProfileActor）の inherited 呼出・既存テストを温存する薄い委譲。
  _replayExtra(time) {
    return this._replayScrub.replayExtra(time);
  }

  _applySnapshot(time) {
    return this._replayScrub.applySnapshot(time);
  }

  // 取得パラメータの読み取り専用アクセサ（実体は MpFetchParams が所有・ISSUE-181）。
  //   `...this._params` の重畳・`this._params.src` の参照（replay subclass 含む）を温存する。
  get _params() {
    return this._fetchParams.values();
  }

  // ---- 互換アクセサ: 旧 host フィールド面（tick 逐次成長ロール・ISSUE-181・A1）----
  //   実体は MpTickGrowth が所有する（host は own field を持たない）。replay subclass
  //   （replay_market_profile_actor.js の push 戦略）が `a._accumulator = acc` /
  //   `a._formingStart = ...` / `a._lastSec = ...` で直接書き込み、`a._formingClient` /
  //   `a._makeAccumulator()` を直接読むため、読み書き両方向を委譲で維持する（面は 1 バイト不変）。
  //   ISSUE-145（足内 tick 更新＝INTRABAR_FORMING_IDS 登録）の駆動経路もこの面の上に成立する。
  get _growing() { return this._growth.growing(); }

  set _growing(value) { this._growth.setGrowing(value); }

  get _accumulator() { return this._growth.accumulator(); }

  set _accumulator(value) { this._growth.setAccumulator(value); }

  get _formingStart() { return this._growth.formingStart(); }

  set _formingStart(value) { this._growth.setFormingStart(value); }

  get _lastSec() { return this._growth.lastSec(); }

  set _lastSec(value) { this._growth.setLastSec(value); }

  get _formingClient() { return this._growth.formingClient(); }

  get _makeAccumulator() { return this._growth.accumulatorFactory(); }

  // ---- 互換アクセサ: 旧 host フィールド面（表示モード遷移ロール・ISSUE-181・A3）----
  //   実体は MpModeTransition が所有する（host は own field を持たない）。読み取り専用:
  //   `this._sessions` は host 自身・MpFetchParams・MpTickGrowth・replay subclass
  //   （replay_market_profile_actor.js:279,350,490）が参照するだけで、書き込みは遷移経路
  //   （MpModeTransition.apply / applyParams）に限られる（＝排他遷移の単一入口を保つ）。
  get _ticklive() { return this._mode.ticklive(); }

  get _sessions() { return this._mode.sessions(); }

  isEnabled() {
    return this._enabled;
  }

  // 取得パラメータ（bins/va/src/range）を設定する。null/undefined のキーは無視する
  //   （getContext の値やサーバ既定を潰さない）。次回 refresh から反映される。
  //   range（レンジpt）は client.buildMarketProfileUrl が barw へ写像する（'auto' は付与しない）。
  //   表示モードの決定（mode 分岐・legacy トグル受理）は MpModeTransition（A3）へ外出しした
  //   （ISSUE-181）。抽出前の 2 経路（mode 指定 / legacy）はいずれも「最後に _onParamsChanged を
  //   1 回」で終わるため、決定を委譲してから 1 回発火する形と同一（ISSUE-066 の伝播タイミング不変）。
  setParams(params = {}) {
    this._fetchParams.set(params);
    // mode（表示モード・排他統合）: 旧 replay/sessions の 2 トグルを 1 つの排他 ENUM へ統合。
    //   明示指定時のみ反映する（undefined は現状維持）。mode は legacy replay/sessions に優先する
    //   （競合時は mode を採用＝二重管理を避ける）。normal|replay|sessions のいずれかへ状態遷移する。
    this._mode.applyParams(params);
    this._onParamsChanged(); // ISSUE-066: mode/src/bins/va 等の変更を tf-period 列へ伝播（即時再取得）。
  }

  // 表示モードの排他遷移（実体は MpModeTransition・ISSUE-181）。以下は subclass の inherited 呼出・
  //   既存テスト・composition root 配線を温存する薄い委譲。
  _applyMode(mode) {
    return this._mode.apply(mode);
  }

  // 日別（sessions）初回オートズームの pending を立てる（MpModeTransition が非 sessions→sessions の
  //   新規入場でのみ呼ぶ）。実体のフラグは MpSessionTiles が持つ（ISSUE-164: 発火条件・順序は不変）。
  _markSessionsFocusPending() {
    return this._tiles.markFocusPending();
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
  //   growing=false へ遷移する際は成長エンジンの累積器/尾部を破棄する（static 復帰＝_enterTicklive 再入の初期化）。
  applyGrowthState({ growing } = {}) {
    return this._growth.applyState({ growing });
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
    return this._growth.isIncremental();
  }

  // 現在選択中の src（未設定は null）。composition root が tf-period 列への src 透過判定に使う。
  srcParam() {
    return this._fetchParams.src();
  }

  // 現在有効な barw（レンジ pt・未設定は null）。composition root が tf-period 列の束ね幅に使う
  //   （ISSUE-054: 「レンジ」を日別プロファイルの全描画経路で効かせる）。
  barwParam() {
    return this._fetchParams.barw();
  }

  // forming 取得の引数（getContext＋params＋base/since）。limit は buildFormingUrl が無視する（全期間 base）。
  //   MP-04 是正: ticklive は dwell（滞在秒 time-at-price）を原子とする機能。base（確定足累積）は backend
  //   controller が src='dwell' を強制し、forming tick から DwellAccumulator が dwell 原子を計算する。
  //   よって actor 境界でも src を 'dwell' に固定し、UI で選択中の src（candle/m1）が base と live 増分の
  //   原子を食い違わせないことを保証する（非 ticklive 表示の原子との不整合を防ぐ）。参照実装 mp_core の
  //   dwell 原子（_session_dwell）に忠実。
  _buildFormingArgs({ base, since }) {
    const args = { ...this._getContext(), ...this._params, ...this._dispExtra(), src: 'dwell', base, since };
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
  //   NOTE: domain GrowthWindow を import せず本 actor 内で同規則を算出する（**複製**）。
  //   かつてここは理由を「actor と growth_window.js の双方が top-level `const TF_BAR_SEC` を持つため
  //   二重宣言衝突（bundle 破損）」と書いていたが、**この理由は成立しない**。`const TF_BAR_SEC` の
  //   宣言は domain/tf_meta.js の 1 箇所のみで、双方ともそれを import している（ISSUE-262）。
  //   実際の阻害要因は、growth_window.js が indicator_ui の A方式バンドル（build.mjs の
  //   MODULE_ORDER）に未登録で symlink も無いこと。よって現時点は複製を残す。
  //   複製が唯一源とずれたら tests/growth_window_rule_parity.test.js が落とす。
  //   複製を消す手順: domain へ symlink を張り MODULE_ORDER へ登録し、本メソッドを
  //   GrowthWindow.forCurrent('normal', tf, now).from への委譲へ置換する。
  _sessionFrom() {
    const candles = this._getCandles();
    const last = Array.isArray(candles) && candles.length ? candles[candles.length - 1] : null;
    const now = last && last.time != null ? Number(last.time) : NaN;
    if (!Number.isFinite(now)) {
      return null;
    }
    // ISSUE-078: セッション始端は NY17:00 ET 基準（sessionDayStart）。1D の forming 始端も同関数
    //   （バー周期＝セッション日）。日中足の forming は従来どおり UTC floor（バー不変）。
    const tf = this._getContext().timeframe;
    const sessionStart = sessionDayStart(now);
    const formingStart = tf === '1D'
      ? sessionStart
      : Math.floor(now / (TF_BAR_SEC[tf] ?? 86400)) * (TF_BAR_SEC[tf] ?? 86400);
    return Math.min(sessionStart, formingStart);
  }

  // ライブ tick 契機。増分（ticklive）: 未 enter なら _enterTicklive、以降は base=0 尾部を addTick して
  //   snapshot を反映。formingStart 変化（rollover）で _enterTicklive を再実行。
  //   非増分: this.refresh() へ byte-identical 委譲（ticklive OFF / formingClient 未注入＝回帰ゼロ）。
  //   tick 逐次成長の実体は MpTickGrowth（A1）へ外出しした（ISSUE-181）。以下は subclass
  //   （ReplayMarketProfileActor）の inherited 呼出・既存テストを温存する薄い委譲。
  async onLiveTick() {
    return this._growth.onLiveTick();
  }

  // UC-01: base=1 を取得して accumulator を init、forming tick 列を畳み込み、snapshot を描画する。
  async _enterTicklive() {
    return this._growth.enter();
  }

  // ticklive を解除する（累積器破棄・通常経路復帰・冪等）。表示モードの ticklive トグルは
  //   MpModeTransition（A3）、成長エンジンの累積器/尾部は MpTickGrowth（A1）が所有する。
  _exitTicklive() {
    this._mode.setTicklive(false);
    this._growth.exit();
  }

  // MP 表示中の右マージン（プロファイル専用領域）を renderer へ委譲する。
  //   on=true で PROFILE_MARGIN_FRACTION（=0.30）ぶんローソクを左へ寄せ、false で復元。
  //   renderer.setRightMarginFraction 非提供時は no-op（後方互換）。冪等。
  _applyProfileMargin(on) {
    return this._layout.applyProfileMargin(on);
  }

  // sessions（日別プロファイル分割）を表示中か。
  //   MP 有効かつ sessions トグル ON のときだけ true（OFF/通常モードでは既存挙動を変えない）。
  isSessions() {
    return this._enabled && !!this._sessions;
  }

  // 日別タイル反映＋初回オートズームは MpSessionTiles（A4）へ外出しした（ISSUE-181）。
  //   以下は subclass（ReplayMarketProfileActor）の inherited 呼出・既存テストを温存する薄い委譲。
  _applySessions(profile) {
    return this._tiles.applySessions(profile);
  }

  _setReplay(on) {
    return this._replayScrub.setReplay(on);
  }

  async onReplayControlsChange() {
    return this._replayScrub.onControlsChange();
  }

  isReplay() {
    return this._replayScrub.isReplay();
  }

  async setReplayCursor(time) {
    return this._replayScrub.setCursor(time);
  }

  // to=T（＋増分2 の from/today／sessions）を重畳して 1 回取得し、profile を反映する（null は前回描画保持）。
  async _fetchAt(time) {
    const profile = await this._client.fetchProfile({
      ...this._getContext(), ...this._params, to: time,
      ...this._replayExtra(time), ...this._sessionsExtra(), ...this._dispExtra(),
    });
    if (profile) {
      this._primitive.setProfile(profile);
      this._applySessions(profile);
    }
  }

  // sessions ON 時のみ context へ sessions:true を載せる（client が &sessions=1 を付与）。OFF は載せない（後方互換）。
  // 取得パラメータの URL コンテキスト写像は MpFetchParams（A6）へ外出しした（ISSUE-181）。
  //   以下は subclass の inherited 呼出・既存テストを温存する薄い委譲（挙動不変・URL byte 不変）。
  _sessionsExtra() {
    return this._fetchParams.sessionsExtra();
  }

  _periodExtra() {
    return this._fetchParams.periodExtra();
  }

  // 単一時計 seam（ISSUE-129）: present は常に空＝URL byte 不変（実時計＝ライブの現在がそのまま正）。
  //   replay subclass が override し、成長 push 中の zp でリビール tick 秒を to（as-seen-at-t の T＝
  //   リプレイの現在時刻）として細粒度化する（backend が now=to で境界日をライブ同一の経過分クランプ
  //   ＝1D でも日内推移が成長）。ctx の後に spread されるため to を上書きできる。
  _clockExtra() {
    return {};
  }

  // 表示幅(bp)→barw(pt) 写像（ISSUE-079 二層構造: 計算=1bp 固定・見せ方=自由）。
  //   dispbp 指定時、最新終値 close から barw = close × bp/1e4 を導出し、既存の resmode='range'
  //   ＋range(pt) 経路（client の &barw=・forming の base 整列・barw ロックまで全て再利用）へ写像する。
  //   backend 変更なしで時代整合（要求時の現在価格基準）を自動確保する。明示 resmode/range が
  //   ある場合（legacy 保存インスタンス）はそれを優先（後方互換）。ローソク未取得は写像せず
  //   サーバ既定（bins=60）へ縮退（非破壊）。
  _dispExtra() {
    return this._fetchParams.dispExtra();
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
    // ISSUE-067: 日別×tf-period 描画モード（sessionsDrawnByTfPeriod=true）では、重い全期間
    //   /market_profile?sessions=1&src=<src>（dwell=1.4s・zp=3.3s／窓非依存の固定約1s）を**叩かない**。
    //   日別プロファイル列は tf-period 列アクターが供給し poc/va も各列が保持する。ここでは日別タイル/
    //   読取欄を null リセットし、初回のみ candle 範囲へ focus する（→可視レンジ変化で tf-period 取得）。
    //   tf-period 列は onParamsChanged（composition_root→tfPeriodActor 即時再取得）と可視レンジ購読で
    //   取得されるため、本フェッチに依存せず<1sで描ける。読取欄の当日MPは列由来へ簡素化（依頼者承認 A案）。
    // ISSUE-080（依頼者裁定 2026-07-15）: 日別×1m/5m×zp は非対応＝代替粒度（日タイル）を出さない。
    //   fetch もせず表示をクリアし、ローソクを可視のまま維持する（「作れないソースは出さない」原則。
    //   gear では option 無効化済みだが、時間足切替で事後にこの状態へ到達し得るため実行時も防御）。
    if (this._sessions
        && mpSourceCapability(this._params.src).blockedSessionTfs.has(this._getContext().timeframe)) {
      if (this._primitive && typeof this._primitive.setSessions === 'function') {
        this._primitive.setSessions(null);
      }
      if (this._renderer && typeof this._renderer.setSessionMP === 'function') {
        this._renderer.setSessionMP(null);
      }
      if (this._renderer && typeof this._renderer.setCandleTransparency === 'function') {
        this._renderer.setCandleTransparency(false);
      }
      return;
    }
    if (this._sessions && this._sessionsDrawnByTfPeriod()) {
      this._applySessions(null);      // タイル非描画（tfDraws は null）＋読取欄クリア＋candle 透明化は tf-period 委譲。
      this._focusSessionsPending();   // 初回のみ candle 範囲へ focus（列を画面内へ）。
      if (this._growing) {
        // ISSUE-083: growing（FOLLOW）中は当日列を tf-period 側で育成する（live tick 経路の refresh は
        //   ここへ毎回到達する＝発火は毎回・再取得の間引きは tf-period 側 throttle が担う）。
        this._onSessionsLiveGrow();
      }
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
        ...this._getContext(), ...this._params, ...this._sessionsExtra(), ...this._periodExtra(),
        ...this._dispExtra(), ...this._clockExtra(),
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

  // ISSUE-067: 日別 focus を candle 範囲だけで行う（sessions フェッチをスキップする tfDraws 経路用）。
  //   実体は MpSessionTiles（A4）が持つ（ISSUE-181）。以下は既存呼出面を温存する薄い委譲。
  _focusSessionsPending() {
    return this._tiles.focusPending();
  }

  // attach 対象の ISeriesPrimitive を解決する（ISSUE-099 🟡-5）。primitive が ProfileSink
  //   ファサードのとき下層 primitive（seriesPrimitive()）を返し、生 primitive（既存テストの fake）は
  //   そのまま返す＝単一 attach 点を維持しつつ挙動不変。
  // チャートレイアウト（attach／右マージン／detach 時の復元）は MpChartLayout（A5）へ外出しした
  //   （ISSUE-181）。以下は subclass の inherited 呼出・既存テスト・composition root 配線を温存する
  //   薄い委譲（挙動不変）。
  _attachTarget() {
    return this._layout.attachTarget();
  }

  _ensureAttached() {
    return this._layout.ensureAttached();
  }

  detach() {
    return this._layout.detach();
  }
}
