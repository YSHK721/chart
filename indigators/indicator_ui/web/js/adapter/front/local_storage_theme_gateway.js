// LocalStorageThemeGateway（adapter/front/local_storage_theme_gateway.js）— ThemeStorePort 実装。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.0
//   §4.9（2 論理キー `indicatorUi.themes.v1` / `indicatorUi.activeTheme.v1` と値スキーマ・
//        activeTheme は「選択中テーマ ＋ 採番カウンタ」の**単一原子**・破損時は当該キーのみ
//        空既定へ初期化し他キーを温存＋console 警告・QuotaExceeded は当該書き込み中止で
//        例外を投げない・**接頭辞を自前で付けず注入された storage ポートをそのまま使う**）、
//   §4.10（lastSeq 単調・id の再利用禁止）、§5.7（F-C4 破損 / F-C5 Quota）。
//
// 参照実装（同型元）: local_storage_template_gateway.js（storage 注入・破損時初期化・Quota 中止）。
//   本ゲートウェイは既存ゲートウェイを継承も改変もせず、同一の作法で独立に実装する（ISP）。
//
// 物理キーの接頭辞（統合 UI の `live:`）は scopedStorage 側が付ける（§4.9・E-17）。
//   本クラスは注入された storage をそのまま使い、接頭辞を二重に付けない。

const THEME_KEY = Object.freeze({
  themes: 'indicatorUi.themes.v1',
  activeTheme: 'indicatorUi.activeTheme.v1',
});

// activeTheme.v1 の値スキーマ（§4.9）へ倒す。読み（破損・スキーマ不一致）と書き（不正入力）で
//   同一の規則を使う＝保存形と復元形が定義上ずれない。解釈できたフィールドは残す（前方互換）。
function normalizeActiveTheme(obj) {
  return {
    themeId: typeof obj?.themeId === 'string' ? obj.themeId : null,
    lastSeq: Number.isInteger(obj?.lastSeq) && obj.lastSeq >= 0 ? obj.lastSeq : 0,
  };
}

export class LocalStorageThemeGateway {
  constructor(storage = (typeof globalThis !== 'undefined' ? globalThis.localStorage : undefined)) {
    this._storage = storage;
  }

  // -- 破損時フォールバック付き読み取り（§4.9: 当該キーのみ初期化・他キー温存・console 警告）----
  _readJson(key, fallback) {
    let raw;
    try {
      raw = this._storage.getItem(key);
    } catch {
      // storage 自体が読めない（プライバシー設定等）。空既定で運用を続ける。
      return fallback;
    }
    if (raw === null || raw === undefined) {
      return fallback;
    }
    try {
      return JSON.parse(raw);
    } catch {
      // F-C4: 破損キーのみ初期化（他キーは温存・全消去しない）。
      this._warn(`[color-theme] 破損キーを初期化: ${key}`);
      this._removeKey(key);
      return fallback;
    }
  }

  // F-C5 QuotaExceeded: 当該書き込みを中止（throw しない）。メモリ状態は呼び出し側で保持。
  _writeJson(key, value) {
    try {
      this._storage.setItem(key, JSON.stringify(value));
    } catch (e) {
      this._warn(`[color-theme] 書き込み中止（${e && e.name ? e.name : 'error'}）: ${key}`);
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

  // スキーマ不一致（JSON としては妥当だが型が違う）も当該キーのみ空既定へ倒す（F-C4）。
  //   キー未設定（初回起動）は破損ではないため警告しない。
  _warnSchema(key) {
    this._warn(`[color-theme] スキーマ不一致のため空既定を使用: ${key}`);
  }

  // -- themes（{ themes: COLOR_THEME[] }）--------------------------------------
  loadThemes() {
    const obj = this._readJson(THEME_KEY.themes, { themes: [] });
    if (Array.isArray(obj?.themes)) {
      return obj.themes;
    }
    this._warnSchema(THEME_KEY.themes);
    return [];
  }

  saveThemes(themes) {
    this._writeJson(THEME_KEY.themes, { themes: Array.isArray(themes) ? themes : [] });
  }

  // -- activeTheme（{ themeId: string | null, lastSeq: int }・単一原子）---------
  //   選択中テーマと採番カウンタは同一の原子性単位（§4.9）。lastSeq 専用メソッドへ割らない
  //   （割ると「id を発行したが選択は書けていない」中間状態が生まれ、§4.10 の
  //   「発行と同時に永続化する」が保てなくなる）。
  loadActiveTheme() {
    const obj = this._readJson(THEME_KEY.activeTheme, {});
    const active = normalizeActiveTheme(obj);
    // 値が「在るのに解釈できない」ときだけスキーマ不一致として警告する。
    //   未設定（初回起動）と themeId === null（テーマ未選択＝既定状態）は破損ではない。
    const badThemeId = obj?.themeId !== undefined && obj?.themeId !== null && active.themeId === null;
    const badLastSeq = obj?.lastSeq !== undefined && active.lastSeq !== obj.lastSeq;
    const dropped = badThemeId || badLastSeq;
    if (dropped) {
      this._warnSchema(THEME_KEY.activeTheme);
    }
    return active;
  }

  saveActiveTheme(active) {
    this._writeJson(THEME_KEY.activeTheme, normalizeActiveTheme(active));
  }
}
