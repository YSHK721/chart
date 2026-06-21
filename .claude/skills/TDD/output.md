# TDD 実行記録 — Trade Markers v7（ホバー減光・カーソル画素近接判定）

対象: `indigators/indicator_ui/web/js/adapter/front/trade_markers_renderer.js` の `_onCrosshair` 改修。
フェーズ: 実装パイプライン フェーズ3「Red/Green」のみ（Refactor はフェーズ4・別担当）。

## §1 要件分析

| 要素 | 内容 |
|---|---|
| 機能 | カーソル画素（param.point）と各マーカー画素の 2 次元距離で最近傍マーカーが許容半径内ならそのトレード i を `_highlight` にする |
| 目的 | hoveredObjectId 単独依存による「ホバー減光発火の不規則」を是正（§13 v7） |
| 入力 | `param`（{point:{x,y}, hoveredObjectId}）／`this._pairs`（i/entry/exit の time・price）／`chart.timeScale().timeToCoordinate`・`mainSeries.priceToCoordinate` |
| 出力 | `_highlight`（trade i または null）→ 単一 `_render()` で marker 減光・ペア線・v6 ローソク減光に連動 |
| 制約 | 距離尺度 Math.hypot 単一固定・`_HIT_RADIUS_PX=12`・null 安全（throw 禁止・hoveredObjectId フォールバック）・tie は小さい i 優先・単一 _render 不変（C2）・後方互換 |
| 対象外 | canvas 実描画・実 hover の体感（ブラウザ確認）／Refactor（フェーズ4）／simulator・presenter（変更不要） |

## §2 テストケース設計（v7 11 件・AAA・境界値網羅）

| TC | 種別 | 設計根拠 |
|---|---|---|
| near within radius highlights | 正常系 | 近接ヒット＋非ハイライト減光発火 |
| far outside radius → null | 異常系（境界外） | 近接→遠方の状態遷移で highlight 解除を検証（弱 assertion 回避） |
| radius boundary 12 hit / 13 not | 境界値 | 半径ちょうど採用・超過非採用 |
| nearest among candidates | 正常系 | 最近傍選択 |
| tie-break lower index | 境界値（同値） | 同距離タイ＝小さい i 決定論 |
| exit-side marker triggers | 正常系 | exit.price からの画素でも発火 |
| param.point undefined → fallback | 異常系（null 安全） | hoveredObjectId フォールバック |
| coord API null → fallback | 異常系（null 安全） | 座標 null フォールバック |
| param null no-throw | 異常系（null 安全） | throw せず highlight 解除 |
| C2 single _render | 不変条件 | setMarkers 1 回・highlight 発火 |
| backward compat（座標 API 非提供） | 後方互換 | hoveredObjectId 経路・throw なし |

## §3 🔴 Red 結果

- 実装事前不在の実証（grep）: `param.point` 参照 0 件・`_HIT_RADIUS_PX`/`Math.hypot` 0 件・`this._chart` 未保持 → v7 機能は事前不在。
- 初回実行: `fail 3 / pass 311`。**8 件のヒット系が現状実装でも Pass**（assertion 弱体＝AP.1 R-7 の疑い）を検出。
- 是正: ヒット系テストに「非ハイライト marker が減光色になる」アンカーを必須化。
- 強化後実行: `fail 7 / pass 307`。失敗 7 件は全て point 近接ヒット系（期待された理由で失敗＝v7 機能の不在）。Pass 4 件は hoveredObjectId フォールバックの後方互換テスト（現状実装でも動くのが正当・AP.1 R-2 非該当）。

### Red 観測ゲート（4 軸）
| 軸 | 判定 |
|---|---|
| ① 過剰実装 | 非該当（実装未着手で Red 観測） |
| ② 成功テスト先行（AP.1 R-2） | 非該当（v7 新機能テストは全失敗。Pass は後方互換アンカー） |
| ③ 実装の事前残存 | 非該当（grep で事前不在を実証） |
| ④ assertion 弱体 | 検出→是正済（fail 3→fail 7 へ強化し v7 機能の不在を検出可能化） |

## §4 🟢 Green 結果

- 実装: `_HIT_RADIUS_PX=12` 定数・constructor で `this._chart` 保持・`_onCrosshair` で `_nearestTradeByPixel` を最優先しフォールバックに hoveredObjectId・無ければ null。`_nearestTradeByPixel` は各 pair の entry/exit 画素と point の `Math.hypot` を測り最近傍が半径内なら i を返す（座標 null スキップ・厳密 `<` で先頭優先）。
- 結果: v7 11 件すべて緑。全 **314 件緑 / fail 0**（ベースライン 303 + v7 11）。回帰破壊なし。
- 最小性: 近接判定追加のみ。_render/_applyCandleDimming 等の既存経路本体は無変更（C2 不変）。setData 直呼びなし（upstream 隔離維持）。
- ビルド: `node build.mjs` 成功（exit 0）。

## §6 完了判定

| 項目 | 判定 |
|---|---|
| テスト存在・実行可能 | ✔ v7 11 件（node:test） |
| Red/Green 出力 | ✔（Refactor はフェーズ4・別担当） |
| カバレッジ（正常/境界/異常/不変/後方互換） | ✔ 11 ケースで網羅 |
| テスト名が機能・期待を記述 | ✔ |
| 回帰（全テスト緑） | ✔ 314/314・build 成功 |
| 横断アンチパターン | 非該当（テスト改変・skip・カバレッジ偽装なし） |

## 違反リスト
**空集合**（強制ルール違反なし。初回 assertion 弱体は Red 段階で是正済）。

## 残存（フェーズ4 引き継ぎ）
- tie-break の配列順依存（`this._pairs` が i 昇順でない場合の堅牢化）は Refactor 候補。現状は presenter 出力が i 昇順で実害なし。
- canvas 実描画・実 hover の発火規則性・`_HIT_RADIUS_PX` 体感調整はブラウザ確認に委譲。
