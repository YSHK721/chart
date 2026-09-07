// LocalStorageTemplateGateway（adapter/front/local_storage_template_gateway.js）— TemplateStorePort 実装。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_チャートテンプレート.md v0.1.1
//   §4.2（3 論理キー `indicatorUi.templates.v1` / `indicatorUi.templateBindings.v1` /
//        `indicatorUi.templateSeq.v1` と値スキーマ・破損時は当該キーのみ空既定へ初期化し他キーを
//        温存＋console 警告・QuotaExceeded は当該書き込み中止で例外を投げない・
//        **接頭辞を自前で付けず注入された storage ポートをそのまま使う**）、
//   §4.3（templateSeq 単調性）、§5.6（F-T2 破損 / F-T5 Quota）、
//   §7.1（既存 `LocalStorageGateway` は無改変＝ISP: 既存ポートを汚さない）。
//
// 参照実装（同型元）: local_storage_gateway.js（storage 注入・破損時初期化・Quota 中止）。
//   本ゲートウェイは既存ゲートウェイを継承も改変もせず、同一の作法で独立に実装する。
//
// 物理キーの接頭辞（統合 UI の `live:`）は scopedStorage 側が付ける（§4.2・E-10）。
//   本クラスは注入された storage をそのまま使い、接頭辞を二重に付けない。

const TEMPLATE_KEY = Object.freeze({
  templates: 'indicatorUi.templates.v1',
  bindings: 'indicatorUi.templateBindings.v1',
  templateSeq: 'indicatorUi.templateSeq.v1',
});

export class LocalStorageTemplateGateway {
  constructor(storage = (typeof globalThis !== 'undefined' ? globalThis.localStorage : undefined)) {
    this._storage = storage;
  }

  // -- 破損時フォールバック付き読み取り（§4.2: 当該キーのみ初期化・他キー温存・console 警告）----
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
      // F-T2: 破損キーのみ初期化（他キーは温存・全消去しない）。
      this._warn(`[template] 破損キーを初期化: ${key}`);
      this._removeKey(key);
      return fallback;
    }
  }

  // F-T5 QuotaExceeded: 当該書き込みを中止（throw しない）。メモリ状態は呼び出し側で保持。
  _writeJson(key, value) {
    try {
      this._storage.setItem(key, JSON.stringify(value));
    } catch (e) {
      this._warn(`[template] 書き込み中止（${e && e.name ? e.name : 'error'}）: ${key}`);
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

  // スキーマ不一致（JSON としては妥当だが型が違う）も当該キーのみ空既定へ倒す（F-T2）。
  _warnSchema(key) {
    this._warn(`[template] スキーマ不一致のため空既定を使用: ${key}`);
  }

  // -- templates（{ templates: CHART_TEMPLATE[] }）------------------------------
  loadTemplates() {
    const obj = this._readJson(TEMPLATE_KEY.templates, { templates: [] });
    if (Array.isArray(obj?.templates)) {
      return obj.templates;
    }
    if (obj && obj.templates !== undefined) {
      this._warnSchema(TEMPLATE_KEY.templates);
    }
    return [];
  }

  saveTemplates(templates) {
    this._writeJson(TEMPLATE_KEY.templates, { templates: Array.isArray(templates) ? templates : [] });
  }

  // -- bindings（{ bindings: { <timeframe>: <templateId> } }）--------------------
  loadBindings() {
    const obj = this._readJson(TEMPLATE_KEY.bindings, { bindings: {} });
    const b = obj?.bindings;
    if (b && typeof b === 'object' && !Array.isArray(b)) {
      return { ...b };
    }
    if (b !== undefined) {
      this._warnSchema(TEMPLATE_KEY.bindings);
    }
    return {};
  }

  saveBindings(bindings) {
    const b = bindings && typeof bindings === 'object' && !Array.isArray(bindings) ? bindings : {};
    this._writeJson(TEMPLATE_KEY.bindings, { bindings: b });
  }

  // -- templateSeq（{ lastSeq: int }・§4.3 単調増加・削除で減算しない）-----------
  loadTemplateSeq() {
    const obj = this._readJson(TEMPLATE_KEY.templateSeq, { lastSeq: 0 });
    const n = obj?.lastSeq;
    if (Number.isInteger(n) && n >= 0) {
      return n;
    }
    if (n !== undefined) {
      this._warnSchema(TEMPLATE_KEY.templateSeq);
    }
    return 0;
  }

  saveTemplateSeq(lastSeq) {
    this._writeJson(TEMPLATE_KEY.templateSeq, { lastSeq: Number.isInteger(lastSeq) && lastSeq >= 0 ? lastSeq : 0 });
  }
}
