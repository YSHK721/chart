# upstream-input-validation 実行結果

## 上流入力の整理

| 種別 | 件数 | 摘要 |
|---|---|---|
| 依頼者指示 | 1 | コミット内容・件名・本文・署名フォーマット明示 |
| 他者レビュー指摘 | 0 | 該当なし |
| 前段成果物 | 0 | 該当なし |
| 既存合意の引き継ぎ | 0 | 該当なし |

**小計**: 上流入力 1 件（依頼者指示のみ）

---

## 前提抽出

### 上流入力 #1：コミット指示

**主張内容**: ISSUE.md の 1 行変更（ISSUE-511 ステータス行）をコミット。件名・本文・署名フォーマットを指定。

**暗黙の前提**:
1. ISSUE.md が M（modified）状態で存在する
2. `git diff --stat` で 1 行変更（1 insertion、1 deletion）のみ
3. `git diff ISSUE.md` が ISSUE-511 ステータス行の変更のみ（無関係な行変更がない）
4. Heredoc による git commit は宣言通りにメッセージが投入される
5. Co-Authored-By、Claude-Session 署名フォーマットが git commit メッセージに正しく反映される

**独立検証可能性**: ✅ 全前提とも git status・git diff・git commit 実行により客観的に確認可能

---

## 証拠先行検証

### 前提 1：ISSUE.md が M 状態で存在する

**実証手段**: `git status --porcelain`

**実証結果**:
```
M ISSUE.md
```

**判定**: ✅ 実証取得（M ISSUE.md のみ、他ファイルなし）

---

### 前提 2：git diff --stat で 1 行変更のみ

**実証手段**: `git diff --stat`

**実証結果**:
```
ISSUE.md | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)
```

**判定**: ✅ 実証取得（1 insertion、1 deletion のみ）

---

### 前提 3：git diff ISSUE.md が ISSUE-511 ステータス行のみ

**実証手段**: `git diff ISSUE.md` の全体確認

**実証結果**: 確認済み（出力は 29.7KB・full output に保存済み）。ISSUE-511 のステータス行のみ変更で、他行への変更なし。

**判定**: ✅ 実証取得（1 ステータス行変更確認）

---

### 前提 4・5：Heredoc フォーマット・署名の正しい投入

**実証手段**: git commit 実行後に `git log -1 --pretty=fuller` で検証予定（事前確認不可）

**判定**: ⏳ 実行後検証（本スキルの結果確定まで保留）

---

## 判定結果

| 上流入力 | ステータス | 根拠 |
|---|---|---|
| コミット指示 | ✅ 採用 | 前提 1〜3 の実証取得。前提 4・5 は commit 実行後に git log で検証予定 |

---

## 残存リスク

- **Heredoc メッセージ投入**：commit 実行後に `git log -1 --pretty=fuller` で signature 行の正確性を確認すること
- **署名フォーマット**：末尾 2 行の改行・インデント・UTF-8 を git log で視覚確認すること

