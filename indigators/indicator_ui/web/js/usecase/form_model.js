// フォームモデル構築（usecase/form_model.js）。
//
// IndicatorDef.params（catalog の ParamDef[]）からプロパティダイアログのフォーム項目を
// 構築し、条件付き有効化・検証委譲・既定復元を提供する純関数群。
// DOM/chart/fetch/localStorage 非依存（母体「純ロジック分離」方針・§10.4）。
//
// 検証単一定義（C-3）: validateForm は domain constraint_eval.js の evaluate へ委譲する。
// range_from<range_to の「両者非 null 時のみ」前提付き検証のみ本モジュールで最小実装する
//   （素の LT は両者 null 時に null<null=false で誤検出するため。設計 §11.2 Q-3）。
// ConstraintEvaluator のロジックは一切変更しない。

import { evaluate, ParamType } from '../domain/constraint_eval.js';

// ParamType → デフォルトコントロール種別の写像（§3.1）。
// control_type 明示があれば優先（buildFormModel 内で上書き）。
const CONTROL_BY_TYPE = Object.freeze({
  [ParamType.INT]: 'number',
  [ParamType.FLOAT]: 'number',
  [ParamType.ENUM]: 'select',
  [ParamType.BOOL]: 'checkbox',
  [ParamType.STRING]: 'text',
  [ParamType.COLOR]: 'color',
  [ParamType.FLOAT_LIST]: 'list',
  [ParamType.ENUM_LIST]: 'multiselect',
});

// ParamDef からコントロール種別を一意に決定（明示 controlType 優先→期間フラグ→ParamType 写像）。
//   isPeriod（基本設計_期間プリセット.md §5.1）が真なら 'period'（期間入力＋プリセット）を既定とする。
//   明示 controlType は従来どおり最優先（期間パラメータでも個別に別コントロールへ倒せる）。
function resolveControlType(pdef) {
  if (pdef.controlType !== null && pdef.controlType !== undefined) {
    return pdef.controlType;
  }
  if (pdef.isPeriod === true) {
    return 'period';
  }
  return CONTROL_BY_TYPE[pdef.type] ?? 'text';
}

// ParamDef → FieldDesc の任意メタデータ既定値（省略キーのフォールバックを 1 箇所に集約）。
const FIELD_META_DEFAULTS = Object.freeze({
  enumValues: null,
  step: null,
  min: null,
  max: null,
  unit: null,
  tooltip: null,
  group: null,
  order: null,
  conditionalEnable: null,
  // conditionalVisible: 条件付き“表示”（トグル）。conditionalEnable（グレーアウト）と対称。
  //   { when: { param, equals } } が偽のとき当該フィールド行を非表示にする（§3.5 拡張）。
  conditionalVisible: null,
  // enumLabels: enum 値 → 表示名マップ（properties_dialog の select 日本語表示）。
  enumLabels: null,
  // optionEnable: ENUM の option 単位の有効述語 (value, values, ctx)->bool（ISSUE-080）。
  //   偽の option は select 上で disabled（灰色・選択不可）。行全体の conditionalEnable と直交。
  optionEnable: null,
  // isPeriod: 期間フラグ（基本設計_期間プリセット.md §5.1）。FieldDesc へ透過し、
  //   adapter（buildPeriod）が min/max とともにプリセット提示の判定に使う。
  isPeriod: false,
});

// pdef の任意メタデータを FIELD_META_DEFAULTS のキーで既定フォールバック付きに解決する。
function pickFieldMeta(pdef) {
  const out = {};
  for (const key of Object.keys(FIELD_META_DEFAULTS)) {
    out[key] = pdef[key] ?? FIELD_META_DEFAULTS[key];
  }
  return out;
}

// 1 ParamDef を FieldDesc へ変換（初期値は currentParams 優先→default フォールバック）。
//   ENUM は enumValues に無い保存値（撤去済み選択肢：ISSUE-082 の mode='replay' 等）を default へ
//   フォールバックする（アクティブ表示無しのセグメント/選択不能値をダイアログへ持ち込まない）。
function paramToField(pdef, currentParams) {
  const hasCurrent = Object.prototype.hasOwnProperty.call(currentParams, pdef.name);
  let value = hasCurrent ? currentParams[pdef.name] : pdef.default;
  if (Array.isArray(pdef.enumValues) && pdef.enumValues.length > 0 && !pdef.enumValues.includes(value)) {
    value = pdef.default;
  }
  return {
    name: pdef.name,
    // label 直接指定（日本語）優先 → labelKey → 既定 label.<name>。
    label: pdef.label ?? pdef.labelKey ?? `label.${pdef.name}`,
    controlType: resolveControlType(pdef),
    value,
    default: pdef.default,
    constraints: pdef.constraints ?? [],
    ...pickFieldMeta(pdef),
  };
}

