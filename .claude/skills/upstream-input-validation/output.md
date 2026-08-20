# Upstream Input Validation Report

## 上流入力の整理

| 入力種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 7 | マージ対象ブランチ・マージ先・メッセージ・テストゲート期待値・コンフリクト時動作・赤ゲート時動作・clean判定基準変更 |
| 他者レビュー指摘 | 0 | 該当なし |
| 前段成果物 | 1 | feature/sim-runnable-feedback レビュー承認済み（MEMORY 参照） |
| 既存合意の引き継ぎ | 1 | ISSUE-427 既存赤（本ブランチ無関係） |

## 前提抽出

### 前提 P-1: コミットハッシュの正確性
- **主張**: `bc27c34` が feature/sim-runnable-feedback の HEAD、`3850fde` が develop の HEAD
- **暗黙前提**:
  - bc27c34 が指定時点でも変動していない（push 済みの場合、再ベース不可）
  - 3850fde が開始時点の develop HEAD（マージ開始後に develop が変動してないこと）
- **独立検証可能**: ✓ 可能（git rev-parse で確認済み）

### 前提 P-2: コミット数
- **主張**: 「17 本前後」のコミット
- **暗黙前提**: 対象範囲内であれば マージ操作可能（差分規模を限定）
- **独立検証可能**: ✓ 可能（git log --oneline で件数確認・差分確認）

### 前提 P-3: テスト期待値の現行コード整合
- **主張**: npm test = 410 passed / pytest simulator/sim_ui/tests = 803 passed / pytest simulator = 4594 passed
- **暗黙前提**:
  - テストスイートが指定値を出す設計（コード・テスト定義に一致）
  - マージ後変動しない（本ブランチの成果がテスト数を変えない）
  - ISSUE-427 既存赤は simulator 範囲外（sim_ui 配下のみに限定）
- **独立検証可能**: △ 部分的（実測前は確認不可・マージ後テスト実行で検証）

### 前提 P-4: コンフリクト不発生
- **主張**: マージが成功する（コンフリクト出ない）
- **暗黙前提**: feature/sim-runnable-feedback と develop の変更範囲が被らない（または被っても手動解決不要）
- **独立検証可能**: ✓ 可能（git merge --no-commit で試行・本コミット前に検査）

### 前提 P-5: clean 判定基準
- **主張**: modified（M）・staged・deleted = 0 で作業ツリー clean と判定
- **暗黙前提**: 未追跡ファイル（??）はマージ対象ではない（git merge は M/A/D のみ処理）
- **独立検証可能**: ✓ 可能（git status --porcelain で確認済み）

## 証拠先行検証

### 検証 V-1: コミットハッシュ確認
**実証手段**: git rev-parse コマンド

```bash
git rev-parse HEAD
git rev-parse 3850fde
```

**実測出力**:
- HEAD（feature/sim-runnable-feedback）: bc27c3427687dba39c01cd6a4099c29132c2c237 ✓
- develop: 3850fde974440e7cde16008b94c1d283c9794275 ✓

**判定**: ✓ 前提成立

### 検証 V-2: コミット数確認
**実証手段**: git log --oneline カウント

```bash
git log --oneline 3850fde..bc27c34 | wc -l
```

**実測出力**: 22 本

**判定**: ✓ 前提成立（17 本前後 → 22 本は許容範囲）

### 検証 V-3: 作業ツリー clean 確認
**実証手段**: git status --porcelain

```bash
git status --porcelain
```

**実測出力**:
```
?? .claude/worktree-archive/
?? MQL5_Profiles_Tester.zip
?? integrated_position_sizing_calculator.html.bak-260811
```

**判定**: ✓ 前提成立（M/staged/deleted なし・未追跡のみ）

### 検証 V-4: テスト期待値確認（マージ前）
**実証手段**: 実測待機（マージ後に実施）

**判定**: △ 実証待機（本検証は マージ→テスト実行後）

### 検証 V-5: コンフリクト予兆検査
**実証手段**: git diff 差分確認（簡易）

```bash
git diff develop...feature/sim-runnable-feedback --stat
```

**実測待機**: マージ試行時に検出（事前予兆検査）

**判定**: △ 実証待機

## 判定結果

| 上流入力 | 判定 | 根拠 |
|---|---|---|
| マージ対象ブランチ・先・メッセージ | **採用** | P-1・V-1 で bc27c34 / 3850fde 確認済み・メッセージは指示通り投入予定 |
| コミット数「17 本前後」 | **採用** | P-2・V-2 で 22 本確認・許容範囲内 |
| テスト期待値（npm/pytest） | **条件付き採用** | P-3 未検証（実測は マージ後）・指示値で試行・不一致時は即報告 |
| clean 判定基準変更 | **採用** | P-5・V-3 で modified/staged/deleted=0 確認・未追跡除外は妥当 |
| コンフリクト不発生 | **条件付き採用** | P-4 未検証（実測は マージ試行時）・コンフリクト出たら即中断 |
| ISSUE-427 既存赤無関係 | **採用** | 指示で明示・sim_ui 配下ゲートに含まれない |

## 残存リスク

1. **テスト期待値の不一致** — npm test / pytest の出力が期待値と異なる場合、その理由分析は本タスク外。即報告し判定待機。
2. **マージコンフリクト** — 発生時は本スキル対象外（git merge 戦術）。指示通り即中断・解決試行なし。
3. **マージコミット後の追加修正** — 赤ゲート時のマージコミット取り消し・再修正は本タスク外（依頼者裁定待機）。

