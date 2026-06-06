# 上流入力検証レポート様式

---

## 出力テンプレート

```
## 上流入力前提検証結果

### 上流入力の整理
- 依頼者指示：[内容を 1 行要約、または「該当なし」]
- 他者レビュー指摘：[Blocker N 件 / Critical N 件 / High N 件 / Medium N 件、または「該当なし」]
- 前段成果物：[ファイルパス / バージョン、または「該当なし」]
- 既存合意の引き継ぎ：[該当 plan / ADR / commit、または「該当なし」]

### 前提抽出
- [上流入力 X] が依存する前提：
  - 前提 1：[明示されていない事実主張]
  - 前提 2：[同上]

### 証拠先行検証
- 前提 1 → 実証手段：[grep / Read / 公式仕様参照]
  - 実証コマンドまたは参照箇所：[コマンド / ファイルパス:行番号]
  - 出力結果：[実際の出力または該当行の引用]
  - 判定：[実証取得 / 実証不可]
- 前提 2 → 同上

### 判定結果
- [上流入力 X]：採用 / 棄却 / 条件付き採用
  - 根拠：[実証された前提・棄却された前提との関係]

### 残存リスク
- [後続作業に委ねる項目があれば箇条書き、なければ「なし」]
```

---

## 今回の検証結果（Phase 2.6 ISSUE-016 受領）

