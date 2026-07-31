// IndicatorController（adapter/front/indicator_controller.js）。
//
// 設計入力: 内部設計書 §3.3.5（UI イベント→facade、Presenter を作らずここで表示変換）、
//   §3.3.6（F3 系列名不一致検出の実行主体・_validateSeriesNames）、
//   UC-01..07（apply/recompute/toggleVisible/remove/toggleFavorite/persist/restore）。
//
// 責務分割:
//   - 純ロジック: _expectedSeriesNames / _validateSeriesNames（F3 照合・DOM 非依存・単体テスト対象）
//   - UI 配線:  bind() 以降（ツールバー/ダイアログ/凡例の DOM イベント → facade → renderer → persistence）
//
// upstream JS の系列追加系 API は一切参照しない（renderer 経由のみ・§2.2 隔離）。

import {
  emptyState,
  apply,
  recompute,
  toggleVisible as facadeToggleVisible,
  remove as facadeRemove,
  toggleFavorite as facadeToggleFavorite,
  setSeriesStyles,
  reconcileSeriesStyles,
} from '../../usecase/facade.js';
import { categories as catalogCategories } from '../../usecase/catalog.js';
import { PropertiesDialog } from './properties_dialog.js';
import { IndicatorLegendView } from './indicator_legend_view.js';
import { buildMpParams, deriveMpMode, deriveMpResmode } from './market_profile_params.js';
import { MarketProfileController } from './market_profile_controller.js';
import { TimeframeController } from './timeframe_controller.js';
import { IndicatorDialogController } from './indicator_dialog_controller.js';
// F3 系列名照合（§3.3.6）の純ロジック（ISSUE-181・SRP で外出し）。
import {
  expandSeriesNamePattern,
  expectedSeriesNames,
  validateSeriesNames,
} from './series_name_matcher.js';
import { INTRABAR_FORMING_IDS } from '../../usecase/intrabar_forming_ids.js';
import { isActorDriven } from '../../usecase/actor_driven_ids.js';
import { STALL_DEADLINE_MS, UpdateScheduler } from './update_scheduler.js';
import { RecomputeGate } from './recompute_gate.js';
import { SeriesRenderRouter } from './series_render_router.js';
import { IndicatorStateStore } from './indicator_state_store.js';

// STALL_DEADLINE_MS の単一ソースは update_scheduler.js（ISSUE-157・SOLID 是正 🔴-1 で抽出）。
//   既存 import（テスト・他ファイル）を壊さないため本モジュールからも再 export する。
//   ※ 「export { X } from モジュール」の再 export 構文は build.mjs の stripModuleSyntax
//     （import 行剥がし）で壊れるため、import 済みシンボルの別行 export
//     （剥がし後は無害なブロック文）にする。
export { STALL_DEADLINE_MS };

// =========================================================================
// フロントロール契約（ISP・ISSUE-099 🟡-3/🟡-4）
// -------------------------------------------------------------------------
// TimeframeController / MarketProfileController は host（IndicatorController インスタンス）の
// 広い公開面（約40メソッド＋20超フィールド）ではなく、ロール専用の狭い契約にのみ依存する。その
// 契約（各 controller が実際に読む/呼ぶ最小メンバー集合）を本ファイルへ単一ソースで明文化する:
//   - @typedef で「ロール型」を宣言（各 controller は JSDoc import 型でこの契約に依存宣言・実行時 import 無し）
//   - 凍結ロール記述オブジェクトで「必須 method / 必須 field / optional field」を列挙（構造充足テストの固定点）
// IndicatorController（present 共有ベース）はメンバー名・挙動を一切変えず、既存構造のまま本契約を
// 構造的に満たす（加法的・非破壊）。symlink 単一ソースで継承される ReplayIndicatorController（replay）も
// 無改変で同契約を満たす（optional field _untilTime は replay subclass のみ在席する reveal seam）。

/**
 * TimeframeController（時間足取得・切替ロール・A3）が host に要求する最小契約。
 *
 * @typedef {object} TimeframeHost
 * @property {function} recomputeGate        再計算バッチの競合ガード（RecomputeGate）を返す。
 * @property {string} _datasetRef            計算対象データセット参照（read）。
 * @property {{uiState: object}} _state      UI 永続状態を保持する純状態オブジェクト（read/write: uiState）。
 * @property {{setCandles: function}} _renderer  メイン系列差替に用いる renderer（read: setCandles を呼ぶ）。
 * @property {function} recomputeAllApplied  適用済み全指標の再計算入口（ライブ更新と共通・host 温存）。
 * @property {function} _persistAll          applied/favorites/uiState を永続化する。
 * @property {{timeframeBtns?: Iterable}} [_el]  時間足ボタン DOM（bind() 後のみ在席・optional）。
 */

/**
 * MarketProfileController（MP アクター駆動オーケストレーションロール・A7）が host に要求する最小契約。
 *
 * @typedef {object} MarketProfileHost
 * @property {?object} _marketProfile        MP アクター（任意注入・未注入時は MP 分岐 no-op）。
 * @property {?function} _mpModeResolver     MP 表示モードの実効解決役（present 固有・任意注入）。
 * @property {?function} _mpGrowthResolver   MP 成長状態の解決役（present 固有・任意注入）。
 * @property {{applied: Array}} _state       適用済みインスタンスを保持する純状態オブジェクト（read/write）。
 * @property {{get: function}} _catalog      指標定義カタログ（read: get）。
 * @property {Map} _meta                     instanceId -> { def } 描画済みメタ。
 * @property {string} _datasetRef            計算対象データセット参照（read）。
 * @property {?object} _document             プロパティダイアログ構築用 document（null 可）。
 * @property {string} _mode                  計算モード（'a'=file:// / 'b'=served）。
 * @property {string} _timeframe             現在の表示時間足（gear ダイアログ context 用・read）。
 * @property {function} _mpParams            MP params 組み立て（subclass override を host 経由で尊重）。
 * @property {function} _isMarketProfile     def が MP 指標か判定する。
 * @property {function} _paramsObject        params（配列/オブジェクト）を平坦オブジェクトへ正規化する。
 * @property {function} _renderLegend        凡例を再描画する。
 * @property {function} _defaultVariant      def の既定 variant を返す。
 * @property {function} _withParams          state の instance params を差し替える。
 * @property {function} _defaultParams       def の既定 params を返す。
 * @property {function} _persistAll          applied/favorites/uiState を永続化する。
 * @property {function} _commitState         協働子が算出した次 state を確定する（直接代入の代替）。
 * @property {?number} [_untilTime]          reveal（replay）の現在バー T。present は非在席（optional）。
 */

// TimeframeHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const TIMEFRAME_HOST_CONTRACT = Object.freeze({
  role: 'TimeframeHost',
  // ISSUE-181: 深さカウンタ（旧 _recomputeDepth / _recomputeLastStartMs）は RecomputeGate が
  //   所有するため契約から外し、ゲート取得メソッド recomputeGate を契約面に置く。
  methods: Object.freeze(['recomputeAllApplied', '_persistAll', 'recomputeGate']),
  // ISSUE-181: 時間足ロールの状態（_timeframe / _recentBars / _loadCandles / 変更購読者）は
  //   TimeframeController が所有するため契約から外れた。host に残るのは他アクターの持ち物のみ。
  fields: Object.freeze(['_datasetRef', '_state', '_renderer']),
  // bind() 後のみ在席（fresh インスタンスでは未在席・controller は optional chaining で許容）。
  optionalFields: Object.freeze(['_el']),
});

// MarketProfileHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const MARKET_PROFILE_HOST_CONTRACT = Object.freeze({
  role: 'MarketProfileHost',
  methods: Object.freeze([
    '_mpParams', '_isMarketProfile', '_paramsObject', '_renderLegend',
    '_defaultVariant', '_withParams', '_defaultParams', '_persistAll',
    // ISSUE-181: state 更新は host のフィールドへ直接代入せず本メソッド経由で依頼する。
    '_commitState',
  ]),
  fields: Object.freeze([
    '_marketProfile', '_mpModeResolver', '_mpGrowthResolver', '_state',
    '_catalog', '_meta', '_datasetRef', '_document', '_mode', '_timeframe',
  ]),
  // reveal seam: replay subclass のみ在席（present base では非在席・controller は != null で許容）。
  optionalFields: Object.freeze(['_untilTime']),
});

export class IndicatorController {
  // mode: 計算モード。'b'=served（ライブ API・params 実反映）/ 'a'=file://（埋め込み事前計算）。
  //   既定 'a'（従来挙動・単体テスト互換）。composition root が served 判定で 'b' を注入する。
  constructor({
    catalog, compute, persistence, renderer, document: doc = null, mode = 'a',
    datasetRef = 'sample', timeframe = '1D', recentBars = null, loadCandles = null,
    marketProfile = null, mpModeResolver = null, mpGrowthResolver = null,
  }) {
    this._catalog = catalog;
    this._compute = compute;
    this._persistence = persistence;
    this._renderer = renderer;
    this._document = doc;
    // 凡例/お気に入り/ダイアログリストの純 DOM 構築を担う View（ISSUE-038・SRP 是正）。
    //   controller は行の view-model＋コールバックを注入するだけ。doc=null でも構築でき、
    //   その場合 View 各メソッドは要素不在で no-op（node 単体テスト互換）。
    this._legendView = new IndicatorLegendView({ document: doc });
    // 描画振分（A9・ISSUE-181）を委譲する協働子。描画先 renderer を自身の出力ポートとして所有する。
    this._router = new SeriesRenderRouter(this, renderer);
    // 永続化・復元（UC-07・A10・ISSUE-181）を委譲する協働子。復元中 Promise は協働子が所有する。
    this._store = new IndicatorStateStore(this);
    this._mode = mode;
    // Market Profile アクター（任意注入）。computeId==='market_profile' の指標を
    //   /compute 経由でなく本アクター（GET /market_profile → primitive）へ委譲する。
    //   既存トグル（#market-profile-toggle）とは別導線（二重導線）。未注入時は MP 分岐が no-op。
    this._marketProfile = marketProfile;
    // ライブ連動: MP 表示モードの実効解決役（present 固有・任意注入）。(userMode)->effectiveMode。
    //   注入時のみ MP へ渡す mode を実効モード（FOLLOW→ticklive / ANALYSIS→記憶モード）へ解決する。
    //   未注入（連動なし＝A方式・MP 不在・連動未配線）は mode をそのまま渡す＝byte 不変。
    this._mpModeResolver = typeof mpModeResolver === 'function' ? mpModeResolver : null;
    // Model A 直交化: MP 成長状態の解決役（present 固有・任意注入）。()->boolean（FOLLOW=true / ANALYSIS=false）。
    //   注入時のみ MP へ growing 信号（applyGrowthState）を適用し、growing 時のみ onLiveTick で成長させる。
    //   未注入（連動なし＝A方式・MP 不在・連動未配線）は growing を適用しない＝byte 不変。
    this._mpGrowthResolver = typeof mpGrowthResolver === 'function' ? mpGrowthResolver : null;
    // 計算対象データセット（B方式の /compute で使用）。既定 'sample'（後方互換・単体テスト互換）。
    this._datasetRef = datasetRef;
    // 時間足取得・切替（A3）を委譲する協働子（ISSUE-094 🔴-4 / ISSUE-181）。setTimeframe /
    //   ボタン同期 / gateway の timeframe・limit 注入を担う。ISSUE-181: 時間足ロールの状態
    //   （現在足・直近表示本数・candles ローダ・変更購読者）は本協働子が所有する（host は
    //   下の互換アクセサで委譲するだけでフィールドを持たない）。ライブ再計算入口
    //   （recomputeAllApplied）は controller 温存。
    this._tf = new TimeframeController(this, { timeframe, recentBars, loadCandles });

    // メモリ状態（facade の純状態オブジェクト）。
    this._state = emptyState();
    // instanceId -> { def } 描画済みメタ（凡例再描画・recompute 用）。
    this._meta = new Map();
    // 再計算バッチの競合ガード（深さカウンタ＋時限）は RecomputeGate が所有する
    //   （ISSUE-181: 状態も一緒に移す。TimeframeController の host フィールド直接代入も解消）。
    //   ライブ更新（LiveUpdater）は独自フラグを持たず isRecomputing() を参照し、再計算中の
    //   tick をスキップする。
    this._gate = new RecomputeGate();
    // ISSUE-157（クロック駆動設計）: 指標更新の「要求フラグ＋クロック」駆動は UpdateScheduler へ
    //   委譲する（SOLID 是正 🔴-1・設計意図の詳細は update_scheduler.js 冒頭コメント参照）。
    //   実体（末尾差分/full 再計算）と外部バッチ述語（isRecomputing・時限式）を依存注入する。
    this._scheduler = new UpdateScheduler({
      runForming: () => this.recomputeFormingTails(),
      runFull: () => this.recomputeAllApplied({ mode: 'full' }),
      isBlocked: () => this.isRecomputing(),
    });
    // MP（A7）アクター駆動のオーケストレーションを委譲する協働子（ISSUE-094 🔴-4）。
    //   host=this を渡し、apply/enable/toggle/remove/gear/reapply/restore/live-recompute を委譲する。
    //   subclass の inherited メソッド呼出（this._toggleMarketProfileVisible 等）・_mpParams override を
    //   温存するため base の各 MP メソッドは本協働子への薄いラッパへ縮退する（byte 挙動不変）。
    this._mp = new MarketProfileController(this);
    // 指標追加ダイアログ（一覧・絞り込み・開閉）を委譲する協働子（ISSUE-181・A8）。
    //   絞り込み UI 状態（旧 this._filter）は協働子が所有する（状態も一緒に移す）。
    this._dialog = new IndicatorDialogController(this);
  }

  // 競合ガード: 再計算バッチ実行中なら true。LiveUpdater が tick 先頭で参照しスキップ判定する。
  //   ISSUE-157: 時限式。深さカウンタはバッチの await がハングすると finally が走らず永久に
  //   正のままになる（＝全ゲートが恒久閉鎖）。STALL_DEADLINE_MS を超えて「実行中」のバッチは
  //   ハングとみなし false を返す（守るべき健全なバッチはもう存在しない）。
  isRecomputing() {
    return this._gate.isBusy();
  }

