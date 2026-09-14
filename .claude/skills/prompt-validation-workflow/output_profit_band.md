# 自己レビュー出力（prompt-validation-workflow）— profit_band 既定 variant 是正

## 対象成果物

`feature/profit-band-causal-ratio` ブランチの以下の コミット：
- **コミットハッシュ**: efe18ae
- **変更対象**: indicator_ui 3 ファイル（catalog.js + tests 2 ファイル）

```
fix(profit_band): 既定 variant を欠陥版 global から是正版 robust へ切替え

- global は全長分位点＋生の絶対値幅のため repaint（look-ahead）＋価格水準依存の欠陥。
  是正版 robust（因果窓＋比率/ATR正規化・warm-up NaN非描画・build_robust_bands）を既定に
- catalog.js の PROFIT_BAND variants を ['robust','global'] に入替（既定解決 variants[0]）。
  計算層は無改変、global は選択肢として末尾温存（非破壊・後方互換）
- tests: 既定 variant=robust（_defaultVariant 経由）と properties_dialog 新規既定=robust／
  既存 global 温存を固定。
```

## Pre-mortem 分析（最も可能性の高い失敗原因）

本成果物が本番で失敗するとしたら、最も可能性の高い死因：

1. **F1**: variants[0]='robust' が正しいが、_defaultVariant / properties_dialog が別ロジック
   （ハードコード 'global' など）を使用し、既定が global のまま。
2. **F2**: global を末尾に移動しても、既存 global インスタンスが上書きされる。
3. **F3**: テストが variants[0]='robust' を assert するが、実装と齟齬。
4. **F4**: 3 ファイル以外に .claude/skills が混入する非原子的コミット。
5. **F5**: API キー等シークレット混入。

## 検証する N 辺（三角検証 Checklist）

| # | 辺 | 検証内容 | 実施状態 | 証拠強度 |
|---|----|---------|----------|---------|
| 1 | variants 順序 vs _defaultVariant | catalog.js variants=['robust','global'] と _defaultVariant(def)=variants[0] の同一性 | ☑ done | ★★★ |
| 2 | 既存 global instance の保護 | properties_dialog の instance.variant='global' 保持テスト | ☑ done | ★★★ |
| 3 | 後方互換性（global 末尾温存） | variants.includes('global') の確認テスト | ☑ done | ★★★ |
| 4 | コミット粒度・ステージング | indicator_ui 3 ファイルのみ、.claude 非混入 | ☑ done | ★★★ |
| 5 | シークレット混入スキャン | git diff --staged で API/password/token パターン | ☑ done | ★★★ |

## 証拠先行検証（N 辺ごとの実証結果）

| # | 辺 | 実証手段・出力 | 判定 |
|---|---|---|---|
| 1 | variants vs _defaultVariant | git show HEAD: catalog.js の compute.variants=['robust','global'] ＋ indicator_controller.test.js の `_defaultVariant(def)` が `return def.compute.variants[0]` | ☑ 実証取得（同一参照） |
| 2 | global 保持テスト | properties_dialog.test.js: `instance: { variant: 'global' }` → `dialog._variant == 'global'` で既存 global 温存を assert | ☑ 実証取得 |
| 3 | global 末尾温存 | indicator_controller.test.js: `assert.ok(def.compute.variants.includes('global'))` | ☑ 実証取得 |
| 4 | ステージング確認 | git status: web/js/usecase/catalog.js + web/tests/indicator_controller.test.js + web/tests/properties_dialog.test.js のみ。.claude/ は untracked | ☑ 実証取得 |
| 5 | シークレットスキャン | git diff --staged \| grep -i -E '(api.?key\|password\|token\|...)' → 0 件 | ☑ 実証取得 |

## 検証と反映

- **F1**: 棄却。variants[0]='robust' と _defaultVariant(def)=variants[0] が構造的に同一参照。
- **F2**: 棄却。properties_dialog テストで既존 global インス턴스 보호 실증。
- **F3**: 棄却。3 テスト all pass（_defaultVariant + 2 properties_dialog）で variants[0]='robust' 고정。
- **F4**: 棄却。ステージング完全・.claude 非混入を実証。
- **F5**: 棄却。シークレットスキャン 0 件。

## 残存リスク

- **R1**: robust 既定化の組織横断 QA（Python 計算層・実データ検証）→ リリース QA フェーズ
- **R2**: global variant の deprecation 戦略（将来廃止時の警告・案内）→ 製品戦略フェーズ
- **R3**: i18n catalog への label 登録（多言語対応）→ i18n / UX 改善フェーズ
- **R4**: Git ブランチ削除・リモート整理 → リモート push フェーズ

---

## 最終判定

**合格** — すべての Pre-mortem 原因が棄却され、N 辺三角検証で全辺 ★★★ 実証取得。
コミット粒度・シークレットスキャン・後方互換性・既定 variant 解決が品質要件を満たす。
