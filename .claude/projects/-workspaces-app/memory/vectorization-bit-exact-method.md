---
name: vectorization-bit-exact-method
description: indigators 指標コアのベクトル化で必須の 1:1 ビット一致検証手順とユーザーの承認フロー
metadata:
  type: feedback
---

indigators/ の各指標コア（MQL4/5 移植）の計算ループをベクトル化（高速化）する際、ユーザーは「速度より 1:1 ビット一致（元挙動の厳密再現）」を最優先する。

**Why:** これらは MQL 指標の 1:1 移植であり、SPEC が bit-exact 再現を保証境界としている。共有モジュール（profit_system / mql_builtins）は複数指標が依存するため、出力が 1bit でも変われば破壊的変更になりうる。

**How to apply:**
- 着手は「課題提示 → ユーザーの『承認する』 → 実装」のフロー。提示は認知負荷最小版（CLAUDE.md 準拠）。
- 実装前に必ず等価性ハーネスを作る: 旧ループ実装と新ベクトル実装を並べ、乱数掃引（数十万要素）＋**退化/敵対ケース**（flat 窓・span=50・pivot 一致値・整数タイ多発・両 direction 等）で要素ごとビット一致（nan-aware）を 0 件確認。
- 浮動小数の**加算順序を保持**する（窓和は `period` 回シフト加算で古→新順を再現／加重合算は左結合順を維持）。`a-b == -(b-a)` の IEEE754 厳密性、半整数演算の丸め非発生を根拠にできる。
- max/min は順序非依存ゆえ `sliding_window_view` で安全。総和は cumsum 不可（順序変化）、シフト加算で順序保持。
- 実装後: 全**依存指標**の pytest を通し、`git stash` で pre-change ベースラインと出力一致＆ベンチ比較。
- 逐次依存の漸化式（EMA/Wilder/SMMA/arctan 隣接差）は素朴ベクトル化不可・1:1 リスクのため対象外とする。
- 実績: profit_volatility / profit_rmm(_macd) の採点 / mql_builtins 窓(MFI 188x・WPR 51x・Stoch 40x) / profit_oscillator RVI(314x) / profit_oscillator2 採点・RCI を全てビット一致で完了。
- 注: profit_oscillator と profit_oscillator2 は**別指標**（複製ではない）。共通は再公開された共有プリミティブのみ。
