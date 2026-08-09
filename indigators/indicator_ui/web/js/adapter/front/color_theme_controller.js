// color_theme_controller.js — 指標カラーテーマ（保存・適用・改名・削除）の協働子。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.1
//   §5.1（UC-C01 保存）／§5.2（UC-C02 適用の手順順序）／§5.3（UC-C03 改名・削除）／
//   §5.5（起動時の復元）／§5.7（F-C1・F-C4・F-C5・F-C6）／§3.4（ビュー自動介入の禁止）／
//   §7.1（ホスト契約にのみ依存する協働子）／§7.3（ISP: 契約は 4 メンバーちょうど）。
//
// 責務（SRP）: テーマ集合・選択中テーマの保持と、UC-C01〜C03 のオーケストレーション。
// 非責務:
//   - 色の決定 …… usecase/color_resolver.js（純関数）
//   - テーマ集合の写像 …… usecase/color_themes.js（純関数）
//   - 永続化の具象 …… adapter/front/local_storage_theme_gateway.js
//   - クロムの配信 …… adapter/front/chrome_theme_applier.js
//   - **系列色の適用** …… host._applyStoredStyles（色の書き手は 1 箇所・R-1）
//   - DOM 構築（メニュー・ダイアログの markup は color_theme_menu / color_theme_dialogs が所有する。
//     本協働子が持つのは「いつ描き直すか・どのダイアログを開くか」だけで、要素は 1 つも作らない。
//     参照実装 chart_template_controller.js と同一の受け渡し規約）
//
// 色の書き手を 2 つ作らない（§7.2 S2・R-1）: 本協働子は系列を走査しない・系列名を知らない・
//   色を決めない。UC-C02 手順 3 は「描画済みインスタンスに対して host._applyStoredStyles を
//   呼ぶ」だけであり、そのメソッドが resolver 経由で色を決めて適用する。協働子が独自に系列を
//   回して色を書くと、再計算・復元・時間足切替の後段（series_render_router.js:103）と本経路の
//   2 人が色を書くことになり、経路ごとに結果が食い違う。
//
// ビュー自動介入の禁止（§3.4・ISSUE-164）: 適用は再計算を伴わない。`/compute` を呼ばず、
//   `setData` / `fitContent` / `setVisibleLogicalRange` / 価格スケールの自動調整の切替 /
//   `scrollToPosition` のいずれにも到達しない。到達しないことは host 射影が構造的に保証し、
//   協働子ソースの走査テストが二重に固定する。
//
// 決定論（時刻）: `createdAt` / `updatedAt` に使う時刻は**注入**で受ける（本モジュールは時計を
//   持たない）。未注入時は 0 を返す時計になる。

import {
  adoptThemes,
  deleteTheme as deleteThemeUc,
  findThemeByName as findThemeByNameUc,
  projectThemeForUse,
  recoverLastSeq,
  renameTheme as renameThemeUc,
  resolveActiveThemeId,
  saveTheme as saveThemeUc,
  unknownRoleTokens,
} from '../../usecase/color_themes.js';
import { resolveAllChrome } from '../../usecase/color_resolver.js';

/**
 * ColorThemeController が host に要求する最小契約（ISP・§7.3 で 4 メンバーへ確定）。
 *
 * @typedef {object} ThemeHost
 * @property {function} _applyStoredStyles  1 インスタンスの保存済みスタイル＋テーマ色を適用する。
 * @property {function} _renderLegend       凡例を再描画する。
 * @property {{applied: Array}} _state      適用済みインスタンス（宣言順）を保持する純状態。
 * @property {Map} _meta                    instanceId -> { def } 描画済みメタ（未描画判定）。
 */

// ThemeHost 契約の実体列挙（構造充足テスト・射影の固定点）。
//   `applySeriesStyle` / `applyLevelLineColor` は IndicatorController に**在席しない**（実体は
//   ChartRenderer）ため契約に置かない。`_catalog` は `_applyStoredStyles` が内部解決するため不要。
//   `_persistAll` / `_commitState` は colorLocked を書く段階 4 で追加する。
export const COLOR_THEME_HOST_CONTRACT = Object.freeze({
  role: 'ThemeHost',
  methods: Object.freeze(['_applyStoredStyles', '_renderLegend']),
  fields: Object.freeze(['_state', '_meta']),
});

function warn(msg) {
  if (typeof console !== 'undefined' && console.warn) {
    console.warn(msg);
  }
}

