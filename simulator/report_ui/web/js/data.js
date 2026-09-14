// report.json（DATA）の防御的アクセサ（read-only・DOM 非依存）。
// graphs.js / heatmap.js に重複していた (data.segments[seg].agg || {}) パターンを集約する。
//
// 責務（SRP）: DATA 構造の欠落（segments / segment / agg のいずれか不在）に対して
//   安全に空オブジェクトを返す R-4 防御を 1 箇所に閉じる。集計値の算出・整形は担わない。

// segments[seg].agg を防御的に取得する（agg 欠落時は {} を返す）。
// data / segments / segment のいずれが欠けても参照例外を起こさず {} を返す。
export function aggOf(data, seg) {
  const segments = (data && data.segments) || {};
  const segment = segments[seg] || {};
  return segment.agg || {};
}
