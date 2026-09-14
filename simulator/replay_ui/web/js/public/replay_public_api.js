// replay_public_api.js — replay core が他サブシステムへ公開する面（ISSUE-479 Wave2 J-4b）。
//
// なぜ必要か: 統合層（unified_ui）は replay core のモジュールを **URL で** 読み込む。従来その
//   URL は `/replay/js/adapter/front/replay_indicator_controller.js` のように replay core の
//   **内部階層**を名指していた。識別子渡しの動的 import で読まれるため import 走査には現れず、
//   replay core 側の配置換えは統合層を無言で 404 にする。
//
//   本モジュールが「他サブシステムが名指してよい唯一の URL」になる。内部でモジュールを移動
//   しても、直すのは本ファイルの相対 import だけで、統合層の URL は変わらない。
//
// 中身を持たない: ここに実装を書くと「公開用の第 2 実装」が生まれる。再輸出だけを置く。
//
// 公開しているもの（統合層がリプレイモードの合成に使う 4 点）:
//   - ReplayIndicatorController : リプレイ時の指標コントローラ（live の派生）。
//   - setupReplay               : リプレイ駆動の据付。
//   - ReplayMarketProfileActor  : リプレイ時の MP アクター。
//   - installReplayBar          : リプレイ操作バーの DOM（replay 層の View が所有）。

export * from '../adapter/front/replay_indicator_controller.js';
export * from '../replay.js';
export * from '../adapter/front/replay_market_profile_actor.js';
export * from '../adapter/front/replay_bar_view.js';
