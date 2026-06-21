# prompt-validation-workflow 自己レビュー（v7 ホバー減光修正レビュー・努力 xhigh）

## Pre-mortem（想定失敗原因）
本レビューが「承認」を出した後に本番で失敗する最有力原因を死因究明視点で推定:

- F1: tie/最近傍判定が「pair 走査順 = i 昇順」前提だが、本番 presenter が非昇順を出すと tie 選択 i が変わる（決定論破れ）。
- F2: `point.x === 0`（チャート左端の有効座標）を null 安全ガードが誤って弾き、左端マーカーで近接判定が無効化される。
- F3: NaN 座標（priceToCoordinate が NaN を返す lwc バージョン）で hypot が NaN になり、減光が発火しない／throw する。
- F4: 軽量レビューで「テスト緑＝全件正しい」と過大評価（追従性バイアス A）。
- F5: simulator worktree 変更を feature 差分と混同し scope 違反と誤判定（追従性バイアス B）。

## 証拠先行検証
- F2: /tmp/edge_test.mjs 実行 → `point(0,0) highlight fired: true`。ガードは `typeof point.x !== 'number'` で 0 を受理。棄却。
- F3: 同 edge test → `NaN point threw: false`。`NaN <= 12` が false で hit せず hoveredObjectId フォールバック→null。安全。棄却。
- F1: pair_primitive_base.js:16 と markersJsonV4(test:292 `i: idx`) でフロント内昇順を実証。本番 presenter 昇順はフロント外で未実証。コード(renderer:99-101)が非昇順時「走査順依存・実害なし」と申し送り済。throw・機能破綻はしない。→ 上流入力検証の残存リスクへ転記済（条件付き）。成立だが軽微・申し送り済。
- F4: node --test 全 314 件 pass を実行確認。加えて exit-side テストの座標を手計算で独立検証（pair0 exit (11,130)・cursor (11,131)・hypot(0,1)=1<12 hit／entry (10,100) は hypot(1,31)≈31>12 非 hit）。境界 12=hit・13=miss も hypot 手計算で一致。部分証拠を全体結論に拡大していない。棄却。
- F5: `git diff develop..HEAD --name-only -- simulator/` 空を実証。worktree の test_compute_stats_golden_mt5.py はコメント追記のみ・本 feature 無関係・初期 gitStatus にも既出。scope 違反ではないと確定。棄却。

## 検証（成立/棄却）
- F1: 成立（軽微・申し送り済・残存リスク転記）。F2/F3/F4/F5: 棄却。

## 反映
- F1 を 🔵（将来改善・申し送り事項）として出力に明記。撤回不要（機能破綻なし）。
- 承認判定は「条件付き承認」ではなく「承認」が妥当だが、worktree 未コミット状態という手続き事項を承認条件として明示（マージ前にコミット必須）。

## 残存リスク
- 本番 presenter の pairs 昇順保証（バックエンド契約レビュー範囲）。
- 実カーソル発火規則性・半径12px 体感（実機ブラウザ確認・設計書明記済）。
- 本 feature の変更が全て worktree 未コミットである手続き事項（レビュー対象差分は確認済だが、マージにはコミット化が前提）。
