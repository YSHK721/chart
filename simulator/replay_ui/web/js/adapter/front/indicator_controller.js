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

// 末尾K差分反映（updateSeriesTail）の対象となる時系列系列か。horizontal_line は末尾K切り
//   せず全件返るため対象外（latest 経路に乗らず remove+redraw へフォールバックする）。
function isTailUpdatable(payload) {
  return payload.kind === 'line' || payload.kind === 'histogram';
}

// [PROTO 再生] 足内（ティック粒度）追従の対象指標。形成中バーを末尾へ差し込み、line/histogram の
//   最終点だけを updateSeriesTail で更新する（horizontal_line の水準線は据え置き）。移動平均に加え、
//   標準化窓を持たない profit_* 8 指標を対象とする（混在 kind は forceTail で末尾差分経路へ倒す）。
const INTRABAR_FORMING_IDS = new Set([
  'moving_averages',
  'profit_mfi', 'profit_rsi', 'profit_stc', 'profit_oscillator2',
  'profit_osi_ma', 'profit_hlband', 'profit_mfi_macd', 'profit_rsi_macd',
  // 標準化窓 W を持つ profit_* のうち、本体（line/histogram）を持つ 6 指標を追加（推奨A）。
  //   因果窓ゆえ過去点は repaint せず、最新点のみ forming で動く（実証済み）。profit_hl_band は
  //   horizontal_line のみ（アニメ可能な本体なし）のため対象外＝末尾差分では動かない。
  'profit_adx_needle', 'profit_arctan', 'profit_oscillator',
  'profit_rmm', 'profit_volatility', 'profit_rmm_macd',
]);

export class IndicatorController {
  // mode: 計算モード。'b'=served（ライブ API・params 実反映）/ 'a'=file://（埋め込み事前計算）。
  //   既定 'a'（従来挙動・単体テスト互換）。composition root が served 判定で 'b' を注入する。
  constructor({
    catalog, compute, persistence, renderer, document: doc = null, mode = 'a',
    datasetRef = 'sample', timeframe = '1D', recentBars = null, loadCandles = null,
    marketProfile = null,
  }) {
    this._catalog = catalog;
    this._compute = compute;
    // Market Profile slim actor（任意注入）。computeId==='market_profile' の指標を /compute 経由でなく
    //   本アクター（GET /market_profile_forming → primitive）へ委譲する。composition root が
    //   setupReplay と同一実体を注入し、menu 状態（setEnabled）を駆動フック（isEnabled）が観測する。
    this._marketProfile = marketProfile;
    this._persistence = persistence;
    this._renderer = renderer;
    this._document = doc;
    this._mode = mode;
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
    // [PROTO 再生] untilTime（再生のその時点・UNIX秒）。undefined=ライブ（present）。
    //   setUntilTime で設定し、_gatewayAdapter の compute へ素通しする。
    this._untilTime = undefined;
    // [PROTO 再生] forming（足内更新中の形成中バー暫定 OHLC）。undefined=確定足のまま計算。
    //   setForming で設定し、_gatewayAdapter の compute へ素通しする（latest 経路でのみ backend が使用）。
    this._forming = undefined;
  }

  // [PROTO 再生] untilTime を設定（以降の再計算がこの時点で計算される＝ライブ同一・df[:t+1]）。
  setUntilTime(t) {
    this._untilTime = t;
  }

  // [PROTO 再生] 形成中バー（暫定 OHLC）を設定。undefined で解除（確定足計算へ戻す）。
  setForming(forming) {
    this._forming = forming;
  }