  // 競合ガード本体（RecomputeGate）を協働子へ公開する（TimeframeController が enter/exit する）。
  recomputeGate() {
    return this._gate;
  }

  // ---- 互換アクセサ: 旧 host フィールド面（_recomputeDepth / _recomputeLastStartMs）----
  //   実体は RecomputeGate が所有する（host はフィールドを持たない）。既存テストが任意の
  //   深さ・開始時刻を注入して時限挙動を検証するため、読み書き両方を委譲で維持する。
  get _recomputeDepth() { return this._gate.depth(); }

  set _recomputeDepth(value) { this._gate.setDepth(value); }

  get _recomputeLastStartMs() { return this._gate.lastStartMs(); }

  set _recomputeLastStartMs(value) { this._gate.setLastStartMs(value); }

  // ---- 互換アクセサ: 旧 host フィールド面（時間足ロール・ISSUE-181）----
  //   実体は TimeframeController が所有する。既存の読み書き（restore の時間足確定、
  //   replay.js の計算窓 _recentBars 差し替え、composition root/テストの参照）を温存する。
  get _timeframe() { return this._tf.current(); }

  set _timeframe(value) { this._tf.setCurrent(value); }

  get _recentBars() { return this._tf.recentBars(); }

  set _recentBars(value) { this._tf.setRecentBars(value); }

  get _loadCandles() { return this._tf.loader(); }

  get _timeframeObserver() { return this._tf.observer(); }

  // =========================================================================
  // 足内（形成中バー）末尾差分再計算 — ライブ・リプレイ同一設計（2026-07-22 ユーザー裁定）
  //   指標の末尾点は「価格（形成中バー）の更新と同じ粒度」で追従する。全再計算（remove+redraw）
  //   はバー確定時のみ。対象は共有登録リスト INTRABAR_FORMING_IDS（ISSUE-145 規約）。
  // =========================================================================

  // 登録指標（INTRABAR_FORMING_IDS）の末尾点のみを latest 差分で再計算する。
  //   forceTail=true で混在 kind（line+horizontal_line＝marod 系）でも末尾差分経路へ倒す
  //   （replay の recomputeFormingLatest と同一機構＝両モードの実体を単一化）。
  //   非登録指標（帯系等）は触らない（因果窓ゆえ足内で動くべき値がない）。
  async recomputeFormingTails() {
    // ISSUE-156（C）: 登録指標を並列リクエストする（サーバは計算プール化済み＝指標間の
    //   レイテンシが重ならない）。各 recomputeInstance は自身の job.series で独立に描画するため
    //   並列安全（_recomputeDepth は深さカウンタ＝並列でも整合）。失敗は個別に握りつぶし
    //   （Promise.allSettled）、他指標の末尾更新を道連れにしない。
    const targets = [...this._state.applied].filter(
      (inst) => INTRABAR_FORMING_IDS.has(inst.indicatorId) && this._meta.has(inst.instanceId),
    );
    await Promise.allSettled(targets.map((inst) => this.recomputeInstance(
      inst.instanceId, null, this._paramsObject(inst.params), { mode: 'latest', forceTail: true },
    )));
  }

  // tick 粒度の末尾差分要求（UpdateScheduler へ委譲・ISSUE-157 クロック駆動設計）。
  //   呼び出し元 API 温存のための薄い委譲（coalesce/latest-wins は scheduler 側）。
  requestFormingRecompute() {
    this._scheduler.requestForming();
  }

  // バー確定時の full 再計算要求（ISSUE-151: 必達・UpdateScheduler へ委譲）。
  requestFullRecompute() {
    this._scheduler.requestFull();
  }

  // =========================================================================
  // F3 系列名照合（§3.3.6・DOM 非依存の純ロジック）
  // =========================================================================

  // 期待集合の算出は series_name_matcher.js（純関数）へ外出しした（ISSUE-181・SRP）。
  //   以下 3 メソッドは replay subclass の this._validateSeriesNames 呼出・差し替えテスト・
  //   既存単体テスト（ctrl._expectedSeriesNames 等）を温存する薄い委譲（挙動不変）。
  _expectedSeriesNames(def, params = null) {
    return expectedSeriesNames(def, params);
  }

  _expandPattern(pattern, params = null) {
    return expandSeriesNamePattern(pattern, params);
  }

  _validateSeriesNames(payloads, def, params = null) {
    return validateSeriesNames(payloads, def, params);
  }

  // 描画振分（kind 別の renderer 呼び分け）は SeriesRenderRouter（A9）へ外出しした（ISSUE-181）。
  //   以下は subclass の inherited 呼出・既存テストを温存する薄い委譲（挙動不変）。
  _draw(instanceId, def, series, params = null) {
    return this._router.draw(instanceId, def, series, params);
  }

  // AppliedInstance.styles（系列名 -> {color?,width?,style?,visible?}）を renderer へ適用する。
  //   未保存（null/空）や renderer 非対応（後方互換 Fake/SSR）は no-op。
  //   ISSUE-110 🔴-1: 適用前に現在の実系列名集合と突合し、実系列に存在しない stale キー
  //   （tgp の q_low/q_high や profit_band の probabilities 変更で系列が改名された等）を
  //   state から剪定する（無反映キーの永続蓄積と params 復帰時の意図せぬ復活を遮断）。
  //   実系列集合が取得不能・空のときは判定不能のため剪定しない（reconcile 側で防御）。
  _applyStoredStyles(instanceId) {
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    if (!inst || !inst.styles || typeof this._renderer.applySeriesStyle !== 'function') {
      return;
    }
    if (typeof this._renderer.getSeriesStyles === 'function') {
      const currentNames = this._renderer.getSeriesStyles(instanceId).map((m) => m.name);
      this._state = reconcileSeriesStyles(this._state, instanceId, currentNames);
    }
    const reconciled = this._state.applied.find((i) => i.instanceId === instanceId);
    const styles = reconciled && reconciled.styles;
    if (!styles) {
      return;
    }
    for (const [name, patch] of Object.entries(styles)) {
      this._renderer.applySeriesStyle(instanceId, name, patch);
    }
  }

  // =========================================================================
  // UC オーケストレーション（facade + ports）
  // =========================================================================

  // MP 種別（アクター委譲型）判定。真なら _draw / _gatewayAdapter をバイパスする。
  // アクター駆動型（/compute を持たない）指標かの判定。具体名分岐は usecase の能力台帳
  //   （actor_driven_ids.js）へ移譲した（SOLID 是正 🔴-4・OCP: 新しいアクター駆動指標の追加は
  //   台帳への 1 行追記で完結し本 controller は不変）。メソッド名は host 契約
  //   （MARKET_PROFILE_HOST_CONTRACT）・subclass override 互換のため温存する。
  _isMarketProfile(def) {
    return isActorDriven(def);
  }

