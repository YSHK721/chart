// color_themes.js — 指標カラーテーマ（usecase・純関数）。
//
// 設計入力（唯一の仕様源）: .doc/indicator-management-ui/基本設計_指標カラーテーマ.md v0.3.0
//   §4.4（COLOR_THEME エンティティ）、§4.7（tfModifier のクランプ・丸め）、§4.10（themeId 採番）、
//   §4.11（上限値）、§5.1（UC-C01 保存）、§5.3（UC-C03 改名・削除）、
//   §5.7（F-C1 名前不正 / F-C3 未知トークン / F-C6 dangling / F-C9 色として解釈不能）。
//
// 責務（SRP）: テーマ集合の追加／更新／改名／削除、名前検証、roleColors・tfModifier の正規化、
//   themeId 採番、選択中テーマ id の解決。
// 非責務: DOM・Storage・chart・console への一切のアクセス（console も外界への副作用・R-4）。
//   色の**適用**（それは color_resolver / adapter）。
//
// 依存（§7.8 内向きのみ）: domain/color_roles.js（色の語彙）・domain/color_value.js（色の値）・
//   domain/tf_meta.js（時間足台帳）。
//   いずれも domain＝usecase から見て内向きであり、同じ usecase の color_resolver.js も
//   `TF_CODES` を同じ形で import している（color_resolver.js:14）。時間足コードの単一情報源は
//   台帳ただ 1 つなので、許容キー集合を引数で受けると「渡し忘れ＝キー検証が無言で消える」経路が
//   できる。検証を注入で殺せないようにするため、台帳を直接引く。
//   時刻は引数で受ける（Date.now を呼ばない＝決定論性・参照実装 chart_templates.js と同流儀）。
//
// 純粋性の規律: 入力（themes 配列・その要素）を破壊しない。更新は新しい配列・オブジェクトを返す
//   （呼び出し側が確定・永続化する）。副作用は 1 つも持たない（console も含む・R-4）。
//   F-C3（未知トークン）は「無視した」という**事実**を戻り値（`ignoredTokens`）で返し、
//   それを警告として出すかどうかは adapter が決める。F-C6 の `changed` が
//   「usecase は事実を返す／adapter が warn して書き戻す」形になっているのと同じ規律で、
//   報告先（開発者コンソール・収集基盤・無出力）の選択を内側に固定しない。

import { COLOR_ROLES, isColorRole } from '../domain/color_roles.js';
import { normalizeHexColor } from '../domain/color_value.js';
import { TF_CODES } from '../domain/tf_meta.js';

// §4.11 で確定した上限値（テンプレートと同型・E-19）。
export const MAX_THEMES = 50;
export const MAX_NAME_LENGTH = 40;
// roleColors のキー数上限は語彙が閉じていることによる構造的上限（§4.11）。
export const MAX_ROLE_COLORS = COLOR_ROLES.length;

// 検証・保存結果の code 語彙（呼び出し側が UI 文言へ写像する）。参照実装 chart_templates.js と同型。
export const CODE = Object.freeze({
  ok: 'ok',
  empty: 'empty',
  tooLong: 'too_long',
  duplicate: 'duplicate',
  limit: 'limit',
  notFound: 'not_found',
});

// ---------------------------------------------------------------------------
// 名前（§4.4 name・§5.1 処理 1-2・§5.3・F-C1）
// ---------------------------------------------------------------------------

// 正規化名 = trim ＋小文字化（§5.1 処理 2）。同名判定はこの値で行う。
export function normalizeThemeName(name) {
  return String(name ?? '').trim().toLowerCase();
}

// 表示用に採用する名前 = trim のみ（表記は入力のまま・§5.1 処理 2）。
export function displayThemeName(name) {
  return String(name ?? '').trim();
}

