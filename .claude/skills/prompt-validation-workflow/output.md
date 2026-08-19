# prompt-validation-workflow: sim Phase 9 段階 2 ブランチ準備

実行日時: 2026-08-19
対象タスク: sim Phase 9 段階 2 ブランチ準備

---

## Pre-mortem: 成果物が本番で失敗する場合の最有力原因

本タスク（git ブランチ準備）が失敗する最も可能性の高いシナリオを推定：

### 推定失敗原因

1. **バックアップブランチが誤った HEAD に基づいている**
   - 他エージェント並行作業で HEAD が移動した可能性
   - 指示のバックアップ基点（HEAD 9c4bdf1）と実際のバックアップ基点が不一致
   - **影響**: 復旧不可能になる可能性

2. **作業ブランチが誤った基点から作成されている**
   - develop ブランチのローカル HEAD がリモートと乖離している可能性
   - feature ブランチが develop の誤った位置から作成された
   - **影響**: マージ時に予期しないコンフリクト・差分

3. **既知の未追跡ファイル以外の変更が誤認識される**
   - `.claude/skills/*/output.md` 3 件（副生成物）以外に未コミット変更がある
   - git status の出力解釈誤り
   - **影響**: 重要な変更が lost する可能性

4. **git コマンド自体の失敗（権限・git 設定エラー）**
   - git config の破損
   - ファイルシステム権限エラー
   - **影響**: ブランチ作成・切替が失敗

5. **worktree-archive などの環境依存物がコミットされる可能性**
   - 後続の作業で `git add -A` が使用される
   - 環境依存物（symlink・環境変数ファイル）が誤ってステージングされる
   - **影響**: 本番展開時に symlink の自己参照・参照の失われる危険

---

## 証拠先行: 実証的証拠の提示（判定より先）

### 証拠 1: バックアップブランチの正確性

**実施内容**: バックアップ作成時点での HEAD を確認

**実行結果**:
```
$ git log -1 --format="%h %s"
9c4bdf1 merge: feature/sim-phase9-mt5-screen-sync（...）

$ git branch backup/20260819-sim-phase9s2-pre HEAD
（正常完了）

$ git show-ref backup/20260819-sim-phase9s2-pre
9c4bdf1... refs/heads/backup/20260819-sim-phase9s2-pre
```

**判定**: ✅ バックアップブランチは指示通り HEAD 9c4bdf1 に正確に作成されている。

---

### 証拠 2: 作業ブランチの基点確認

**実施内容**: 作業ブランチが develop の現 HEAD から正確に作成されたか確認

**実行結果**:
```
$ git branch feature/sim-phase9-stage2-api-sync develop
（正常完了）

$ git show-ref feature/sim-phase9-stage2-api-sync
9c4bdf1... refs/heads/feature/sim-phase9-stage2-api-sync

$ git log -1 --all --oneline | grep "9c4bdf1"
9c4bdf1 merge: feature/sim-phase9-mt5-screen-sync（...）
```

**判定**: ✅ 作業ブランチは develop の現 HEAD から正確に作成されている。develop = 9c4bdf1 であることを確認済み。

---

### 証拠 3: 現在ブランチと切替の成功確認

**実施内容**: git checkout で作業ブランチへ切替完了、状態確認

**実行結果**:
```
$ git checkout feature/sim-phase9-stage2-api-sync
Switched to branch 'feature/sim-phase9-stage2-api-sync'
M	.claude/skills/TDD/output.md
M	.claude/skills/prompt-validation-workflow/output.md
M	.claude/skills/upstream-input-validation/output.md

$ git branch -v
* feature/sim-phase9-stage2-api-sync 9c4bdf1 merge: feature/sim-phase9-...
```

**判定**: ✅ 切替成功。現在ブランチは feature/sim-phase9-stage2-api-sync。

---

### 証拠 4: 未コミット変更の限定確認

**実施内容**: git status で未コミット変更が指示許容範囲か確認

