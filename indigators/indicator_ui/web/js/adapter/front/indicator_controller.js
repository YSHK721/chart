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
  listForView,
  apply,
  recompute,
  toggleVisible as facadeToggleVisible,
  remove as facadeRemove,
  toggleFavorite as facadeToggleFavorite,
  setSeriesStyles,
  reconcileSeriesStyles,
  deserialize,
} from '../../usecase/facade.js';
import { PropertiesDialog } from './properties_dialog.js';
import { IndicatorLegendView } from './indicator_legend_view.js';
import { buildMpParams, deriveMpMode, deriveMpResmode } from './market_profile_params.js';
import { MarketProfileController } from './market_profile_controller.js';
import { TimeframeController } from './timeframe_controller.js';
import { seriesKind } from '../../domain/series_kind.js';
import { barStyleEditableFor } from '../../usecase/form_model.js';
import { INTRABAR_FORMING_IDS } from '../../usecase/intrabar_forming_ids.js';
import { isActorDriven } from '../../usecase/actor_driven_ids.js';
import { STALL_DEADLINE_MS, UpdateScheduler } from './update_scheduler.js';

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
 * @property {string} _timeframe             現在の表示時間足（read/write）。
 * @property {number} _recomputeDepth        再計算バッチの競合ガード深さ（read/write）。
 * @property {string} _datasetRef            計算対象データセット参照（read）。
 * @property {?number} _recentBars           直近表示本数（compute の limit）。null=制限なし（read）。
 * @property {{uiState: object}} _state      UI 永続状態を保持する純状態オブジェクト（read/write: uiState）。
 * @property {{setCandles: function}} _renderer  メイン系列差替に用いる renderer（read: setCandles を呼ぶ）。
 * @property {?function} _loadCandles        時間足切替時の candles 再取得ローダ（B方式のみ・A方式は null）。
 * @property {?function} _timeframeObserver  時間足変更の購読者（任意・1 個）。
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
 * @property {?number} [_untilTime]          reveal（replay）の現在バー T。present は非在席（optional）。
 */

// TimeframeHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const TIMEFRAME_HOST_CONTRACT = Object.freeze({
  role: 'TimeframeHost',
  methods: Object.freeze(['recomputeAllApplied', '_persistAll']),
  fields: Object.freeze([
    '_timeframe', '_recomputeDepth', '_recomputeLastStartMs', '_datasetRef', '_recentBars',
    '_state', '_renderer', '_loadCandles', '_timeframeObserver',
  ]),
  // bind() 後のみ在席（fresh インスタンスでは未在席・controller は optional chaining で許容）。
  optionalFields: Object.freeze(['_el']),
});

// MarketProfileHost 契約の実体列挙（構造充足テスト・依存面部分集合テストの固定点）。
export const MARKET_PROFILE_HOST_CONTRACT = Object.freeze({
  role: 'MarketProfileHost',
  methods: Object.freeze([
    '_mpParams', '_isMarketProfile', '_paramsObject', '_renderLegend',
    '_defaultVariant', '_withParams', '_defaultParams', '_persistAll',
  ]),
  fields: Object.freeze([
    '_marketProfile', '_mpModeResolver', '_mpGrowthResolver', '_state',
    '_catalog', '_meta', '_datasetRef', '_document', '_mode', '_timeframe',
  ]),
  // reveal seam: replay subclass のみ在席（present base では非在席・controller は != null で許容）。
  optionalFields: Object.freeze(['_untilTime']),
});