// 正規化名が一致する既存テーマを返す（無ければ null）。同名保存の上書き判定（§5.1 処理 2）の
//   単一の判定源。空名（trim 後 0 文字）は一致なし＝名前検証（F-C1）へ委ねる。
export function findThemeByName({ themes = [], name } = {}) {
  const normalized = normalizeThemeName(name);
  if (normalized.length === 0) {
    return null;
  }
  return (themes ?? []).find((t) => t && normalizeThemeName(t.name) === normalized) ?? null;
}

// 名前検証（§4.4: 1〜40 文字・前後空白は trim／§5.3: 正規化名の重複不可）。
//   excludeThemeId: 改名・上書き保存時に「自分自身との一致」を重複から除外する（§5.3）。
export function validateThemeName(name, { themes = [], excludeThemeId = null } = {}) {
  const display = displayThemeName(name);
  if (display.length === 0) {
    return { ok: false, code: CODE.empty };
  }
  if (display.length > MAX_NAME_LENGTH) {
    return { ok: false, code: CODE.tooLong };
  }
  const normalized = normalizeThemeName(name);
  const clash = (themes ?? []).some(
    (t) => t && t.themeId !== excludeThemeId && normalizeThemeName(t.name) === normalized,
  );
  if (clash) {
    return { ok: false, code: CODE.duplicate };
  }
  return { ok: true, code: CODE.ok };
}

// ---------------------------------------------------------------------------
// roleColors の正規化（§4.4 値は #rrggbb 小文字・§5.1 処理 3・F-C3・F-C9）
// ---------------------------------------------------------------------------

// 色の値そのものの正規化は domain/color_value.js が単一情報源（受理集合は既存 toHex
//   ＝property_control_builders.js:35-49 と同一・§7.7 前提条件 4）。本モジュールが持つのは
//   「テーマのトークン値として、解釈不能な色をどう扱うか」という方針だけで、値の直し方は持たない。
export function normalizeRoleColor(value) {
  return normalizeHexColor(value);
}

// roleColors（{ [token]: 色 }）を保存形へ正規化する。
//   - キーが語彙外（F-C3）: 当該キーを無視し、無視したキーを `ignoredTokens` で**報告する**
//     （警告として出すかは呼び出し側＝adapter の決定。本モジュールは console を持たない・R-4）。
//   - 値が色として解釈不能（F-C9）: 当該トークンを未宣言として落とす（既定へ降格させる）。
//     これは語彙内の事象であり `ignoredTokens` には載せない（F-C3 とは別事由・警告対象でもない）。
//   入力は破壊しない。0 件は恒等テーマ（§4.4）として正当。
//
// @returns {{roleColors: Object<string,string>, ignoredTokens: string[]}}
//   `ignoredTokens` は入力の列挙順。1 呼び出しにつき 1 本の一覧なので、呼び出し側が素直に
//   書くと警告は 1 回になる（キー数ぶん増えない＝F-C3 の「1 回」を構造で保つ）。
export function normalizeRoleColors(roleColors) {
  const out = {};
  const ignoredTokens = [];
  if (!roleColors || typeof roleColors !== 'object' || Array.isArray(roleColors)) {
    return { roleColors: out, ignoredTokens };
  }
  for (const [token, value] of Object.entries(roleColors)) {
    if (!isColorRole(token)) {
      ignoredTokens.push(token);
      continue;
    }
    const hex = normalizeRoleColor(value);
    if (hex !== null) {
      out[token] = hex;
    }
  }
  return { roleColors: out, ignoredTokens };
}

// ---------------------------------------------------------------------------
// tfModifier の正規化（§4.4 l ∈ [-1,1]・小数第 3 位／§4.7 クランプと丸め）
// ---------------------------------------------------------------------------

// 保存値を「適用時に §4.7 が算出する値」と一致させる（クランプ・丸めを保存時に済ませる）。
//   同一の式を使うため、正規化の有無で描画結果は変わらない（保存形が §4.4 の値域を満たすだけ）。
function clampRoundL(raw) {
  const clamped = Math.min(1, Math.max(-1, raw));
  return Math.floor(clamped * 1000 + 0.5) / 1000;
}

