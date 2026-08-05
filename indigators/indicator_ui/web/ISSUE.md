
## ISSUE-266: [設計是正] A方式バンドル（file:// 単一 HTML）を廃止する（依頼者指示 2026-08-05）
- **ステータス**: RESOLVED（2026-08-05・refactor/drop-a-mode-bundle）
- **重大度**: Medium（実運用コードの設計を歪めていた制約の除去）
- **背景**: `node build.mjs` が全 ES Modules を 1 つの IIFE スコープへ連結し、サーバ無しで開ける自己完結 HTML（`out/prototype.html`）を生成する方式。
- **廃止の根拠（実測）**:
  1. **使われていなかった**。現行ソースからの再ビルドが構文エラー（ISSUE-265）で起動不能なまま誰も気付いていなかった。追跡下の生成物は 2026-08-02 のもので陳腐化していた。
  2. **実運用コードの設計を歪めていた**。import を剥がして 1 スコープへ連結するため (a) 全モジュールのトップレベル名が衝突しうる (b) 新規モジュールは `MODULE_ORDER` への手動登録が要る、という制約が付き、その制約を理由に実運用側へ規則の複製が生まれていた（例: `market_profile_actor._sessionFrom` が `GrowthWindow` の規則を複製・ISSUE-262 参照）。
- **削除したもの**: `web/build.mjs` / `out/prototype.html` / `tests/build_module_order.test.js` / `tests/bundle_builds_and_parses.test.js` / `pair_dim_alpha_single_source.test.js` の (C) ケース（バンドル前提のもの）。いずれも git 履歴から復元可能。
- **残したもの**: 実行時の A方式経路（`modeForProtocol` の 'a' / `EmbeddedComputeGateway` / `SAMPLE_DATA` / `mode` 分岐 15 箇所）。バンドルが無くなったため到達不能だが、削除は別途判断とする（**未対応**）。
- **検証**: 実 UI（8000・B方式）で JS 148 本・1,302ms・canvas 11/11・時間足 9 足・コンソールエラー 0。回帰: Python 2,533 / indicator_ui web 1,075 / market_profile web 325 / replay_ui web 301 / unified_ui web 43 全通過。
