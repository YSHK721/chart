// ladder_row（domain/ladder_row.js）— 第 1 表の行の**論理識別子**の唯一源。
//
// なぜ独立した規則なのか（ISSUE-502 段階 4C・F-2）:
//   同じ「どの行か」の判定を、印の移動検出（next_target_glow）・なめらか再生の台帳
//   （smooth_ladder）・版面の書き換え先（reach_sheet_view の levelRowRefs）の 3 か所が使う。
//   3 か所が各自で `${row.timeframe}|${row.label}` を組むと、識別の定義が 3 つになり、
//   片方だけ「価格も混ぜる」と直した日に**移動検出だけが黙って壊れる**（版面は描かれ続ける
//   ので出力の検査では原理的に落ちない）。定義はここ 1 つに閉じる。
//
// 計算量: 1 行あたり定数時間。行数が増えても発行も再計算も生まない。

/**
 * 行の論理識別子。
 *
 * label は同名でも時間足で別行になりうるので併記する。**価格は含めない**——水準の値が
 * 動いただけの行を「別の行へ移動した」と誤認しないため（印の移動検出の前提）。
 *
 * @param {{timeframe: *, label: *}} row `/reach_sheet` の行
 * @returns {string}
 */
export function rowKeyOf(row) {
  return `${row.timeframe}|${row.label}`;
}