// 許容キー集合は台帳（domain/tf_meta.TF_CODES）ただ 1 つ。呼び出し側は差し替えられない
//   （差し替え可能にすると「渡さない＝検証しない」という無言の抜け道が生まれる）。
const TF_SET = new Set(TF_CODES);

// tfModifier（{ [timeframe]: l }）を保存形へ正規化する。
//   - キーが台帳（TF_CODES）に無い時間足は落とす（§4.4 キーは TF_CODES のみ）。
//   - 値が number（有限）でないキーは落とす。数値は [-1,1] へクランプし小数第 3 位へ丸める。
//   - null / 非オブジェクトは null（§4.4 の「null 可」）。入力は破壊しない。
export function normalizeTfModifier(tfModifier) {
  if (!tfModifier || typeof tfModifier !== 'object' || Array.isArray(tfModifier)) {
    return null;
  }
  const out = {};
  for (const [tf, value] of Object.entries(tfModifier)) {
    if (!TF_SET.has(tf)) {
      continue;
    }
    if (typeof value !== 'number' || !Number.isFinite(value)) {
      continue;
    }
    out[tf] = clampRoundL(value);
  }
  return out;
}

// ---------------------------------------------------------------------------
// 同梱プリセット（§9 T-1 の確定）
// ---------------------------------------------------------------------------

// 「基本」テーマ。目的 2（カラーに意味をもたせる）の解き方を 1 つ具体化したもの。
//
// 眼目は **警戒（alert）を赤から外すこと**。実測（E-26・E-27）では緑／赤が「方向・強度・勝敗」の
//   3 意味を同時に担い、`#d2433a` が「抵抗帯（弱気）」と「外れ値」の 2 意味を持っていた。ここを
//   分離しない限り、テーマを作れるようにしただけでは色から意味を復元できるようにならない。
//
// 配色の方針（有彩色を 6 系統に抑える＝目的 1 の認知負荷軽減）:
//   - 方向        … ローソクを含めて明るい teal / red へ引き上げる（依頼者指示 2026-08-09）。
//                    色相は現行を保つ（teal 対 red）。teal は赤との弁別が緑よりも保たれるため、
//                    §3.2 N-8 で自動適合を持たない本機能では、既定として不利にならない選択を採る。
//                    彩度・明度だけを上げるので「方向」という意味は変えずに、地との分離が上がる。
//   - 警戒        … 琥珀。方向（赤）と独立した意味であることを色相で示す（上方向の外れ値もある）。
//   - 指標の中身  … 寒色系（青・紫・シアン）でまとめ、方向・警戒と体温を変える。
//   - 補助の線    … 無彩色の明度差だけで「読ませる（level）／読ませない（muted）」を分ける。
//   - 地          … 現行のまま。見慣れた地の上で、図だけが意味を持つようにする。
//   - 現在地      … 画面で最も明るい無彩色。「今」を有彩色の意味群と衝突させない。
//
// 時刻は 0 固定（プリセットは実行時に生成されるのではなく定義そのもの＝決定論。`Date.now()` を
//   モジュール評価時に呼ぶと、同じビルドが起動ごとに違う値を持つ）。
const BASIC_PRESET = Object.freeze({
  themeId: 'thm#0',
  name: '基本',
  roleColors: Object.freeze({
    bullish: '#00bfa5',
    bearish: '#ff5252',
    alert: '#ffa726',
    neutral: '#90a4ae',
    primary: '#42a5f5',
    secondary: '#7e57c2',
    range: '#26c6da',
    level: '#78909c',
    muted: '#546e7a',
    surface: '#131722',
    grid: '#1f2530',
    border: '#2a2e39',
    text: '#d1d4dc',
    highlight: '#f5f5f5',
  }),
  tfModifier: null,
  createdAt: 0,
  updatedAt: 0,
});

export const PRESET_THEMES = Object.freeze([BASIC_PRESET]);

// プリセットの id 集合。採番（§4.10）は `lastSeq + 1` から始まるため、プリセットは採番域の外
//   （`thm#0`）に置く＝ユーザーのテーマと id が衝突しない。
export const PRESET_THEME_IDS = Object.freeze(PRESET_THEMES.map((t) => t.themeId));

