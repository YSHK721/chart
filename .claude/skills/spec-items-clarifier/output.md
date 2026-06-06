# Spec Items Output Template

S-5「仕様書出力」で全 9 項目確定後、本テンプレート構造に従って成果物を出力する。
読み込みを省略した出力生成は §4 強制ルール「出力テンプレート参照」で禁止される。

---

## 1. 出力フォーマット（必須構造）

```
━━━━━━━━━━━━━━━━━━━━━━━━━
仕様書：[機能名]
生成日：[YYYY-MM-DD]
━━━━━━━━━━━━━━━━━━━━━━━━━

1. Objective / Background
   [Item 1 の確定値（references/items.md Item 1「出力形式」に従う）]

2. Scope / Out of Scope
   [Item 2 の確定値（references/items.md Item 2「出力形式」に従う）]

3. Assumptions
   [Item 3 の確定値（references/items.md Item 3「出力形式」に従う）]

4. Constraints
   [Item 4 の確定値（references/items.md Item 4「出力形式」に従う）]

5. Input / Trigger
   [Item 5 の確定値（references/items.md Item 5「出力形式」に従う）]

6. Processing Logic
   [Item 6 の確定値（references/items.md Item 6「出力形式」に従う）]

7. Target Entities
   [Item 7 の確定値（references/items.md Item 7「出力形式」に従う）]

8. Output / Result
   [Item 8 の確定値（references/items.md Item 8「出力形式」に従う）]

9. Exception Handling
   [Item 9 の確定値（references/items.md Item 9「出力形式」に従う）]

━━━━━━━━━━━━━━━━━━━━━━━━━

## 選択サマリー（設計判断の記録）

| 項目 | 選択 | 採用した設計判断 |
|------|------|----------------|
| Item1 | [A/B/C] | [判断の要約] |
| Item2 | [A/B/C] | [判断の要約] |
| Item3 | [A/B/C] | [判断の要約] |
| Item4 | [A/B/C] | [判断の要約] |
| Item5 | [A/B/C] | [判断の要約] |
| Item6 | [A/B/C] | [判断の要約] |
| Item7 | [A/B/C] | [判断の要約] |
| Item8 | [A/B/C] | [判断の要約] |
| Item9 | [A/B/C] | [判断の要約] |

## 未確定事項（TBD）

| 項目 | 内容 | 決定者 | 期限 |
|------|------|--------|------|
| [項目] | [未確定の内容] | [TBD] | [TBD] |

（TBD が存在しない場合は本表に「該当なし」と明示する）

━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 2. 必須遵守事項

| 項目 | 遵守事項 |
|------|----------|
| Item 1〜9 の構造 | `references/items.md` の各 Item「出力形式」に準拠する。フィールド名・表構造を改変しない |
| 選択サマリー | 9 行（Item1〜Item9）すべてを記入する。1 行も省略しない |
| TBD 表 | 該当なしの場合も表自体は出力する。表本体に「該当なし」と記述する |
| 区切り線 | `━━━` 行は省略しない（仕様書の境界マーカーとして機能する） |
| 機能名・生成日 | 1 行目のヘッダーに必ず記入する。生成日は ISO 8601 形式（YYYY-MM-DD） |

## 3. アンチパターン（出力時の典型ミス）

- 1〜9 のうち一部を「省略可」と判断して空欄にする
- 「選択サマリー」を「最終仕様だけで十分」として削除する
- TBD が無いことを理由に表ごと削除する
- `references/items.md` の Item 出力形式と異なる構造（独自フォーマット）で記述する
- ユーザー選択（A/B/C）と仕様テキストが対応しない（推測補完が混入する）