/**
 * 永続層から起動時のテーマ状態を復元する（§5.5・§4.10・F-C6）。
 *
 * 復元の規則をここ 1 箇所に持つ理由: 起動時のクロム配信（組み立て点＝composeChartShell）と
 * 協働子の初期状態は**同じ解決結果**でなければならない。2 箇所で読むと、dangling の縮退や
 * lastSeq の復旧が片側にだけ効き、「地の色」と「協働子が思っている選択中テーマ」がずれる。
 *
 * ここは **テーマ集合を採用する唯一の読み出し境界** でもある。`themes.v1` は外から書き換わり
 * うる（旧版・手編集・他端末）ため、消費する値は §4.4 の形へ射影する（projectThemeForUse）。
 * 射影を消費の入口で 1 度やることで「roleColors の値は必ず正規化済み hex6」が全消費者で成立し、
 * 消費者ごとに未正規化値の判定を書く必要が構造的に無くなる（F-C9 の取り残しの除去）。
 *
 * 保持するテーマ集合そのものは **原形のまま** 採用する（§4.9 前方互換・§5.3）。ここで書き換えると、
 * 解釈できないだけの領域（未知トークン）が読み込んだ時点で消え、改名しただけで永続層から失われる。
 *
 * @param {object} gateway ThemeStorePort（loadThemes / loadActiveTheme / saveActiveTheme）。
 * @returns {{themes: Array, activeThemeId: ?string, lastSeq: number, theme: ?object}}
 *   `themes` は原形（永続化の対象）、`theme` は選択中テーマの射影（消費の対象）。
 */
export function loadThemeState(gateway) {
  const themes = adoptThemes(gateway.loadThemes());
  // F-C3: 未知トークンを報告するのは adapter（usecase は console を持たない・R-4）。
  //   一覧は 1 回の読み出しにつき 1 本なので、警告も 1 回になる（トークン数ぶん増えない）。
  const ignoredTokens = unknownRoleTokens(themes);
  if (ignoredTokens.length > 0) {
    warn(`[color-theme] 未知のトークンは解釈できないため無視する（保存値は温存）: ${[...new Set(ignoredTokens)].join(', ')}`);
  }
  const active = gateway.loadActiveTheme();
  // §4.10: activeTheme.v1 破損（lastSeq が 0 へ倒れた等）でも id を再利用しないよう引き上げる。
  const lastSeq = recoverLastSeq(active.lastSeq, themes);
  const resolved = resolveActiveThemeId({ themes, activeThemeId: active.themeId });
  if (resolved.changed) {
    warn(`[color-theme] 参照先テーマが不在のためテーマ未選択へ縮退: ${active.themeId}`);
  }
  if (resolved.changed || lastSeq !== active.lastSeq) {
    // F-C6 の遅延クリーンアップと lastSeq の復旧は同一原子（activeTheme.v1）で書く。
    gateway.saveActiveTheme({ themeId: resolved.activeThemeId, lastSeq });
  }
  return {
    themes,
    activeThemeId: resolved.activeThemeId,
    lastSeq,
    theme: projectThemeForUse(themeById(themes, resolved.activeThemeId)).theme,
  };
}

function themeById(themes, themeId) {
  if (themeId == null) {
    return null;
  }
  return (themes ?? []).find((t) => t && t.themeId === themeId) ?? null;
}