  // MP アクターへ渡す取得 params（resmode/bins/va/src/range）を組み立てる（apply/gear/restore 共通）。
  //   ISSUE-094 🔴-4: MP のパラメータ・スキーマ写像は market_profile_params.js（純関数）へ外出しした。
  //   本メソッドは薄い委譲のみ（subclass の super._mpParams / 既存テストの ctrl._mpParams 呼出を温存）。
  _mpParams(p = {}) {
    return buildMpParams(p);
  }

  // MP 委譲一式（apply/enable/toggle/remove/gear/reapply/restore/live-recompute）は
  //   market_profile_controller.js（MarketProfileController）へ外出しした（ISSUE-094 🔴-4）。
  //   以下は subclass の inherited 呼出（this._applyMpGrowth 等）・既存テスト・composition root 配線を
  //   温存するための薄い委譲ラッパ（挙動は抽出前と byte 等価）。_mpParams override は host 経由で尊重される。
  _applyMpGrowth() {
    return this._mp.applyMpGrowth();
  }

  reapplyMarketProfileMode() {
    return this._mp.reapplyMode();
  }

  // resmode/mode（表示・解像度モード）の後方互換ヘルパは market_profile_params.js（純関数）へ外出しした
  //   （ISSUE-094 🔴-4）。本メソッドは薄い委譲のみ（内部呼出・既存呼出の互換温存）。
  _deriveResmode(p = {}) {
    return deriveMpResmode(p);
  }

  _deriveMode(p = {}) {
    return deriveMpMode(p);
  }

  // UC-02 指標追加: seq 採番→compute（gen=0）→F3→描画→persist。
  //   MP 種別（computeId==='market_profile'）は /compute をバイパスし MarketProfileActor へ委譲する。
  // 指標の適用/削除の**完了**を購読する（任意・1 個）。ISSUE-037。
  //
  //   リプレイ層（`replay.js`）は「適用/削除のあとに減光境界を再同期する」必要があるが、
  //   render を経ない経路のため、従来は `controller.applyIndicator` / `removeInstance` を
  //   **実行時に monkeypatch** して後処理を差し込んでいた（destroy で原状復帰）。
  //   monkeypatch は (1) 差し替え順序に依存して壊れる (2) 復元漏れが静かに残る
  //   (3) subclass の override と二重に噛む、という脆さがある。
  //   購読スロットを公開して置き換える（`setTimeframeObserver` と同型の規律）。
  //
  //   通知は「適用・削除が完了した後」に 1 回。適用が no-op（未知 id 等）でも通知する
  //   ＝ monkeypatch 時代と同一（呼び出しごとに後処理が走っていた）。
  setAppliedObserver(observer) {
    this._appliedObserver = typeof observer === 'function' ? observer : null;
  }

  _notifyApplied() {
    if (this._appliedObserver) {
      this._appliedObserver();
    }
  }

  // 適用（購読者への通知を伴う薄いラッパ）。実処理は _applyIndicatorInner。
  async applyIndicator(indicatorId, variant) {
    const result = await this._applyIndicatorInner(indicatorId, variant);
    this._notifyApplied();
    return result;
  }

  async _applyIndicatorInner(indicatorId, variant) {
    // ISSUE-153: 復元（restore＝_state 丸ごと置換）と競合すると、先に適用した instance が
    //   state から消えて描画だけ残る（孤児化）。復元中は完了を待ってから適用する。
    if (this._restoreInFlight) {
      await this._restoreInFlight;
    }
    const def = this._catalog.get(indicatorId);
    if (!def) {
      return null;
    }
    const params = this._defaultParams(def);
    if (this._isMarketProfile(def)) {
      return this._applyMarketProfile(def, variant, params);
    }
    const gateway = this._gatewayAdapter();
    const { state, instance } = await apply(
      this._state,
      { indicatorId, variant: variant ?? this._defaultVariant(def), params, datasetRef: this._datasetRef },
      gateway,
    );
    this._state = state;
    this._meta.set(instance.instanceId, { def });
    this._draw(instance.instanceId, def, this._lastSeries, params);
    this._persistAll();
    this._renderLegend();
    return instance;
  }

  // MP 委譲ラッパ（実体は MarketProfileController・ISSUE-094 🔴-4）。subclass の inherited 呼出
  //   （this._toggleMarketProfileVisible / this._removeMarketProfile）と既存テスト（ctrl._onGearMarketProfile）を
  //   温存するための薄い委譲（挙動は抽出前と byte 等価）。
  _applyMarketProfile(def, variant, params) {
    return this._mp.applyMarketProfile(def, variant, params);
  }

  _toggleMarketProfileVisible(inst) {
    return this._mp.toggleVisible(inst);
  }

  _removeMarketProfile(inst) {
    return this._mp.removeInstance(inst);
  }

  _onGearMarketProfile(inst, def) {
    return this._mp.onGear(inst, def);
  }

  // 協働子が算出した次 state を確定する（ISSUE-181: 協働子が host の private フィールドへ
  //   直接代入しない＝状態の所有者は host、更新の依頼は本メソッド経由に一本化する）。
  _commitState(state) {
    this._state = state;
  }

  // 直近 compute 応答 series を確定する（協働子が host のフィールドへ直接代入しないための依頼口）。
  //   applyIndicator/restore の単発（直列）経路が _lastSeries を読むため面を維持する。
  _commitLastSeries(series) {
    this._lastSeries = series;
  }

  // 復元した時間足を確定する（所有者は TimeframeController。協働子が host のフィールドへ
  //   直接代入しないための依頼口）。
  _commitTimeframe(timeframe) {
    this._tf.setCurrent(timeframe);
  }

  // AppliedInstance（不変・凍結）の params のみ差し替えた state を返す（_withVariant と同型）。
  _withParams(state, instanceId, values) {
    const pairs = Object.entries(values ?? {});
    return {
      ...state,
      applied: state.applied.map((i) =>
        i.instanceId === instanceId
          ? Object.assign(Object.create(Object.getPrototypeOf(i)), i, { params: pairs })
          : i),
    };
  }

  // UC-03 再計算（設定変更・variant 切替）: generation 競合破棄は facade.recompute に集約（§6.6）。
  //   opts.mode='latest' は Latest 増分計算（gateway へ mode 伝播・末尾K点を updateSeriesTail へ
  //   差分反映し remove+_draw の全描画はしない）。既定 'full' は従来どおり remove+redraw。
  //   commitParams=true（ユーザーの明示操作＝歯車 OK / variant 切替 / デフォルト復元）は、
  //   計算の完了を待たずに params を live state へ確定する（ISSUE-201。下記 _computeInstance 参照）。
  async recomputeInstance(instanceId, newVariant, newParams, { mode = 'full', forceTail = false, commitParams = false } = {}) {
    const job = await this._computeInstance(instanceId, newVariant, newParams, { mode, forceTail, commitParams });
    if (!job || !job.accepted) {
      return job ? job.accepted : false;
    }
    // 単体再計算は計算直後に同期描画→persist→legend（従来の挙動と等価）。
    this._renderInstance(job);
    this._persistAll();
    this._renderLegend();
    return true;
  }

