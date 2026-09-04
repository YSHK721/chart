# 銘柄仕様の供給経路 基本設計書（ISSUE-445 恒久策・ISSUE-368 の延長）

- 起票: 2026-08-25
- 対象 Issue: ISSUE-445（実 MT5 の JP225 銘柄仕様が fixture `case.yaml` と食い違う）
- 関連: ISSUE-368（銘柄同一性はデータ供給側の台帳）・ISSUE-446（MT5 ライブ接続）・
  `marketdata/symbol_spec.py` A-1 裁定（2026-08-20）・TBD-D（二重所在）
- 位置づけ: ISSUE-445「次の一手 3」の設計。**未承認・未実装**（本書は設計のみ）。

---

## 1. 何を解くのか（根本原因の定式化）

ISSUE-445 の切り分け（2026-08-25・確定）で判明した誤りは、値が 1 つ間違っていたことではない。
**「供給元が出力していない値を、人が権威台帳に書き足せる」構造**そのものである。

### RC-1: 銘柄仕様の権威が「人が書いた転記物」にある

> **本節は起票時（2026-08-25）の状態の記録であり、現在形ではない。** 段階 2 で権威を供給元
> スナップショットへ移し、**段階 3-E2（2026-08-26）で `case.yaml` の `symbol:` から重複 5 キーを
> 撤去した**（残るのは `name` のみ）。以下の記述はその是正前の姿である。根本原因の記録として
> 書き換えずに残す。

- `case.yaml` の `symbol:` ブロックには見出し「銘柄仕様 (実 MT5 由来の確定値)」が付くが、
  MT5 レポート（xlsx `Settings` 8 項目 / `tester.log` / `report.json`）に `contract_size` の
  記載は**一度も無い**（実測）。`contract_size: 10` は誤前提からの逆算であり、出所が無い。
- `case.yaml` 自身が冒頭で「本 case.yaml は人が読むための**メタ要約**であり、数値の最終
  オラクルは report.json 側」と宣言している。にもかかわらず
  `simulator/sim_ui/adapter/symbol_spec_catalog.py:8-10` は case.yaml を
  「結果に効く定数の**唯一のオラクル**」に指定した。**メタ要約が権威に昇格している。**
- 供給元（MT5 端末）と突き合わせる機構が無いため、誤りは検出されない。実際 2026-06-18 の
  fixture 作成から 2026-08-25 のライブ実接続まで、誤りは 2 か月以上検出されなかった。

### RC-2: `MaSlope` が参照実装 `.mq5` の `NormalizeLot` を持たない

- 参照実装 `expert/MA_Slope_EA.mq5:OpenPosition()` は
  `double volume = NormalizeLot(Lot);` を通してから発注する。`NormalizeLot` は
  `SYMBOL_VOLUME_MIN/STEP/MAX` を**実行時に読み**、`v < min` なら `v = min` へ持ち上げる。
- 移植先 `simulator/adapter/strategy/ma_slope.py:_build_order()` は
  `volume=cfg["lot_size"]` と入力値をそのまま使う（実測）。正規化段が丸ごと欠落している。
- この欠落が RC-1 の誤りを**相殺**した。バックテストで `lot` が単独で現れる箇所は無く、
  常に積 `lot × contract_size` として使われる（`domain/position.py` の `floating_pnl` /
  `required_margin`）。真値 `1.0 × 1.0` と誤値 `0.1 × 10` の積は等しく `1.0` である。
- `Order.validate()` は**バックテスト実行経路から呼ばれていない**（実測: `simulator` 配下の
  非テストコードでの `.validate(` 出現は `sim_ui/usecase/submit_job.py` と
  `usecase/account_engine.py` のみ）。`volume_min` 違反を捕まえる最後の網も機能しない。

### RC-1 と RC-2 の関係

RC-1 が誤りを**生み**、RC-2 が誤りを**隠した**。片方だけ直しても解決しない
（§6 の実測が示すとおり、RC-1 だけ直すと golden が壊れ、RC-2 だけ直しても誤値は残る）。

---

## 2. 設計方針（1 行）

**銘柄仕様は供給元のスナップショットだけを権威とし、人が数値を書く箇所を 0 にする。
そのうえで、供給元と独立な証拠（golden レポート）から機械導出した値と突き合わせるゲートを置く。**

「値を 10 → 1 に書き換える」は対症療法である。同じ誤りを次の銘柄・次の fixture で必ず繰り返す。

