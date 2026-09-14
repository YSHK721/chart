// required_bars.js — サーバが申告した「計算に要する最小バー数」の読み取り（ISSUE-283）。
//
// 純関数。文言は解析しない（error.violations の構造化面だけを見る）。指標名で分岐しないため、
//   申告する指標が増えても本モジュールは無改変（OCP）。
//
// 置き場所（ISSUE-479 Wave2 J-1 SRP）: 元は adapter/front/indicator_controller.js が持っていたが、
//   これは「サーバ応答の意味を読む」規則であって画面の都合ではない。adapter から usecase へ移す。
//   既存の import 面（indicator_controller.js からの再エクスポート）は維持する。

// エラーの violations から必要バー数を取り出す（未申告は null）。
export function requiredBarsOf(error) {
  const list = error && Array.isArray(error.violations) ? error.violations : [];
  for (const v of list) {
    const n = v && Number(v.requiredBars);
    if (Number.isFinite(n) && n > 0) {
      return n;
    }
  }
  return null;
}