  // 計算フェーズ（async）: state を更新し、描画に必要な series を job へ退避して返す（ISSUE-023）。
  //   renderer は呼ばない。複数指標を一括描画する recomputeAllApplied は全 job を集めてから
  //   _renderInstance を await を挟まない同期パスで呼び、中間ペイント（バラバラ更新）を防ぐ。
  //   ISSUE-165: 並列実行しても安全（recomputeAllApplied / recomputeFormingTails が並列に呼ぶ）。
  //   - series は per-call gateway（gateway.lastSeries）から取る。共有 this._lastSeries だと
  //     並列時に他インスタンスの compute 完了が microtask 間へ割り込んで上書きし取り違える。
  //   - state は丸ごと代入せず当該 instance 行のみマージする。丸ごと代入は並列時に同一
  //     スナップショット由来の最後の代入が勝ち、兄弟インスタンスの世代前進が失われる（lost update）。
  //   ISSUE-201（同一インスタンスの lost update・2026-07-29 実測）: バッチは開始時の
  //     スナップショット params で計算し、完了時にその行を live state へマージする。よって
  //     計算中にユーザーが歯車で params を変えると、**旧 params の行が新 params を上書きする**
  //     （実測: OK 直後に length=200 で 1 回計算 → 0.5 秒後に length=9 の tick 計算が完了 →
  //      保存値が 9 に戻り、以後ずっと 9。ユーザーには「価格が更新されると設定が元に戻る」と見える）。
  //     恒久対策は 2 点セット:
  //       (a) ユーザーの明示操作（commitParams=true）は **await 前に** params を live state へ確定する
  //           ＝ params の正はユーザー操作であり、計算結果ではない。
  //       (b) await 中に当該行が差し替わっていたら（他の確定・他バッチの反映）、この結果は
  //           旧設定由来なので **破棄**する（accepted:false）。描画も state も触らない。次のクロックが
  //           新しい params で計算し直す。ISSUE-105 の「await 中に除去されたら破棄」と同型の規律。
  async _computeInstance(instanceId, newVariant, newParams, { mode = 'full', forceTail = false, commitParams = false } = {}) {
    const meta = this._meta.get(instanceId);
    if (!meta) {
      return null;
    }
    // variant を差し替える場合は def はそのまま、gateway が variant でキー解決。
    if (newVariant) {
      this._state = this._withVariant(this._state, instanceId, newVariant);
    }
    const params = newParams ?? this._defaultParams(meta.def);
    // (a) ユーザーの明示操作は計算を待たずに params を確定する（in-flight 計算の完了順に依存しない）。
    if (commitParams) {
      this._state = this._withParams(this._state, instanceId, params);
    }
    // Latest 差分可否を「要求前」に def から確定する（混在/horizontal 指標は full を要求し
    //   trim されない full データで全描画する＝混在バグ回避）。
    // [reveal seam] forceTail=true（replay 足内追従）は混在指標でも末尾差分（updateSeriesTail）経路へ
    //   倒す。末尾差分は既存系列の最終点のみ更新し horizontal_line は据え置く（履歴潰れ回避）。present は
    //   forceTail 既定 false ＝ this._defCanTailUpdate 判定のみ（byte 挙動不変）。
    const wantLatest = mode === 'latest' && (forceTail || this._defCanTailUpdate(meta.def));
    const gateway = this._gatewayAdapter(newVariant, wantLatest ? 'latest' : 'full');
    // 競合ガード: 再計算中は isRecomputing()=true（ライブ更新の tick がスキップ判定に参照）。
    //   finally で確実にデクリメント（例外時もカウンタが残らない）。ネスト時は最外で解除。
    //   開始時刻の記録は isRecomputing() の時限判定（ISSUE-157）に使う。
    this._gate.enter();
    // (b) 破棄判定の基準。行オブジェクトは変更のたびに差し替えられる（_withParams/_withVariant/
    //   マージのいずれも新しい行を作る）ため、同一性比較で「await 中に変わったか」が判定できる。
    const rowAtCall = this._state.applied.find((i) => i.instanceId === instanceId);
    try {
      const result = await recompute(this._state, instanceId, params, this._datasetRef, gateway);
      // 競合削除ガード（ISSUE-105 🟡-2）: recompute の await 中に凡例 close（removeInstance）で
      //   当該インスタンスが state から除去されると、result.state は除去前スナップショット由来のため
      //   反映すると除去済みインスタンスが「復活」する。これがフェーズ2 で再描画されると
      //   凡例行の無い残留系列（ゾンビペイン）＋永続化汚染を生む。await 後の live state で在席を
      //   確認し、除去済みなら live state に触れず accepted:false を返す。
      const removedDuringAwait = !this._state.applied.some((i) => i.instanceId === instanceId);
      // (b) await 中に当該行が差し替わった＝この結果は旧 params/variant 由来。state も描画も触らない。
      const supersededDuringAwait = !!rowAtCall
        && this._state.applied.find((i) => i.instanceId === instanceId) !== rowAtCall;
      if (removedDuringAwait || supersededDuringAwait || !result.accepted) {
        // 非採用（世代競合破棄・除去済み）は state 変更なし＝live state を据え置く（ISSUE-165:
        //   スナップショット丸ごと代入をやめ、並列時に兄弟の変更を巻き戻さない）。
        return { instanceId, accepted: false };
      }
      // 採用: result.state（スナップショット＋自行のみ更新）から当該 instance 行だけを
      //   live state へマージする（ISSUE-165: 丸ごと代入の lost update 恒久解消）。
      //   facade.recompute の差分は applied[idx] の 1 行のみ（seqCounters/uiState は不変）。
      const row = result.state.applied.find((i) => i.instanceId === instanceId);
      if (row) {
        this._state = {
          ...this._state,
          applied: this._state.applied.map((i) => (i.instanceId === instanceId ? row : i)),
        };
      }
      const inst = this._state.applied.find((i) => i.instanceId === instanceId);
      return {
        instanceId,
        accepted: true,
        def: meta.def,
        params,
        wantLatest,
        // per-call gateway に捕捉された series を job へ確保（ISSUE-165: 共有 _lastSeries は
        //   並列時に他インスタンスの完了割り込みで上書きされ取り違えるため使わない）。
        series: gateway.lastSeries,
        // 非表示状態を描画時に維持するためのフラグ（redraw は可視で再生成するため）。
        hidden: !!(inst && !inst.visible),
      };
    } finally {
      this._gate.exit();
    }
  }

  // 描画フェーズ（同期・実体は SeriesRenderRouter）。replay subclass が this._renderInstance を
  //   呼び、既存テストが差し替えるためメソッド面を温存する（ISSUE-181・薄い委譲）。
  _renderInstance(job) {
    return this._router.renderJob(job);
  }

  _defCanTailUpdate(def) {
    return this._router.canTailUpdate(def);
  }

  _drawLatest(instanceId, def, series, params = null) {
    return this._router.drawLatest(instanceId, def, series, params);
  }

  // UC-04 表示/非表示。
  toggleVisible(instanceId) {
    this._state = facadeToggleVisible(this._state, instanceId);
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    if (inst) {
      this._renderer.setVisible(instanceId, inst.visible);
    }
    this._persistAll();
    this._renderLegend();
  }

