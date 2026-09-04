// live_public_api.js — live core が他サブシステムへ公開する面（ISSUE-479 Wave2 J-4b）。
//
// なぜ必要か（D-1 の実測）:
//   dashboard core と統合層は live core のモジュールを **URL で** 読み込む。従来その URL は
//   `/live/js/usecase/period_presets.js` のように live core の**内部階層**を名指していた。
//   これらは識別子渡しの動的 import（`const P = '...'; await import(P);`）で読まれるため
//   import 走査には原理的に現れず、live core 側の配置換えは無言で 404 を生む——
//   壊れたことがテストにも型にも映らない。
//
//   本モジュールが「他 core が名指してよい唯一の URL」になる。live core の内部でモジュールを
//   移動しても、直すのは本ファイルの相対 import だけで、消費者の URL は変わらない。
//   `/live/js/public/*.js` 以外を他 core から名指していないことは、各 core の web/tests の
//   依存方向ゲート（G-3）が機械的に固定する。
//
// 中身を持たない: ここに実装を書くと「公開用の第 2 実装」が生まれる。再輸出だけを置く。
//
// 公開しているもの（＝実際に他 core が借りているものだけ。使われない再輸出は置かない）:
//   - 期間プリセット換算表（`usecase/period_presets.js`）: 期間 → 本数の唯一源。
//   - なめらか tick 再生（`adapter/front/live_tick_player.js`）: 再生機構の参照実装。
//   - 表示対象 ref の解決規則（`adapter/front/dataset_ref_query.js`）: `?dataset=` の解釈の
//     唯一源（ISSUE-447 A-3 案 U1）。統合層はこれを参照するだけで、複製を持たない。
//
// ここに合成根の `bootstrap` を置かない理由（実測 2026-09-04）:
//   本ファイルは dashboard core が動的 import する（`dashboard_ui/.../composition_root_front.js`
//   の LIVE_PUBLIC_API_PATH）。`bootstrap` を再輸出すると live のチャートアプリ一式
//   （直接 import だけで 19 本＋その推移閉包）が dashboard の読み込みに巻き込まれる。
//   借り手が要らないものを運ぶのは浪費なので、合成根は別の公開面 `live_root_api.js` に置く
//   （公開面は 1 core 1 本という制約は無い。分けるのは重さの境界を切るためである）。

export * from '../usecase/period_presets.js';
export * from '../adapter/front/live_tick_player.js';
export { DATASET_REF_QUERY_PARAM, resolveDatasetRef } from '../adapter/front/dataset_ref_query.js';