---

## 3. 構造（SOLID 写像）

### 3.1 現状の違反

| 原則 | 違反 | 実体 |
|---|---|---|
| SRP | `case.yaml` が 2 アクターを兼務 | 「テストケースの期待値記録」（変更起点＝ケース追加）と「銘柄仕様台帳」（変更起点＝ブローカーの仕様改定）が同一ファイルに同居 |
| DIP | 抽象の裏に真実が無い | `RunOptionsPort` は成立しているが、具体側 `SymbolSpecCatalog` が**リテラルを所有**し供給元へ繋がっていない |
| OCP | 銘柄追加が改変になる | 銘柄を増やすたびにカタログへ literal を書き足す。供給元から取り込む構造なら追加のみ |
| LSP | — | 違反なし |
| ISP | — | 違反なし |

### 3.2 恒久構造：供給の連鎖（1 方向・各段の所有者は 1 つ）

```
  [供給元] MT5 端末（OANDA-Japan MT5 Live）            ← 唯一の権威
      │
      │  tools/capture_mt5_symbol_spec.py（Windows VM 上で実行）
      │  ・mt5.symbol_info(sym)._asdict() と mt5.account_info()._asdict() を
      │    **丸ごと**落とす（人が値を選ばない・書かない）
      ▼
  [スナップショット] marketdata/symbol_specs/<server>/<symbol>.json
      │  ・機械生成物。ファイル冒頭に「自動生成・手で編集しない」を明記
      │    （既存の symbol_spec_generated.js と同じ規律）
      │  ・取得メタ（取得時刻 UTC / server / company / terminal build / symbol）同梱
      ▼
  [境界] SymbolSpecPort.spec_for(symbol) -> SymbolSpec     ← usecase
      │      実装: MT5SnapshotSymbolSpecRepository（adapter・読むだけ）
      ├──→ SymbolSpecCatalog（sim_ui・RunProfile を組む。リテラルを持たない）
      ├──→ ライブ発注アダプタ（将来・ISSUE-446 の Windows 側実装 1 個）
      └──→ tools/gen_js_parity_golden.py（JS へは生成物で配る＝既存規約）
```

### 3.3 検出ゲート（誤りを赤にする機構）

供給元と**独立な**証拠は golden レポートである。実行結果は仕様の関数だから、逆に仕様を導出できる。

```
  fixtures/mt5/<case>/expected/report.json（実 MT5 テスターの確定出力）
      │  Mt5ReportSpecDerivation（report から機械導出できる値**だけ**を導出）
      ▼
  derived = {
      contract_size    : profit / (Δprice × executed_volume) の一致検定
      executed_volume  : deals[].vol の集合
      digits           : deals[].price の観測小数桁の最大
      account_leverage : settings.leverage（"1:10"）
      currency         : settings.currency
  }
      │
      ├─ snapshot と一致 → 緑
      └─ 不一致 → 赤。人が裁定する（仕様改定か記録誤りか）
```

**このゲートがあれば ISSUE-445 は fixture 作成時点（2026-06-18）に赤で止まっていた。**

導出できない値（`volume_min` / `volume_step` / `volume_max` / `stops_level`）はゲートの対象外に
する。導出できないものを導出したふりをしない（憶測禁止）。これらは snapshot が単独の権威。

### 3.4 `leverage` の所在を是正する（新規発見・実測）

- ISSUE-445 が実測した `mt5.symbol_info('JP225')` のフィールドに `leverage` は**無い**
  （実測表は digits / point / trade_contract_size / trade_tick_size / trade_tick_value /
  volume_min / volume_step / volume_max / spread / trade_mode）。
- `tester.log:13` も `initial deposit 10000 JPY, leverage 1:10` と**口座の行**に記録している。
- したがって `leverage` は口座属性であり、`usecase/models.py:SymbolSpec` が 8 フィールド目に
  持っているのは SRP 違反である（変更起点が違う: 口座の契約 vs 銘柄の契約）。
- 本設計では snapshot を `symbol` / `account` の 2 セクションに分け、`leverage` は account 側から
  供給する。`SymbolSpec` からの分離自体は既存 IF（`build_interactor` の引数）に触れるため
  **段階 3 送り**とし、段階 2 では「供給元だけを正す（`leverage` は account セクションから引く）」に留める。

### 3.5 二重所在（TBD-D）の扱い

段階 2 完了後の台帳所在は次の 2 つになる。**供給元が 2 つあるので所在 2 は正しい**（1 供給元 = 1 台帳）。

