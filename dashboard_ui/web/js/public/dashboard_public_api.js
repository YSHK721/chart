// dashboard_public_api.js — dashboard core が他サブシステムへ公開する面（ISSUE-479 Wave2b J-5）。
//
// なぜ必要か（replay_public_api.js / sim_public_api.js と同じ理由）: 統合層（unified_ui）は
//   dashboard core のモジュールを **URL で** 読み込む。従来その URL は
//   `/dashboard/js/adapter/front/composition_root_front.js` と内部階層を名指していた。
//   識別子渡しの動的 import で読まれるため import 走査には原理的に現れず、dashboard core 側の
//   配置換えは統合層を無言で 404 にする。
//
//   本モジュールが「他サブシステムが名指してよい唯一の URL」になる。内部でモジュールを移動
//   しても、直すのは本ファイルの相対 import だけで、統合層の URL は変わらない。
//
// 中身を持たない: ここに実装を書くと「公開用の第 2 実装」が生まれる。再輸出だけを置く。
// 名前を明示する: `export *` では何を公開しているかがファイルから読めない。
//
// 公開しているもの（＝実際に統合層が借りているものだけ）:
//   - setupDashboardDisplay : dashboard 表示層（価格ラダー・各時間足の一覧）の据付。
//                             器は統合層が渡し（#um-dashboard-area）、中身は dashboard が所有する。

export { setupDashboardDisplay } from '../adapter/front/composition_root_front.js';