// IndicatorDef.params → フォームモデル。
// 戻り値: { fields: FieldDesc[], groups: [{ key, fields: FieldDesc[] }] }。
// - uiVisible===false の param は除外（§3.3.1）。
// - グループは初出順、グループ内は order 昇順（order 無しは元配列順を保持・安定ソート）。
// - group=null は先頭の無見出しグループ（参照 UI「基本」群相当・§3.4）。
export function buildFormModel(def, currentParams = {}) {
  const fields = (def.params ?? [])
    .filter((p) => p.uiVisible !== false)
    .map((p) => paramToField(p, currentParams));

  // グループ初出順を保持しつつ集約。
  const groupOrder = [];
  const byGroup = new Map();
  for (const field of fields) {
    const key = field.group ?? null;
    if (!byGroup.has(key)) {
      byGroup.set(key, []);
      groupOrder.push(key);
    }
    byGroup.get(key).push(field);
  }

  const groups = groupOrder.map((key) => ({
    key,
    fields: sortByOrder(byGroup.get(key)),
  }));

  return { fields, groups };
}

// order 昇順の安定ソート（order 無し=元順を維持）。
// 元インデックスを退避して order 同値・null 同士は元順を保つ（安定性を明示）。
function sortByOrder(fields) {
  const indexed = fields.map((field, originalIndex) => ({ field, originalIndex }));
  indexed.sort((a, b) => {
    const orderA = a.field.order;
    const orderB = b.field.order;
    if (orderA === null && orderB === null) return a.originalIndex - b.originalIndex;
    if (orderA === null) return 1;
    if (orderB === null) return -1;
    if (orderA !== orderB) return orderA - orderB;
    return a.originalIndex - b.originalIndex;
  });
  return indexed.map((entry) => entry.field);
}

// 各パラメータの有効/無効を決定（§3.5 条件付き有効化）。
// conditionalEnable が偽のとき disabled。未指定は常時 enabled。2 形式を受ける:
//   - オブジェクト { when: {param, equals} }: 他 param 値との単一等価（従来）。
//   - 関数 (values, context) => boolean: 複合述語。context は外部状態（例 timeframe）を渡す
//     （ISSUE-070: mode=sessions×対応tf で tf-period が列を描くとき解像度をグレーアウト）。
// 実コード対応: atr_period は normalize=="atr" のときのみ ATR 計算で使用（robust_bands.py:135-138）。
export function computeEnabled(def, values = {}, context = {}) {
  const enabled = {};
  for (const pdef of def.params ?? []) {
    const cond = pdef.conditionalEnable;
    if (cond === null || cond === undefined) {
      enabled[pdef.name] = true;
      continue;
    }
    if (typeof cond === 'function') {
      enabled[pdef.name] = !!cond(values, context);
      continue;
    }
    const { param, equals } = cond.when;
    enabled[pdef.name] = values[param] === equals;
  }
  return enabled;
}

// 各パラメータの表示/非表示を決定（§3.5 条件付き表示・computeEnabled と対称）。
// conditionalVisible.when（{param,equals}）が偽のとき非表示（hidden）。未指定は常時 visible。
// 静的除外（uiVisible===false）は buildFormModel が担い、本関数は動的トグルを担う（併存）。
// 用途: market_profile の bins は resmode==bins のとき表示 / range は resmode==range のとき表示（解像度トグル）。
export function computeVisible(def, values = {}, context = null) {
  const visible = {};
  const variant = context && context.variant;
  for (const pdef of def.params ?? []) {
    // variant スコープ（ISSUE-278 #8）: その variant の add_* が受理しない param は表示しない。
    //   受理集合の正は back（GET /catalog の paramScopes）で、catalog.applyServerParamScopes が
    //   ParamDef.variants へ overlay する。null＝全 variant 共通（従来どおり常時表示）。
    //   条件付き表示（下）より先に評価する＝受理されない param はそもそも UI に存在しない。
    if (Array.isArray(pdef.variants) && variant && !pdef.variants.includes(variant)) {
      visible[pdef.name] = false;
      continue;
    }
    const cond = pdef.conditionalVisible;
    if (cond === null || cond === undefined) {
      visible[pdef.name] = true;
      continue;
    }
    // ISSUE-081: 関数述語 (values, ctx)->bool を受理（computeEnabled と対称・ctx=timeframe 等）。
    if (typeof cond === 'function') {
      visible[pdef.name] = !!cond(values, context);
      continue;
    }
    const { param, equals } = cond.when;
    visible[pdef.name] = values[param] === equals;
  }
  return visible;
}