// 末尾K差分反映（updateSeriesTail）の対象となる時系列系列か。horizontal_line は末尾K切り
//   せず全件返るため対象外（latest 経路に乗らず remove+redraw へフォールバックする）。
function isTailUpdatable(payload) {
  return seriesKind(payload.kind).tailUpdatable;
}

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
    // 時間足（§チャート表示時間選択・1 分足原子から resample）。compute/candles に伝搬する。
    this._timeframe = timeframe;
    // 直近表示本数（§配信設計: リサンプル＋直近 N 本）。compute の limit に伝搬する。null=制限なし。
    this._recentBars = recentBars;
    // 時間足切替時に candles を再取得するローダ (datasetRef, timeframe) → Promise<candles|null>。
    //   B方式のみ注入される（A方式は SAMPLE_DATA・再集計不可のため null）。
    this._loadCandles = loadCandles;
    // 時間足変更の購読者（任意・1 個）。setTimeframe 適用後に新時間足を通知する。
    //   売買マーカーの該当時間足フィルタ等、時間足に連動する描画の配線点。
    this._timeframeObserver = null;

    // メモリ状態（facade の純状態オブジェクト）。
    this._state = emptyState();
    // instanceId -> { def } 描画済みメタ（凡例再描画・recompute 用）。
    this._meta = new Map();
    // ダイアログ絞り込み UI 状態。
    this._filter = { tab: 'indicator', category: null, query: '', favoriteOnly: false };
    // 再計算実行中の深さ（競合ガードの単一権威）。ライブ更新（LiveUpdater）は独自フラグを
    //   持たず isRecomputing() を参照し、再計算中の tick をスキップする。bool ではなく深さ
    //   カウンタにするのは、setTimeframe（candles 取得 await＋全指標再計算）が内側の
    //   recomputeInstance をネスト呼びするため。bool だと内側 finally がバッチ途中で解除し、
    //   その隙に tick が割り込む（torn なバッチ）。カウンタなら最外バッチ終了まで true を維持する。
    this._recomputeDepth = 0;
    // ISSUE-157（クロック駆動設計）: 指標更新の「要求フラグ＋クロック」駆動は UpdateScheduler へ
    //   委譲する（SOLID 是正 🔴-1・設計意図の詳細は update_scheduler.js 冒頭コメント参照）。
    //   実体（末尾差分/full 再計算）と外部バッチ述語（isRecomputing・時限式）を依存注入する。
    this._scheduler = new UpdateScheduler({
      runForming: () => this.recomputeFormingTails(),
      runFull: () => this.recomputeAllApplied({ mode: 'full' }),
      isBlocked: () => this.isRecomputing(),
    });
    // isRecomputing() の時限化に使う（外部バッチのハングでゲートが恒久 true にならない）。
    this._recomputeLastStartMs = 0;
    // ISSUE-153: 復元実行中の Promise（null=非実行）。applyIndicator が完了待ちに使う。
    this._restoreInFlight = null;
    // MP（A7）アクター駆動のオーケストレーションを委譲する協働子（ISSUE-094 🔴-4）。
    //   host=this を渡し、apply/enable/toggle/remove/gear/reapply/restore/live-recompute を委譲する。
    //   subclass の inherited メソッド呼出（this._toggleMarketProfileVisible 等）・_mpParams override を
    //   温存するため base の各 MP メソッドは本協働子への薄いラッパへ縮退する（byte 挙動不変）。
    this._mp = new MarketProfileController(this);
    // 時間足取得・切替（A3）を委譲する協働子（ISSUE-094 🔴-4）。setTimeframe / ボタン同期 /
    //   gateway の timeframe・limit 注入を担う。ライブ再計算入口（recomputeAllApplied）は controller 温存。
    this._tf = new TimeframeController(this);
  }

  // 競合ガード: 再計算バッチ実行中なら true。LiveUpdater が tick 先頭で参照しスキップ判定する。
  //   ISSUE-157: 時限式。深さカウンタはバッチの await がハングすると finally が走らず永久に
  //   正のままになる（＝全ゲートが恒久閉鎖）。STALL_DEADLINE_MS を超えて「実行中」のバッチは
  //   ハングとみなし false を返す（守るべき健全なバッチはもう存在しない）。
  isRecomputing() {
    if (this._recomputeDepth <= 0) {
      return false;
    }
    return (Date.now() - this._recomputeLastStartMs) <= STALL_DEADLINE_MS;
  }

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

  // SeriesDef.series_name（dynamic は series_name_pattern 展開）の期待集合を返す。
  //   params を渡すと、pattern が *FromParam を宣言する系列は現在の params から期待名を
  //   生成する（moving_averages: 任意期間 252 等を許容・§3.3.6 拡張）。params 省略時は
  //   pattern の静的 buckets/pcts へフォールバック（profit_band 等・後方互換）。
  _expectedSeriesNames(def, params = null) {
    const names = new Set();
    for (const s of def.series ?? []) {
      if (s.dynamic && s.seriesNamePattern) {
        for (const name of this._expandPattern(s.seriesNamePattern, params)) {
          names.add(name);
        }
      } else if (s.seriesName) {
        names.add(s.seriesName);
      }
    }
    return names;
  }

  // series_name_pattern を展開（{bucket} {pct} 形式）。
  //   pattern.bucketsFromParam / pctsFromParam が指定され params が与えられた場合は、当該 param
  //   値リストからトークンを生成する（bucketsUpper=大文字化 / pctsInt=整数文字列化）。これにより
  //   ユーザが入力した任意期間（pcts 静的リスト外の 252 等）も期待集合に含まれ F3 を通過する。
  //   未指定・params 無し時は従来どおり静的 buckets/pcts を直積展開する（profit_band 28 系列等）。
  _expandPattern(pattern, params = null) {
    const template = pattern.template ?? '';
    let buckets = pattern.buckets ?? [''];
    let pcts = pattern.pcts ?? [''];
    if (params) {
      if (pattern.bucketsFromParam && Array.isArray(params[pattern.bucketsFromParam])) {
        buckets = params[pattern.bucketsFromParam].map(
          (v) => (pattern.bucketsUpper ? String(v).toUpperCase() : String(v)),
        );
      }
      if (pattern.pctsFromParam && Array.isArray(params[pattern.pctsFromParam])) {
        pcts = params[pattern.pctsFromParam].map(
          (v) => (pattern.pctsInt ? String(Math.round(Number(v))) : String(v)),
        );
      }
    }
    const out = [];
    for (const bucket of buckets) {
      for (const pct of pcts) {
        out.push(template.replace('{bucket}', bucket).replace('{pct}', pct));
      }
    }
    return out;
  }

  // F3: 期待集合に含まれない系列はスキップ（renderLine に渡さない）＋ console.warn 記録。
  //   params は dynamic pattern の *FromParam 展開に用いる（省略時は静的フォールバック）。
  _validateSeriesNames(payloads, def, params = null) {
    const expected = this._expectedSeriesNames(def, params);
    return (payloads ?? []).filter((p) => {
      const ok = expected.has(p.name);
      if (!ok && typeof console !== 'undefined' && console.warn) {
        console.warn(`[F3] 系列名不一致のためスキップ: instance=${def.id} name=${p.name}`);
      }
      return ok;
    });
  }

  // 描画: F3 通過系列を kind 別に renderer へ渡す（line / histogram / horizontal_line）。
  //   params は F3 期待名の動的生成（moving_averages の任意期間）に用いる。
  //   placement='overlay' は価格 pane(0) のローソクへ重畳（バンド等）、'pane' は専用 pane
  //   （v5 ネイティブ・独立価格軸＋指標名＋高さドラッグ）。renderer が pane 生成と水準線配線を担う。
  _draw(instanceId, def, series, params = null) {
    const validated = this._validateSeriesNames(series, def, params);
    // kind → 描画経路は series_kind 台帳（renderRoute）で一元化（新種別は台帳追記で完結・OCP）。
    //   単一前進走査で振り分けるため各経路内の順序は従来 filter と同一。未知 kind は非描画。
    const routed = { line: [], histogram: [], horizontal: [] };
    for (const p of validated) {
      // 案A（btlm_trail_marod）: barStyleEditable 一致系列（front カタログ由来・backend 非関与）へ
      //   bar_editable=true を注入する。renderer はこのヒントで line ⇄ histogram スワップ対象を識別
      //   し保持データを退避する（p.kind を消費する本ループが唯一の front 系列メタ付与点＝同所）。
      //   非一致系列にはキーを付けない（renderer の bar_editable===true ゲートが false のまま＝非波及）。
      if (barStyleEditableFor(def, p.name)) {
        p.bar_editable = true;
      }
      const route = seriesKind(p.kind).renderRoute;
      if (routed[route]) {
        routed[route].push(p);
      }
    }
    const lines = routed.line;
    const histograms = routed.histogram;
    const hlines = routed.horizontal;
    const opts = { pane: def.placement !== 'overlay', name: this._label(def) };
    if (histograms.length > 0) {
      this._renderer.renderHistogram(instanceId, histograms, opts);
    }
    if (lines.length > 0) {
      this._renderer.renderLine(instanceId, lines, opts);
    }
    for (const h of hlines) {
      this._renderer.renderHorizontal(instanceId, h.lines ?? []);
    }
    // ISSUE-109: 保存済みスタイル上書きを再適用する（redraw/restore/時間足切替で系列は
    //   ペイロード既定色で再生成されるため、描画の最後に毎回上書きし直す＝永続反映）。
    this._applyStoredStyles(instanceId);
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
  async applyIndicator(indicatorId, variant) {
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
  async recomputeInstance(instanceId, newVariant, newParams, { mode = 'full', forceTail = false } = {}) {
    const job = await this._computeInstance(instanceId, newVariant, newParams, { mode, forceTail });
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
  async _computeInstance(instanceId, newVariant, newParams, { mode = 'full', forceTail = false } = {}) {
    const meta = this._meta.get(instanceId);
    if (!meta) {
      return null;
    }
    // variant を差し替える場合は def はそのまま、gateway が variant でキー解決。
    if (newVariant) {
      this._state = this._withVariant(this._state, instanceId, newVariant);
    }
    const params = newParams ?? this._defaultParams(meta.def);
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
    this._recomputeDepth += 1;
    this._recomputeLastStartMs = Date.now();
    try {
      const result = await recompute(this._state, instanceId, params, this._datasetRef, gateway);
      // 競合削除ガード（ISSUE-105 🟡-2）: recompute の await 中に凡例 close（removeInstance）で
      //   当該インスタンスが state から除去されると、result.state は除去前スナップショット由来のため
      //   反映すると除去済みインスタンスが「復活」する。これがフェーズ2 で再描画されると
      //   凡例行の無い残留系列（ゾンビペイン）＋永続化汚染を生む。await 後の live state で在席を
      //   確認し、除去済みなら live state に触れず accepted:false を返す。
      const removedDuringAwait = !this._state.applied.some((i) => i.instanceId === instanceId);
      if (removedDuringAwait || !result.accepted) {
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
      this._recomputeDepth -= 1;
    }
  }

  // 描画フェーズ（同期）: 退避済み job の series を renderer へ反映する。await を挟まないため
  //   複数 job を連続実行しても中間ペイントが起きず、全指標が同時に更新される（ISSUE-023）。
  _renderInstance(job) {
    if (!job || !job.accepted) {
      return;
    }
    if (job.wantLatest) {
      // Latest: 末尾K点を series.update で差分反映（過去確定足は不変・全描画しない）。
      this._drawLatest(job.instanceId, job.def, job.series, job.params);
    } else {
      // params 変更で系列名が変わりうる（tgp の分位線 btlm_q{N}＝q_low/q_high 依存）ため、
      // setData 差し替えでは改名系列が更新されず古い系列が残留・消失する。remove+redraw で
      // 全系列を現在名で再生成する（line / horizontal_line 共通）。
      // ISSUE-149: keepPane=true で pane を温存（従来の全除去→末尾 addPane は更新のたびに
      //   pane が最下段へ移動していた）。redraw は既存 pane（同じ位置）へ再生成される。
      this._renderer.remove(job.instanceId, { keepPane: true });
      this._draw(job.instanceId, job.def, job.series, job.params);
    }
    // 非表示状態を維持（redraw は可視で再生成するため）。
    if (job.hidden) {
      this._renderer.setVisible(job.instanceId, false);
    }
  }

  // def の全系列が末尾K差分可能（line/histogram のみ・horizontal_line を含まない）か。
  //   latest 要求の可否を「計算前」に def の系列定義から判定する（結果データの kind ではない）。
  //   混在/horizontal 指標は backend が line/histogram を末尾K点へ trim する一方フロントは全差替に
  //   落ちるため、trim 済みデータで全描画＝ライン履歴が 1 点に潰れる。よって最初から full を要求する。
  _defCanTailUpdate(def) {
    const series = def?.series ?? [];
    if (series.length === 0) {
      return false;
    }
    return series.every(isTailUpdatable);
  }

  // Latest: F3 通過系列の末尾K点を {instanceId}::{name} キーで updateSeriesTail へ差分反映する。
  _drawLatest(instanceId, def, series, params = null) {
    const validated = this._validateSeriesNames(series, def, params);
    for (const p of validated) {
      if (isTailUpdatable(p)) {
        this._renderer.updateSeriesTail(`${instanceId}::${p.name}`, p.data ?? []);
      }
    }
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
  removeInstance(instanceId) {
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
    this._timeframeObserver = observer;
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

  // UC-07 永続化・復元。
  _persistAll() {
    this._persistence.saveApplied(this._state.applied.map((i) => this._toJson(i)));
    this._persistence.saveFavorites(this._state.favorites);
    this._persistence.saveUiState(this._state.uiState);
  }

  // ISSUE-153: restore は保存状態で _state を丸ごと置換するため、読込直後〜復元完了の間に
  //   applyIndicator された指標が state から消え「描画だけ残る孤児」になる（以後どの再計算にも
  //   乗らず凍結＝『ライブで btlm_trail が更新されない』の真因）。復元中フラグを公開し、
  //   applyIndicator 側が完了を待つことで競合を排除する。
  async restore() {
    const run = this._restoreRun();
    this._restoreInFlight = run;
    try {
      await run;
    } finally {
      this._restoreInFlight = null;
    }
  }

  async _restoreRun() {
    const json = {
      applied: this._persistence.loadApplied(),
      favorites: this._persistence.loadFavorites(),
      uiState: this._persistence.loadUiState(),
    };
    this._state = deserialize(JSON.stringify({ ...json, seqCounters: {} }));
    // 永続化された時間足を復元（compute は gateway 経由で this._timeframe を注入するため再計算前に確定）。
    //   初期足（constructor 値・composition root が candles 取得済み）と異なる場合のみ candles を再取得。
    const savedTimeframe = this._state.uiState?.timeframe;
    if (savedTimeframe && savedTimeframe !== this._timeframe) {
      this._timeframe = savedTimeframe;
      if (typeof this._loadCandles === 'function') {
        const candles = await this._loadCandles(this._datasetRef, savedTimeframe);
        if (candles && candles.length > 0) {
          this._renderer.setCandles(candles);
        }
      }
    }
    this._syncTimeframeButtons();
    // 復元した時間足を購読者へ通知する（売買マーカーの該当時間足フィルタが restore 後の
    //   現在時間足を正しく評価できるようにする。通知欠落だと該当時間足でも非表示になる逆動作）。
    this._timeframeObserver?.(this._timeframe);
    // 各 instance を再計算して再描画（A方式は variant 事前計算データで復元）。
    for (const inst of this._state.applied) {
      const def = this._catalog.get(inst.indicatorId);
      if (!def) {
        continue;
      }
      this._meta.set(inst.instanceId, { def });
      // MP 種別は /compute で計算しようとして失敗させない。復元は MP 側協働子へ委譲する
      //   （保存 params を actor へ渡し、可視だった場合のみ有効化して再取得・表示・ISSUE-094 🔴-4）。
      if (this._isMarketProfile(def)) {
        await this._mp.restoreInstance(inst);
        continue;
      }
      try {
        const gateway = this._gatewayAdapter(inst.variant);
        // B方式は保存 params で再計算（実反映）。A方式は params 無視で id:variant キー解決。
        const restoreParams = this._paramsObject(inst.params);
        const result = await gateway.compute({ indicatorId: inst.indicatorId, variant: inst.variant, params: restoreParams, datasetRef: this._datasetRef, generation: inst.generation });
        this._lastSeries = result.series;
        this._draw(inst.instanceId, def, this._lastSeries, restoreParams);
        if (!inst.visible) {
          this._renderer.setVisible(inst.instanceId, false);
        }
      } catch {
        // 事前計算未収録 variant はスキップ（A方式制限）。
      }
    }
    this._renderLegend();
    this._renderDialogList();
  }

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
    return {
      instanceId: i.instanceId,
      indicatorId: i.indicatorId,
      variant: i.variant,
      params: i.params,
      visible: i.visible,
      generation: i.generation,
      seq: i.seq,
      createdAt: i.createdAt,
      styles: i.styles ?? null,
    };
  }

  // =========================================================================
  // DOM 配線（ブラウザ/バンドルでのみ実行。node 単体テストは触らない）
  // =========================================================================

  bind() {
    const doc = this._document;
    if (!doc) {
      return;
    }
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
    if (e.openBtn) {
      e.openBtn.addEventListener('click', () => this._openDialog());
    }
    if (e.closeBtn) {
      e.closeBtn.addEventListener('click', () => this._closeDialog());
    }
    if (e.search) {
      e.search.addEventListener('input', (ev) => { this._filter.query = ev.target.value; this._renderDialogList(); });
    }
    for (const t of e.tabs ?? []) {
      t.addEventListener('click', () => { this._setActive(e.tabs, t); this._filter.tab = t.dataset.tab; this._renderDialogList(); });
    }
    for (const c of e.cats ?? []) {
      c.addEventListener('click', () => {
        this._setActive(e.cats, c);
        this._filter.category = c.dataset.category || null;
        this._filter.favoriteOnly = c.dataset.category === '__favorites__';
        this._renderDialogList();
      });
    }
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

  // ダイアログ開: DOM の is-open トグルは View へ委譲。uiState 更新・リスト再描画は controller に残す
  //   （dialog 要素在席時のみ状態を進める従来ガードを保持＝byte 挙動不変）。
  _openDialog() {
    if (this._el?.dialog) {
      this._legendView.setDialogOpen(true);
      this._state.uiState = { ...this._state.uiState, dialogOpen: true };
      this._renderDialogList();
    }
  }

  _closeDialog() {
    if (this._el?.dialog) {
      this._legendView.setDialogOpen(false);
      this._state.uiState = { ...this._state.uiState, dialogOpen: false };
    }
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

  // ダイアログの指標リストを再描画する。行の view-model（label/category/favorite）＋コールバックを
  //   組み立て、DOM 構築は IndicatorLegendView へ委譲する（ISSUE-038・SRP 是正）。挙動不変:
  //   お気に入り絞り込み（listForView）・star の stopPropagation・row クリックの apply+close を保持。
  _renderDialogList() {
    const favorites = this._state.favorites;
    const defs = listForView({ ...this._filter, favorites });
    const rows = defs.map((def) => ({
      label: this._label(def),
      category: (def.category?.nameKey ?? '').split('.').pop(),
      favorite: favorites.includes(def.id),
      onToggleFavorite: () => this.toggleFavorite(def.id),
      onPick: () => { this.applyIndicator(def.id, this._defaultVariant(def)); this._closeDialog(); },
    }));
    this._legendView.renderDialogList(rows);
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
    return this.recomputeInstance(inst.instanceId, nextVariant, values);
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
      this.recomputeInstance(inst.instanceId, nextVariant, this._defaultParams(def));
    } else {
      this.recomputeInstance(inst.instanceId, null, this._defaultParams(def));
    }
  }
}