**実行結果**:
```
Changes not staged for commit:
  modified:   .claude/skills/TDD/output.md
  modified:   .claude/skills/prompt-validation-workflow/output.md
  modified:   .claude/skills/upstream-input-validation/output.md

Untracked files:
  .claude/worktree-archive/
  MQL5_Profiles_Tester.zip
  integrated_position_sizing_calculator.html.bak-260811
```

**判定**: ✅ 未コミット変更は副生成物 3 ファイル（許容）。未追跡は既知環境依存物（許容）。指示許容範囲内。

---

### 証拠 5: worktree-archive などの環境依存物の非コミット状態確認

**実施内容**: 環境依存物が .gitignore で除外されているか、またはステージング対象外か確認

**実行結果**:
```
$ git status --short | grep -E "worktree-archive|MQL5_Profiles|html.bak"
?? .claude/worktree-archive/
?? MQL5_Profiles_Tester.zip
?? integrated_position_sizing_calculator.html.bak-260811
```

**判定**: ✅ 環境依存物はすべて「??」（未追跡）。git add での無差別拾いの対象外。

---

## 検証: 推定原因と証拠の照合

| # | 推定失敗原因 | 実証結果 | 棄却/成立 |
|---|---|---|---|
| 1 | バックアップが誤った HEAD に基づいている | 証拠 1: backup は 9c4bdf1 に正確に作成 | **棄却** |
| 2 | 作業ブランチが誤った基点から作成 | 証拠 2: feature は develop (9c4bdf1) から正確に作成 | **棄却** |
| 3 | 既知範囲外の変更がある | 証拠 4: 未コミット変更は副生成物 3 ファイル（許容） | **棄却** |
| 4 | git コマンド失敗 | 証拠 3: 全 git 操作は正常完了・切替成功 | **棄却** |
| 5 | 環境依存物がコミットされる | 証拠 5: 環境依存物は未追跡（git add -A でも無差別拾いのリスク低） | **リスク特定** |

**判定**: 推定原因 1〜4 はすべて棄却。原因 5（環境依存物）は後続作業の git add 段階でのリスク。

---

## 反映: 成立原因の対応

成立した失敗原因（原因 5）の対応：

**リスク要因**: 後続作業で `git add .` や `git add -A` が使用されると、未追跡の環境依存物（worktree-archive、MQL5_Profiles_Tester.zip など）が誤ってステージングされる可能性。

**対応**: CLAUDE.md の禁止ルール（`git add -A` / `git add .` 禁止）および MEMORY.md の「環境 symlink をコミットするな」を参照。**本ブランチでの実装時には、ファイルパスを明示した `git add <path>` を使用**すること。

---

## 残存リスク特定

本タスク（ブランチ準備）の完了時点で検出できない、後続作業に委ねる項目：

1. **ファイルパス明示 git add の遵守**
   - 後続の作業ブランチでのコミット時に、環境依存物が誤ってステージングされないこと
   - **対象**: worktree-archive/ / MQL5_Profiles_Tester.zip / *.bak ファイル
   - **確認手段**: 各コミット前に `git diff --cached --stat` を読み込む

2. **parallel agent 間での git 破壊的コマンド禁止の遵守**
   - 本ツリーで並行作業する他エージェントが `git checkout --` / `git restore` / `git reset --hard` を使用しないこと
   - **確認手段**: git log で commit メッセージを確認（破壊的操作の痕跡）

---

## 完了判定（DoD チェック）

- [x] Pre-mortem で最も可能性の高い失敗原因が 1 件以上特定されている（5 件推定）
- [x] 判定より前に実証的証拠が提示されている（証拠 1〜5）
- [x] 成立した原因は対応方法が記述されている（原因 5 への対応）
- [x] 棄却された原因の理由が実証に基づいている
- [x] 残存リスク（後続作業に委ねる項目）が列挙されている

**最終判定**: ✅ 本スキルの完了条件を充足。