| 台帳 | 供給元 | 権威範囲 |
|---|---|---|
| `marketdata/symbol_specs/OANDA-Japan-MT5-Live/JP225.json`（新設・機械生成） | OANDA-Japan MT5 | MT5 銘柄の全仕様 |
| `marketdata/symbol_spec.py:SYMBOL_SPECS`（現行・A-1 裁定値） | OANDA 証券 CFD | CFD の呼び値・表示桁 |

**A-1 裁定には触れない。** ISSUE-445 で崩れたのは「両者を分ける判別子として挙げた
`contract_size` の値」であって、「証券 CFD と MT5 が同一商品である」ことは依然として
**証明されていない**。同一性が実測で確定するまで統合しない。統合の検討は段階 3。

---

## 4. 参照実装の厳守（RC-2 の是正）

`MaSlope` に参照実装 `.mq5:NormalizeLot()` を移植する。追加する条件・境界は参照実装と 1:1 とし、
足さない／削らない。

```
参照実装 MA_Slope_EA.mq5:NormalizeLot(lot)
    min, max, step = SymbolInfoDouble(VOLUME_MIN / VOLUME_MAX / VOLUME_STEP)
    v = lot
    if step > 0 : v = MathRound(v / step) * step
    if v < min  : v = min
    if max > 0 and v > max : v = max
    digits = (step > 0) ? ceil(-log10(step)) : 2 ; digits < 0 → 0
    return NormalizeDouble(v, digits)
```

供給経路: `build_interactor` の `strategy_params` に `volume_min` / `volume_max` / `volume_step`
の 3 キーを追加する（既存 `point_size` / `digits` / `stops_level` と同じ扱い。他戦略は未参照のため無害）。

**現行値では恒等写像である**（`step=0.1, min=0.1, lot=0.1` → `round(0.1/0.1)*0.1 = 0.1`。
カタログ値 `step=0.01, min=0.01` でも `0.1`）。よって段階 1 単独で golden は不変になる想定であり、
これを段階 1 の通過条件として実測で確認する。

---

## 5. 段階分割（最小可逆段階を既定・各段の通過条件）

| 段階 | 内容 | 可逆性 | 通過条件 |
|---|---|---|---|
| **0** | 検出ゲートの新設のみ（`Mt5ReportSpecDerivation` ＋突合テスト）。値は 1 つも変えない | 追加のみ。既存 0 改変 | 突合テストが `contract_size` 不一致（10 対 1.0）を**赤として検出**する。CI 緑を保つため `xfail(strict=True)` で「既知の不整合」として固定し、段階 2 で外す |
| **1** | `NormalizeLot` の移植（RC-2）＋ `strategy_params` へ volume 3 キー追加 | 追加のみ。1 コミット revert で戻る | 全テスト緑・reconcile の `trades=1164` / `net=-6173.9` / `balance=3826.1` が**不変**（恒等写像の実証） |
| **2** | snapshot 導入 ＋ 参照先を snapshot へ切替（RC-1）。カタログと reconcile のリテラル撤去 | 1 コミット revert で戻る | 段階 0 の xfail が緑へ転じる／reconcile が §6 の実測どおり **bit-exact 不変**／全テスト緑 |
| **3** | `case.yaml` の `symbol:` ブロック撤去・`SymbolSpec` からの `leverage` 分離・TBD-D 統合の裁定 | **撤去を含む＝別ターンで改めて裁定** | 本書では通過条件を定めない（段階 2 完了後に、実測を添えて改めて起票する） |

**段階 2 の着手前提**: Windows VM 上で `capture_mt5_symbol_spec.py` を実行して snapshot を得ること。
ISSUE-445 の実測表を人が JSON へ書き写すのは **RC-1 の再生産**であり、行わない。

---

## 6. 設計の前提を実測で確定させた結果（2026-08-25・本設計の根拠）

リポジトリ無改変のまま `build_interactor` を 3 通りのパラメータで実走し、`trades` の
`(side, entry_time, entry_price, exit_time, exit_price, exit_reason, pnl())` を SHA-256 で比較した。