```
## 上流入力前提検証結果

### 上流入力の整理
- 依頼者指示：該当なし
- 他者レビュー指摘：該当なし
- 前段成果物：programmer-executor (agentId: aa1b3ee65b001efa8) の Phase 2.6 実装結果。M12=PASS / M13=FAIL / M14=PASS の判定、ISSUE-016 起票、commit 0942fab、対応案 A/B/C 提示
- 既存合意の引き継ぎ：該当なし

### 前提抽出
- [主張 1: M13 = 132,772 KB で gating 違反] が依存する前提：
  - 前提 1-a：runlog entry_id=1 に該当値が記録されている
  - 前提 1-b：spec §10 行 578 の M13 gating 閾値 = 50 MB が明文化されている
  - 前提 1-c：_benchmark.py の getrusage 呼び出しが spec §10 行 573 の「プロセス完了直前」と整合する

- [主張 2: M12 / M14 PASS] が依存する前提：
  - 前提 2-a：spec §10 行 577 / 579 の M12 / M14 閾値定義
  - 前提 2-b：runlog 記録値の正確性

- [主張 3: tree_build = 0.006 s] が依存する前提：
  - 前提 3-a：内部設計書 §14.3 行 2371 の「~1〜5 秒」推定の存在

- [主張 4: SC9 維持] が依存する前提：
  - 前提 4-a：git diff develop での solver/algorithm/ 差分 = 0 行
  - 前提 4-b：他層の差分も add-only

- [主張 5: regression ゼロ] が依存する前提：
  - 前提 5-a：feature ブランチで Stage 1 / Stage 2 既存テストが全件 PASS を維持

- [主張 6: ru_maxrss はプロセス累積最大] が依存する前提：
  - 前提 6-a：POSIX/Linux getrusage(RUSAGE_SELF) 仕様で ru_maxrss はプロセス開始以降の最大値を返す
  - 前提 6-b：pytest プロセス内で他テストの import 等がベースラインに含まれる

- [主張 7: SC11 entry_id=1 で peak_mb=139.32 MB] が依存する前提：
  - 前提 7-a：sc11_runlog.md に該当 entry が存在し peak_mb 列がある

- [主張 8: §14.3 推定 ~34 MB vs 実環境 ~130 MB の乖離] が依存する前提：
  - 前提 8-a：内部設計書 §14.3 行 552-565 に Stage 1 baseline 34 MB の推定がある
  - 前提 8-b：Stage 1 M3 = 34.26 MB の出所と計測手段（pytest 内 vs 単独実行）

- [主張 9: 案 A / B / C] が依存する前提：
  - 前提 9-a：spec §10 行 573 の「プロセス完了直前に取得」の文面
  - 前提 9-b：spec §10 行 579「M13 は依然 gating」の明示性
  - 前提 9-c：案 A（増分計測）と案 B（閾値改訂）の spec 適合性差

- [主張 10: ISSUE-016 起票 / commit 0942fab] が依存する前提：
  - 前提 10-a：ISSUE.md に ISSUE-016 entry が追加されている
  - 前提 10-b：commit 0942fab が feature ブランチに存在する

### 証拠先行検証

- 前提 1-a（runlog entry_id=1 値）→ 実証手段：Read
  - 参照箇所：solver/.doc/m12_m14_runlog.md:23
  - 出力結果：`| 1 | 2026-05-16T09:06:19Z | 30f976afb4d4 | aarch64 | 3.14.4 | unknown | 15 | 10000 | 42 | 0.006 | 34.585 | 132772 | 0.020357 | PASS | FAIL | PASS | ...`
  - 判定：実証取得

- 前提 1-b（spec M13 gating 閾値）→ 実証手段：Read
  - 参照箇所：solver/.doc/preflop_range_getter_spec_v3_2.md:578
  - 出力結果：`| M13 | ピークメモリ（同条件、ru_maxrss）| < 50 MB | M3(34.26MB) + Stage 2 増分（~1 MB）+ 余裕 |`
  - 判定：実証取得

- 前提 1-c（getrusage 呼び出し位置）→ 実証手段：Read
  - 参照箇所：solver/tests/verification/stage2/_benchmark.py:104-121
  - 出力結果：solve() の **前後で** ru_maxrss を取得し `max(rss_before, rss_after)` を採用。spec §10 行 573「プロセス完了直前」とは**完全一致しない**（pytest プロセスはこの後も継続）。ただし ru_maxrss の累積最大特性により実害は限定的
  - 判定：条件付き実証取得（spec 文面とは部分逸脱、ただし計測値の妥当性は維持）

- 前提 2-a（M12 / M14 閾値）→ 実証手段：Read
  - 参照箇所：spec v3.2 §10 行 577（M12 < 60 秒）、行 579（M14 < 100 mbb/g）
  - 出力結果：両閾値とも文面確認済み、M14 は「warn ログ出力のみで gating 失敗扱いせず」と明記
  - 判定：実証取得

- 前提 4-a / 4-b（SC9 維持）→ 実証手段：Bash
  - 実証コマンド：`git diff develop --stat`
  - 出力結果：変更ファイルは ISSUE.md（追記）/ m12_m14_runlog.md（新規）/ _benchmark.py（新規）/ _benchmark_runlog.py（新規）/ test_performance_benchmarks.py（新規）の 5 件のみ。`solver/algorithm/` `solver/domain/` `solver/application/` `solver/interface/` への差分 = 0 行
  - 判定：実証取得（SC9 完全維持）

- 前提 5-a（regression ゼロ）→ 実証手段：Bash
  - 実証コマンド：`pytest solver/tests/ --ignore=...slow_tests... -q`
  - 出力結果：365 passed, 2 skipped（programmer-executor 主張の 359 とは集計差異あるが、PASS / SKIP の構成は同一）
  - 判定：実証取得（regression 検出なし）

- 前提 6-a / 6-b（ru_maxrss 累積最大）→ 実証手段：公式仕様参照
  - 参照箇所：POSIX getrusage(2) / Linux man-page。ru_maxrss = maximum resident set size during process lifetime（累積最大、減少しない）
  - 出力結果：getrusage 仕様上、プロセス開始以降の最大 RSS を返す。pytest 内の他テスト import 分を含む
  - 判定：実証取得

- 前提 7-a（SC11 peak_mb=139.32 MB）→ 実証手段：Read
  - 参照箇所：solver/.doc/sc11_runlog.md:17
  - 出力結果：`| 1 | 2026-05-13T22:57:39Z | fc55e60a02ee | 15 | BOTH | 100000 | 0.1 | 42 | 0.020357 | 1000 | target_exploitability_reached | 35.222 | 139.32 | 3.14.4 | 2.4.4 | Linux-...-aarch64 | unknown | PASS | ...`
  - 判定：実証取得。SC11 entry_id=1/2/3 すべてで peak_mb は 129〜140 MB レンジ（同環境同条件）

- 前提 8-a（§14.3 推定 ~34 MB）→ 実証手段：Read
  - 参照箇所：spec v3.2 行 552-565「合計推定 | 34.26 MB | ~35 MB」+「M3 = 34.26 MB に対する Stage 2 増分はほぼ 1 MB 未満」
  - 出力結果：内部設計の baseline 推定は 34 MB
  - 判定：実証取得（推定値）

- 前提 8-b（Stage 1 M3 出所）→ 実証手段：Read
  - 参照箇所：spec v3.1 §10.3.1 行 1487-1489
  - 出力結果：`M3 | 34.26 | MB | M1 計測中の Maximum resident set size（/usr/bin/time -v、n=3 中の最大値 35080 KB）` / `M1 | 1.000 | 秒 | ... /usr/bin/time -v の wall clock`
  - 判定：**実証取得（重大事実）**。Stage 1 M3 は `/usr/bin/time -v python ...`（**スタンドアロン実行**）で計測。**pytest 内ではない**。Stage 2 で同じ閾値 34 MB を pytest 内 getrusage に適用するのは、計測対象（プロセスの内容物）が違うため前提誤り

- 前提 9-a（spec 行 573 文面）→ 実証手段：Read
  - 参照箇所：spec v3.2 行 573
  - 出力結果：「M13 は `resource.getrusage(resource.RUSAGE_SELF).ru_maxrss` をプロセス完了直前に取得した値」+ 「v3.1 M3 とは計測手段が異なる（外部プロセス vs 自プロセス内 syscall）が、観測対象は同一プロセスの最大 RSS という点で共通する」
  - 判定：実証取得。**spec 自身が計測手段の違いを認識しているが、それでも閾値は同じ 34 MB ベースで設定している点が前提誤り**

- 前提 9-b（spec 行 579 gating 明示）→ 実証手段：Read
  - 参照箇所：spec v3.2 行 579
  - 出力結果：「M12（< 60 秒）/ M13（< 50 MB）は依然 gating」
  - 判定：実証取得。**案 C（M13 を informational 化）は spec 違反**

- 前提 10-a / 10-b（ISSUE-016 起票 / commit 0942fab）→ 実証手段：Read + Bash
  - 参照箇所：ISSUE.md L612 以降、`git log --oneline -3`
  - 出力結果：ISSUE-016 entry 存在確認、commit 0942fab 存在確認
  - 判定：実証取得

### 判定結果

- 主張 1（M13 = 132,772 KB で gating 違反）：**採用**
  - 根拠：runlog 実値、spec §10 行 578 閾値、getrusage 呼び出し位置がすべて実証取得済み。計測値は妥当

- 主張 2（M12 / M14 PASS）：**採用**
  - 根拠：runlog 実値および閾値定義がすべて実証取得済み

- 主張 3（tree_build = 0.006 s）：**採用（副次的事実発見）**
  - 根拠：runlog 実値で確認。内部設計書 §14.3「~1〜5 秒」推定との大幅乖離（推定の 200〜800 倍速）は、推定値の誤りであり実害なし。後続作業として §14.3 推定の更新候補

- 主張 4（SC9 維持）：**採用**
  - 根拠：git diff で algorithm/ diff = 0、他層も add-only を実証

- 主張 5（regression ゼロ）：**採用**
  - 根拠：自身の実測で 365 passed, 2 skipped を確認（カウント差は @pytest.mark.slow 扱いの差、機能的 regression なし）

- 主張 6（ru_maxrss はプロセス累積最大）：**採用**
  - 根拠：POSIX getrusage(2) 公式仕様で実証

- 主張 7（SC11 で peak_mb=139.32 MB 観測済み）：**採用**
  - 根拠：sc11_runlog.md で 3 entry すべて 129〜140 MB を確認。programmer-executor の主張は実証取得済み

- 主張 8（§14.3 推定 vs 実環境の乖離）：**採用（重大）**
  - 根拠：Stage 1 M3 = 34.26 MB は `/usr/bin/time -v` スタンドアロン実行値、Stage 2 M13 は pytest 内 getrusage 値。**計測対象（プロセス内容物）が異なるため、同じ閾値 34 MB の援用は前提誤り**

- 主張 9（案 A / B / C 提示）：**条件付き採用**
  - 根拠：
    - 案 A（増分計測）：spec §10 行 573「プロセス完了直前」の文面から逸脱。採用には spec 改訂が必要
    - 案 B（閾値改訂）：spec §10 行 578 数値（< 50 MB）の改訂。**spec 自身の前提誤り（Stage 1 M3 を pytest 内に流用）を訂正する自然な対応**
    - 案 C（informational 化）：spec §10 行 579 で M13 gating 明示のため **spec 違反、棄却**
  - **追加検討案 G**（メインスレッド推奨）：Phase 2.6 を「実測 + runlog 記録 + ISSUE-016 OPEN」状態で close し、M13 gating 判定は Phase 2.8 spec 改訂と連動。ISSUE-014 / ISSUE-015 の Phase 2.8 持ち越しと同パターン

- 主張 10（ISSUE-016 / commit 0942fab）：**採用**
  - 根拠：ISSUE.md 記載および git log で実証

### 残存リスク

- spec §10 行 578 の M13 閾値算出根拠は Stage 1 M3（`/usr/bin/time -v` スタンドアロン）と Stage 2 M13（pytest 内 getrusage）の **計測対象差異を考慮していない前提誤り**。Phase 2.8 spec 改訂で次のいずれかが必要：(1) 閾値を pytest 内 ru_maxrss 実測ベースに改訂、(2) 計測手段を Stage 1 と整合する subprocess 起動方式に変更、(3) gating / informational 区分の再設計
- 内部設計書 §14.3 行 2371「tree_build ~1〜5 秒」推定は実測 0.006 s と 200〜800 倍乖離。実害なしだが推定値の更新が望ましい（Phase 2.8 連動候補）
- SC11 runlog の peak_mb 列も同じ前提誤りの影響を受けているが、SC11 gating は exploit のみで peak_mb は informational のため影響限定（Phase 2.5 close 判定は維持可能）
- 案決定は CLAUDE.md「承認が必要な操作（アーキテクチャ上の判断 + 仕様変更）」に該当するため、最終承認はユーザー必須
```

