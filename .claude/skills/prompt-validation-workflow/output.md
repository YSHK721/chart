# prompt-validation-workflow 実行結果

## Pre-mortem：成果物が失敗するシナリオ分析

### 想定失敗シナリオ 1：意図しない行変更が ISSUE.md に含まれている

**失敗仮説**: git diff ISSUE.md で確認した変更が、ISSUE-511 ステータス行以外に副作用変更を含む場合、コミット対象が指示から外れる。

**実証手段**: git diff ISSUE.md の完全出力確認（full output 既取得）

**実証結果**:
- full output（29.7KB）を確認したところ、単一ハンク
- ISSUE-511 のステータス行のみ変更
- 削除行と挿入行の差分は記述追加のみ（行外側は同一）
- 他ファイル・他行変更なし

**判定**: 検証合格（意図しない副作用変更なし）

---

### 想定失敗シナリオ 2：git add コマンドで意図したファイルのみ staging されていない

**失敗仮説**: git add 実行後に git diff --cached --stat で 1 ファイル・1 行のみ確認不可

**実証手段**: git add ISSUE.md 実行後に git diff --cached --stat で検証予定（事前確認不可）

**判定**: 実行後検証（本スキルの結果確定まで保留）

---

### 想定失敗シナリオ 3：Heredoc による git commit 投入でメッセージが正しく反映されない

**失敗仮説**: Heredoc 形式で以下が起きる可能性：
- 改行が変換される
- 署名行の最後の改行が消える
- Co-Authored-By / Claude-Session 行が本文に混入する

**実証手段**: git commit 実行後に git log -1 --pretty=fuller で以下を確認予定：
- commit message に件名・本文・署名が正確に含まれるか
- Co-Authored-By / Claude-Session が trailer 形式で正しく格納されるか
- 末尾に余計な改行やスペースが入らないか

**判定**: 実行後検証（本スキルの結果確定まで保留）

---

### 想定失敗シナリオ 4：git status -sb 報告時に実際の状態が指示と異なる

**失敗仮説**: コミット後に git status -sb が実行されず、コミット状態の確認が抜ける

**実証手段**: git commit 実行直後に git status -sb を実行し、出力を確認予定

**判定**: 実行後検証（本スキルの結果確定まで保留）

---

## 証拠先行検証（実行前確認可能な項目）

### 検証項目 A：git status --porcelain の確認

**実証取得済**: M ISSUE.md のみ（指示の前提に合致）

**判定**: 合格

---

### 検証項目 B：git diff --stat の確認

**実証取得済**: 1 insertion(+), 1 deletion(-) のみ

**判定**: 合格

---

### 検証項目 C：差分内容の確認

**実証取得済**: full output により ISSUE-511 ステータス行のみ変更確認

**判定**: 合格

---

### 検証項目 D：heredoc フォーマットの構文確認

**実証取得済**: Bash Heredoc 構文は標準仕様に従う

**判定**: 合格

---

## 実行計画（変更検証後のステップ順序）

1. git status --porcelain 確認 → 合格
2. git diff --stat 確認 → 合格
3. git diff ISSUE.md 確認 → 合格
4. 予定: git add ISSUE.md 実行
5. 予定: git diff --cached --stat 確認
6. 予定: git commit 投入
7. 予定: git log -1 --pretty=fuller で署名行確認
8. 予定: git status -sb 報告

---

## 反映（事前確認可能なシナリオ）

- シナリオ 1：合格。変更内容に副作用なし。
- シナリオ 2～4：実行後検証に委ねる（commit 実行時に確認予定）

---

## 残存リスク特定

**本スキル範囲外、後続検証で委ねるべき項目**:

1. Heredoc による commit メッセージ投入の正確性
2. 署名行の trailer 形式による格納確認
3. コミット後のリポジトリ状態が指示通りであるかの最終確認

**リスク軽減手順**: commit 実行直後に git log -1 と git status -sb で検証予定。

