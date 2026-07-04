// LocalStorageGateway（adapter/front/local_storage_gateway.js）— StatePersistencePort 実装。
//
// 設計入力: 内部設計書 §6.1（物理スキーマ・全キー indicatorUi. プレフィックス + .vN）、
//   §6.2（破損キーは当該キーのみ初期化・他温存・console 警告／QuotaExceeded(F5) は当該書込み中止）、
//   §6.2 seq 採番（next = (counters[id] ?? 0)+1、同時に永続化・単調増加 §5.7）。
// localStorage を触る唯一の点。storage は注入可（既定 globalThis.localStorage）。

const KEY = Object.freeze({
  schemaVersion: 'indicatorUi.schemaVersion',
  favorites: 'indicatorUi.favorites.v1',
  applied: 'indicatorUi.applied.v1',
  seqCounters: 'indicatorUi.seqCounters.v1',
  uiState: 'indicatorUi.uiState.v1',
});

const DEFAULT_UI = Object.freeze({ lastTab: 'indicator', lastCategory: '', dialogOpen: false });

export class LocalStorageGateway {
  constructor(storage = (typeof globalThis !== 'undefined' ? globalThis.localStorage : undefined)) {
    this._storage = storage;
  }

  // -- 破損時フォールバック付き読み取り（§6.2: 当該キーのみ初期化・他温存）------
  _readJson(key, fallback) {
    let raw;
    try {
      raw = this._storage.getItem(key);
    } catch {
      return fallback;
    }
    if (raw === null || raw === undefined) {
      return fallback;
    }
    try {
      return JSON.parse(raw);
    } catch {
      // 破損: 当該キーのみ初期化（他キーは温存）。全消去しない。
      this._warn(`[persist] 破損キーを初期化: ${key}`);
      this._removeKey(key);
      return fallback;
    }
  }

  // QuotaExceeded(F5): 当該書き込みを中止（throw しない）。メモリ状態は呼び出し側で保持。
  _writeJson(key, value) {
    try {
      this._storage.setItem(key, JSON.stringify(value));
    } catch (e) {
      this._warn(`[persist] 書き込み中止（${e && e.name ? e.name : 'error'}）: ${key}`);
    }
  }

  _removeKey(key) {
    try {
      this._storage.removeItem(key);
    } catch {
      // 削除失敗は無視（次回読みでも fallback を返す）。
    }
  }

  _warn(msg) {
    if (typeof console !== 'undefined' && console.warn) {
      console.warn(msg);
    }
  }

  // -- favorites -------------------------------------------------------------
  loadFavorites() {
    const obj = this._readJson(KEY.favorites, { ids: [] });
    return Array.isArray(obj?.ids) ? obj.ids : [];
  }

  saveFavorites(ids) {
    this._writeJson(KEY.favorites, { ids: Array.isArray(ids) ? ids : [] });
  }

  // -- applied ---------------------------------------------------------------
  loadApplied() {
    const obj = this._readJson(KEY.applied, { instances: [] });
    return Array.isArray(obj?.instances) ? obj.instances : [];
  }

  saveApplied(instances) {
    this._writeJson(KEY.applied, { instances: Array.isArray(instances) ? instances : [] });
  }

  // -- uiState ---------------------------------------------------------------
  loadUiState() {
    const obj = this._readJson(KEY.uiState, { ...DEFAULT_UI });
    if (!obj || typeof obj !== 'object' || Array.isArray(obj)) {
      return { ...DEFAULT_UI };
    }
    return { ...DEFAULT_UI, ...obj };
  }

  saveUiState(state) {
    this._writeJson(KEY.uiState, { ...DEFAULT_UI, ...(state ?? {}) });
  }

  // -- seq 採番（§5.7 単調カウンタ）-----------------------------------------
  nextSeq(indicatorId) {
    const obj = this._readJson(KEY.seqCounters, { counters: {} });
    const counters = obj && typeof obj.counters === 'object' && !Array.isArray(obj.counters) ? obj.counters : {};
    const next = (counters[indicatorId] ?? 0) + 1;
    counters[indicatorId] = next;
    this._writeJson(KEY.seqCounters, { counters });
    return next;
  }
}