const PRESET_ID_SET = new Set(PRESET_THEME_IDS);

// 当該 id が同梱プリセットのものか（全域的・未知値でも例外を投げない）。
export function isPresetThemeId(themeId) {
  return typeof themeId === 'string' && PRESET_ID_SET.has(themeId);
}

// 永続層のテーマ集合へプリセットを**合成**する（集合の初期値として書き込まない）。
//
//   書き込み方式を採らない理由（§5.3 の削除・改名が意味を失うため）:
//     - 集合へ初期値として書くと、ユーザーが削除しても次回起動で復活する。
//     - 同名で保存し直すと上書き確認が走り、プリセットの席が奪われる／二重に増える。
//   合成方式なら、同じ `themeId` を持つ永続値が在ればそちらが勝ち（改名・色の変更を尊重）、
//   削除の記録（`removedPresetIds`）が在れば復活させない。ユーザーの操作を無効化しない。
//
// @param {Array} themes 永続層のテーマ集合（原形）。
// @param {{removedPresetIds?: string[]}} [opts] 削除済みプリセット id（永続層が持つ）。
// @returns {Array} プリセット（先頭）＋ ユーザーのテーマ。入力は破壊しない。
export function withPresets(themes = [], { removedPresetIds = [] } = {}) {
  const mine = adoptThemes(Array.isArray(themes) ? themes : []);
  const mineIds = new Set(mine.map((t) => t.themeId));
  const removed = new Set(Array.isArray(removedPresetIds) ? removedPresetIds : []);
  const presets = PRESET_THEMES.filter(
    (p) => !mineIds.has(p.themeId) && !removed.has(p.themeId),
  );
  return [...presets, ...mine];
}

// ---------------------------------------------------------------------------
// 読み出し境界: 消費のための射影（§4.4・§4.9 前方互換・§5.3）
// ---------------------------------------------------------------------------

// 永続層のテーマ集合から「テーマとして成立しない要素」（null・非オブジェクト・配列）だけを落とす。
//   成立する要素の**中身は原形のまま**返す（§4.9: 未知のトークンキーは温存する）。入力は破壊しない。
export function adoptThemes(themes = []) {
  return (themes ?? []).filter((t) => t && typeof t === 'object' && !Array.isArray(t));
}

// テーマ 1 件を「消費のための形」へ射影する（読み取り専用の写像・戻り値を永続化しない）。
//
//   `themes.v1` は外から書き換わりうる（旧版・手編集・他端末）ため、保存経路（saveTheme）を
//   通っていない値が入りうる。射影を通さないと「roleColors の値は正規化済み hex6 のみ」という
//   前提が消費者ごとに崩れ、判定の細部（`== null` か `isHex6` か）で結果が食い違う
//   （実測: 水準線経路が既定色 `#2962ff` を捏造して全水準線が青一色になった）。消費者ごとに
//   判定を足すのは取り残しを増やすだけなので、**消費の入口で形を揃える**。
//
//   ここが**射影**であって書き換えでないことが要（§4.9・§5.3）: 永続値を書き換えると、
//   解釈できないだけの領域（未知トークン）が読み込んだ時点で消え、改名しただけで失われる。
//   温存と無視は両立する — 保存値は原形、消費値は語彙内の解釈可能な宣言だけ。
//   規則は保存時（saveTheme）と同一関数を使う＝保存形と消費形が定義上ずれない。
//
// @returns {{theme: ?object, ignoredTokens: string[]}} `ignoredTokens` は F-C3 の報告（警告は adapter）。
export function projectThemeForUse(theme) {
  if (!theme || typeof theme !== 'object' || Array.isArray(theme)) {
    return { theme: null, ignoredTokens: [] };
  }
  const { roleColors, ignoredTokens } = normalizeRoleColors(theme.roleColors);
  return {
    theme: { ...theme, roleColors, tfModifier: normalizeTfModifier(theme.tfModifier) },
    ignoredTokens,
  };
}