export class ColorThemeController {
  /**
   * @param {ThemeHost} host ThemeHost 契約の射影（createHostView の戻り値）。
   * @param {object} deps
   * @param {object} deps.gateway ThemeStorePort 実装（LocalStorageThemeGateway）。
   * @param {?object} [deps.chromeApplier] ChromeThemeApplier（apply({slots, tokens}) のみ使用）。
   * @param {?object} [deps.state] 起動時に解決済みのテーマ状態（loadThemeState の戻り値）。
   *   未注入時は自分で gateway から復元する（単体・後方互換）。
   * @param {?object} [deps.menu] ColorThemeMenu（render(vm) のみ使用・null 可）。
   * @param {?object} [deps.dialogs] ColorThemeDialogs（openEdit / openManage・null 可）。
   * @param {function} [deps.now] UNIX 秒を返す時刻源。未注入時は実時刻を用いる
   *   （既定つき注入。参照実装 `chart_template_controller.js:86` と同一の idiom）。
   */
  constructor(host, {
    gateway, chromeApplier = null, state = null, menu = null, dialogs = null, now = null,
  } = {}) {
    this._host = host;
    this._gateway = gateway;
    this._chrome = chromeApplier;
    // UI 部品は**注入で受ける**（参照実装 chart_template_controller.js:79-80 と同一）。
    //   未注入（単体テスト・SSR）は render / ダイアログ入口が no-op になるだけで、
    //   保存・適用・改名・削除の振る舞いは変わらない。
    this._menu = menu;
    this._dialogs = dialogs;
    // 時刻源は既定つき注入。テストは固定時刻を注入して決定論を保ち、本番は実時刻を使う。
    //   既定を 0 にすると createdAt/updatedAt が全テーマで 0 になり、§4.4 の「作成時刻・最終更新
    //   時刻」が意味を失う（一覧の並びや更新の前後関係が判定できなくなる）。
    this._now = typeof now === 'function' ? now : () => Math.floor(Date.now() / 1000);
    const initial = state ?? loadThemeState(gateway);
    this._themes = initial.themes;
    this._activeThemeId = initial.activeThemeId;
    this._lastSeq = initial.lastSeq;
  }

  // ---- 参照面 ---------------------------------------------------------------
  themes() { return this._themes; }

  activeThemeId() { return this._activeThemeId; }

  // 選択中テーマ（未選択は null）。IndicatorController の colorThemeProvider とクロム配信の値源。
  //   返すのは**消費のための射影**（§4.4 の形）で、保持している永続値は原形のまま（§4.9）。
  //   色を消費する経路をここ 1 本にすることで、解釈不能値の判定を消費者ごとに書かずに済む。
  activeTheme() { return projectThemeForUse(themeById(this._themes, this._activeThemeId)).theme; }

  // 正規化名が一致する既存テーマを返す（無ければ null）。保存時の上書き確認の判定源。
  //   判定は usecase の純関数のみが行い、ダイアログは結果を受け取るだけ（DIP・判定源の一本化）。
  findThemeByName(name) {
    return findThemeByNameUc({ themes: this._themes, name });
  }

  // メニュー再描画用のビューモデル（適用・保存・改名・削除の後の push と、開くたびの pull で共用）。
  viewModel() {
    return { themes: this._themes, activeThemeId: this._activeThemeId };
  }

  render() {
    if (this._menu && typeof this._menu.render === 'function') {
      this._menu.render(this.viewModel());
    }
  }

  // ---- UC-C02 適用（§5.2・この順序で固定）------------------------------------
  /**
   * テーマを適用する。`themeId = null` は「テーマなし」（既定色へ戻す）。
   * 再計算は行わない（色は既存系列とチャート全体のオプションだけで完結する）。
   *
   * @param {?string} themeId 適用するテーマ id（不在なら F-C6 で「テーマなし」へ縮退）。
   * @returns {?string} 適用後の選択中テーマ id。
   */
  applyTheme(themeId) {
    const resolved = resolveActiveThemeId({ themes: this._themes, activeThemeId: themeId ?? null });
    if (resolved.changed) {
      warn(`[color-theme] 適用対象のテーマが不在: ${themeId}（テーマ未選択として解決）`);
    }
    const id = resolved.activeThemeId;
    // 手順 1: 選択中テーマを確定して永続化する。
    this._setActiveThemeId(id);
    const theme = this.activeTheme();
    // 手順 2: 地（クロム）を先に決める。JS 機構と :root の --ct-* が同時に更新される。
    this._applyChrome(theme);
    // 手順 3: 図（指標系列）を宣言順に載せる。色を決めて書くのは host 側 1 箇所（R-1）。
    //   反復中に host._state が差し替わる（_applyStoredStyles は styles を剪定して state を
    //   作り直す）ため、走査対象は開始時点のスナップショットで固定する。
    const host = this._host;
    for (const instance of [...host._state.applied]) {
      if (!host._meta.has(instance.instanceId)) {
        continue; // 未描画（復元途中・計算失敗）は適用対象が存在しない。
      }
      host._applyStoredStyles(instance.instanceId);
    }
    // 手順 4: 凡例は反復の外で 1 回だけ再描画する。
    host._renderLegend();
    // 手順 5: メニューの選択状態（is-active）を現在値へ追随させる（未注入時は no-op）。
    this.render();
    return id;
  }

