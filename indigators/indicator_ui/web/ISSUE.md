
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

## ISSUE-345: [復元] 「宣言順の描画ゲート」が実際には完了順になっていた（ペインの並びが起動ごとに変わる）（2026-08-09）
- **ステータス**: RESOLVED（2026-08-09）
- **重大度**: Medium（ペインの並びが非決定的になり、並び順の永続化が成立しない）
- **背景**: `IndicatorStateStore.rebuildApplied` は「compute は並列・描画は宣言順に直列化する（完了順に描くと pane の並びが起動ごとに変わる＝ISSUE-149 の並び順保証が壊れる）」と自ら宣言していた。
- **実測（実 UI・:8000・2026-08-09）**: 保存状態 `applied=[profit_rsi#1, profit_adx_needle#1]` に対し、復元後の画面は ADXNeedle が上・RSI が下。宣言順と実際の並びが食い違っていた。
- **原因**: 描画ゲートを `drawChain = drawChain.then(...)` で作るのが **自分の compute が解決した後**だったため、チェーンに繋がれる順序＝compute の完了順になっていた。ISSUE-202 の compute 並列化を入れた際に混入。宣言順のゲートを名乗るコードが、実際には完了順のゲートだった。
- **抜本的対策**: compute の発行（並列）と描画チェーンの構築（宣言順）を分離する。先に全件の compute を発行して promise を保持し、**描画チェーンは宣言順にループで先に組む**。各リンクは自分の compute を待ってから描く。これで並列性（ISSUE-202）と並び順の決定性（ISSUE-149）が両立する。
- **検証**: 「compute の完了順を宣言順と逆にしても描画順は宣言順」「失敗指標を飛ばしても残りは宣言順」「compute は並列（直列なら 60ms のところ 55ms 未満）」の 3 検定を追加。是正前は前 2 件が赤（実測 `['fast#1','slow#1']`）。live 1203 / replay 338 全緑。
- **関連**: ISSUE-149（pane の並び順保証）・ISSUE-202（起動所要の並列化）。並び順の永続化（2026-08-09 実装）はこの是正が前提。