  // UC-05 削除。
  // 削除（購読者への通知を伴う薄いラッパ）。実処理は _removeInstanceInner（ISSUE-037）。
  removeInstance(instanceId) {
    const result = this._removeInstanceInner(instanceId);
    this._notifyApplied();
    return result;
  }

  _removeInstanceInner(instanceId) {
    this._renderer.remove(instanceId);
    this._state = facadeRemove(this._state, instanceId);
    this._meta.delete(instanceId);
    this._persistAll();
    this._renderLegend();
  }

  // 時間足切替（§チャート表示時間選択）。関心事は TimeframeController（A3）へ外出しした（ISSUE-094 🔴-4）。
  //   本メソッドは composition root/テスト（controller.setTimeframe）を温存する薄い委譲（byte 挙動不変）。
  setTimeframe(timeframe) {
    return this._tf.setTimeframe(timeframe);
  }

  // 時間足変更の購読者を登録する（任意・1 個）。setTimeframe 適用後に新時間足で呼ばれる。
  setTimeframeObserver(observer) {
    this._tf.setObserver(observer);
  }

  // 適用済み全指標を現在の params / 時間足で再計算・再描画する（ライブ更新の再計算入口）。
  //   competition ガード（generation+1・accepts 破棄）は recomputeInstance に集約済み。
  //   適用が無ければ何もしない（no-op）。
  //   フェーズ1（並列計算・ISSUE-165）: /compute 系指標は全件を並列に要求する（サーバは計算
  //     プール化済み＝指標間のレイテンシが重ならない。時間足切替 1 秒以内の必達要件）。
  //     旧・直列必須の根拠だった 2 つの共有状態は _computeInstance 側で恒久是正済み
  //     （series=per-call gateway 捕捉・state=当該 instance 行のみマージ）。いずれかの compute が
  //     例外なら従来どおり本メソッドは reject し、フェーズ2（描画・persist）へ進まない。描画はしない。
  //   フェーズ2（同期一括描画）: await を挟まず全 job を描画する。中間ペイントが起きないため、
  //     メイン系列（opts.preRender＝setCandles）と全指標が同時に更新される（ISSUE-023）。
  //   persist/legend は描画後に1回だけ。適用 0 でも preRender（候補：メイン系列差し替え）は実行する。
  async recomputeAllApplied({ mode = 'full', preRender = null, skip = null } = {}) {
    // フェーズ1: 並列計算（描画なし）。
    const targets = [];
    for (const inst of [...this._state.applied]) {
      // skip 述語（ISSUE-158 ②・replay 専用 additive）: 一括リビール済み指標の per-step 計算を
      //   省略する。present（ライブ）は skip を渡さない＝挙動不変。
      if (skip && skip(inst)) {
        continue;
      }
      const meta = this._meta.get(inst.instanceId);
      if (!meta) {
        continue;
      }
      // MP 種別は /compute を持たない（backend に compute 無し）。再計算経路（ライブ tick /
      //   足切替）で /compute へ流出させると例外→setTimeframe では preRender 前で全スキップ。
      //   /compute を通さず MP 側協働子（actor.onLiveTick／refresh 委譲）へ外出しする（ISSUE-094 🔴-4）。
      if (this._isMarketProfile(meta.def)) {
        await this._mp.onLiveRecompute(inst);
        continue;
      }
      targets.push(inst);
    }
    const settled = await Promise.allSettled(targets.map((inst) =>
      this._computeInstance(inst.instanceId, null, this._paramsObject(inst.params), { mode })));
    // 例外は従来（直列 await）と同じく呼び出し元へ伝播する（描画・persist はしない）。
    //   全 settled 後に投げるため、他指標の in-flight compute が宙に残らない。
    const rejected = settled.find((s) => s.status === 'rejected');
    if (rejected) {
      throw rejected.reason;
    }
    // jobs は applied 順（targets 順）＝フェーズ2 の描画順は従来と不変。
    const jobs = settled
      .map((s) => s.value)
      .filter((job) => job && job.accepted);
    // フェーズ2: ここから await を挟まない同期一括描画。
    if (preRender) {
      // ISSUE-196（不変条件の構造的保証）: preRender はメインローソク系列の time 集合を入れ替える
      //   （時間足切替・リプレイの足リビール）。本バッチで再描画されない指標の系列は旧 time を
      //   持ち続けるため、lwc の「時間軸の time は当該系列にも存在する」不変条件が破れ、
      //   preRender 内の setData（および以後の全ペイント）が `Value is null` を throw する。
      //   その例外は本バッチを中断させ、指標が旧足のまま固着して次クロックの再計算も同じ throw で
      //   失敗し続ける（実測 102〜160 件/30〜45 秒・full 再計算失敗が反復）。
      //   ここで「再描画されない指標」の系列データを同一同期ブロック内で空にし、違反状態を
      //   発生させない（try/catch で例外を握る応急処置は行わない＝原因側を消す）。
      //   skip 述語で除外した一括リビール指標（replay）は preRender 内の revealTo が同期で
      //   描き直すため、空化 → preRender の順序で結果は不変。
      const drawnIds = new Set(jobs.map((job) => job.instanceId));
      for (const inst of this._state.applied) {
        if (!drawnIds.has(inst.instanceId) && typeof this._renderer.clearInstanceData === 'function') {
          this._renderer.clearInstanceData(inst.instanceId);
        }
      }
      preRender();
    }
    if (jobs.length === 0) {
      return;
    }
    for (const job of jobs) {
      // 競合ガード（ISSUE-105 🟡-2）: フェーズ1 の await 中に凡例 close（removeInstance）で
      //   当該インスタンスが state から除去されていた場合、accepted 済み job を _renderInstance で
      //   描画すると renderer に系列/ペインが再生成され、凡例行の無い「ゾンビペイン」が残留し
      //   ライブ更新を受け続ける。描画直前に state 在席を確認し、除去済みなら描画せず（保険で
      //   renderer からも除く）。通常時（削除なし）は必ず在席＝従来挙動と不変。
      if (!this._state.applied.some((i) => i.instanceId === job.instanceId)) {
        this._renderer.remove(job.instanceId);
        continue;
      }
      this._renderInstance(job);
    }
    this._persistAll();
    this._renderLegend();
  }

  // 時間足セレクタの active 表示同期は TimeframeController へ外出しした（ISSUE-094 🔴-4）。
  //   restore()/bind() の this._syncTimeframeButtons() 呼出を温存する薄い委譲（byte 挙動不変）。
  _syncTimeframeButtons() {
    return this._tf.syncButtons();
  }

  // UC-06 お気に入り切替。
  toggleFavorite(indicatorId) {
    this._state = facadeToggleFavorite(this._state, indicatorId);
    this._persistAll();
    this._renderDialogList();
  }

  // UC-07 永続化・復元は IndicatorStateStore（A10）へ外出しした（ISSUE-181）。
  //   以下は subclass の inherited 呼出（this._persistAll）・composition root（controller.restore）・
  //   既存テストを温存する薄い委譲（挙動不変）。
  _persistAll() {
    return this._store.persistAll();
  }

