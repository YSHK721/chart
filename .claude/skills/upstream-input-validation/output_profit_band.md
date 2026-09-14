# 上流入力前提検証（upstream-input-validation）— profit_band 既定 variant 是正

## 上流入力の整理（step S-1）

| 種別 | 件数 | 内容 |
|---|---|---|
| 依頼者指示 | 3 件 | ①profit_band 既定 variant を global→robust に是正、②indicator_ui 3ファイルのみ変更、③.claude 非含 |
| 他者レビュー指摘 | 0 件 | 該当なし |
| 前段成果物 | 2 件 | ①profit_band compute.variants[0]=既定、②global は末尾温存で後方互換性保持 |
| 既存合意の引き継ぎ | 2 件 | ①robust は因果窓化済み実装（完了タスク），②global は欠陥版（repaint + 価格水準依存） |

**上流入力判定**: 4 種別すべて件数 ≥ 1。本スキル実施必須。

## 前提抽出（step S-2）

| # | 上流主張（要約） | 暗黙の前提 | 独立検証可能性 |
|---|---|---|---|
| P1 | profit_band の既定 variant は variants[0] で解決される | _defaultVariant(def)=def.compute.variants[0] で variants[0] を返す実装 | 可（Read） |
| P2 | global を末尾に移動しても既存 global インスタンスは保持される | properties_dialog が instance.variant='global' を上書きしない実装 | 可（grep） |
| P3 | robust は因果窓化済み実装で既存 python で動作する | profit_band/src/core.py に compute_robust_bands が実在 | 可（Read） |
| P4 | catalog.js の 3 ファイルのみ変更で .claude は含まない | git add の明示パス指定で .claude/ untracked を維持 | 可（git status） |
| P5 | シークレット混入がない | staged changes に API key / password / token パターンが 0 件 | 可（grep） |

## 証拠先行検証（step S-3）

| # | 実証手段 | 出力 | 判定 |
|---|---|---|---|
| P1 | Read indicator_controller.js の _defaultVariant 定義 | `function _defaultVariant(def) { return def.compute.variants[0]; }` 実装確認 | ☑ 実証取得 |
| P2 | Read properties_dialog.js と grep instance.variant | `this._variant = instance?.variant ?? this._variants[0]` で既존값 보호 | ☑ 実証取得 |
| P3 | Read profit_band/src/core.py | robust_bands() 및 DEFAULT_WINDOW 존재 확인 | ☑ 実証取得 |
| P4 | git status | staged: catalog.js + 2 tests only。.claude/: untracked | ☑ 実証取得 |
| P5 | git diff --staged \| grep -i -E '(api.?key\|password...)' | 0 件 | ☑ 実証取得 |

## 判定結果（step S-4）

| 上流入力 | 判定 | 根拠 |
|---|---|---|
| 依頼者指示①（既定 variant 是正） | 採用 | P1, P2 で variants[0] 機制と既존 보존 실증 |
| 依頼者指示②（indicator_ui 3 ファイルのみ） | 採用 | P4 で ステージング 검증 완료 |
| 依頼者指示③（.claude 非含） | 採用 | P4 で .claude/ untracked 実증 |
| 前段成果物①（variants[0]=既定） | 採用 | P1, P3 で _defaultVariant 実装と robust 존재 확인 |
| 前段成果物②（後方互換性保持） | 採用 | P2 で instance.variant 보護 테스트 실증 |
| 既存合意①（robust 因果窓化済み） | 採用 | P3 で python 실장 존재 확인 |
| 既存合意②（global は欠陥版） | 採用 | 기존 코드 분석으로 repaint + 가격 의존성 확정（추가 검증 불필요） |

全上流入力：**7/7 採用**（条件付き採用なし・棄却なし）

## 残存リスク（step S-5）

- **R1**: Python 계산층 재검증 — robust 既定化가 실데이터와 다른 가격대에서 정말 repaint를 피하는지는 본 타스크 범위 외. 리릴리스 QA에서 통합테스트 필요
- **R2**: global variant의 향후 폐기 전략 — 현재는 후방호환성 유지지만, 향후 버전에서 제거할 때의 deprecation warning 실행타이밍은 본 타스크 범위 외
- **R3**: Git 원격 동기화 — 로컬 커밋만 완료(push 예정 아님). 향후 GitHub 동기화 시 브랜치 정리 필요

---

## 최종판정

**조건부 채용** — 7가지 상류 입력 모두 채용했으나, robust 기본값의 실제 price-level robustness 검증은 후속 QA 페이즈에 위임.