  // ---- UC-C01 保存（§5.1）----------------------------------------------------
  //   保存は**適用ではない**（後条件: チャート上の色は変化しない）。失敗は CODE をそのまま返し、
  //   例外は投げない（呼び出し側のダイアログがインライン表示へ写像する）。
  saveTheme({ name, roleColors = {}, tfModifier = null } = {}) {
    const res = saveThemeUc({
      themes: this._themes,
      lastSeq: this._lastSeq,
      name,
      roleColors,
      tfModifier,
      now: this._now(),
    });
    // F-C3: 未知トークンを無視したという**事実**は usecase が戻り値で返す（純関数は console を
    //   持たない・R-4）。それを開発者へ報告するのはこの層の決定で、一覧は 1 呼び出しにつき
    //   1 本なので警告も 1 回になる（トークン数ぶん増えない）。名前検証で中止した呼び出しは
    //   色を見ていないため空＝無言、という現行の振る舞いもそのまま従う。
    if (res.ignoredTokens.length > 0) {
      warn(`[color-theme] 未知のトークンを無視: ${res.ignoredTokens.join(', ')}`);
    }
    if (!res.ok) {
      return res; // F-C1: 既存データは不変。
    }
    this._themes = res.themes;
    this._gateway.saveThemes(res.themes);
    if (res.lastSeq !== this._lastSeq) {
      // §4.10: 発行と同時に永続化する（activeTheme.v1 は選択中テーマと採番の単一原子）。
      this._lastSeq = res.lastSeq;
      this._persistActive();
    }
    // 保存は**適用ではない**が、一覧には新しい（あるいは上書きされた）テーマが載る。
    this.render();
    return res;
  }

  // ---- UC-C03 改名・削除（§5.3）----------------------------------------------
  //   改名: チャート上の色は変化しない。
  renameTheme(themeId, name) {
    const res = renameThemeUc({
      themes: this._themes, themeId, name, now: this._now(),
    });
    if (!res.ok) {
      return res;
    }
    this._themes = res.themes;
    this._gateway.saveThemes(res.themes);
    this.render();
    return res;
  }

  // 削除: activeThemeId が当該 id なら null にするが、**チャート上の色は変更しない**。
  //   次に描画が起こった時点から既定で解決される。この非対称は意図的（誤操作の被害を広げない）。
  deleteTheme(themeId) {
    const res = deleteThemeUc({
      themes: this._themes, themeId, activeThemeId: this._activeThemeId,
    });
    this._themes = res.themes;
    this._gateway.saveThemes(res.themes);
    if (res.activeThemeId !== this._activeThemeId) {
      this._setActiveThemeId(res.activeThemeId);
    }
    this.render();
    return res;
  }

  // ---- ダイアログ入口（メニューのコールバック先・§6.2）------------------------
  //   参照実装 chart_template_controller.js:308-333 と同一の形。未注入時は no-op。

  // UC-C01 作成・保存。保存は**適用ではない**（§5.1 後条件）ため applyTheme は呼ばない。
  openCreateDialog() {
    if (!this._dialogs || typeof this._dialogs.openEdit !== 'function') {
      return;
    }
    this._dialogs.openEdit({
      findExisting: (name) => this.findThemeByName(name),
      // F-C1: 失敗は CODE のままダイアログへ返す（文言写像はダイアログ側）。
      onSubmit: ({ name, roleColors, tfModifier }) => this.saveTheme({ name, roleColors, tfModifier }),
    });
  }

  // UC-C03 改名・削除。いずれもチャート上の色は変えない（§5.3）＝描き直すのは一覧だけ。
  openManageDialog() {
    if (!this._dialogs || typeof this._dialogs.openManage !== 'function') {
      return;
    }
    this._dialogs.openManage({
      themes: this._themes,
      onRename: (themeId, name) => this.renameTheme(themeId, name),
      onDelete: (themeId) => this.deleteTheme(themeId),
    });
  }

  // ---- 内部 -----------------------------------------------------------------
  _setActiveThemeId(themeId) {
    this._activeThemeId = themeId ?? null;
    this._persistActive();
  }

  _persistActive() {
    this._gateway.saveActiveTheme({ themeId: this._activeThemeId, lastSeq: this._lastSeq });
  }

  // クロム配信（§5.2 手順 2）。applier 未注入（SSR・単体）は no-op で、指標側の適用は継続する。
  _applyChrome(theme) {
    if (!this._chrome || typeof this._chrome.apply !== 'function') {
      return;
    }
    this._chrome.apply(resolveAllChrome(theme));
  }
}
