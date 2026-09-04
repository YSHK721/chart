// 配信 index.html の一覧（構造ガードの**単一ソース**）。
//
// なぜ独立したモジュールか（ISSUE-368 工程 2 是正 1）: ツールバーの器（空マウント）を HTML へ
//   直書きさせない構造ガードは、色テーマ（color_theme_toolbar_mount.test.js）に続き
//   ポジションサイズ計算機（position_sizing_toolbar_mount.test.js）でも必要になった。
//   一覧を各ガードへ手書き複製すると、配信ページが増減したときに**片方だけ更新される**
//   （複製は必ず取り残しを生む）。一覧は 1 箇所だけに置き、ガードはここを読む。
//
// 実測の訂正（工程 2 是正 1）: 4 枚は indicator_ui / replay_ui / **report_ui** / unified_ui。
//   `simulator/sim_ui/web/index.html` は実在する 5 枚目だが chart_app_wiring を通らず
//   （独自 lwc5_chart_renderer.js）、ツールバーの器も持たないため対象外。
//
// パスは **tests/ ディレクトリからの相対**。解決は読み手側で
//   `fileURLToPath(new URL(rel, import.meta.url))` を行う（従来の書き方をそのまま保つ）。
export const INDEX_HTML = [
  '../../../../indigators/indicator_ui/web/index.html',
  '../../../../simulator/replay_ui/web/index.html',
  '../../../../simulator/report_ui/web/index.html',
  '../../../../unified_ui/web/index.html',
];
