# prompt-validation-workflow Input

## 指示内容の要約
リポジトリ /workspaces/app（ブランチ develop）で、残りの未ステージ/未追跡ファイルを4件の原子的コミットに分割投入。各コミットは Conventional Commits 形式で、ファイルは明示パスで `git add` する。

## 依頼内容の詳細

### コミット1（chore: gitignore）
- Files: `.gitignore`
- Commit message:
```
chore(backtest): 突合用throwaway生データとfixture生成物をgitignore

backtest/tests/confirmation/（179MBの突合用生MT5データ・使い捨てスクリプト）と、
fixture 配下の OS/Python 生成物（.DS_Store / __pycache__）を追跡対象外にする
（!backtest/tests/fixtures/** の再包含を打ち消す）。
```

### コミット2（test: 2026-01 オラクル fixture）
- Files: `backtest/tests/fixtures/mt5/ma_slope_jp225_202601/expected/report.json`
- Commit message:
```
test(backtest): 2026-01 every-tick MT5突合オラクル fixture を追加

実MT5 every-tick（JP225 MA_Slope・2026-01）の deal/stats オラクル（report.json）。
既存 ma_slope_jp225_202501 fixture と同形式。ISSUE-017〜020 の literal bit-exact 突合の
参照基準（trades 1444・net -4649・balance 5351）。
```

### コミット3（docs: backtest 設計文書）
- Files: `.doc/backtest/BACKTEST_CLEAN_ARCH.md .doc/backtest/BACKTEST_DESIGN.md .doc/backtest/BACKTEST_METRICS.md .doc/backtest/BACKTEST_SPEC.md .doc/backtest/BACKTEST_PROCESS.md`
- Commit message:
```
docs(backtest): バックテスト設計文書一式を追加

CLEAN_ARCH / DESIGN / METRICS / SPEC / PROCESS。バックテストエンジン実装の
設計・指標・仕様・処理フローの一次文書。
```

### コミット4（docs: testing-notes）
- Files: `docs/testing-notes.md`
- Commit message:
```
docs: テスト/TDD 役割ノート(testing-notes)を追加
```

## 除外対象（ステージしてはいけない）
1. `.claude/projects/` — Claude ランタイムメモリ
2. `.claude/skills/prompt-validation-workflow/output.md` — スキル出力
3. `.claude/skills/upstream-input-validation/output.md` — スキル出力
4. `.doc/indicator-management-ui/INDICATOR_CALC_MODEL.md` — backtest 範囲外

## 要件・制約
- 禁止コマンド検出時は即中断
- 各コミット：明示パスで `git add`（`-A` / `.` 禁止）
- Conventional Commits 形式
- フッタ： Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
- リモート push 禁止