  async restore() {
    return this._store.restore();
  }

  // 復元実行中 Promise の互換アクセサ（実体は IndicatorStateStore が所有する）。
  //   applyIndicator の競合ガードと、既存テストの直接注入を温存する。
  get _restoreInFlight() { return this._store.inFlight(); }

  set _restoreInFlight(value) { this._store.setInFlight(value); }

  // =========================================================================
  // ヘルパ
  // =========================================================================

  _defaultVariant(def) {
    const variants = def.compute?.variants ?? ['default'];
    return variants[0];
  }

  _defaultParams(def) {
    const params = {};
    for (const p of def.params ?? []) {
      if (p.default !== null && p.default !== undefined) {
        params[p.name] = p.default;
      }
    }
    return params;
  }

  // AppliedInstance.params（[k,v] ペア配列・facade 形）または object を object へ正規化する。
  _paramsObject(params) {
    if (Array.isArray(params)) {
      return Object.fromEntries(params);
    }
    return params ?? {};
  }

  _withVariant(state, instanceId, variant) {
    const next = { ...state, applied: state.applied.map((i) => i) };
    next.applied = state.applied.map((i) =>
      i.instanceId === instanceId ? Object.assign(Object.create(Object.getPrototypeOf(i)), i, { variant }) : i,
    );
    return next;
  }

  // [reveal seam] compute リクエストへ追加する reveal 拡張フィールドを返す（既定=空）。
  //   present は reveal 概念を持たないため空オブジェクト（compute ボディ byte 挙動不変）。
  //   replay subclass が { untilTime, forming } を返すよう override し、時点計算・足内追従を注入する。
  _extraComputeFields() {
    return {};
  }

  // facade.apply/recompute が呼ぶ compute をラップし、応答 series を捕捉（描画用）。
  //   時間足（timeframe）・直近表示本数（limit）の注入は TimeframeController（A3）へ委譲する
  //   （ISSUE-094 🔴-4・facade は純粋を保つ）。B方式は /compute がこれで resample・範囲制限し candles と
  //   時間軸を揃える。A方式は余剰フィールドを無視。
  _gatewayAdapter(variantOverride, mode) {
    const compute = this._compute;
    const self = this;
    const gw = {
      // per-call series 捕捉（ISSUE-165）: 本 adapter は呼び出しごとに生成されるため、
      //   compute 応答の series を自身へ保持すれば並列実行でも取り違えない。
      lastSeries: null,
      async compute(req) {
        // 計算.時間足（params.timeframe）の per-indicator override は TimeframeController が解決する。
        //   backend は params.timeframe を受理引数に含めない（_accepted_kwargs で除外）ため副作用なし。
        const tfParam = req && req.params ? req.params.timeframe : undefined;
        const result = await compute.compute({
          ...req,
          variant: variantOverride ?? req.variant,
          timeframe: self._tf.effectiveTimeframe(tfParam),
          limit: self._tf.limit(),
          // mode（full/latest）を素通し。未指定は compute_http_client がボディに含めない（後方互換）。
          mode: mode === 'latest' ? 'latest' : undefined,
          // [reveal seam] reveal 拡張フィールド（untilTime/forming 等）を素通しする。present は
          //   空オブジェクト（byte 挙動不変＝compute_http_client の `!== undefined` gate で不送信）。
          //   replay subclass が _extraComputeFields を override して untilTime/forming を注入する。
          ...self._extraComputeFields(),
        });
        gw.lastSeries = result.series;
        // 共有 _lastSeries は applyIndicator/restore の単発（直列）経路が読むため併記維持。
        self._lastSeries = result.series;
        return result;
      },
    };
    return gw;
  }

  _toJson(i) {
    return this._store.toJson(i);
  }

  // =========================================================================
  // DOM 配線（ブラウザ/バンドルでのみ実行。node 単体テストは触らない）
  // =========================================================================

  // 指標カタログのカテゴリからサイドバー項目を生成する（冪等・既存の「すべて」「お気に入り」
  //   の後ろへ追加）。DOM 不在・要素不在では何もしない（node 単体テストの部分 DOM を許容）。
  _renderCategorySideItems(doc) {
    const side = doc.querySelector?.('.dialog-side');
    if (!side || typeof doc.createElement !== 'function') {
      return;
    }
    // 再バインド時の二重生成を防ぐ（生成済み項目のみ除去し、静的 2 件は残す）。
    for (const el of side.querySelectorAll?.('[data-generated-category]') ?? []) {
      el.remove?.();
    }
    for (const c of catalogCategories()) {
      const b = doc.createElement('button');
      b.className = 'side-item';
      b.type = 'button';
      b.dataset.category = c.key;
      b.dataset.generatedCategory = '1';
      b.textContent = c.label;
      side.appendChild(b);
    }
  }

  bind() {
    const doc = this._document;
    if (!doc) {
      return;
    }
    // カテゴリのサイドバー項目をカタログから生成してから DOM 参照を採る（ISSUE-221）。
    //   静的 HTML に直書きすると新カテゴリの指標追加時に同時改変が必要になり、実際に
    //   oscillator(10)・band(2) が欠落して 24 指標中 12 件が絞り込みから到達不能だった。
    this._renderCategorySideItems(doc);
    this._el = {
      openBtn: doc.getElementById('indicator-open-btn'),
      dialog: doc.getElementById('indicator-dialog'),
      closeBtn: doc.getElementById('indicator-dialog-close'),
      search: doc.getElementById('indicator-search'),
      list: doc.getElementById('indicator-list'),
      tabs: doc.querySelectorAll('[data-tab]'),
      cats: doc.querySelectorAll('[data-category]'),
      timeframeBtns: doc.querySelectorAll('[data-timeframe]'),
      // ISSUE-117: 時間足ドロップダウンのトリガーラベル（現在足の表記を syncButtons が反映）。
      timeframeMenuLabel: doc.getElementById('tf-menu-label'),
      legend: doc.getElementById('legend'),
    };
    const e = this._el;
    // 指標追加ダイアログ（開閉・検索・タブ・カテゴリ）の配線は IndicatorDialogController へ
    //   委譲する（ISSUE-181・絞り込み状態 _filter ごと移送済み）。
    this._dialog.bindElements(e);
    // 時間足セレクタ（日/週/月…）。A方式（SAMPLE_DATA・再集計不可）は無効化する。
    for (const b of e.timeframeBtns ?? []) {
      if (this._mode === 'a') {
        b.disabled = true;
        b.title = 'A方式（file://）では時間足切替は無効です';
        continue;
      }
      b.addEventListener('click', () => this.setTimeframe(b.dataset.timeframe));
    }
    this._syncTimeframeButtons();
    this._renderDialogList();
    this._renderLegend();
  }

  // ダイアログ開閉・一覧描画は IndicatorDialogController（A8）へ外出しした（ISSUE-181）。
  //   以下は subclass の inherited 呼出・既存テスト（ctrl._renderDialogList）を温存する薄い委譲。
  _openDialog() {
    return this._dialog.open();
  }

