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

export class IndicatorController {
  // mode: 計算モード。'b'=served（ライブ API・params 実反映）/ 'a'=file://（埋め込み事前計算）。
  //   既定 'a'（従来挙動・単体テスト互換）。composition root が served 判定で 'b' を注入する。
  constructor({ catalog, compute, persistence, renderer, document: doc = null, mode = 'a' }) {
    this._catalog = catalog;
    this._compute = compute;
    this._persistence = persistence;
    this._renderer = renderer;
    this._document = doc;
    this._mode = mode;

    // メモリ状態（facade の純状態オブジェクト）。
    this._state = emptyState();
    // instanceId -> { def } 描画済みメタ（凡例再描画・recompute 用）。
    this._meta = new Map();
    // ダイアログ絞り込み UI 状態。
    this._filter = { tab: 'indicator', category: null, query: '', favoriteOnly: false };
  }

  // =========================================================================
  // F3 系列名照合（§3.3.6・DOM 非依存の純ロジック）
  // =========================================================================

  // SeriesDef.series_name（dynamic は series_name_pattern 展開）の期待集合を返す。
  _expectedSeriesNames(def) {
    const names = new Set();
    for (const s of def.series ?? []) {
      if (s.dynamic && s.seriesNamePattern) {
        for (const name of this._expandPattern(s.seriesNamePattern)) {
          names.add(name);
        }
      } else if (s.seriesName) {
        names.add(s.seriesName);
      }
    }
    return names;
  }

  // series_name_pattern を展開（{bucket} {pct}% 形式・profit_band 28 系列）。
  _expandPattern(pattern) {
    const out = [];
    const template = pattern.template ?? '';
    const buckets = pattern.buckets ?? [''];
    const pcts = pattern.pcts ?? [''];
    for (const bucket of buckets) {
      for (const pct of pcts) {
        out.push(template.replace('{bucket}', bucket).replace('{pct}', pct));
      }
    }
    return out;
  }

  // F3: 期待集合に含まれない系列はスキップ（renderLine に渡さない）＋ console.warn 記録。
  _validateSeriesNames(payloads, def) {
    const expected = this._expectedSeriesNames(def);
    return (payloads ?? []).filter((p) => {
      const ok = expected.has(p.name);
      if (!ok && typeof console !== 'undefined' && console.warn) {
        console.warn(`[F3] 系列名不一致のためスキップ: instance=${def.id} name=${p.name}`);
      }
      return ok;
    });
  }

  // 描画: F3 通過系列を kind 別に renderer へ渡す（line / horizontal_line）。
  _draw(instanceId, def, series) {
    const validated = this._validateSeriesNames(series, def);
    const lines = validated.filter((p) => p.kind === 'line');
    const hlines = validated.filter((p) => p.kind === 'horizontal_line');
    if (lines.length > 0) {
      this._renderer.renderLine(instanceId, lines);
    }
    for (const h of hlines) {
      this._renderer.renderHorizontal(instanceId, h.lines ?? []);
    }
  }

  // =========================================================================
  // UC オーケストレーション（facade + ports）
  // =========================================================================

  // UC-02 指標追加: seq 採番→compute（gen=0）→F3→描画→persist。
  async applyIndicator(indicatorId, variant) {
    const def = this._catalog.get(indicatorId);
    if (!def) {
      return null;
    }
    const params = this._defaultParams(def);
    const gateway = this._gatewayAdapter();
    const { state, instance } = await apply(
      this._state,
      { indicatorId, variant: variant ?? this._defaultVariant(def), params, datasetRef: 'sample' },
      gateway,
    );
    this._state = state;
    this._meta.set(instance.instanceId, { def });
    this._draw(instance.instanceId, def, this._lastSeries);
    this._persistAll();
    this._renderLegend();
    return instance;
  }

  // UC-03 再計算（設定変更・variant 切替）: generation 競合破棄は facade.recompute に集約（§6.6）。
  async recomputeInstance(instanceId, newVariant, newParams) {
    const meta = this._meta.get(instanceId);
    if (!meta) {
      return false;
    }
    // variant を差し替える場合は def はそのまま、gateway が variant でキー解決。
    if (newVariant) {
      this._state = this._withVariant(this._state, instanceId, newVariant);
    }
    const params = newParams ?? this._defaultParams(meta.def);
    const gateway = this._gatewayAdapter(newVariant);
    const { state, accepted } = await recompute(this._state, instanceId, params, 'sample', gateway);
    this._state = state;
    if (accepted) {
      // 系列を再生成せず setData 差し替え（line のみ）。horizontal は再描画。
      const validated = this._validateSeriesNames(this._lastSeries, meta.def);
      for (const p of validated) {
        if (p.kind === 'line') {
          this._renderer.setData(`${instanceId}::${p.name}`, p.data ?? []);
        }
      }
      const hlines = validated.filter((p) => p.kind === 'horizontal_line');
      if (hlines.length > 0) {
        this._renderer.remove(instanceId);
        this._draw(instanceId, meta.def, this._lastSeries);
      }
      this._persistAll();
      this._renderLegend();
    }
    return accepted;
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
    // 各 instance を再計算して再描画（A方式は variant 事前計算データで復元）。
    for (const inst of this._state.applied) {
      const def = this._catalog.get(inst.indicatorId);
      if (!def) {
        continue;
      }
      this._meta.set(inst.instanceId, { def });
      try {
        const gateway = this._gatewayAdapter(inst.variant);
        // B方式は保存 params で再計算（実反映）。A方式は params 無視で id:variant キー解決。
        const restoreParams = this._paramsObject(inst.params);
        const result = await gateway.compute({ indicatorId: inst.indicatorId, variant: inst.variant, params: restoreParams, datasetRef: 'sample', generation: inst.generation });
        this._lastSeries = result.series;
        this._draw(inst.instanceId, def, this._lastSeries);
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

  // facade.apply/recompute が呼ぶ compute をラップし、応答 series を捕捉（描画用）。
  _gatewayAdapter(variantOverride) {
    const compute = this._compute;
    const self = this;
    return {
      async compute(req) {
        const result = await compute.compute({ ...req, variant: variantOverride ?? req.variant });
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
    const currentParams = (inst.params && Object.keys(inst.params).length > 0)
      ? inst.params
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
