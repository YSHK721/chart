# upstream-input-validation 実行結果（v7 ホバー減光修正レビュー）

## 上流入力の整理
- 依頼者指示: 1 件（フェーズ5 コードレビュー依頼／受入基準§13／観点・確認テスト指定）
- 他者レビュー指摘: 0 件（該当なし）
- 前段成果物: 2 件（feature/trade-markers-hover-fix の差分／設計書 §13）
- 既存合意の引き継ぎ: 1 件（C2 単一 _render 不変・grep0件規約・presenter i 昇順）

## 前提抽出
1. [前段] pair 構造が `{ i, side, win, entry:{time,price}, exit:{time,price} }` である（_nearestTradeByPixel の side.time/side.price/pair.i アクセスの成立条件）
2. [前段] presenter（_attachPairLines の入力 json.pairs）が常に i 昇順を出す（tie 最小 i 決定論の成立条件）
3. [依頼/引継] coordinate 変換 API 利用が grep0件規約に違反しない（規約対象は setData のみ）
4. [依頼] simulator/SP/presenter に変更が及ばない（フロント1ファイル＋テストに閉じる）
5. [依頼] §13 が hoveredObjectId 単独依存を是正しカーソル画素近接へ移す方針である

## 証拠先行検証
1. grep `pairs` 構造定義 → pair_primitive_base.js:16 `// pairs: [{ i, side, win, entry:{time,price}, exit:{time,price} }]`、markersJsonV4 (test:292-295) が同形を生成。実証取得。
2. _attachPairLines は json.pairs をそのまま _pairs に格納（renderer:211）。markersJsonV4 は idx 昇順で i を採番（test:292 `i: idx`）。本番 presenter の昇順保証はフロント外＝実コードで未確認だが、コード内コメント(renderer:99-101)が「非昇順入力時は走査順依存・実害なし(申し送り)」と明記。条件付き実証。
3. grep → 規約対象は `mainSeries.setData`（test:460）。timeToCoordinate/priceToCoordinate は pair_lines_primitive.js でも使用済の純粋読み取り API。実証取得。
4. `git diff develop..HEAD --name-only -- simulator/` 空、`git diff develop..HEAD --name-only -- web/` も空（コミット差分なし＝全て worktree 変更）。実証取得。
5. 設計書 §13 diff 全文確認（症状・根本原因・修正方針・受入が記載）。実証取得。

## 判定結果
1. 採用（pair 構造実証済）
2. 条件付き採用（フロント内では昇順・本番 presenter 昇順はフロント外で未実証だが、非昇順時もコメント通り tie 規則が走査順依存になるのみで throw・誤動作なし。残存リスクへ転記）
3. 採用（規約対象外を実証）
4. 採用（コミット差分 0 を実証）
5. 採用（設計書実証）

## 残存リスク
- 本番 presenter が json.pairs を i 昇順で出す保証はフロント外（バックエンド/SP）に存在し、本レビュー範囲（フロント1ファイル）では実証不能。非昇順時も機能破綻はせず tie 選択 i が走査順依存になるのみ（コード申し送り済）。後続のバックエンド契約レビューに委ねる。
- 実カーソルでの発火規則性・半径12px の体感は node:test 範囲外（実機委譲・設計書明記済）。
