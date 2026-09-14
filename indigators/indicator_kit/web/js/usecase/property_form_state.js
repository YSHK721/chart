// property_form_state.js — プロパティ ダイアログのフォーム値所有と検証判定（純ロジック・ISSUE-502 段階 4D）。
//
// 責務（単一の変更軸）: **フォームの現在値（params / variant / 未解決入力エラー）を所有し、
//   その値に対する判定（違反・OK 可否・有効化・表示・option 単位の有効化）を返す**こと。
//   DOM は一切触らない（判定の「適用」＝クラス付け替えや disabled 代入は adapter の責務）。
//
// なぜ分けるか（是正前の実測）:
//   PropertiesDialog（829 行）は「DOM 構築・イベント配線のみ」と冒頭で宣言しながら、
//   フォーム値の所有（_values / _variant）と検証・OK 制御（_revalidate / _pendingErrors）を
//   同居させていた。値と判定は DOM が無くても意味を持つ規則であり、DOM の都合（タブ再構築・
//   要素の作り直し）とは別の理由で変わる。同居している限り、判定規則は実 DOM を組み立てないと
//   検証できず、単体で回帰を固定できない。
//
// 判定規則そのものは form_model.js の純関数が唯一源であり、本クラスは再実装しない
//   （値の所有と、判定の呼び出し順・合成だけを持つ）。
import {
  buildFormModel,
  computeEnabled,
  computeVisible,
  resetToDefaults,
  validateForm,
} from './form_model.js';

export class PropertyFormState {
  // { def, params, variant, context }
  //   def     : IndicatorDef（catalog.get の戻り）
  //   params  : 初期値（AppliedInstance.params 相当。未指定は定義の既定値）
  //   variant : 初期バリアント（未指定は variants[0]）
  //   context : 述語へ渡す外部状態（例 { timeframe, servedMode, datasetRef }）
  constructor({ def, params = {}, variant = null, context = {} }) {
    this._def = def;
    this._context = context || {};
    // 現在のフォーム値（name -> value）。初期は params 優先→default。
    const model = buildFormModel(def, params || {});
    this._values = {};
    for (const f of model.fields) {
      this._values[f.name] = f.value;
    }
    // バリアント（profit_band global↔robust 等）。OK 時に variant 変更を実反映する。
    this._variants = (def.compute && def.compute.variants) || ['default'];
    this._variant = variant || this._variants[0];
    // 値へ反映できていない入力エラー（name -> message）。在席中は OK を押せない。
    this._pendingErrors = new Map();
  }

  // ---- 値の所有 -------------------------------------------------------------
  // 呼び出し時解決の遅延アクセサ（コントロール生成器が保持するのは本オブジェクトへの参照ではなく
  //   getValue/setValue の呼び出し）。デフォルト復元で入れ物を差し替えても最新を参照する。
  get values() {
    return this._values;
  }

  replaceValues(next) {
    this._values = next;
    return this._values;
  }

  getValue(name) {
    return this._values[name];
  }

  setValue(name, value) {
    this._values[name] = value;
  }

  // 既定値へ戻す（フォーム内のみ・OK 押下まで適用しない）。
  resetToDefaults() {
    return this.replaceValues({ ...resetToDefaults(this._def) });
  }

  // ---- バリアント -----------------------------------------------------------
  get variants() {
    return this._variants;
  }

  get variant() {
    return this._variant;
  }

  set variant(next) {
    this._variant = next;
  }

  // 述語（conditionalEnable / conditionalVisible / optionEnable）へ渡す評価コンテキスト。
  //   外部状態へ **選択中の variant** を重ねる。variant ごとに受理 param が異なる（ISSUE-278 #8）
  //   ため、可視判定は variant を知る必要がある。
  evalContext() {
    return { ...this._context, variant: this._variant };
  }

  // ---- 未解決の入力エラー（期間表記の換算失敗など）--------------------------
  setPendingError(name, message) {
    if (message) {
      this._pendingErrors.set(name, message);
    } else {
      this._pendingErrors.delete(name);
    }
  }

  get pendingErrorCount() {
    return this._pendingErrors.size;
  }

  // ---- 判定 -----------------------------------------------------------------
  // F-11 リアルタイム検証（§5）。条件付き非表示のフィールドは検証対象外とする
  //   （隠れた bins が既定 60 のままなら妥当だが、空値等でも OK を阻害させないため）。
  //   戻り: { violations: 表示すべき違反, ok: OK を押せるか }
  validation() {
    const { violations } = validateForm(this._def, this._values);
    const visible = computeVisible(this._def, this._values, this.evalContext());
    const effective = violations.filter((v) => visible[v.param] !== false);
    return {
      violations: effective,
      ok: effective.length === 0 && this._pendingErrors.size === 0,
    };
  }

  // 条件付き有効化（§3.5）。name -> boolean。
  enablement() {
    return computeEnabled(this._def, this._values, this.evalContext());
  }

  // 条件付き表示（§3.5 拡張）。name -> boolean。
  visibility() {
    return computeVisible(this._def, this._values, this.evalContext());
  }

  // ISSUE-080: ENUM の option 単位の有効化判定（optionEnable 述語）。
  //   `optionValues` は画面に出ている option の値（文字列）。判定だけを返し、DOM へは触らない。
  //   戻り: { enabled: boolean[]（optionValues と同順）, firstEnabled: 最初に有効な raw 値 | null,
  //           currentDisabled: 選択中の値が無効化されたか }
  optionEnablement(pdef, optionValues) {
    const ctx = this.evalContext();
    const current = String(this._values[pdef.name]);
    let firstEnabled = null;
    let currentDisabled = false;
    const enabled = optionValues.map((ov) => {
      // enum 値は raw（文字列/数値）。表示値から元型へ復元して述語へ渡す。
      const raw = (pdef.enumValues ?? []).find((v) => String(v) === ov) ?? ov;
      const ok = !!pdef.optionEnable(raw, this._values, ctx);
      if (ok && firstEnabled === null) {
        firstEnabled = raw;
      }
      if (!ok && current === ov) {
        currentDisabled = true;
      }
      return ok;
    });
    return { enabled, firstEnabled, currentDisabled };
  }
}
