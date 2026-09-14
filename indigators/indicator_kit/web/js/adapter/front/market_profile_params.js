// market_profile_params.js — Market Profile（A7）のパラメータ・スキーマ写像（純関数）。
//
// ISSUE-094 🔴-4: indicator_controller.js（A6 指標管理 UI）へ混在していた MP のスキーマ知識
//   （保存 params → actor 取得 params への写像・後方互換マイグレーション）を純関数へ外出しする。
//   MP の param スキーマが変わったとき（＝A7 の変更要求）に A6 controller を編集させないための分離。
//   挙動は抽出前の IndicatorController._mpParams / _deriveMode / _deriveResmode と byte 等価。
//   DOM・facade・this 状態に非依存の純関数（node 単体テスト対象・controller は薄い委譲のみ保持）。

// resmode（解像度モード）を決める後方互換ヘルパ（restore と apply の両経路で共用）。
//   - 明示 resmode があればそのまま返す（後方互換補完のみ・上書きしない）。
//   - resmode 欠落かつ range がレンジ数値集合 → 'range'（保存したレンジを維持し client が &barw= を送る）。
//   - resmode 欠落かつ range='auto' → 'bins'（従来通り bins フォールバック）。
//   - resmode も range も無い旧インスタンスは null を返し resmode を付与しない（client 既定 = bins）。
export function deriveMpResmode(p = {}) {
  if (p.resmode != null) {
    return p.resmode;
  }
  if (p.range == null) {
    return null;
  }
  const BAR_WIDTHS = new Set(['10', '25', '50', '100', '250', '500']);
  return BAR_WIDTHS.has(String(p.range)) ? 'range' : 'bins';
}

// mode（表示モード）を決める後方互換ヘルパ（deriveMpResmode と同方針・apply/gear/restore 共用）。
//   - 明示 mode があればそのまま返す（legacy との競合時は mode 優先＝後方互換補完は上書きしない）。
//   - mode 欠落かつ legacy sessions:true → 'sessions'（両 true の旧データも sessions 優先）。
//   - mode 欠落かつ legacy replay:true → 'normal'（ISSUE-082: リプレイ撤去）。
//   - mode 欠落かつ legacy が明示 false（両 OFF）→ 'normal'（restore で両 OFF を再現）。
//   - mode も legacy キーも無い旧インスタンスは null（mode を付与しない＝actor 既定=通常）。
export function deriveMpMode(p = {}) {
  if (p.mode != null) {
    // ISSUE-082: リプレイモードは present から撤去済み。保存済み mode='replay' は 'normal' へ正規化。
    return p.mode === 'replay' ? 'normal' : p.mode;
  }
  // 両 true の旧データは sessions 優先（排他統合のため一方に確定させる）。
  if (p.sessions === true) {
    return 'sessions';
  }
  if (p.replay === true) {
    return 'normal'; // ISSUE-082: legacy replay:true も normal へ（リプレイ撤去）。
  }
  // legacy キーが存在し明示 false（両 OFF）なら normal を導出する（両フラグ不在は null）。
  if (p.replay != null || p.sessions != null) {
    return 'normal';
  }
  return null;
}

// MP アクターへ渡す取得 params（resmode/bins/va/src/range/mode/period/dispbp）を組み立てる
//   （apply/gear/restore 共通）。limit は転送しない（MP は全期間集計固定）。resmode を転送し、
//   client が resmode で bins/barw の送信を排他化する。range は null/未指定のとき載せない
//   （値指定時のみ付与。'auto' は撤去済だが後方互換で除外を残す）。
export function buildMpParams(p = {}) {
  const out = { va: p.va, src: p.src };
  // bins（legacy・ISSUE-079 で catalog から撤去済み）: 旧保存インスタンスにのみ存在。保存時のみ転送。
  if (p.bins != null) {
    out.bins = p.bins;
  }
  // period（期間: 全期間/当日・ISSUE-071 (b)案）: 保存時のみ転送する（period 未保存の旧インスタンスの
  //   転送 payload を変えない＝undefined キーを載せない。actor 側も null/undefined キーは無視する）。
  if (p.period != null) {
    out.period = p.period;
  }
  // dispbp（表示幅 bp・ISSUE-079）: 保存時のみ転送（旧インスタンスの payload 不変・actor が
  //   barw(pt) へ写像する）。
  if (p.dispbp != null) {
    out.dispbp = p.dispbp;
  }
  // mode（表示モード）: 旧 replay(BOOL)/sessions(BOOL) を統合した排他 ENUM
  //   ['normal','replay','sessions'] を actor へ転送する。undefined は載せない（actor 既定=通常）。
  //   legacy キー（replay/sessions）自体は actor へ送らない（mode に一本化・二重管理を避ける）。
  const mode = deriveMpMode(p);
  if (mode != null) {
    out.mode = mode;
  }
  // resmode（解像度モード）: client が bins/barw の送信を排他化する。
  const resmode = deriveMpResmode(p);
  if (resmode != null) {
    out.resmode = resmode;
  }
  if (p.range != null && p.range !== 'auto') {
    out.range = p.range;
  }
  return out;
}