// フォーム検証（F-11・§5）。
// ConstraintEvaluator.evaluate（単一定義・C-3）へ委譲し、その違反へ
// range_from<range_to の前提付き違反（両者非 null 時のみ）を付加する（§11.2 Q-3）。
// 戻り値: { violations: Violation[], ok: boolean }。
export function validateForm(def, values) {
  const violations = evaluate(def.params, values);

  // range_from<range_to: 両者が非 null（指定）のときのみ評価。素の LT を
  // ConstraintEvaluator に置くと両者 null 時に null<null=false で誤検出するため、
  // 「両者非 null」前提を本モジュールで前処理する（ConstraintEvaluator は不変）。
  const hasRangeFrom = paramExists(def, 'range_from');
  const hasRangeTo = paramExists(def, 'range_to');
  if (hasRangeFrom && hasRangeTo) {
    const from = values.range_from;
    const to = values.range_to;
    if (isNonNullNumber(from) && isNonNullNumber(to) && !(from < to)) {
      violations.push({
        param: 'range_from',
        constraint: 'lt(range_from,range_to)',
        expected: 'range_from<range_to',
        actual: from,
      });
    }
  }

  return { violations, ok: violations.length === 0 };
}

function paramExists(def, name) {
  return (def.params ?? []).some((p) => p.name === name);
}

function isNonNullNumber(v) {
  return v !== null && v !== undefined && typeof v === 'number';
}

// 全パラメータを ParamDef.default へ復元（§7 デフォルト復元）。
// 戻り値: { name: default }。default が null の param も null として返す。
export function resetToDefaults(def) {
  const values = {};
  for (const pdef of def.params ?? []) {
    values[pdef.name] = pdef.default;
  }
  return values;
}

// ---------------------------------------------------------------------------
// スタイル/可視性タブの行モデル（ISSUE-110 🟡-1: 純ロジックを usecase へ集約）
// ---------------------------------------------------------------------------