  // [PROTO 再生] 足内更新: 形成中バーを差し込み、対象指標（INTRABAR_FORMING_IDS）の末尾点のみ
  //   latest 差分再計算する。混在 kind（line+horizontal_line）でも forceTail=true で末尾差分経路へ
  //   倒し、line/histogram の最終点のみ更新（水準線は据え置き＝履歴潰れなし）。tgp 帯等の対象外は
  //   足確定値のまま（頻度分離 帯=足/判定=足内）＝触らない。forming 解除は finally で必ず行い、
  //   後続の確定足計算に formingを残さない。
  async recomputeFormingLatest(forming) {
    this.setForming(forming);
    try {
      for (const inst of [...this._state.applied]) {
        if (!INTRABAR_FORMING_IDS.has(inst.indicatorId)) {
          continue;                                        // 足内 latest 対象外（帯系等）は触らない
        }
        if (!this._meta.has(inst.instanceId)) {
          continue;
        }
        await this.recomputeInstance(
          inst.instanceId, null, this._paramsObject(inst.params), { mode: 'latest', forceTail: true },
        );
      }
    } finally {
      this.setForming(undefined);                          // 確定計算へ formingを残さない
    }
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

  // MP 種別判定（computeId==='market_profile'）。/compute をバイパスし slim actor へ委譲する。
  _isMarketProfile(def) {
    return def?.compute?.computeId === 'market_profile';
  }

  // slim actor へ渡す forming param を整形する（effective なもののみ＝bins/va）。
  _mpParams(params) {
    const p = this._paramsObject(params);
    return { bins: p.bins, va: p.va };
  }

  // MP アクターへ params を渡して有効化する（setParams→setEnabled(true)＝取得＋表示）。
  //   現在バー T（_untilTime）が確定していれば即 enterBar で base を描画する（未確定時は render seam が駆動）。
  //   未注入時は no-op。
  async _enableMarketProfile(params) {
    if (!this._marketProfile) {
      return;
    }
    if (typeof this._marketProfile.setParams === 'function') {
      this._marketProfile.setParams(this._mpParams(params));
    }
    this._marketProfile.setEnabled(true);
    if (this._untilTime != null && typeof this._marketProfile.enterBar === 'function') {
      await this._marketProfile.enterBar(this._untilTime);
    }
  }

  // MP 専用適用パス: /compute をバイパスし、state には no-op gateway で instance を登録して
  //   凡例表示・永続化・restore の対象に含める。描画は MarketProfileReplayActor（forming→primitive）へ委譲。
  //   MP 単一インスタンス制約: 既に MP 適用済みなら新規 legend 行を作らず既存を返す（no-op）。
  async _applyMarketProfile(def, variant, params) {
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

  // UC-02 指標追加: seq 採番→compute（gen=0）→F3→描画→persist。
  //   MP 種別（computeId==='market_profile'）は /compute をバイパスし actor へ委譲する。
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

  // UC-03 再計算（設定変更・variant 切替）: generation 競合破棄は facade.recompute に集約（§6.6）。
  //   opts.mode='latest' は Latest 増分計算（gateway へ mode 伝播・末尾K点を updateSeriesTail へ
  //   差分反映し remove+_draw の全描画はしない）。既定 'full' は従来どおり remove+redraw。
  async recomputeInstance(instanceId, newVariant, newParams, { mode = 'full', forceTail = false } = {}) {
    // MP 設定変更: /compute へ流さず setParams + 現在バーで再 enterBar（gear onApply 経路）。
    const meta = this._meta.get(instanceId);
    if (meta && this._isMarketProfile(meta.def)) {
      return this._recomputeMarketProfile(instanceId, newParams);
    }
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
    //   forceTail=true（[PROTO 再生] 足内追従）は混在指標でも末尾差分（updateSeriesTail）経路へ
    //   倒す。末尾差分は既存系列の最終点のみ更新し horizontal_line は据え置くため、全差替の
    //   履歴潰れ（🔴 回帰 indicator_controller_latest）は起きない。前提＝直前に full 描画済み。
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

  // UC-04 表示/非表示。MP は renderer を持たず actor.setEnabled(visible) へ委譲する。
  toggleVisible(instanceId) {
    this._state = facadeToggleVisible(this._state, instanceId);
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    if (inst) {
      const def = this._catalog.get(inst.indicatorId);
      if (this._isMarketProfile(def)) {
        if (this._marketProfile) {
          this._marketProfile.setEnabled(inst.visible);
        }
      } else {
        this._renderer.setVisible(instanceId, inst.visible);
      }
    }
    this._persistAll();
    this._renderLegend();
  }

  // UC-05 削除。MP は renderer.remove を持たず actor.setEnabled(false)+detach（あれば）へ委譲する。
  removeInstance(instanceId) {
    const inst = this._state.applied.find((i) => i.instanceId === instanceId);
    const def = inst ? this._catalog.get(inst.indicatorId) : null;
    if (this._isMarketProfile(def)) {
      if (this._marketProfile) {
        this._marketProfile.setEnabled(false);
        if (typeof this._marketProfile.detach === 'function') {
          this._marketProfile.detach();
        }
      }
    } else {
      this._renderer.remove(instanceId);
    }
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
      if (this._meta.has(inst.instanceId)) {
        // MP は /compute へ流さない（render seam の enterBar が駆動する）＝skip。
        if (this._isMarketProfile(this._meta.get(inst.instanceId).def)) {
          continue;
        }
        const job = await this._computeInstance(inst.instanceId, null, this._paramsObject(inst.params), { mode });
        if (job && job.accepted) {
          jobs.push(job);
        }
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
      // MP は /compute へ流さない。保存 params/可視状態で actor を復元する（render seam が base を描画）。
      if (this._isMarketProfile(def)) {
        if (this._marketProfile) {
          if (typeof this._marketProfile.setParams === 'function') {
            this._marketProfile.setParams(this._mpParams(this._paramsObject(inst.params)));
          }
          this._marketProfile.setEnabled(!!inst.visible);
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

  // instance の params（pairs 形式）を差し替える（MP gear の設定変更で state を更新し restore へ反映）。
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

  // MP 設定変更（gear onApply）: /compute を呼ばず state.params を更新し、actor へ setParams +
  //   現在バー T（_untilTime）で再 enterBar（base 取り直し）。未注入/未確定時は state 更新のみ。
  async _recomputeMarketProfile(instanceId, newParams) {
    const meta = this._meta.get(instanceId);
    const params = newParams ?? this._defaultParams(meta.def);
    this._state = this._withParams(this._state, instanceId, params);
    if (this._marketProfile) {
      if (typeof this._marketProfile.setParams === 'function') {
        this._marketProfile.setParams(this._mpParams(params));
      }
      if (this._untilTime != null && typeof this._marketProfile.enterBar === 'function') {
        await this._marketProfile.enterBar(this._untilTime);
      }
    }
    this._persistAll();
    this._renderLegend();
    return true;
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
          // [PROTO 再生] untilTime を素通し（undefined=ライブ present）。
          untilTime: self._untilTime,
          // [PROTO 再生] forming（足内更新の形成中バー）を素通し（undefined=確定足のまま）。
          forming: self._forming,
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

  _openDialog() {
    if (this._el?.dialog) {
      this._el.dialog.classList.add('is-open');
      this._state.uiState = { ...this._state.uiState, dialogOpen: true };
      this._renderDialogList();
    }
  }

  _closeDialog() {
    if (this._el?.dialog) {
      this._el.dialog.classList.remove('is-open');
      this._state.uiState = { ...this._state.uiState, dialogOpen: false };
    }
  }

  _setActive(group, active) {
    for (const el of group ?? []) {
      el.classList.toggle('is-active', el === active);
    }
  }

  _label(def) {
    // displayNameKey の末尾を表示名相当に（i18n 解決器を持たないプロトタイプ）。
    const k = def.displayNameKey ?? def.id;
    return k.includes('.') ? k.split('.').pop() : k;
  }

  _renderDialogList() {
    const doc = this._document;
    if (!doc || !this._el?.list) {
      return;
    }
    const favorites = this._state.favorites;
    const defs = listForView({ ...this._filter, favorites });
    const list = this._el.list;
    list.innerHTML = '';
    for (const def of defs) {
      const row = doc.createElement('div');
      row.className = 'ind-row';
      const star = doc.createElement('button');
      star.className = 'ind-fav' + (favorites.includes(def.id) ? ' is-on' : '');
      star.textContent = favorites.includes(def.id) ? '★' : '☆';
      star.addEventListener('click', (ev) => { ev.stopPropagation(); this.toggleFavorite(def.id); });
      const name = doc.createElement('span');
      name.className = 'ind-name';
      name.textContent = this._label(def);
      const cat = doc.createElement('span');
      cat.className = 'ind-cat';
      cat.textContent = (def.category?.nameKey ?? '').split('.').pop();
      row.append(star, name, cat);
      row.addEventListener('click', () => { this.applyIndicator(def.id, this._defaultVariant(def)); this._closeDialog(); });
      list.append(row);
    }
  }

  _renderLegend() {
    const doc = this._document;
    if (!doc || !this._el?.legend) {
      return;
    }
    const legend = this._el.legend;
    legend.innerHTML = '';
    for (const inst of this._state.applied) {
      const def = this._catalog.get(inst.indicatorId);
      const row = doc.createElement('div');
      row.className = 'legend-row';

      const label = doc.createElement('span');
      label.className = 'legend-label';
      label.textContent = `${def ? this._label(def) : inst.indicatorId}${inst.variant && inst.variant !== 'default' ? ' (' + inst.variant + ')' : ''}`;

      const eye = doc.createElement('button');
      eye.className = 'legend-eye';
      eye.title = inst.visible ? '非表示にする' : '表示する';
      eye.textContent = inst.visible ? '👁' : '🙈';
      eye.addEventListener('click', () => this.toggleVisible(inst.instanceId));

      const gear = doc.createElement('button');
      gear.className = 'legend-gear';
      gear.title = '設定';
      gear.textContent = '⚙';
      gear.addEventListener('click', () => this._onGear(inst, def));

      const close = doc.createElement('button');
      close.className = 'legend-remove';
      close.title = '削除';
      close.textContent = '✕';
      close.addEventListener('click', () => this.removeInstance(inst.instanceId));

      row.append(label, eye, gear, close);
      legend.append(row);
    }
  }

  // 設定: 歯車クリックでプロパティダイアログを開く（§7.1）。
  //   現 AppliedInstance のパラメータを読込→編集→OK で recomputeInstance（generation+1・§6.6）。
  //   A 方式 gateway は params を無視し id:variant キーで解決するため、variant 以外の値変更は
  //   描画へ未反映（H-1）。ダイアログ内に A 方式注記を明示表示する（§9.3・サイレント不一致回避）。
  _onGear(inst, def) {
    const doc = this._document;
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