| 変種 | contract_size | lot | volume_min/step | trades | net | balance | trades sha256 |
|---|---|---|---|---|---|---|---|
| V0 現行 | 10.0 | 0.1 | 0.1 / 0.1 | 1164 | -6173.899999999994 | 3826.100000000006 | `2ca785cd…36e20` |
| V1 真値・正規化なし | 1.0 | 0.1 | 1.0 / 1.0 | **3315** | **-1885.32** | **8114.68** | `edcde6c0…9780a` |
| V2 真値・正規化あり相当 | 1.0 | **1.0** | 1.0 / 1.0 | 1164 | -6173.899999999994 | 3826.100000000006 | `2ca785cd…36e20` |

- **V0 == V2 は bit-exact**（sha256 一致）。真値へ是正しても、参照実装どおり lot を正規化すれば
  golden は 1 ビットも動かない。
- **V1 は壊れる**（trades 1164 → 3315・net が 1/10 側へ）。証拠金が 1/10 になりストップアウトが
  起きないためである。**値の是正だけを先行させてはならない**ことの実証。
- ゆえに段階 1（`NormalizeLot`）は段階 2（値の是正）の**前提**であり、順序は交換できない。

計測スクリプト: `scratchpad/spec_equivalence_probe.py`（リポジトリ外・読み取りのみ）。

---

## 7. 影響範囲

### 変更が及ぶ箇所（段階 1・2）

| ファイル | 変更 |
|---|---|
| `simulator/adapter/strategy/ma_slope.py` | `NormalizeLot` 相当を追加（段階 1） |
| `simulator/main/__init__.py` | `strategy_params` へ volume 3 キー追加（段階 1） |
| `tools/capture_mt5_symbol_spec.py` | 新設（段階 2・VM 実行） |
| `marketdata/symbol_specs/<server>/<symbol>.json` | 新設・機械生成物（段階 2） |
| `marketdata/symbol_spec_snapshot.py` | 新設・snapshot ローダ（依存ゼロ＝stdlib `json` のみ・段階 2） |
| `simulator/sim_ui/adapter/symbol_spec_catalog.py` | リテラル 8 定数 → snapshot 参照（段階 2） |
| `simulator/tests/fixtures/mt5/spec_derivation.py` | 新設・report 導出（段階 0） |
| `simulator/tests/integration/test_ma_slope_reconcile.py` | spec 供給を snapshot 経由へ（段階 2） |
| `simulator/sim_ui/tests/integration/test_run_options_mt5_gate.py` | オラクルを case.yaml → snapshot ＋導出ゲートへ（段階 2） |

### 非対象（YAGNI・触れない）

- `marketdata/symbol_spec.py:SYMBOL_SPECS` と A-1 裁定（§3.5）。`symbol_spec.py:24-35` が
  記録する「6 ファイル 18 か所」は CFD 側 `tick` の話であり、本設計は**そこに触れない**。
- JP225 以外の銘柄（dataset 実体が確定するまで追加しない）。
- ~~`Order.validate()` を実行経路へ結線すること（RC-2 とは別の欠落。段階 3 以降で別途裁定）。~~
  → **段階 3-C として実施済み（2026-08-26）**。`usecase/_execution.admit_orders` を発注受理の
  唯一の門として新設し、`RunBacktestInteractor` の 3 呼出点を通した。違反は送出する
  （拒否＋続行にしない — 原典 EA は `OrderSend` の前に自前で `NormalizeLot` を掛けており、
  不正発注はサーバへ到達しない＝MT5 の拒否挙動は**参照実装の定義域外**であるため、推測で
  作り込まない）。実測: 既存テストへの影響 0 件（違反検出 0・全走件数が着手前と同一）。

---

## 8. 未検証・残るリスク

- snapshot は 2026-08-25 時点の値であり、run（2026-06-18）時点の値ではない。両者が一致することは
  §6 の V0==V2 と ISSUE-445 の証拠 1〜3 で**結果として**確認できるが、「OANDA が間に仕様を
  変更していないこと」の直接証拠ではない。将来 OANDA が仕様を改定すれば §3.3 のゲートが赤になる。
  **これは欠陥ではなく設計意図**（無言の変更を見えるようにする）。赤が出たら人が裁定する。
- `digits` の導出（観測小数桁の最大）は、全価格がたまたま整数だった場合に過小評価する。
  本 fixture では `38325.7` 等が観測されるため成立する（実測）が、一般には成立しない。
  ゲートの対象は「snapshot より小さい桁が観測されないこと」の**片側検査**に留める。
- ISSUE-445「次の一手 2」（`history_deals_get` によるライブ側の追認）は未実施。段階 2 の
  capture 実行時に併せて取得すれば追認が済む。
