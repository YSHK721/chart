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
  withPresets,
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
 * @param {object} gateway ThemeStorePort（loadThemes / saveThemes / loadActiveTheme /
 *   saveActiveTheme / loadRemovedPresetIds / saveRemovedPresetIds）。
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
  // 選択中テーマの解決は**プリセットを含む集合**に対して行う（§9 T-1）。永続層だけで判定すると、
  //   プリセットを選択中のときに「参照先が不在」と誤判定して F-C6 で縮退し、起動のたびに
  //   テーマ未選択へ戻る（選択が保持されない）。
  const visible = withPresets(themes, { removedPresetIds: gateway.loadRemovedPresetIds() });
  const resolved = resolveActiveThemeId({ themes: visible, activeThemeId: active.themeId });
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
    theme: projectThemeForUse(themeById(visible, resolved.activeThemeId)).theme,
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
    // ライブプレビュー中の下書き（null＝プレビューしていない）。**保存されない状態**であり、
    //   永続層にも選択中テーマ id にも影響しない。復元用の控えではないことに注意
    //   （控えではなく「今どう見せているか」そのもの。解除は元のテーマで塗り直すだけ）。
    this._previewTheme = null;
  }

  // ---- 参照面 ---------------------------------------------------------------
  // 同梱プリセット（§9 T-1）は**参照面で合成**する。永続層（this._themes）には混ぜない。
  //   混ぜるとこの集合がそのまま saveThemes の入力になり、プリセットが themes.v1 へ実体化して
  //   「書き込まない」という設計が崩れる（削除しても次回起動で復活する状態へ逆戻りする）。
  //   同じ themeId の永続値が在ればそちらが勝ち（編集・改名を尊重）、削除の記録が在れば出さない。
  themes() {
    return withPresets(this._themes, { removedPresetIds: this._removedPresetIds() });
  }

  // 削除済みプリセットの記録（永続層が持つ）。一覧の合成と、書き込み 3 経路の解決に同じ値を渡す
  //   （出所を 1 つにする＝「一覧には出ないのに保存では解決される」という食い違いを作らない）。
  //   ThemeStorePort の一員として**必ず在る**ものとして呼ぶ。`typeof … === 'function'` の在席
  //   ガードを置くと、実装し忘れた port を渡しても無言で「削除記録なし」に倒れ、削除したはずの
  //   プリセットが復活する（無言の死）。契約違反は例外として表に出す。
  _removedPresetIds() {
    return this._gateway.loadRemovedPresetIds();
  }

  activeThemeId() { return this._activeThemeId; }

  // 選択中テーマ（未選択は null）。IndicatorController の colorThemeProvider とクロム配信の値源。
  //   返すのは**消費のための射影**（§4.4 の形）で、保持している永続値は原形のまま（§4.9）。
  //   色を消費する経路をここ 1 本にすることで、解釈不能値の判定を消費者ごとに書かずに済む。
  //   探索は合成後の集合に対して行う（プリセットを選択中でもその実体が引ける）。
  //   プレビュー中は下書きが勝つ。値源をここ 1 本に保つことで、指標側（colorThemeProvider の
  //   pull）もクロム側も、追加の配線なしに下書きへ追随する。
  activeTheme() {
    return this._previewTheme
      ?? projectThemeForUse(themeById(this.themes(), this._activeThemeId)).theme;
  }

  // 正規化名が一致する既存テーマを返す（無ければ null）。保存時の上書き確認の判定源。
  //   判定は usecase の純関数のみが行い、ダイアログは結果を受け取るだけ（DIP・判定源の一本化）。
  findThemeByName(name) {
    return findThemeByNameUc({ themes: this.themes(), name });
  }

  // メニュー再描画用のビューモデル（適用・保存・改名・削除の後の push と、開くたびの pull で共用）。
  viewModel() {
    return { themes: this.themes(), activeThemeId: this._activeThemeId };
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
    // 解決は**合成後の集合**（＝一覧に見える集合）に対して行う。永続層だけで判定すると、同梱
    //   プリセット（§9 T-1）は「不在」と誤判定されて F-C6 で縮退し、メニューに出ているのに
    //   押しても既定色のままになる（無言の死）。loadThemeState の解決規則と同一（対称）。
    const resolved = resolveActiveThemeId({ themes: this.themes(), activeThemeId: themeId ?? null });
    if (resolved.changed) {
      warn(`[color-theme] 適用対象のテーマが不在: ${themeId}（テーマ未選択として解決）`);
    }
    const id = resolved.activeThemeId;
    // 手順 1: 選択中テーマを確定して永続化する。
    this._setActiveThemeId(id);
    // 手順 2〜4: 塗り直し。プレビューと**同じ 1 本**を通る（色の書き手を増やさない）。
    this._repaint(this.activeTheme());
    // 手順 5: メニューの選択状態（is-active）を現在値へ追随させる（未注入時は no-op）。
    this.render();
    return id;
  }

  /**
   * 保存前の下書きをチャート上へ反映する（ライブプレビュー・段階 5-C-3）。
   *
   * `draft = null` でプレビューを解除し、元のテーマ（選択中テーマ／テーマなし）へ戻す。
   *
   * **保存ではない**: 永続層へ 1 バイトも書かず、選択中テーマ id も動かさない。
   * **ビューへの介入でもない**（§3.4）: 再計算・時間足・可視範囲・価格スケール・スクロールの
   *   いずれにも到達しない。色は既存系列とチャート全体のオプションだけで完結する。
   *
   * 復元用のスナップショットを**持たない**理由（3 点とも既存コードで確認できる）:
   *   1. 系列色は毎回 `baseColor`（生成時に 1 度だけ書かれる不変フィールド）から作り直される。
   *   2. `resolveAllChrome` は 20 slot を**全数**返す（＝全上書き＝可逆）。
   *   3. クロム出力は「保持色 × 表示モード」からの導出 1 本（ISSUE-356 の是正で確立）。
   * よって解除は「元のテーマで塗り直す」だけで元に戻る。控えを持つと真の状態が 2 つに割れ、
   * ISSUE-356 と同型の食い違いが再発する。
   *
   * @param {?{roleColors?: Object, tfModifier?: ?Object}} draft 下書き（null で解除）。
   */
  previewTheme(draft) {
    // 下書きも**消費のための射影**を通す（§4.4 の形へ正規化し、導出を埋める）。保存済みテーマと
    //   同じ経路にすることで、プレビューで見えた色と保存後の色が構成上一致する。
    this._previewTheme = draft ? projectThemeForUse({
      roleColors: draft.roleColors, tfModifier: draft.tfModifier ?? null,
    }).theme : null;
    this._repaint(this.activeTheme());
  }

  // 色を書く手順（§5.2 手順 2〜4）。**適用とプレビューが共有する唯一の経路**。
  //   ここを 2 本に増やすと経路ごとに結果が食い違う（ISSUE-356 の 3 症状はすべてこれが原因で、
  //   単体の状態を見るテストはすべて緑だった）。
  _repaint(theme) {
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
  }

  // ---- UC-C01 保存（§5.1）----------------------------------------------------
  //   保存は**適用ではない**（後条件: チャート上の色は変化しない）。失敗は CODE をそのまま返し、
  //   例外は投げない（呼び出し側のダイアログがインライン表示へ写像する）。
  //   themeId を伴う保存は「その席の更新」（編集ダイアログから開いた既存テーマ）。名前を変えても
  //   同じテーマを直したことになる（無いと改名のたびに新規テーマが増える）。
  saveTheme({
    name, roleColors = {}, tfModifier = null, themeId = null,
  } = {}) {
    const res = saveThemeUc({
      themes: this._themes,
      lastSeq: this._lastSeq,
      name,
      roleColors,
      tfModifier,
      themeId,
      now: this._now(),
      // 解決集合を一覧（themes()）と揃えるために渡す。渡さないと同梱プリセットが不在扱いになり、
      //   同名保存が新規採番になって一覧に同名が 2 件並ぶ（§9 T-1・実測）。
      removedPresetIds: this._removedPresetIds(),
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
  //   改名: チャート上の色は変化しない。プリセットが対象なら、同じ themeId のまま永続層へ
  //   実体化して名前を更新する（新規採番しない＝一覧が二重にならない）。
  renameTheme(themeId, name) {
    const res = renameThemeUc({
      themes: this._themes,
      themeId,
      name,
      now: this._now(),
      removedPresetIds: this._removedPresetIds(),
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
    const removedBefore = this._removedPresetIds();
    const res = deleteThemeUc({
      themes: this._themes,
      themeId,
      activeThemeId: this._activeThemeId,
      removedPresetIds: removedBefore,
    });
    this._themes = res.themes;
    this._gateway.saveThemes(res.themes);
    // 同梱プリセットは定義（コード）側に在るため、行を消しても次回起動で合成し直されて復活する。
    //   削除を持続させるのは削除の記録だけ。「記録が要るか」は usecase が決め（判定を 2 箇所に
    //   置かない）、変化が無ければ入力と同一参照が返るので、無用な書き込みも起きない。
    if (res.removedPresetIds !== removedBefore) {
      this._gateway.saveRemovedPresetIds(res.removedPresetIds);
    }
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
      // 保存前の下書きをチャートへ映す（段階 5-C-3）。閉じるときに null で解除される。
      onPreview: (draft) => this.previewTheme(draft),
    });
  }

  // 保存済みテーマの**編集**（依頼者指示 2026-08-09）。既存の名前・宣言済みトークンを初期値として
  //   開き、1 色だけ直して保存できるようにする。themeId を保存要求へ載せるため、名前を変えても
  //   同じ席が更新される（新規テーマが増えない）。未知 id は no-op（F-C6 と同型の縮退）。
  openEditDialog(themeId) {
    if (!this._dialogs || typeof this._dialogs.openEdit !== 'function') {
      return;
    }
    const theme = themeById(this.themes(), themeId);
    if (!theme) {
      return;
    }
    this._dialogs.openEdit({
      title: 'テーマを編集',
      theme,
      // 同名判定からは自分自身を除く（自分と同じ名前のままの保存を「上書き確認」にしない）。
      findExisting: (name) => {
        const hit = this.findThemeByName(name);
        return hit && hit.themeId === theme.themeId ? null : hit;
      },
      onSubmit: (payload) => this.saveTheme(payload),
      onPreview: (draft) => this.previewTheme(draft),
    });
  }

  // UC-C03 改名・削除。いずれもチャート上の色は変えない（§5.3）＝描き直すのは一覧だけ。
  openManageDialog() {
    if (!this._dialogs || typeof this._dialogs.openManage !== 'function') {
      return;
    }
    this._dialogs.openManage({
      themes: this.themes(),
      onRename: (themeId, name) => this.renameTheme(themeId, name),
      onDelete: (themeId) => this.deleteTheme(themeId),
      // 保存済みテーマの色を直す唯一の導線（依頼者指示 2026-08-09）。管理ダイアログを閉じて
      //   編集ダイアログを既存値つきで開く（同時に 2 枚開かない＝_openShell の後勝ちに従う）。
      onEdit: (themeId) => this.openEditDialog(themeId),
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