// テーマ集合が持つ未知トークン（§5.7 F-C3）の一覧。**報告のためだけ**の純関数で、値は
//   1 つも書き換えない。読み出し時に 1 度だけ数え上げることで、警告の回数を「読み出し 1 回に
//   つき 1 本」に保つ（消費のたびに警告すると系列数ぶん増える）。
export function unknownRoleTokens(themes = []) {
  const out = [];
  for (const t of adoptThemes(themes)) {
    out.push(...projectThemeForUse(t).ignoredTokens);
  }
  return out;
}

// ---------------------------------------------------------------------------
// themeId 採番（§4.10）
// ---------------------------------------------------------------------------

// 次の themeId を発行する。形式 `thm#{seq}`・seq は lastSeq + 1（§4.10）。
//   発行と同時に永続化する（activeTheme.v1 と単一原子で書く）のは呼び出し側の責務。
export function nextThemeId(lastSeq) {
  const seq = (Number.isInteger(lastSeq) ? lastSeq : 0) + 1;
  return { themeId: `thm#${seq}`, lastSeq: seq };
}

// activeTheme.v1 破損時の復旧（§4.10）: 初期化後の lastSeq を themes 内の既存 `thm#N` の
//   最大 N 以上へ引き上げる（id の再利用・衝突を避ける）。lastSeq は減算しない。
export function recoverLastSeq(lastSeq, themes = []) {
  let max = Number.isInteger(lastSeq) ? lastSeq : 0;
  for (const t of themes ?? []) {
    const m = /^thm#(\d+)$/.exec(t && t.themeId ? String(t.themeId) : '');
    if (m) {
      const n = Number(m[1]);
      if (Number.isInteger(n) && n > max) {
        max = n;
      }
    }
  }
  return max;
}

// ---------------------------------------------------------------------------
// UC-C01 テーマ作成・保存（§5.1）
// ---------------------------------------------------------------------------

// themeId が一致する 1 件だけを差し替えた新しい配列を返す（入力は破壊しない）。
//   保存（上書き）と改名の双方が同じ差し替えを行うため、写像はここ 1 箇所に置く。
function replaceTheme(themes, themeId, updated) {
  return themes.map((t) => (t && t.themeId === themeId ? updated : t));
}

// テーマを保存する。§5.1 の処理順（1 名前検証 → 2 同名判定と新規採番／上書き → 3 色の正規化）に従う。
//   - 正規化名が既存と一致 → その既存テーマを上書き更新（themeId 保持・name は入力の表記・
//     roleColors / tfModifier を置換・createdAt 不変・updatedAt 更新）。件数上限に達していても可。
//   - 一致しない → 新規採番して追加（上限 50 件で拒否＝F-C1）。
//   保存は**適用ではない**（§5.1 後条件: チャート上の色は変化しない）。永続化は呼び出し側。
//   戻り値の `ignoredTokens` は F-C3 で無視した未知トークン（§5.1 の処理順どおり、名前検証で
//   中止した呼び出しは色を見ていないので必ず空）。警告を出すのは呼び出し側＝adapter（R-4）。
export function saveTheme({
  themes = [], lastSeq = 0, name, roleColors = {}, tfModifier = null, now = 0, themeId = null,
} = {}) {
  const list = themes ?? [];
  // 更新対象の決め方は 2 通りで、**id が優先**する。
  //   (a) themeId 指定（編集ダイアログから開いた既存テーマ）… 名前を変えても同じ席を直す。
  //       これが無いと、名前を変えた瞬間に「別テーマの新規作成」になり、元のテーマが残って
  //       増殖する（＝一度確定したテーマを直せない状態が別の形で再現する）。
  //   (b) 正規化名の一致（§5.1 処理 2 の上書き）… 新規作成の導線から同名を保存した場合。
  const byId = typeof themeId === 'string'
    ? list.find((t) => t && t.themeId === themeId) ?? null : null;
  const existing = byId ?? findThemeByName({ themes: list, name });
  // 同名は「上書き」であって検証エラーではないため、自分自身を除外して検証する（§5.1 処理 2）。
  const verdict = validateThemeName(name, {
    themes: list,
    excludeThemeId: existing ? existing.themeId : null,
  });
  if (!verdict.ok) {
    // §5.1 処理順 1 で中止＝色を見ていない。無視したトークンは存在しない。
    return {
      ok: false, code: verdict.code, themes: list, lastSeq, themeId: null, ignoredTokens: [],
    };
  }
  const { roleColors: colors, ignoredTokens } = normalizeRoleColors(roleColors);
  const modifier = normalizeTfModifier(tfModifier);
  if (existing) {
    const updated = {
      ...existing,
      name: displayThemeName(name),
      roleColors: colors,
      tfModifier: modifier,
      updatedAt: now,
    };
    return {
      ok: true,
      code: CODE.ok,
      themes: replaceTheme(list, existing.themeId, updated),
      lastSeq,
      themeId: existing.themeId,
      ignoredTokens,
    };
  }
  if (list.length >= MAX_THEMES) {
    return {
      ok: false, code: CODE.limit, themes: list, lastSeq, themeId: null, ignoredTokens,
    };
  }
  const issued = nextThemeId(lastSeq);
  const created = {
    themeId: issued.themeId,
    name: displayThemeName(name),
    roleColors: colors,
    tfModifier: modifier,
    createdAt: now,
    updatedAt: now,
  };
  return {
    ok: true,
    code: CODE.ok,
    themes: [...list, created],
    lastSeq: issued.lastSeq,
    themeId: issued.themeId,
    ignoredTokens,
  };
}

