// ComputeError（domain/compute_error.js）— 計算 API 由来エラーの単一定義。
//
// 設計入力: 内部設計書 §6.3.4（error.type）/ §7.1.1（ComputeGateway 契約）。
//
// 経緯: 旧来 compute_http_client.js（error_type）と embedded_compute_gateway.js（type）が
//   それぞれ独自の ComputeError を定義していた。種別フィールド名の差（error_type / type）を
//   保ったまま 1 クラスへ集約する。本クラスは両プロパティ（type と error_type）を同値で
//   保持し、いずれの呼び出し側も破壊しない（既存テストの instanceof / .error_type / .type 双方を満たす）。
//
// domain 層（DOM/fetch/localStorage 非依存）。両アダプタは本クラスを re-export する。

export class ComputeError extends Error {
  // kind: 種別文字列。error_type / type のどちらで渡しても受け、両プロパティへ同値を載せる。
  //   既定は 'internal'（§6.3.4 の汎用フォールバック）。
  constructor(message, { error_type, type, violations } = {}) {
    super(message);
    this.name = 'ComputeError';
    const kind = error_type ?? type ?? 'internal';
    // 両系統の呼び出し側・テストが参照する 2 プロパティを同値で公開する。
    this.error_type = kind;
    this.type = kind;
    // ISSUE-283: サーバが申告した機械可読な診断（例: 履歴不足の requiredBars / actualBars）。
    //   文言を解析せずに「あと何本必要か」を判断するための面。未申告は空配列。
    this.violations = Array.isArray(violations) ? violations : [];
  }
}
