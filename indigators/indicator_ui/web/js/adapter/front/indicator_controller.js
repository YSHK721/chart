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
  deserialize,
} from '../../usecase/facade.js';
import { PropertiesDialog } from './properties_dialog.js';
import { IndicatorLegendView } from './indicator_legend_view.js';
import { buildMpParams, deriveMpMode, deriveMpResmode } from './market_profile_params.js';

// 末尾K差分反映（updateSeriesTail）の対象となる時系列系列か。horizontal_line は末尾K切り
//   せず全件返るため対象外（latest 経路に乗らず remove+redraw へフォールバックする）。
function isTailUpdatable(payload) {
  return payload.kind === 'line' || payload.kind === 'histogram';
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
  }

  // 競合ガード: 再計算バッチ実行中なら true。LiveUpdater が tick 先頭で参照しスキップ判定する。
  isRecomputing() {
    return this._recomputeDepth > 0;
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
    const lines = validated.filter((p) => p.kind === 'line');
    const histograms = validated.filter((p) => p.kind === 'histogram');
    const hlines = validated.filter((p) => p.kind === 'horizontal_line');
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
  }

  // =========================================================================
  // UC オーケストレーション（facade + ports）
  // =========================================================================

  // MP 種別（アクター委譲型）判定。真なら _draw / _gatewayAdapter をバイパスする。
  _isMarketProfile(def) {
    return def?.compute?.computeId === 'market_profile';
  }

  // MP アクターへ渡す取得 params（resmode/bins/va/src/range）を組み立てる（apply/gear/restore 共通）。
  //   ISSUE-094 🔴-4: MP のパラメータ・スキーマ写像は market_profile_params.js（純関数）へ外出しした。
  //   本メソッドは薄い委譲のみ（subclass の super._mpParams / 既存テストの ctrl._mpParams 呼出を温存）。
  _mpParams(p = {}) {
    return buildMpParams(p);
  }

  // MP アクターへ params を渡す共通経路（apply/gear/restore/連動 再適用で共用）。
  //   ライブ連動（mpModeResolver 注入時）は mode を選択表示モード（gear 記憶／未選択は既定 normal）へ解決してから
  //   渡す（'ticklive' 置換はしない＝直交化）。解決役は同時に userMode（gear 選択）を記憶する。mode 未指定
  //   （旧インスタンス）は解決しない（actor 既定＝通常）。未注入時は _mpParams の結果をそのまま渡す＝byte 不変。
  //   さらに growth 解決役（mpGrowthResolver 注入時）は setParams 後に growing 信号（applyGrowthState）を適用する。
  //   FOLLOW=growing=true（成長 ON）／ANALYSIS=false（static）。未注入時は applyGrowthState を呼ばない＝byte 不変。
  //   marketProfile 未注入時は no-op（呼び出し側の guard と二重防御）。
  _applyMpParams(p) {
    if (!this._marketProfile) {
      return;
    }
    const params = this._mpParams(p);
    if (params.mode != null && this._mpModeResolver) {
      params.mode = this._mpModeResolver(params.mode);
    }
    this._marketProfile.setParams(params);
    this._applyMpGrowth();
  }

  // 直交化: 現在の成長状態（mpGrowthResolver）を MP アクターへ growing 信号として適用する。
  //   setParams（mode 遷移で _exitTicklive→growing リセット）の後に呼び、mode を維持したまま growing を確定する。
  //   解決役未注入 or actor が applyGrowthState 非所持なら no-op（byte 不変）。返り値 growing を呼び出し側が使う。
  _applyMpGrowth() {
    if (!this._mpGrowthResolver || !this._marketProfile) {
      return false;
    }
    const growing = !!this._mpGrowthResolver();
    if (typeof this._marketProfile.applyGrowthState === 'function') {
      this._marketProfile.applyGrowthState({ growing });
    }
    return growing;
  }

  // ライブ連動: チャート FOLLOW/ANALYSIS 遷移時に、現在表示中 MP の実効モードを再適用する（present 固有）。
  //   GrowthCoordinator.onLiveStateChange → reapply として配線される。連動未配線（mpModeResolver 未注入）
  //   時は呼ばれない設計だが、MP 不在/無効/未表示時も自己 guard で no-op（副作用なし）。
  //   実効モードは resolver(null)（記憶更新なし・実効解決のみ）で強制し、保存 params（bins/va/src/range）は
  //   維持したまま mode だけ差し替えて refresh する（既存 setParams→refresh 経路を再利用・actor 不変）。
  async reapplyMarketProfileMode() {
    if (!this._marketProfile || !this._mpModeResolver) {
      return;
    }
    if (typeof this._marketProfile.isEnabled === 'function' && !this._marketProfile.isEnabled()) {
      return; // MP 未表示（enabled=false）は再適用不要。
    }
    const inst = this._state.applied.find(
      (i) => this._isMarketProfile(this._catalog.get(i.indicatorId)) && i.visible,
    );
    if (!inst) {
      return; // 表示中 MP インスタンスが無い。
    }
    const params = this._mpParams(this._paramsObject(inst.params));
    params.mode = this._mpModeResolver(null); // 選択表示モード（gear 記憶／未選択は既定）を維持（'ticklive' 置換なし）。
    this._marketProfile.setParams(params);
    // 直交化: mode を維持したまま growing だけをトグルする（applyGrowthState）。FOLLOW=growing=true / ANALYSIS=false。
    const growing = this._applyMpGrowth();
    // growing 時のみ成長エンジンを起動する。present の成長は forming を onLiveTick（→_enterTicklive）で取得する
    //   （live loop(recomputeAllApplied)/初期 add と同一経路）。refresh は /market_profile の base 累積を描くだけで
    //   forming を発火しないため、growing では onLiveTick を呼ぶ。非成長（static＝ANALYSIS）は refresh で選択モードを反映。
    if (growing && typeof this._marketProfile.onLiveTick === 'function') {
      await this._marketProfile.onLiveTick();
    } else if (typeof this._marketProfile.refresh === 'function') {
      await this._marketProfile.refresh();
    }
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

  // MP 専用適用パス: /compute をバイパスし、state には no-op gateway で instance を登録して
  //   凡例表示・永続化・restore の対象に含める。描画は MarketProfileActor（GET /market_profile →
  //   primitive）へ委譲する。_draw（F3 系列描画）は通さない。
  async _applyMarketProfile(def, variant, params) {
    // MP 単一インスタンス制約: 既に MP が適用済みなら新規 legend 行を作らず no-op で
    //   既存インスタンスを返す（二重 legend 行→単一 actor 駆動での状態乖離を防ぐ）。
    //   actor へは触れない: 既存が非表示なら表示状態の乖離、gear 変更済みなら params
    //   の既定値クロバーを招くため、可視・params の現状を保存する。
    const existing = this._state.applied.find(
      (i) => this._isMarketProfile(this._catalog.get(i.indicatorId)),
    );
    if (existing) {
      return existing;
    }
    const { state, instance } = await apply(
      this._state,
      { indicatorId: def.id, variant: variant ?? this._defaultVariant(def), params, datasetRef: this._datasetRef },
      { compute: async () => ({ generation: 0 }) },
    );
    this._state = state;
    this._meta.set(instance.instanceId, { def });
    await this._enableMarketProfile(params);
    this._persistAll();
    this._renderLegend();
    return instance;
  }

  // MP アクターへ params を渡して有効化する（setParams→setEnabled(true)＝取得＋表示）。
  //   setEnabled(true) は内部で refresh も行う。未注入時は no-op。
  async _enableMarketProfile(params) {
    if (!this._marketProfile) {
      return;
    }
    this._applyMpParams(params);
    await this._marketProfile.setEnabled(true);
    // [reveal seam] reveal（replay）では現在バー T（_untilTime）が確定していれば即 enterBar で base を
    //   描画する。present は _untilTime を持たない（undefined）ため常に skip（byte 挙動不変）。
    if (this._untilTime != null && typeof this._marketProfile.enterBar === 'function') {
      await this._marketProfile.enterBar(this._untilTime);
    }
  }

  // MP 凡例 eye: 表示/非表示トグル（state.visible を反転し actor.setEnabled へ同期）。
  async _toggleMarketProfileVisible(inst) {
    this._state = facadeToggleVisible(this._state, inst.instanceId);
    const updated = this._state.applied.find((i) => i.instanceId === inst.instanceId);
    if (this._marketProfile && updated) {
      await this._marketProfile.setEnabled(updated.visible);
    }
    this._persistAll();
    this._renderLegend();
  }

  // MP 凡例 close: 非表示＋detach してから applied/meta から除去する（renderer.remove は不要＝
  //   MP は renderer に系列を持たない）。
  async _removeMarketProfile(inst) {
    if (this._marketProfile) {
      await this._marketProfile.setEnabled(false);
      if (typeof this._marketProfile.detach === 'function') {
        this._marketProfile.detach();
      }
    }
    this._state = facadeRemove(this._state, inst.instanceId);
    this._meta.delete(inst.instanceId);
    this._persistAll();
    this._renderLegend();
  }

  // MP 凡例 gear: プロパティダイアログで bins/va/src を編集し、onApply で setParams+refresh。
  //   /compute は呼ばない。DOM 不在時は現 params で即時反映（フォールバック）。
  _onGearMarketProfile(inst, def) {
    const doc = this._document;
    const stored = this._paramsObject(inst.params);
    const currentParams = (stored && Object.keys(stored).length > 0)
      ? stored
      : this._defaultParams(def);
    const applyParams = async (values) => {
      this._state = this._withParams(this._state, inst.instanceId, values);
      if (this._marketProfile) {
        this._applyMpParams(values);
        // [reveal seam] reveal（replay）かつ **push 成長中**（isGrowingPush＝growing かつ非 sessions）のときだけ
        //   現在バー T で enterBar（forming push で base 取り直し）。sessions+growing / 非成長は refresh(as-of-T)
        //   へ落とす（成長軸 aware）。present は _untilTime 未設定ゆえ常に refresh＝従来どおり（byte 挙動不変）。
        //   Phase5: 旧 isTicklive()（表示モード）ゲートから isGrowingPush()（成長軸）へ移行（ticklive 撤去）。
        if (this._untilTime != null && typeof this._marketProfile.enterBar === 'function'
            && typeof this._marketProfile.isGrowingPush === 'function'
            && this._marketProfile.isGrowingPush()) {
          await this._marketProfile.enterBar(this._untilTime);
        } else if (typeof this._marketProfile.refresh === 'function') {
          await this._marketProfile.refresh();
        }
      }
      this._persistAll();
      this._renderLegend();
    };
    // applyParams は async。未 await の fire-and-forget のため拒否を .catch で捕捉し
    //   unhandledRejection 化を防ぐ（refresh 失敗等）。
    const runApply = (values) => {
      applyParams(values).catch((err) => {
        if (typeof console !== 'undefined' && console.error) {
          console.error('[MP] gear apply failed', err);
        }
      });
    };
    if (!doc || typeof PropertiesDialog !== 'function') {
      runApply(currentParams);
      return;
    }
    const dialog = new PropertiesDialog({
      document: doc,
      def,
      instance: { ...inst, params: currentParams },
      mode: this._mode,
      // ISSUE-070: MP 解像度パラメータのグレーアウト判定に現 timeframe と served/A方式を渡す
      //   （tf-period が日別列を描くとき resmode/bins/range は無効＝GRID_W 固定のため）。
      context: { timeframe: this._timeframe, servedMode: this._mode },
      onApply: (values) => { runApply(values); },
      onCancel: () => {},
    });
    dialog.open();
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
  //   ※ this._lastSeries は gateway compute が上書きする共有フィールドのため、compute 直後に
  //     job へ確保する（描画まで遅延すると次指標の series で上書きされ取り違える）。
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
    this._recomputeDepth += 1;
    try {
      const result = await recompute(this._state, instanceId, params, this._datasetRef, gateway);
      this._state = result.state;
      if (!result.accepted) {
        return { instanceId, accepted: false };
      }
      const inst = this._state.applied.find((i) => i.instanceId === instanceId);
      return {
        instanceId,
        accepted: true,
        def: meta.def,
        params,
        wantLatest,
        // compute 直後の series を job へ確保（共有 _lastSeries の上書き対策）。
        series: this._lastSeries,
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
      this._renderer.remove(job.instanceId);
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

  // 時間足切替（§チャート表示時間選択・1 分足原子から resample）。
  //   1) candles を新時間足で再取得しメイン系列を差し替え（B方式のみ・直近 recentBars 本）。
  //   2) 適用済み全指標を新時間足で再計算・再描画（candles と時間軸を揃える）。
  //   3) uiState に時間足を永続化（restore で復元）。
  //   A方式（loadCandles 無し・SAMPLE_DATA）では candles 再取得を行わない（再集計不可）。
  async setTimeframe(timeframe) {
    if (!timeframe || timeframe === this._timeframe) {
      return;
    }
    this._timeframe = timeframe;
    this._syncTimeframeButtons();
    // バッチ全体（candles 取得 await＋全指標再計算）を競合ガードで包む。これがないと
    //   _loadCandles の await 中は isRecomputing()=false となり、その隙にライブ tick が
    //   割り込んで二重 compute する（🟡-2）。最外で increment し finally で確実に解除する。
    this._recomputeDepth += 1;
    try {
      // candles を新時間足で再取得（取得のみ・描画は下のバッチへ遅延）。
      let candles = null;
      if (typeof this._loadCandles === 'function') {
        candles = await this._loadCandles(this._datasetRef, timeframe);
      }
      // メイン系列差し替えを指標の再描画と同じ同期バッチへ含め、全要素を同時更新する（ISSUE-023）。
      //   取得失敗・A方式（candles 無し）は preRender=null でメイン系列を据え置く。
      const preRender = candles && candles.length > 0
        ? () => this._renderer.setCandles(candles)
        : null;
      // 適用済み全指標を新時間足で再計算（params 据え置き・generation+1・gateway が timeframe 注入）。
      //   再計算ループは recomputeAllApplied に集約（ライブ更新と共通の単一入口・挙動/順序/generation 採否不変）。
      await this.recomputeAllApplied({ preRender });
    } finally {
      this._recomputeDepth -= 1;
    }
    this._state.uiState = { ...this._state.uiState, timeframe };
    this._persistAll();
    // 時間足購読者へ新時間足を通知する（売買マーカーの該当時間足フィルタ等）。
    this._timeframeObserver?.(this._timeframe);
  }

  // 時間足変更の購読者を登録する（任意・1 個）。setTimeframe 適用後に新時間足で呼ばれる。
  setTimeframeObserver(observer) {
    this._timeframeObserver = observer;
  }

  // 適用済み全指標を現在の params / 時間足で再計算・再描画する（ライブ更新の再計算入口）。
  //   competition ガード（generation+1・accepts 破棄）は recomputeInstance に集約済み。
  //   適用が無ければ何もしない（no-op）。
  //   フェーズ1（直列計算）: 各指標を順に計算し state を更新する（this._state は呼び出し時 clone・
  //     最後の代入が勝つため並列化は generation の lost update を生む＝直列必須）。描画はしない。
  //   フェーズ2（同期一括描画）: await を挟まず全 job を描画する。中間ペイントが起きないため、
  //     メイン系列（opts.preRender＝setCandles）と全指標が同時に更新される（ISSUE-023）。
  //   persist/legend は描画後に1回だけ。適用 0 でも preRender（候補：メイン系列差し替え）は実行する。
  async recomputeAllApplied({ mode = 'full', preRender = null } = {}) {
    // フェーズ1: 直列計算（描画なし）。
    const jobs = [];
    for (const inst of [...this._state.applied]) {
      const meta = this._meta.get(inst.instanceId);
      if (!meta) {
        continue;
      }
      // MP 種別は /compute を持たない（backend に compute 無し）。再計算経路（ライブ tick /
      //   足切替）で /compute へ流出させると例外→setTimeframe では preRender 前で全スキップ。
      //   /compute を通さず actor.refresh（現時間足で再取得）へ委譲し、MP も新足へ追従させる。
      if (this._isMarketProfile(meta.def)) {
        // [reveal seam] present の MP actor は onLiveTick（ticklive ON=forming 増分 / OFF=refresh 委譲）を持つ。
        //   typeof gate で present は従来どおり onLiveTick を呼び（byte 挙動不変）、onLiveTick を持たない
        //   replay slim actor は skip（render seam の enterBar/feedTick が MP を駆動する）。
        if (this._marketProfile && inst.visible && typeof this._marketProfile.onLiveTick === 'function') {
          await this._marketProfile.onLiveTick();
        }
        continue;
      }
      const job = await this._computeInstance(inst.instanceId, null, this._paramsObject(inst.params), { mode });
      if (job && job.accepted) {
        jobs.push(job);
      }
    }
    // フェーズ2: ここから await を挟まない同期一括描画。
    if (preRender) {
      preRender();
    }
    if (jobs.length === 0) {
      return;
    }
    for (const job of jobs) {
      this._renderInstance(job);
    }
    this._persistAll();
    this._renderLegend();
  }

  // 時間足セレクタの active 表示を現在値へ同期する（DOM 在席時のみ）。
  _syncTimeframeButtons() {
    for (const b of this._el?.timeframeBtns ?? []) {
      b.classList.toggle('is-active', b.dataset.timeframe === this._timeframe);
    }
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

  async restore() {
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
      // MP 種別は /compute で計算しようとして失敗させない。保存 params を actor へ渡し、
      //   可視だった場合のみ有効化して再取得・表示する。
      if (this._isMarketProfile(def)) {
        const rp = this._paramsObject(inst.params);
        if (this._marketProfile) {
          this._applyMpParams(rp);
          if (inst.visible) {
            await this._marketProfile.setEnabled(true);
          }
        }
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
  //   時間足（timeframe）・直近表示本数（limit）は facade を介さずここで注入する（facade は純粋を保つ）。
  //   B方式は /compute がこれで resample・範囲制限し candles と時間軸を揃える。A方式は余剰フィールドを無視。
  _gatewayAdapter(variantOverride, mode) {
    const compute = this._compute;
    const self = this;
    return {
      async compute(req) {
        // 計算.時間足（params.timeframe）の per-indicator override。'chart'/未指定はグローバル
        //   時間足（this._timeframe）に追従、特定足（1h 等）は当該足で計算（MTF）。backend は
        //   params.timeframe を受理引数に含めない（_accepted_kwargs で除外）ため副作用なし。
        const tfParam = req && req.params ? req.params.timeframe : undefined;
        const effectiveTimeframe = tfParam && tfParam !== 'chart' ? tfParam : self._timeframe;
        const result = await compute.compute({
          ...req,
          variant: variantOverride ?? req.variant,
          timeframe: effectiveTimeframe,
          limit: self._recentBars ?? undefined,
          // mode（full/latest）を素通し。未指定は compute_http_client がボディに含めない（後方互換）。
          mode: mode === 'latest' ? 'latest' : undefined,
          // [reveal seam] reveal 拡張フィールド（untilTime/forming 等）を素通しする。present は
          //   空オブジェクト（byte 挙動不変＝compute_http_client の `!== undefined` gate で不送信）。
          //   replay subclass が _extraComputeFields を override して untilTime/forming を注入する。
          ...self._extraComputeFields(),
        });
        self._lastSeries = result.series;
        return result;
      },
    };
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
      onApply: (values, variant) => {
        // variant 変更は実描画反映（事前計算 series が存在・§9.2）。同一 variant の
        //   場合は null を渡し現 variant を維持。任意パラメータ変更は A 方式では未反映
        //   （ダイアログ内に注記済み・H-1）。
        const nextVariant = variant && variant !== inst.variant ? variant : null;
        this.recomputeInstance(inst.instanceId, nextVariant, values);
      },
      onCancel: () => {},
    });
    dialog.open();
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
