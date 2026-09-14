// live_root_api.js — live core の**合成根**を他サブシステムへ公開する面（ISSUE-479 Wave2b J-5）。
//
// なぜ必要か: 統合層（unified_ui）は live core を単一 mount の土台として **URL で** 読み込む。
//   従来その URL は `/live/js/adapter/front/composition_root_front.js` と live core の
//   **内部階層**を名指していた。識別子渡しの動的 import（`const P = '…'; import(P)`）で読まれる
//   ため import 走査には原理的に現れず、live core 側の配置換えは統合ページを無言で 404 にする
//   ——統合ページが真っ白になるまで誰も気付けない。
//
// なぜ `live_public_api.js` と分けるのか（実測 2026-09-04）:
//   `live_public_api.js` は dashboard core が動的 import する（期間プリセットと tick 再生を
//   借りるため）。そこへ `bootstrap` を混ぜると、live のチャートアプリ一式（直接 import だけで
//   19 本＋その推移閉包）が dashboard の読み込みに巻き込まれる。借り手が要らないものを運ぶのは
//   浪費なので、重い合成根はこの面に切り分ける。公開面は「他 core が名指してよい唯一の場所」で
//   あって「1 core に 1 本」という制約は無い。
//
// 中身を持たない: ここに実装を書くと「公開用の第 2 実装」が生まれる。再輸出だけを置く。
// 名前を明示する: `export *` では何を公開しているかがファイルから読めない。
//
// 公開しているもの（統合層が単一 mount に使う 1 点）:
//   - bootstrap : chart / mainSeries / renderer / controller / live pollers / リプレイ層ハンドルを
//                 1 回だけ生成する live core の合成根。

export { bootstrap } from '../adapter/front/composition_root_front.js';