// ---------------------------------------------------------------------------
// UC-C03 改名・削除（§5.3）と dangling activeThemeId（F-C6）
// ---------------------------------------------------------------------------

// 改名: name を検証（1〜40 文字・他テーマとの正規化名重複不可。自身の現在名と同一正規化名への
//   変更は許容）して更新し updatedAt を進める。themeId・roleColors・tfModifier・createdAt は不変。
//   チャート上の色は変化しない（§5.3）。
export function renameTheme({ themes = [], themeId, name, now = 0 } = {}) {
  const list = themes ?? [];
  const target = list.find((t) => t && t.themeId === themeId);
  if (!target) {
    return { ok: false, code: CODE.notFound, themes: list };
  }
  const verdict = validateThemeName(name, { themes: list, excludeThemeId: themeId });
  if (!verdict.ok) {
    return { ok: false, code: verdict.code, themes: list };
  }
  const updated = { ...target, name: displayThemeName(name), updatedAt: now };
  return {
    ok: true,
    code: CODE.ok,
    themes: replaceTheme(list, themeId, updated),
  };
}

// 削除: 当該テーマを除去し、activeThemeId が当該 id なら null にする（§5.3）。
//   lastSeq は減算・削除しない（§4.10 id の再利用禁止）ため本関数は lastSeq を扱わない。
//   チャート上の色は変更しない（次の描画から既定で解決される・§5.3 の非対称性）。
export function deleteTheme({ themes = [], themeId, activeThemeId = null } = {}) {
  const list = themes ?? [];
  return {
    themes: list.filter((t) => !t || t.themeId !== themeId),
    activeThemeId: activeThemeId === themeId ? null : activeThemeId,
  };
}

// F-C6 dangling activeThemeId: 参照先テーマが不在なら「テーマ未選択」として解決する。
//   changed === true のとき、呼び出し側が activeTheme.v1 を書き戻す（遅延クリーンアップ）。
export function resolveActiveThemeId({ themes = [], activeThemeId = null } = {}) {
  if (activeThemeId === null || activeThemeId === undefined) {
    return { activeThemeId: null, changed: false };
  }
  const exists = (themes ?? []).some((t) => t && t.themeId === activeThemeId);
  if (exists) {
    return { activeThemeId, changed: false };
  }
  return { activeThemeId: null, changed: true };
}