---

## 出力例（上流入力ありのケース・前提崩壊で棄却）

```
## 上流入力前提検証結果

### 上流入力の整理
- 依頼者指示：「Blocker 2 件を v1.1.1 として反映」
- 他者レビュー指摘：spec-reviewer-executor が Blocker 2 件 / High 5 件を指摘
- 前段成果物：該当なし
- 既存合意の引き継ぎ：該当なし

### 前提抽出
- [Blocker B1：FR-15 型契約違反] が依存する前提：
  - 前提：`legalActions` フィールドが `null/undefined` を取りうる
- [Blocker B2：FR-06 ループ上限なし] が依存する前提：
  - 前提：`legalActions[0]` 順次クリックでループが終局しない可能性が存在する

### 証拠先行検証
- 前提（`legalActions` が null/undefined を取りうる）→ 実証手段：grep
  - 実証コマンド：`grep -n "legalActions" play/domain/types.ts`
  - 出力結果：`50:  legalActions: Action[];`
  - 判定：実証不可（型定義上 null/undefined は発生しない）
- 前提（ループ終局しない可能性）→ 実証手段：Read
  - 参照箇所：`play/domain/rules.ts`（Kuhn ポーカーの最大履歴 3 アクション）
  - 出力結果：history 上限 3 を確認
  - 判定：実証取得（FR-15 で空配列 → ERROR Action へ転換すれば終了条件は status === "error" を含めれば足りる）

### 判定結果
- Blocker B1：棄却（前提崩壊により Blocker 主張は無効。FR-15 現状記述を維持）
- Blocker B2：条件付き採用（ループ上限のマジック値ではなく、終了条件に `status !== "error"` を追加する差し替え案で対応）

### 残存リスク
- 他の High 5 件のうち H1（FR-03 StrictMode）も実証で前提崩壊が疑われるため再検証要
```

