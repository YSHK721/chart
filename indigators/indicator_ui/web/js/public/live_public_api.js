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
//
// まだここに無いもの:
//   `adapter/front/dataset_ref_query.js`（統合層が使う）と合成根の `bootstrap` は、経路の形を
//   既存検定が固定しているため J-5（unified_root の MODES 表駆動化）で一緒に寄せる。
//   残件は unified_ui/web/tests/js_cross_subsystem_paths.test.js の台帳が機械的に追跡する。

export * from '../usecase/period_presets.js';
export * from '../adapter/front/live_tick_player.js';