// 実描画系列（renderer.getSeriesStyles の戻り）から編集行 view-model を構築する純関数。
//   bucket 規則系列（SeriesDef.seriesNamePattern.buckets の非空トークン）は系統（bucket）粒度に
//   畳む（内部設計_パラメータ設定ダイアログ.md §6.1「28 行は冗長」）。
//   系列名→bucket の対応は pattern.template から接頭辞を導出して判定する
//   （template.replace('{bucket}', b) の '{pct}' 以前）＝命名規約の知識を書式ハードコードせず
//   pattern 自身から得る（テンプレート変更時の二重修正を不要にする）。
// 戻り値: [{ label, names, kind, heat, color, width, style, visible }]（color/width/style は実描画値・
//   null あり得る。kind は行の描画種別（'line'|'histogram'・bucket 行は先頭系列の種別）で、
//   histogram 行は線幅/線種の編集対象外（ISSUE-111: 描画種別と設定項目の整合）。
//   heat はバー別ヒート配色の histogram（ISSUE-112: 色もユーザー編集対象外＝ヒート絶対優先）。
//   表示既定や hex 変換は呼び出し側 = adapter の責務）。
// 正規表現メタ文字をエスケープする（動的系列パターン照合用）。
function _escapeRegex(s) {
  return String(s).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// 動的 SeriesDef の seriesNamePattern（template/{bucket}/{pct}）が実系列名に一致するか。
function _dynamicPatternMatches(pattern, name) {
  const tpl = String(pattern?.template ?? '');
  if (!tpl) {
    return false;
  }
  const buckets = (Array.isArray(pattern.buckets) ? pattern.buckets : ['']).map(
    (b) => (b == null ? '' : String(b)),
  );
  const parts = tpl.split(/\{bucket\}|\{pct\}/);
  const tokens = tpl.match(/\{bucket\}|\{pct\}/g) ?? [];
  let re = '^';
  for (let i = 0; i < parts.length; i += 1) {
    re += _escapeRegex(parts[i]);
    if (i < tokens.length) {
      re += tokens[i] === '{pct}'
        ? '\\d+'
        : `(?:${buckets.map(_escapeRegex).join('|')})`;
    }
  }
  re += '$';
  return new RegExp(re).test(name);
}

// 案A（btlm_trail）: 実系列名 name が「pointStyleEditable=true」の SeriesDef に一致するか。
//   静的は seriesName 完全一致、動的は seriesNamePattern 照合。未付与指標は常に false（非波及ゲート）。
function _pointStyleEditableFor(def, name) {
  for (const sd of def?.series ?? []) {
    if (!sd || sd.pointStyleEditable !== true) {
      continue;
    }
    if (sd.dynamic && sd.seriesNamePattern) {
      if (_dynamicPatternMatches(sd.seriesNamePattern, name)) {
        return true;
      }
    } else if (sd.seriesName != null && sd.seriesName === name) {
      return true;
    }
  }
  return false;
}

// 案A（btlm_trail_marod）: 実系列名 name が「barStyleEditable=true」の SeriesDef に一致するか。
//   _pointStyleEditableFor と同型（静的は seriesName 完全一致、動的は seriesNamePattern 照合）。
//   未付与指標は常に false（非波及ゲート）。棒グラフ表示（スタイルタブ）と renderer 系列スワップの
//   二重ゲートの単一判定源（indicator_controller が payload 注入に、form_model が行モデルに使う）。
function _barStyleEditableFor(def, name) {
  for (const sd of def?.series ?? []) {
    if (!sd || sd.barStyleEditable !== true) {
      continue;
    }
    if (sd.dynamic && sd.seriesNamePattern) {
      if (_dynamicPatternMatches(sd.seriesNamePattern, name)) {
        return true;
      }
    } else if (sd.seriesName != null && sd.seriesName === name) {
      return true;
    }
  }
  return false;
}

// 公開版（adapter が payload 注入に用いる単一判定源。DOM/renderer 非依存の純関数）。
export function barStyleEditableFor(def, name) {
  return _barStyleEditableFor(def, name);
}

export function buildSeriesStyleRows(def, seriesStyles) {
  const bucketDefs = [];
  for (const s of def?.series ?? []) {
    const p = s.dynamic && s.seriesNamePattern;
    if (p && Array.isArray(p.buckets)) {
      for (const b of p.buckets) {
        if (b) {
          const prefix = String(p.template ?? '').replace('{bucket}', b).split('{pct}')[0];
          if (prefix) {
            bucketDefs.push({ bucket: b, prefix });
          }
        }
      }
    }
  }
  const rows = [];
  const byBucket = new Map();
  for (const st of seriesStyles ?? []) {
    const name = String(st.name ?? '');
    const hit = bucketDefs.find((bd) => name === bd.bucket || name.startsWith(bd.prefix));
    if (hit) {
      let row = byBucket.get(hit.bucket);
      if (!row) {
        row = {
          label: hit.bucket, names: [], kind: st.kind ?? 'line', heat: st.heat === true,
          color: st.color ?? null, width: st.width ?? null, style: st.style ?? null, visible: true,
          display: st.display ?? null,
          pointStyleEditable: _pointStyleEditableFor(def, st.name),
          barStyleEditable: _barStyleEditableFor(def, st.name),
        };
        byBucket.set(hit.bucket, row);
        rows.push(row);
      }
      row.names.push(st.name);
      row.visible = row.visible && st.visible !== false;
    } else {
      rows.push({
        label: st.name, names: [st.name], kind: st.kind ?? 'line', heat: st.heat === true,
        color: st.color ?? null, width: st.width ?? null, style: st.style ?? null,
        visible: st.visible !== false,
        display: st.display ?? null,
        pointStyleEditable: _pointStyleEditableFor(def, st.name),
        barStyleEditable: _barStyleEditableFor(def, st.name),
      });
    }
  }
  return rows;
}