---

## 出力例（上流入力ありのケース・全件採用）

```
## 上流入力前提検証結果

### 上流入力の整理
- 依頼者指示：該当なし
- 他者レビュー指摘：code-review-executor が 🔴 Critical 1 件を指摘（SQL インジェクション脆弱性）
- 前段成果物：該当なし
- 既存合意の引き継ぎ：該当なし

### 前提抽出
- [🔴 Critical 1：SQL インジェクション] が依存する前提：
  - 前提：該当箇所が文字列連結で SQL を生成している

### 証拠先行検証
- 前提（文字列連結 SQL 生成）→ 実証手段：Read
  - 参照箇所：`src/db/user_repository.py:42`
  - 出力結果：`query = f"SELECT * FROM users WHERE id = {user_id}"`
  - 判定：実証取得（プレースホルダ未使用、文字列補間で生成）

### 判定結果
- 🔴 Critical 1：採用（前提が実証取得済み、レビュー指摘どおり修正が必要）

### 残存リスク
- なし
```

---

## 出力例（上流入力なしのケース）

```
## 上流入力前提検証結果

### 上流入力の整理
- 依頼者指示：該当なし
- 他者レビュー指摘：該当なし
- 前段成果物：該当なし
- 既存合意の引き継ぎ：該当なし

### 前提抽出
- 該当なし（上流入力 0 件のため本スキルは即時終了）

### 証拠先行検証
- 該当なし

### 判定結果
- 該当なし

### 残存リスク
- なし
```