  _closeDialog() {
    return this._dialog.close();
  }

  // グループ内 active トグル（純 DOM）は View へ委譲する（bind の tab/category 配線から呼ばれる）。
  _setActive(group, active) {
    this._legendView.setActive(group, active);
  }

  _label(def) {
    // displayNameKey の末尾を表示名相当に（i18n 解決器を持たないプロトタイプ）。
    const k = def.displayNameKey ?? def.id;
    return k.includes('.') ? k.split('.').pop() : k;
  }

  _renderDialogList() {
    return this._dialog.renderList();
  }

  // 凡例を再描画する。行の view-model（label/visible）＋コールバックを組み立て、DOM 構築は
  //   IndicatorLegendView へ委譲する（ISSUE-038・SRP 是正）。挙動不変:
  //   - label は def 解決失敗時 indicatorId フォールバック＋非 default variant を括弧付与。
  //   - MP は renderer に系列を持たないため eye/close を MP 専用ハンドラへ分岐する（isMp）。
  //     gear は _onGear 内部で MP 分岐する。これらのハンドラ本体（reveal/gear seam を含む）は
  //     controller に残し、subclass の override（toggleVisible/removeInstance 等）を温存する。
  _renderLegend() {
    const rows = this._state.applied.map((inst) => {
      const def = this._catalog.get(inst.indicatorId);
      const isMp = this._isMarketProfile(def);
      return {
        label: `${def ? this._label(def) : inst.indicatorId}${inst.variant && inst.variant !== 'default' ? ' (' + inst.variant + ')' : ''}`,
        visible: inst.visible,
        onEye: () => (isMp
          ? this._toggleMarketProfileVisible(inst)
          : this.toggleVisible(inst.instanceId)),
        onGear: () => this._onGear(inst, def),
        onClose: () => (isMp
          ? this._removeMarketProfile(inst)
          : this.removeInstance(inst.instanceId)),
      };
    });
    this._legendView.renderLegend(rows);
  }

  // 設定: 歯車クリックでプロパティダイアログを開く（§7.1）。
  //   現 AppliedInstance のパラメータを読込→編集→OK で recomputeInstance（generation+1・§6.6）。
  //   A 方式 gateway は params を無視し id:variant キーで解決するため、variant 以外の値変更は
  //   描画へ未反映（H-1）。ダイアログ内に A 方式注記を明示表示する（§9.3・サイレント不一致回避）。
  _onGear(inst, def) {
    const doc = this._document;
    // MP は /compute を持たない専用パス（setParams+refresh）で設定を反映する。
    if (this._isMarketProfile(def)) {
      this._onGearMarketProfile(inst, def);
      return;
    }
    if (!doc || !def || typeof PropertiesDialog !== 'function') {
      // DOM 不在（node 単体テスト等）または未解決 def は従来の最小再計算へフォールバック。
      this._gearRecompute(inst, def);
      return;
    }
    // 現 instance の params をフォーム初期値に展開（未保持は ParamDef.default）。
    // instance.params は pairs 形式（paramsToPairs）で保存されるため object へ変換してから
    // 渡す（restore と同様）。変換しないと name で引けず再オープン時に既定値へ戻る。
    const stored = this._paramsObject(inst.params);
    const currentParams = (stored && Object.keys(stored).length > 0)
      ? stored
      : this._defaultParams(def);
    const instanceForDialog = { ...inst, params: currentParams };

    const dialog = new PropertiesDialog({
      document: doc,
      def,
      instance: instanceForDialog,
      mode: this._mode,
      // ISSUE-109: スタイル/可視性タブの行と初期値は実描画系列（renderer が保持する現スタイル）から
      //   構築する（カタログ SeriesDef はスタイル既定を持たない＝プレースホルダ表示の是正）。
      //   getSeriesStyles 未実装の renderer（後方互換 Fake/SSR）は null＝dialog の静的フォールバック
      //   （ISSUE-110 🔵-1: applySeriesStyle 側と同じ typeof ガードへ統一）。
      seriesStyles: typeof this._renderer.getSeriesStyles === 'function'
        ? this._renderer.getSeriesStyles(inst.instanceId) : null,
      // 期間プリセット（基本設計_期間プリセット.md §6.5）: 換算の基準となる datasetRef と
      //   チャートの現在足を渡す。指標側の計算時間足 override（params.timeframe）はダイアログが
      //   values から解決するため、ここでは override 前のチャート足を渡す。
      //   MP 側（market_profile_controller.js）は既に context を渡しており本項と同型。
      context: { timeframe: this._timeframe, datasetRef: this._datasetRef },
      onApply: (values, variant, extra) => this._applyDialogResult(inst, currentParams, values, variant, extra),
      onCancel: () => {},
    });
    dialog.open();
  }

  // ダイアログ OK の適用（_onGear から抽出・ISSUE-110）。
  //   ・variant 変更は実描画反映（事前計算 series が存在・§9.2）。同一 variant は null＝現状維持。
  //   ・スタイル差分は state へ先にマージし、recompute 後の redraw（_draw→_applyStoredStyles）で
  //     適用される（仕様 §6.1 の適用順 recompute→スタイル適用）。
  //   ・ISSUE-110 🟡-2: params/variant が無変更で styles のみ変更の場合は recompute（/compute 往復＋
  //     系列 remove/redraw）を省略し、applySeriesStyle 直適用＋persist の高速経路を通す
  //     （§6.1「スタイル変更は描画オプションのみで再計算不要」）。
  _applyDialogResult(inst, currentParams, values, variant, extra) {
    const nextVariant = variant && variant !== inst.variant ? variant : null;
    const patch = extra && extra.styles;
    const hasStyles = !!(patch && Object.keys(patch).length > 0);
    if (hasStyles) {
      this._state = setSeriesStyles(this._state, inst.instanceId, patch);
    }
    if (hasStyles && !nextVariant && this._sameParams(values, currentParams)) {
      this._applyStoredStyles(inst.instanceId);
      this._persistAll();
      return Promise.resolve(true);
    }
    // ユーザーの明示操作＝params の正。in-flight のライブ計算に上書きされないよう即確定する（ISSUE-201）。
    return this.recomputeInstance(inst.instanceId, nextVariant, values, { commitParams: true });
  }

  // params の等値判定（キー順・参照に依らない深い比較。FLOAT_LIST 等の配列値も対象）。
  _sameParams(a, b) {
    const norm = (o) => JSON.stringify(Object.keys(o ?? {}).sort().map((k) => [k, o[k]]));
    return norm(a) === norm(b);
  }

  // variant トグル/最小再計算（DOM 不在時のフォールバック・従来挙動を保持）。
  _gearRecompute(inst, def) {
    const variants = def?.compute?.variants ?? ['default'];
    if (variants.length > 1) {
      const idx = variants.indexOf(inst.variant);
      const nextVariant = variants[(idx + 1) % variants.length];
      this.recomputeInstance(inst.instanceId, nextVariant, this._defaultParams(def), { commitParams: true });
    } else {
      this.recomputeInstance(inst.instanceId, null, this._defaultParams(def), { commitParams: true });
    }
  }
}
