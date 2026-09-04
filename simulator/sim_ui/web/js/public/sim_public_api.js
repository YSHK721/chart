// sim_public_api.js — sim core が他サブシステムへ公開する面（ISSUE-479 Wave2b J-5）。
//
// なぜ必要か（replay_public_api.js と同じ理由）: 統合層（unified_ui）は sim core のモジュールを
//   **URL で** 読み込む。従来その URL は `/sim/js/adapter/front/composition_root_front.js` と
//   sim core の**内部階層**を名指していた。識別子渡しの動的 import（`const P = '…'; import(P)`）で
//   読まれるため import 走査には原理的に現れず、sim core 側の配置換えは統合層を無言で 404 にする
//   ——壊れたことがテストにも型にも映らない。
//
//   本モジュールが「他サブシステムが名指してよい唯一の URL」になる。内部でモジュールを移動
//   しても、直すのは本ファイルの相対 import だけで、統合層の URL は変わらない。統合層が
//   この URL をどこから知るかも表 1 枚（`unified_ui/web/js/mode_table.js` の displayLayerPath）に
//   閉じている。
//
// 中身を持たない: ここに実装を書くと「公開用の第 2 実装」が生まれる。再輸出だけを置く。
// 名前を明示する: `export *` では何を公開しているかがファイルから読めず、消費者が要る名前が
//   消えても気付けない。
//
// 公開しているもの（＝実際に統合層が借りているものだけ。使われない再輸出は置かない）:
//   - setupSimDisplay : sim 表示層（器・3 窓・取引明細）の据付。統合層は器を渡すだけで、
//                       中身の所有権は sim 側にある。
//   子文書側の入口 `mountSimReportView` は sim 自身のページ（report_view.html）が同一 core 内で
//   読むものなので、公開面には出さない。

export { setupSimDisplay } from '../adapter/front/composition_root_front.js';
