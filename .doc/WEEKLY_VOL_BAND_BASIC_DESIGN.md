# 週次ボラティリティ・バンド戦略 基本設計書

## 1. 文書情報

- 作成日：2026-06-25
- バージョン：v0.1.0
- 作成者：system-basic-design エージェント
- 承認者：（未承認・レビュー待ち）
- 入力一次情報：`/workspaces/app/.doc/WEEKLY_VOL_BAND_SPEC_v1_0.md`（v1.0 確定版）
- 整合対象（無改変）：既存クリーンアーキ（`simulator/domain` `simulator/usecase` `simulator/adapter` `simulator/framework` `simulator/main`）・既存ポート（`simulator/usecase/ports.py`・`simulator/usecase/optimize_ports.py`）・既存UC（`run_is_oos.py` `walk_forward.py` `optimize.py` `compute_stats.py`）
- 体裁の手本：`/workspaces/app/.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md`
- 変更履歴：
  - v0.1.0 (2026-06-25) 初版。週次ボラバンド戦略（仕様 v1.0）の基本設計。3関心3アクター分離（UC-WV1/2/3）・新規ポート4本・domain新規VO5本・統計検定 pure numpy 実装方針・既存無改変制約を文書化。

> **【状態注記 2026-07-18】** 本書に記載される推定・検証系システム（UC-WV1・UC-WV3）の実装は死滅コード監査により撤去済み（コミット 0b1a1bd）。現存は実行系（UC-WV2）と domain/usecase VO のみ。本文書は設計記録として保存する。

---

## 2. プロジェクト概要

> 出力元：S-1 要件分析と設計方針決定

### 2.1 システム概要

- **位置付け**：既存の committed バックテストエンジン（`simulator/`・クリーンアーキテクチャ・MT5 bit-exact 突合済・IS/OOS 分割 UC 整備済）の上に載る、日経225 現物指数CFD ロング専用「週次ボラティリティ・バンド戦略」の推定・執行・検証パイプライン。週末に半実現ボラから翌週のストップ/利確バンド幅を予測し、週初ロング・OCO 決済・金曜引け強制手仕舞いで運用し、採否を規定の統計検証手続き（SPA/Kupiec/Christoffersen）で一意決定する。
- **解決する業務課題**：(1) 週末ギャップリスクの排除（金曜引け強制手仕舞い）、(2) 損益比の事前確定（半実現ボラ予測由来の S/T/N を式で一意算出）、(3) データスヌーピングを統制した採否判定（IS/OOS 分割＋SPA でパラメータ選択の多重性を補正）。
- **3関心の分離**：本戦略は性質の異なる 3 つの関心を 3 アクター（3 UC）へ分離する。(WV1) 週末バッチ推定＝リスク推定担当、(WV2) 週次執行＝執行担当、(WV3) 検証＝検証担当。各 UC は仕様 §3.1（運用サイクル）・§3.2（検証手続き）に 1:1 対応する。

### 2.2 開発目的・背景

- **背景**：仕様書 v1.0（確定版）が S・T・N の算出式（§2.5）、検証手続き S1〜S6（§3.2）、Definition of Done（§6）を「同一入力から一意に再現される」決定論手続きとして確定済み。本設計はこの決定論手続きを、既存クリーンアーキの層責務・依存方向を崩さずに実装可能な粒度へ落とす。
- **達成したい目標**：(1) 既存エンジン・既存ポート・既存UC を無改変のまま、週次ボラバンド戦略の推定・執行・検証を新規ファイル追加のみで実現する。(2) 仕様 §6 DoD（S/T/N 再現一意・S1〜S6 決定論・look-ahead 不使用・seed 固定で SPA p 値再現・採用時 OOS 全閾値確認可能）を非機能要件として設計に組み込む。(3) 統計検定（OLS/Newey-West/Kupiec/Christoffersen/SPA/Politis-White）を新規ライブラリ追加なしの pure numpy で実装し、技術スタック無断変更を回避する。

### 2.3 適用範囲・制約条件

#### スコープ

- **含む**（仕様 §1）：単一銘柄（日経225 現物指数CFD）、ロング専用、週次保有（最長5営業日）、OCO 決済、金曜引け強制手仕舞い、パラメータの検証手続きによる自動選択。
- **含まない**（仕様 §1）：ショート、複数銘柄、週またぎ保有、積み増し、裁量介入。
- **本書の設計レベル**：基本設計（層責務・依存方向・ポート配置・概念データモデル・処理フロー・技術スタック選定）。クラス設計・物理 DB 設計・実装コード・数値アルゴリズムの行レベル詳細は内部設計に委譲する（§4 抽象度維持）。

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 概要 | 仕様対応 | 担当UC |
|---|---|---|---|
| FR-WV-01 | 5分OHLC から各週 半実現分散 RS⁺_w / RS⁻_w（符号別二乗和・週次集計）を算出する | §2.3, §3.1-1 | WV1 |
| FR-WV-02 | 日足OHLC から週次 Garman-Klass 全体ボラ σ̂ᵗᵒᵗᵃˡ_w を算出する | §2.3 | WV1 |
| FR-WV-03 | log-semivariance-HAR（説明変数 {1週,4週平均,12週平均}）を OLS＋Newey-West(lag=4)・推定窓 260週ローリングで毎週末再推定し、翌週 σ̂⁺_w / σ̂⁻_w を予測する | §2.3, §3.1-2 | WV1 |
| FR-WV-04 | σ̂ 算出不可・推定窓 < 260週 はノートレード確定として出力する | §2.6, §3.1-2 | WV1 |
| FR-WV-05 | 予測結果（週ごとの σ̂⁺_w/σ̂⁻_w・ノートレード判定）を週次の永続データとして保存・参照する | §3.1（週末バッチ→週初） | WV1 |
| FR-WV-06 | ストップ S=O·exp(−1.96·σ̂⁻_w)、利確 T=O·exp(z(p_tp)·σ̂⁺_w)、数量 N=f_risk·Capital/(O−S) を式で一意算出する | §2.5 | WV2 |
| FR-WV-07 | エントリー規則 e（E0 無条件／E1(θ): 前週 close-to-close ≤ −θ·σ̂ᵗᵒᵗᵃˡ_w）の真偽を前週リターンに適用して決定する | §2.4, §3.1-4 | WV2 |
| FR-WV-08 | 週初寄りで規則真なら N 単位ロング＋OCO（S 成行ストップ＋T 指値）を発注する | §3.1-5 | WV2 |
| FR-WV-09 | 週内は S・T のうち時系列で先に到達した方で決済（同一バー両到達はストップ優先）する | §2.3, §3.1-6 | WV2 |
| FR-WV-10 | 金曜引け（または週最終取引日引け）で未決済なら強制手仕舞い（結果=時間切れ）する | §3.1-7 | WV2 |
| FR-WV-11 | 週次純損益＝(決済価格−エントリー価格)·N − c_spread − c_comm − r_fund·(保有日数/365)·建玉額 ± 配当調整 を計上する | §3.1-8 | WV2 |
| FR-WV-12 | 例外・境界（週初/金曜非取引日・ギャップ突破・σ̂ 算出不可・イベント週）を §2.6 のとおり一意に処理する | §2.6 | WV2 |
| FR-WV-13 | 週ごとログ（§4.2 の14項目）を記録し、イベント週を別集計可能にする | §4.2 | WV2 |
| FR-WV-14 | IS（前半70%）/OOS（後半30%）に時系列分割し OOS を封印する（S1） | §3.2 S1 | WV3 |
| FR-WV-15 | IS で全20候補（e×p_tp）の評価統計量 f_k（コスト控除後・週次純リターン平均）を算出する（S2） | §2.4, §3.2 S2 | WV3 |
| FR-WV-16 | IS に Hansen SPA（定常ブートB=5000・Politis-White 自動ブロック長・再センタリング）を適用し p 値を算出する（S3） | §3.2 S3 | WV3 |
| FR-WV-17 | SPA p≥0.05 なら戦略棄却、p<0.05 なら f_k 最大かつ f_k>0 を選択候補とする（S4） | §3.2 S4 | WV3 |
| FR-WV-18 | 選択候補のみを OOS で 1 回だけ実行する（S5） | §3.2 S5 | WV3 |
| FR-WV-19 | OOS で (a) 週次純リターン平均>0 (b) Kupiec p≥0.05 (c) Christoffersen p≥0.05 を全て満たせば採用、1つでも欠ければ不採用（S6） | §3.2 S6, §4.1 | WV3 |
| FR-WV-20 | 採用時は OOS で §4.1 全しきい値を満たすことをログで確認可能にし、棄却時は SPA p 値と最良 f_k を記録する | §6 DoD | WV3 |

#### 非機能要件サマリー（数値目標を含む）

| 区分 | 数値目標 | 仕様対応 |
|---|---|---|
| NFR-D1 算出一意性 | 同一入力（O・σ̂⁻・σ̂⁺・p_tp・Capital・f_risk）から S・T・N が §2.5 式で 1 通りに再現（数値例：O=39000,σ̂⁻=0.020,σ̂⁺=0.025,p_tp=0.50 → S=37,501・T=39,663・N=6.67） | §6, 数値例 |
| NFR-D2 手続き決定論 | S1〜S6 が決定論的に実行され、選択パラメータ（e,p_tp）と採否が一意に出力される | §3.2, §6 |
| NFR-D3 seed 固定再現 | 同一データ・同一乱数シードで SPA p 値が一致する（seed は params 固定・決定論再現） | §4.1, §6 |
| NFR-D4 look-ahead 排除 | σ̂・S・T・N の計算に当日以降の H/L/C を不使用（依存グラフ検証に合格） | §4.1, §6 |
| NFR-Q1 ストップ被覆 | Kupiec p ≥ 0.05（実到達率 ≈ α_stop=0.05） | §4.1 |
| NFR-Q2 ストップ独立性 | Christoffersen p ≥ 0.05 | §4.1 |
| NFR-Q3 利確校正 | T 到達率 と p_tp の差 ≤ ±5%ポイント | §4.1 |
| NFR-Q4 OOS 期待値 | 週次純リターン平均 > 0 | §4.1 |
| NFR-Q5 データスヌーピング統制 | SPA p < 0.05 で採用候補抽出（S4） | §4.1 |
| NFR-Q6 サンプル下限 | 検証週数 ≥ 260、ストップ到達 ≥ 30回（複数年プール） | §4.1 |
| NFR-S1 既存無改変 | `simulator/domain`・`usecase`（既存ファイル）・既存 `adapter`・`framework`・`main` の差分 0 行。新規ポートは既存 ports.py/optimize_ports.py を編集せず新規ファイルへ追加 | 依頼制約 |
| NFR-S2 既存データ非波及 | `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`・既存生成物への書き込み 0 件 | 依頼制約・user memory「no-ripple」 |
| NFR-S3 依存方向 | usecase は domain のみ依存（pandas/adapter/framework/main を import しない）。pandas は adapter/framework/main に限定 | 依頼制約・既存規約 |
| NFR-S4 技術スタック不変 | 新規ライブラリ（statsmodels 等）追加 0 件。numpy/pandas は既存依存のみ使用 | 依頼制約・CLAUDE.md 禁止事項 |

#### 制約条件（技術 / 運用 / プロジェクト規約）

| 区分 | 制約 |
|---|---|
| 絶対制約 C1（運用・破壊禁止） | 既存データの改変・波及を禁止する。`marketdata/`・`fixtures/`・`confirmation/`・既存生成物は読み取り専用。出力は新規パスのみ（CLAUDE.md「破壊的変更＝共有リソースの破壊的変更」・user memory「no-ripple-to-existing-data」） |
| 絶対制約 C2（技術・無改変） | 既存ファイルは無改変（新規追加のみ）。既存ポート（ports.py/optimize_ports.py）・既存UC（run_is_oos/walk_forward/optimize/compute_stats）・既存 domain VO を編集しない。新規ポート/UC/VO は新規ファイルへ置く |
| 絶対制約 C3（技術・スタック固定） | 技術スタックのバージョン無断変更禁止（CLAUDE.md 禁止事項）。新規ライブラリ追加禁止。統計検定は pure numpy 実装。numpy/pandas は既存依存のみ |
| 絶対制約 C4（依存方向） | クリーンアーキの内向き依存規律。usecase は domain のみ依存。pandas は adapter/framework/main に隔離。**numpy は domain/usecase で許容**（既存規約：`bar.py:9`「domain 層は numpy のみ依存可」・`compute_stats.py:36` が usecase で numpy 使用済の先例） |
| プロジェクト規約 C5 | `.claude/CLAUDE.md`：指示範囲外の変更・破壊的変更を禁止。乱数 seed は params 固定（決定論再現・仕様 §4.1） |

#### 設計上の課題と技術的リスク

| ID | 課題／リスク | 備考 |
|---|---|---|
| 課題-WV1 | 週末バッチ推定（WV1）が出力する週次予測（σ̂⁺_w/σ̂⁻_w）を、週次執行（WV2・StrategyPort 経路）へ受け渡す永続境界をどう定義するか。WV1（バッチ）と WV2（バー列走査）はライフサイクルが異なる（前者は週末1回・後者は週内バー単位） |
| 課題-WV2 | 統計検定（OLS/Newey-West/Kupiec/Christoffersen/SPA/Politis-White）の numpy 実装を、クリーンアーキの**どの層**へ置くか。既存規約は numpy を usecase でも許容（compute_stats.py 先例）するが、依頼は「numpy/pandas は adapter に限定」。両解釈が成立し得るため代替案比較で確定する（§3.4・§10.2） |
| 課題-WV3 | 金曜引け強制手仕舞い（時間ベース強制決済）を既存 Interactor のどの機構（on_position_check による曜日判定 close / SessionCalendarPort / trading_end 相当）で表現するか。既存に取引終端機構が無い可能性があり詳細設計の確認事項（§9.3 TBD-3） |
| リスク-WV1 | look-ahead 混入。σ̂・S・T・N は「前週金曜引けまでの確定データのみ」で計算する必要があり（仕様 §3.1 週末バッチ）、当日以降 H/L/C の不使用を依存グラフで検証する必要がある（NFR-D4） |
| リスク-WV2 | SPA 定常ブートストラップ（B=5000）の seed 固定再現性。numpy の乱数生成器の状態管理を誤ると p 値が再現しない（NFR-D3） |
| リスク-WV3 | OCO（S 成行ストップ＋T 指値）と「同一バー両到達はストップ優先」を既存 Order/Interactor 機構（pending_oco config・SL/TP）で正確に表現できるか（§6.3） |

---

## 3. システムアーキテクチャ

> 出力元：S-1 採用パターン選定 ／ S-2 アーキテクチャ設計

### 3.1 全体構成図

```
┌──────────────────────────────────────────────────────────────────────┐
│ [新規] 実行入口層（tools）                                              │
│   simulator/tools/run_weekly_vol_band_cli.py                          │
│   - 日足/5分OHLC・配当データを読み取り専用ロード（pandas は本層に限定）  │
│   - config（c_spread/c_comm/r_fund=既定0.0・seed・f_risk=0.01 等）注入  │
│   - 推定→執行→検証を新規UC群へ委譲し結果を新規出力先のみへ書く          │
└───────────┬────────────────────┬───────────────────┬─────────────────┘
            │ (WV1 推定)          │ (WV2 執行)         │ (WV3 検証)
            ▼                     ▼                    ▼
┌────────────────────┐ ┌──────────────────────┐ ┌──────────────────────┐
│ [新規UC] WV1        │ │ [既存UC・無改変]      │ │ [新規UC] WV3          │
│ 週末バッチ推定      │ │ run_backtest 経路     │ │ 検証(S1〜S6)          │
│ usecase/            │ │ ＋[新規 StrategyPort  │ │ usecase/              │
│ weekly_vol_         │ │  実装]週次執行戦略    │ │ weekly_vol_band_      │
│ estimate.py         │ │ adapter/strategy/     │ │ validate.py           │
│ - RS⁺/RS⁻ 週次集計  │ │ weekly_vol_band.py    │ │ - IS/OOS 分割         │
│ - HAR 推定/予測呼出 │ │ - S/T/N を Order 化   │ │  （run_is_oos 再利用） │
│  (VarianceEstimator │ │ - エントリ規則 e 適用 │ │ - SPA→Kupiec→         │
│   Port 経由)        │ │ - OCO 発注            │ │  Christoffersen 呼出   │
│ - 予測を            │ │ - 金曜引け close      │ │ (BacktestTestPort/    │
│  VolBandRepository  │ │ (既存 StrategyPort    │ │  SpaTestPort 経由)     │
│  Port へ保存        │ │  契約のみ・新IB不要)  │ │ - 採否一意判定        │
└────────┬───────────┘ └──────────┬───────────┘ └──────────┬───────────┘
         │ Port DI                 │ 既存公開IF             │ Port DI
         ▼                         ▼                        ▼
┌──────────────────────────────────────────────────────────────────────┐
│ [新規ポート4本（新規ファイル・既存 ports.py 無改変）]                   │
│   usecase/weekly_vol_ports.py                                         │
│   - VarianceEstimatorPort : HAR(OLS+NW lag4・260週) で σ̂⁺/σ̂⁻ 予測     │
│   - VolBandRepositoryPort  : 週次予測(VarianceForecast)の保存/参照     │
│   - BacktestTestPort       : Kupiec / Christoffersen 検定             │
│   - SpaTestPort            : Hansen SPA(定常ブート B=5000・PW block)   │
└────────┬─────────────────────────────────────────────────────────────┘
         │ 実装（adapter 層・numpy で pure 実装）
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ [新規 adapter 実装（numpy 使用・pandas は repository 入出力境界のみ）]   │
│   adapter/estimator/log_semivariance_har.py  (VarianceEstimatorPort)  │
│   adapter/repository/vol_band_repo.py         (VolBandRepositoryPort)  │
│   adapter/stats/backtest_tests.py             (BacktestTestPort)      │
│   adapter/stats/hansen_spa.py                 (SpaTestPort)           │
└────────┬─────────────────────────────────────────────────────────────┘
         │ 部品再利用（無改変）
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ [既存・無改変] committed エンジン・既存UC・既存ポート・既存 domain VO   │
│   main.build_interactor / RunBacktestInteractor.execute              │
│   usecase/run_is_oos.py（IS/OOS 分割：WV3 が再利用）                  │
│   usecase/ports.py（StrategyPort：WV2 戦略が実装）                    │
│   domain/{bar,order,trade_record,...}（既存 VO）                      │
└──────────────────────────────────────────────────────────────────────┘

[新規 domain VO（新規ファイル・既存 domain 無改変）] domain/weekly_vol/*.py:
  VolatilityBand / VarianceForecast / TradingWeek / OcoOrderPair / BacktestTestResult
```

- **データフロー**：(WV1) 日足/5分OHLC（読み取り専用）→ tools が adapter repository 経由でロード → WV1 UC が RS⁺/RS⁻ 週次集計 → VarianceEstimatorPort（adapter・HAR numpy 実装）で σ̂⁺_w/σ̂⁻_w 予測 → VolBandRepositoryPort で週次予測（VarianceForecast）を永続化。(WV2) 週次執行戦略（新規 StrategyPort 実装）が VolBandRepository から当週予測を参照し S/T/N（VolatilityBand）を Order（OcoOrderPair）化、既存エンジンが週内バーを走査して OCO 決済・金曜引け close。(WV3) 検証 UC が IS/OOS 分割（run_is_oos 再利用）→ IS で全20候補 f_k → SpaTestPort で SPA p 値 → 選択候補 → OOS 実行 → BacktestTestPort で Kupiec/Christoffersen → 採否一意判定 → 新規出力先へレポート。

### 3.2 アーキテクチャパターン選択理由

- **採用パターン**：クリーンアーキテクチャ準拠の「3関心3アクター分離＋新規ポートによる外部依存隔離」パターン。推定（WV1）・執行（WV2）・検証（WV3）を独立 UC（および WV2 は既存 StrategyPort 実装）へ分離し、統計計算・永続化という numpy/pandas 依存の外部関心を新規ポート4本で隔離して adapter 実装へ追い出す（DIP）。

| 評価軸 | 案A：3関心3アクター分離＋新規ポート隔離（採用） | 案B：単一巨大 UC に推定・執行・検証を集約 | 案C：tools スクリプトに全ロジック直書き（UC/ポートなし） |
|---|---|---|---|
| 要件適合（推定/執行/検証の独立サイクル） | ○ WV1 週末バッチ・WV2 バー走査・WV3 検証を各々独立に実行/テスト可 | △ ライフサイクル混在（週末1回 vs バー単位 vs IS/OOS）で凝集低下 | △ 再利用不能・後段拡張不可 |
| C2 既存無改変 | ○ 既存ファイル 0 行差分・新規ファイルのみ | ○ | ○ |
| C4 依存方向（usecase→domain のみ・pandas/numpy 隔離） | ○ 統計/永続を新規ポートで adapter へ追い出す | ✕ 単一 UC に numpy/pandas 直結＝依存方向違反リスク | △ tools は main 依存可だが責務肥大・テスト不能 |
| 関心の分離（SRP・公式設計原則） | ○ 推定/執行/検証が単一責務 | ✕ 3 関心が 1 UC に同居 | ✕ 3 関心が 1 スクリプトに同居 |
| 既存資産再利用（run_is_oos/StrategyPort/Interactor） | ○ WV3 が run_is_oos・WV2 が StrategyPort・既存エンジンを部品利用 | △ 再利用するが UC 肥大 | △ 直書きで再利用構造が崩れる |
| テスト容易性（決定論・seed 固定検証 NFR-D3） | ○ SpaTestPort をモック差替し UC を純粋にテスト可 | ✕ numpy 直結で UC 単体テストに numpy 実行必須 | ✕ E2E のみ |
| 技術スタック不変（C3・pure numpy） | ○ ポート実装内に numpy 局所化・statsmodels 不要 | △ numpy が UC に拡散 | △ |

- **採用根拠**：案 A は「3関心の独立ライフサイクル（週末バッチ/バー走査/IS-OOS）」と「C2 既存無改変」「C4 依存方向（DIP で統計/永続を adapter へ隔離）」を同時に満たす。公式設計原則の関心の分離（SRP）・依存性逆転（DIP）に整合し、既存資産（run_is_oos・StrategyPort・Interactor）を部品再利用できる。出典：公式設計原則（SRP/DIP）＋既存規約（ISOOS 設計の「committed の上にUC を重ねる」パターン）。
- **棄却理由**：案 B は推定（週末1回）・執行（バー単位）・検証（IS/OOS）の異なるライフサイクルを 1 UC に同居させ凝集が低下し、numpy が UC に直結して C4 依存方向と NFR-D3 のテスト容易性を損なう。案 C は再利用不能で WV3 が run_is_oos を呼べず、tools にロジックが閉じてテスト不能（既存 ISOOS 設計の案 C 棄却理由と同型）。
- 出典：（実務的推奨／仮説）＋公式設計原則（SRP/DIP）＋プロジェクト規約（C2）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| UC（WV1/WV3） | 純 Python（`@dataclass`・標準ライブラリ・型注釈） | 既存と同一（追加なし） | pydantic 検証層 | C3 技術スタック追加禁止。既存 usecase が pydantic 非依存（run_is_oos.py/optimize.py の dataclass 様式に整合） |
| WV2 戦略 | 既存 `StrategyPort` 実装（新規 adapter/strategy） | 既存 | 新規 Input Boundary | 依頼「既存 StrategyPort 実装で実現＝新 IB 不要」。`stop_entry_probe.py` が手本（on_new_bar で Order・SL/TP・OCO config） |
| 統計検定（HAR/OLS/NW/Kupiec/Christoffersen/SPA/PW block） | **pure numpy**（新規 adapter 実装） | numpy（既存依存） | statsmodels / scipy.stats / arch | C3：新規ライブラリ追加禁止。numpy は既存依存。OLS（正規方程式）・Newey-West（HAC 共分散）・Kupiec（尤度比）・Christoffersen（マルコフ尤度比）・定常ブート（Politis-Romano）・PW block（自己相関積分）はいずれも numpy の行列演算・乱数・FFT で実装可能 |
| 永続化（VolBandRepository） | 標準ライブラリ（JSON/CSV）＋ pandas（adapter 入出力境界のみ） | 既存 | DB | C3。週次予測は小規模時系列。既存 repository（ohlc_csv.py 等）が pandas を adapter に閉じるパターンに整合 |
| 入力ロード（tools） | pandas（tools/adapter に限定） | 既存 | — | 既存 ohlc_csv.py / tools の pandas 利用パターンを継承。pandas は usecase へ漏らさない（C4） |

- **採用技術と代替技術の比較（統計検定ライブラリ）**：

| 評価軸 | pure numpy（採用） | statsmodels | scipy.stats / arch |
|---|---|---|---|
| 要件適合（HAR+NW+SPA+PW block 全て） | ○ 全検定を numpy で実装可 | ○ OLS/NW/HAR 提供 | △ SPA・PW block は非提供（自前実装併用） |
| C3 技術スタック不変（新規ライブラリ 0） | ○ 既存 numpy のみ | ✕ 新規依存追加（CLAUDE.md 禁止事項違反） | ✕ 新規依存追加 |
| seed 固定再現性（NFR-D3） | ○ numpy.random.Generator で seed 完全制御 | △ 内部乱数の制御が間接的 | △ |
| 既存規約整合（numpy は domain/usecase 許容・compute_stats.py 先例） | ○ | ✕ statsmodels は既存に不在 | ✕ |
| 学習/運用コスト | △ 検定の自前実装コスト（既知アルゴリズム・参照論文明示） | ○ ライブラリ提供 | △ |

- **採用根拠**：statsmodels/scipy/arch は要件（特に SPA・Politis-White block 自動選択）を一部しか提供せず、かつ C3「技術スタックのバージョン無断変更禁止／新規ライブラリ追加禁止」（CLAUDE.md 絶対遵守ルール）に違反する。pure numpy は既存依存のみで全検定を実装でき、numpy.random.Generator により seed 固定再現性（NFR-D3）を完全制御できる。検定アルゴリズムは仕様 §関連ドキュメントに参照論文（White 2000 / Hansen 2005 / Politis-Romano 1994 / Politis-White 2004 / Corsi 2009 / Kupiec 1995 / Christoffersen 1998）が明示済みで、自前実装の根拠が確立している。
- **棄却理由**：statsmodels/scipy/arch の追加は C3・CLAUDE.md 禁止事項に該当し、無断のスタック変更となる。
- 技術スタックの追加・バージョン変更は 0 件（C3 遵守）。出典：プロジェクト規約（C3・CLAUDE.md）＋（実務的推奨／仮説）。

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| tools（実行入口・新規） | CLI 引数解釈・読み取り専用ロード（pandas）・config 注入（c_spread/c_comm/r_fund=0.0・seed・f_risk）・新規出力先への書込・UC 群とポート実装の組立（Composition Root） | usecase（新規UC）・adapter（ポート実装）・main（build_interactor） | プロジェクト規約（既存 tools の Composition Root パターン） |
| usecase（WV1 推定・WV3 検証・新規） | RS 週次集計・推定/検定の手続き統括・採否一意判定。**domain のみ依存**（adapter/framework/main・pandas を import しない。numpy 利用はポート実装へ委譲し UC は純手続きに留める） | domain・新規ポート抽象 | 公式設計原則（クリーンアーキ：usecase は domain のみ依存。run_is_oos.py L7 で既存規約として実証） |
| usecase（既存・無改変） | run_is_oos（IS/OOS 分割）・RunBacktestInteractor（1 run）の部品提供 | domain | C2 |
| adapter/strategy（WV2 執行・新規） | 当週予測参照→S/T/N 算出→OcoOrderPair の Order 化→エントリ規則 e 適用→金曜引け close。既存 `StrategyPort` を実装（新 Input Boundary 不要） | usecase（StrategyPort 抽象）・domain（Order/新規VO） | 依頼＋`stop_entry_probe.py` 手本 |
| adapter（統計/推定/永続実装・新規） | VarianceEstimatorPort（HAR numpy）・BacktestTestPort（Kupiec/Christoffersen numpy）・SpaTestPort（SPA numpy）・VolBandRepositoryPort（永続）の具象実装。**numpy を使用**（domain/usecase へ numpy 計算を漏らさず本層に局所化）。pandas は repository 入出力境界のみ | domain・新規ポート抽象 | 課題-WV2 の確定（下記） |
| domain（新規 VO・新規ファイル・既存 domain 無改変） | VolatilityBand / VarianceForecast / TradingWeek / OcoOrderPair / BacktestTestResult を frozen dataclass・振る舞いなし・不変条件検証で定義 | （なし／numpy のみ可） | `bar.py` L19-50 の VO 様式 |

- **依存方向**：tools → {usecase（新規UC）, adapter（ポート実装）, main} → domain。新規UC（WV1/WV3）は新規ポート抽象（usecase 層に置く Protocol/ABC）に依存し、具象実装（numpy）は tools（Composition Root）が注入する（DIP）。これにより新規 UC は domain のみ依存を保つ。出典：公式設計原則（依存方向の内向き規律・DIP）。

- **課題-WV2 の確定（統計検定 numpy をどの層へ置くか・代替案比較）**：

| 評価軸 | 案W2-A：統計検定 numpy を adapter ポート実装へ（採用） | 案W2-B：compute_stats.py 先例に倣い usecase に numpy 直書き |
|---|---|---|
| 依頼前提「numpy/pandas は adapter に限定」整合 | ○ numpy を adapter に隔離（依頼の意図に最大限整合） | △ numpy が usecase に出る（依頼文字どおりには非整合） |
| 既存規約整合（numpy は domain/usecase 許容＝`bar.py:9`・`compute_stats.py:36` 先例） | ○（adapter も当然 numpy 可） | ○（compute_stats.py が usecase numpy の先例） |
| UC 単体テスト容易性（NFR-D3 seed 固定・モック差替） | ○ SpaTestPort をモックして UC を純粋にテスト可 | △ UC テストに numpy 実行が常に必要 |
| DIP（外部関心の隔離） | ○ 統計計算＝外部技術的関心を Port で逆転 | △ UC に技術詳細が混入 |

  - **採用＝案W2-A**：統計検定の numpy 実装を adapter のポート具象（VarianceEstimatorPort/BacktestTestPort/SpaTestPort 実装）へ置く。理由：(1) 依頼前提「numpy/pandas は adapter に限定」を最大限尊重（追従性バイアスを排し、依頼を実証検証した結果、numpy 部分は既存 compute_stats.py 先例と矛盾するが、adapter 配置なら依頼意図と既存規約の双方に整合する安全側）。(2) 統計計算は技術的外部関心であり DIP で隔離するのが公式設計原則に整合。(3) UC を numpy 非依存に保ち seed 固定再現テスト（NFR-D3）でポートをモック差替できる。
  - **既存規約との関係（重要・上流前提の差分）**：依頼文「numpy/pandas は adapter に隔離」は、既存規約（`bar.py:9`「domain 層は numpy のみ依存可」・`compute_stats.py:36` が usecase で numpy 使用）と numpy について矛盾する。本設計は実証（upstream-input-validation で確認）の上、numpy は domain/usecase でも許容される（既存規約）が、本機能の統計検定は adapter へ配置する（依頼意図尊重＋DIP）と確定する。実装者が compute_stats.py 先例に倣い usecase 実装を選んでも既存規約には反しないため、最終配置は詳細設計の余地として §10.2 に明記する。出典：公式設計原則（DIP）＋既存規約（実証）＋（実務的推奨／仮説）。

---

## 4. 機能設計

> 出力元：S-3 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID | 担当UC |
|---|---|---|---|---|---|
| F-WV1-01 | 半実現分散週次集計 | 5分リターン符号別二乗和で RS⁺_w/RS⁻_w を週次集計 | 高 | FR-WV-01 | WV1 |
| F-WV1-02 | 全体ボラ週次集計 | 日足から Garman-Klass σ̂ᵗᵒᵗᵃˡ_w を週次集計 | 高 | FR-WV-02 | WV1 |
| F-WV1-03 | HAR 推定・予測 | log-semivariance-HAR を OLS+NW(lag4)・260週ローリングで再推定し σ̂⁺_w/σ̂⁻_w 予測 | 高 | FR-WV-03 | WV1 |
| F-WV1-04 | ノートレード判定 | σ̂ 算出不可・窓<260週 をノートレード確定 | 高 | FR-WV-04 | WV1 |
| F-WV1-05 | 予測永続化 | 週次予測（VarianceForecast）を保存・参照 | 高 | FR-WV-05 | WV1 |
| F-WV2-01 | S/T/N 算出 | S=O·exp(−1.96·σ̂⁻)・T=O·exp(z(p_tp)·σ̂⁺)・N=f_risk·Cap/(O−S) | 高 | FR-WV-06 | WV2 |
| F-WV2-02 | エントリ規則適用 | e（E0/E1(θ)）を前週リターンに適用 | 高 | FR-WV-07 | WV2 |
| F-WV2-03 | OCO 発注 | 週初寄りで N ロング＋OCO（S 成行ストップ＋T 指値） | 高 | FR-WV-08 | WV2 |
| F-WV2-04 | 週内決済 | S・T 先着決済（同一バー両到達はストップ優先） | 高 | FR-WV-09 | WV2 |
| F-WV2-05 | 金曜引け強制手仕舞い | 週最終取引日引けで未決済を強制 close（時間切れ） | 高 | FR-WV-10 | WV2 |
| F-WV2-06 | 純損益計上 | コスト・ファンディング・配当調整控除後の週次純損益 | 高 | FR-WV-11 | WV2 |
| F-WV2-07 | 例外・境界処理 | §2.6 の8境界を一意処理（ギャップ突破・非取引日・イベント週） | 高 | FR-WV-12 | WV2 |
| F-WV2-08 | 週次ログ記録 | §4.2 の14項目記録・イベント週別集計 | 中 | FR-WV-13 | WV2 |
| F-WV3-01 | IS/OOS 分割 | 前半70%/後半30%・OOS 封印（run_is_oos 再利用） | 高 | FR-WV-14 | WV3 |
| F-WV3-02 | IS 候補評価 | 全20候補の f_k 算出 | 高 | FR-WV-15 | WV3 |
| F-WV3-03 | SPA 検定 | Hansen SPA（B=5000・PW block・再センタリング）p 値 | 高 | FR-WV-16 | WV3 |
| F-WV3-04 | 選択候補抽出 | p≥0.05 棄却／p<0.05 で f_k 最大かつ>0 を選択 | 高 | FR-WV-17 | WV3 |
| F-WV3-05 | OOS 実行 | 選択候補のみ OOS で1回実行 | 高 | FR-WV-18 | WV3 |
| F-WV3-06 | 採否一意判定 | Kupiec/Christoffersen/期待値の全充足で採用 | 高 | FR-WV-19 | WV3 |
| F-WV3-07 | 検証レポート | 採用時 OOS 全閾値確認可能・棄却時 SPA p 値と最良 f_k 記録 | 高 | FR-WV-20 | WV3 |

### 4.2 機能詳細仕様（主要機能のみ）

#### F-WV1-03 HAR 推定・予測（VarianceEstimatorPort 経由）

- **入力**：週次系列 {RS⁺_w}・{RS⁻_w}（log 変換後）、推定窓 260 週、Newey-West lag=4、対象週 w（予測対象＝翌週）。
- **前提条件**：当該週末時点で確定済みデータのみ（look-ahead 排除・NFR-D4）。利用可能週数 ≥ 260（ウォームアップ充足）。
- **処理（概念）**：log-semivariance を被説明変数、説明変数 {当週 RS, 過去4週平均 RS, 過去12週平均 RS} で OLS 推定、係数共分散を Newey-West(lag=4) で HAC 補正、推定窓を 1 週ずつ前進（260週ローリング）。翌週 σ̂⁺_w（RS⁺ 系）/σ̂⁻_w（RS⁻ 系）を予測。
- **後条件**：VarianceForecast（week_id・σ̂⁺_w・σ̂⁻_w・推定可否フラグ）を得る。利用可能週数 < 260 または推定不能なら推定可否フラグ＝false（ノートレード）。
- **例外条件**：σ̂⁺ または σ̂⁻ 算出不可・窓 < 260週 → VarianceForecast の推定可否＝false（仕様 §2.6）。
- **層配置**：HAR/OLS/NW の numpy 数値計算は VarianceEstimatorPort の adapter 実装に置く（§3.4 課題-WV2）。UC（WV1）は RS 週次集計の純手続きと Port 呼出・永続化呼出のみ。

#### F-WV2-01 / F-WV2-03 S/T/N 算出と OCO 発注（StrategyPort 実装）

- **入力**：週初寄り値 O、当週 VarianceForecast（σ̂⁺_w/σ̂⁻_w）、p_tp（検証で確定済）、Capital、f_risk（=0.01 固定）、エントリ規則 e（検証で確定済）、前週 close-to-close リターン、σ̂ᵗᵒᵗᵃˡ_w。
- **前提条件**：当週 VarianceForecast が推定可否＝true。`z(p_tp)`：z(0.40)=0.842/z(0.50)=0.674/z(0.60)=0.524/z(0.70)=0.385（仕様 §2.5）。
- **処理（概念）**：(1) S=O·exp(−1.96·σ̂⁻_w)、T=O·exp(z(p_tp)·σ̂⁺_w)、N=f_risk·Capital/(O−S) を式で一意算出（VolatilityBand）。(2) エントリ規則 e を前週リターンに適用し真偽判定（F-WV2-02）。(3) 真なら週初寄り成行ロング N 単位＋OCO（S 成行ストップ＋T 指値）を OcoOrderPair として Order 化（既存 Order の kind/price/sl/tp/volume＋pending_oco config）。
- **後条件**：エントリ Order 群（OcoOrderPair）を返す（既存 StrategyPort.on_new_bar 契約）。規則偽または推定不可なら空 Order（ノートレード）。
- **例外条件（§2.6）**：寄りが S 未満（ギャップ突破）→寄り値約定・差をスリッページ計上。同一バー両到達→ストップ優先（pending_oco＋約定順規約）。週初非取引日→最初の取引日寄り。
- **層配置**：既存 `StrategyPort`（on_init/on_new_bar/on_position_check/on_tick）を実装。新 Input Boundary 不要（依頼・`stop_entry_probe.py` 手本）。

#### F-WV2-05 金曜引け強制手仕舞い

- **入力**：建玉、当週 TradingWeek（週内取引日・週最終取引日）、現バー bar_index。
- **処理（概念・第一候補）**：StrategyPort.on_position_check で「当バーが週最終取引日の引けバー」を判定し、未決済建玉に "close" を返す（結果=時間切れ）。金曜非取引日は週最終取引日の引けに繰り上げ（§2.6）。
- **後条件**：週最終取引日引けで建玉 0（週またぎ保有を排除）。
- **TBD**：既存 Interactor に時間ベース強制決済の機構（trading_end 相当）が無い場合の実現手段は詳細設計の確認事項（§9.3 TBD-3）。on_position_check 曜日判定 close を第一候補とする。

#### F-WV3-03 / F-WV3-06 SPA 検定と採否一意判定

- **入力（SPA）**：IS の全20候補の f_k 系列（週次純リターン）、B=5000、seed（params 固定）。
- **処理（SPA・概念）**：統計量 V=max_k √n·f̄_k、帰無分布を定常ブートストラップ（Politis-Romano）で生成、ブロック長を Politis-White(2004) で自動選択、再センタリング後の V*_b が観測 V を超える割合を p 値とする（SpaTestPort・adapter numpy 実装）。
- **判定（S4）**：p≥0.05 → 戦略棄却（終了）。p<0.05 → f_k 最大かつ f_k>0 を選択候補。
- **処理（採否・S6）**：選択候補を OOS で1回実行 → BacktestTestPort で Kupiec（ストップ到達率 vs α_stop=0.05）・Christoffersen（独立性）→ (a)週次純リターン平均>0 (b)Kupiec p≥0.05 (c)Christoffersen p≥0.05 を全充足で採用、1つでも欠ければ不採用。
- **後条件**：BacktestTestResult（採否・SPA p・選択(e,p_tp)・Kupiec p・Christoffersen p・利確校正差・OOS 期待値）。決定論（NFR-D2）・seed 固定で再現（NFR-D3）。
- **層配置**：UC（WV3）は手続き統括・閾値判定の純ロジック。SPA/Kupiec/Christoffersen の numpy 計算は adapter ポート実装。IS/OOS 分割は run_is_oos 再利用。

### 4.3 処理フロー図

```
【WV1 週末バッチ推定】（前週金曜引けまでの確定データのみ・look-ahead 排除）
[start] tools → WV1 UC
  │ 入力: 5分OHLC, 日足OHLC, config(seed)
  ▼
(1) 5分リターン符号別二乗和 → RS⁺_w, RS⁻_w 週次集計
  ▼
(2) 利用可能週数 < 260 ? ──yes──► VarianceForecast(推定可否=false)＝ノートレード → 保存 → [end]
  │ no
  ▼
(3) VarianceEstimatorPort: log-semivariance-HAR OLS+NW(lag4) 260週ローリング
  │     → σ̂⁺_w, σ̂⁻_w 予測（算出不可なら推定可否=false）
  ▼
(4) VolBandRepositoryPort: VarianceForecast を週次保存
  ▼
[end]

【WV2 週次執行】（既存 StrategyPort 経路・既存エンジンが週内バー走査）
[週初寄りバー] StrategyPort.on_new_bar
  │ VolBandRepository から当週 VarianceForecast 参照
  ▼
(1) 推定可否=false ? ──yes──► 空Order（ノートレード）
  │ no
  ▼
(2) S/T/N 算出（VolatilityBand）＋エントリ規則 e 適用
  │     規則偽 ──► 空Order
  ▼
(3) OCO 発注（OcoOrderPair: ロングN + S成行ストップ + T指値, pending_oco）
  │
  ▼ [週内バー] 既存 Interactor: S/T 先着決済（同一バー両到達=ストップ優先）
  │
  ▼ [週最終取引日引けバー] on_position_check → 未決済を close（時間切れ）
  ▼
(4) 純損益計上（gross − c_spread − c_comm − r_fund·保有日数/365·建玉額 ± 配当）＋週次ログ
  ▼
[次週へ]

【WV3 検証】（S1〜S6 決定論）
[start] tools → WV3 UC
  ▼
S1 IS/OOS 分割（run_is_oos 再利用・OOS 封印）
  ▼
S2 IS 全20候補(e×p_tp) の f_k 算出
  ▼
S3 SpaTestPort: SPA p 値（B=5000・PW block・seed 固定）
  ▼
S4 p≥0.05 ? ──yes──► 戦略棄却（SPA p・最良 f_k 記録）→ [end]
  │ no
  ▼ f_k 最大かつ f_k>0 → 選択候補(e*,p_tp*)
S5 OOS で選択候補のみ1回実行（コスト込み）
  ▼
S6 BacktestTestPort: Kupiec p, Christoffersen p / OOS 期待値
  │  (a)期待値>0 ∧ (b)Kupiec p≥0.05 ∧ (c)Christoffersen p≥0.05 ?
  │     全充足 ──► 採用（OOS 全閾値ログ）
  │     1つでも欠ける ──► 不採用
  ▼
[end] BacktestTestResult → 新規出力先（JSON/MD）
```

### 4.4 業務フロー・ユースケース

- **アクター分離（依頼の確定済上位構造）**：
  - **WV1 リスク推定担当**：週末バッチで翌週バンド幅（σ̂⁺_w/σ̂⁻_w）を予測する。
  - **WV2 執行担当**：当週予測から S/T/N を算出し週初ロング・OCO・金曜引け手仕舞いを実行する（既存 StrategyPort 実装）。
  - **WV3 検証担当**：IS/OOS と統計検定で採否を一意に決定する。
- **ユースケース**：「分析者が、週次ボラバンド戦略のパラメータ（e,p_tp）を IS/OOS 分割と SPA/Kupiec/Christoffersen で一意に検証し、採否（運用するか・どの候補か）をデータから決定する」。WV1/WV2 は WV3 が呼び出すバックテスト経路の構成要素であり、運用時は WV1（週末）→WV2（週内）が反復する。

---

## 5. データ設計

> 出力元：S-3 データ設計

### 5.1 データモデル概要（概念・実装非依存）

- **入力**：`{ 日足OHLC, 5分OHLC, 配当落ちデータ, config(c_spread/c_comm/r_fund=0.0既定・f_risk=0.01・seed・α_stop=0.05・探索グリッド), Capital }`。
- **中間（WV1）**：`{ 週次 RS⁺_w/RS⁻_w, 週次 σ̂ᵗᵒᵗᵃˡ_w, VarianceForecast 系列 }`。
- **中間（WV2）**：`{ VolatilityBand(S/T/N), OcoOrderPair, 週次決済結果, 週次純損益 }`。
- **中間（WV3）**：`{ 候補別 f_k 系列, SPA p 値, 選択候補(e*,p_tp*), OOS BacktestStats }`。
- **出力**：`{ 週次ログ（§4.2 の14項目）, BacktestTestResult（採否・各検定 p 値・選択候補） }`。

### 5.2 主要エンティティ定義（新規 domain VO ＝ frozen・振る舞いなし・既存 `bar.py` 様式）

| エンティティ | 概要 | 主要属性 | 関連エンティティ |
|---|---|---|---|
| VarianceForecast（新規VO） | 週末バッチが出力する翌週の半実現ボラ予測 | week_id、σ̂⁺_w、σ̂⁻_w、σ̂ᵗᵒᵗᵃˡ_w、推定可否フラグ（不可＝ノートレード） | TradingWeek, VolatilityBand |
| VolatilityBand（新規VO） | 予測から導出する当週のバンド（執行パラメータ） | O（週初寄り）、S（ストップ）、T（利確）、N（数量）、p_tp、不変条件：O−S>0・S>0・T>O | VarianceForecast, OcoOrderPair |
| TradingWeek（新規VO） | 1 取引週の境界・取引日集合 | week_id、週初取引日、週最終取引日、取引日リスト、event_flag（日銀/FOMC/SQ/主要指標） | VarianceForecast, VolatilityBand |
| OcoOrderPair（新規VO） | OCO の発注ペア（既存 Order を内包） | エントリ Order（ロング N 成行）、ストップ Order（S 成行）、利確 Order（T 指値）、不変条件：両子注文は択一決済（OCO） | VolatilityBand, 既存 Order |
| BacktestTestResult（新規VO） | 検証手続き S1〜S6 の最終結果 | 採否（採用/不採用/戦略棄却）、SPA p、選択候補(e*,p_tp*)、最良 f_k、Kupiec p、Christoffersen p、利確校正差、OOS 週次純リターン平均 | （WV3 出力） |
| 週次ログレコード（新規・概念） | §4.2 の観測可能性14項目 | week_id, O, σ̂⁺_w, σ̂⁻_w, S, T, N, entry_flag, exit_type(利確/ストップ/時間切れ), 保有日数, gross_pnl, cost, net_pnl, event_flag | VolatilityBand, VarianceForecast |
| Bar / Order / TradeRecord / BacktestStats（既存・無改変） | 価格バー・発注・確定トレード・成績 DTO | （既存定義） | OcoOrderPair, BacktestTestResult |

- **抽象度維持**：物理テーブル・クラス名・DDL は内部設計に委譲（本書は概念定義・主要属性・不変条件のみ）。新規 VO は既存 `bar.py`（frozen dataclass・`__post_init__` 不変条件検証・numpy のみ依存・振る舞いなし）の様式に整合させる。

### 5.3 データフロー図

```
日足OHLC ──GK週次集計──► σ̂ᵗᵒᵗᵃˡ_w ─┐
5分OHLC ──符号別二乗和──► RS⁺/RS⁻_w ─┼─► VarianceEstimatorPort(HAR/OLS/NW・adapter numpy)
                                      │        │
                                      │        ▼ σ̂⁺_w / σ̂⁻_w
                                      │   VarianceForecast ──VolBandRepositoryPort──► 週次予測ストア(新規パス)
                                      │                                                      │
                                      └──────────────────────────────────────────────────────┤参照
                                                                                              ▼
週初寄りO ＋ 当週予測 ＋ p_tp ＋ Capital ──► VolatilityBand(S/T/N) ──► OcoOrderPair ──► 既存Interactor(週内走査)
                                                                                              │
                                            金曜引けclose ◄── on_position_check ◄────────────┘
                                                                                              ▼
                                                            週次決済結果/純損益 ──► 週次ログ(新規パス)
                                                                                              │
【WV3】IS/OOS分割(run_is_oos再利用) ──► IS f_k×20 ──SpaTestPort──► SPA p ──選択候補──► OOS実行
                                                                                              │
                                            BacktestTestPort(Kupiec/Christoffersen) ──► BacktestTestResult ──► 新規パス(JSON/MD)
```

### 5.4 データライフサイクル

- **入力データ（日足/5分/配当）**：読み取り専用（C1・NFR-S2）。改変・移動・削除を行わない。
- **週次予測ストア（VarianceForecast）**：WV1 が週末に生成し WV2 が参照する新規パスの永続データ。既存生成物を上書きしない（新規パスのみ・NFR-S2）。決定論（同一入力で同一予測）。
- **中間（VolatilityBand/OcoOrderPair/f_k 系列）**：プロセスメモリ上のみ（永続化しない）。
- **出力（週次ログ・BacktestTestResult）**：新規パスのみ（JSON/MD）。既存ディレクトリへの書込 0 件（出力先検証は §6.5）。保持期間・アーカイブは運用判断（§9.3 TBD-4）。
- **乱数 seed**：params 固定（決定論再現・NFR-D3）。SPA ブートストラップの乱数状態は seed から完全に再現する。

---

## 6. インターフェース設計

> 出力元：S-3 インターフェース設計

### 6.1 API 設計概要（内部 UC/ポート IF・概念レベル）

- **種別**：プロセス内の関数/UC/ポート呼び出し（HTTP/REST・画面は本機能に含めない）。
- **新規ポート4本（新規ファイル `simulator/usecase/weekly_vol_ports.py`・既存 ports.py/optimize_ports.py 無改変・C2）**：
  - **VarianceEstimatorPort**（概念シグネチャ）：`forecast(rs_plus_series, rs_minus_series, target_week, window=260, nw_lag=4) -> VarianceForecast`。log-semivariance-HAR を OLS+NW で推定し翌週 σ̂⁺/σ̂⁻ を返す。算出不可は推定可否＝false の VarianceForecast。
  - **VolBandRepositoryPort**（概念シグネチャ）：`save(forecast: VarianceForecast) -> None` / `get(week_id) -> VarianceForecast | None`。週次予測の永続化・参照（新規パスのみ）。
  - **BacktestTestPort**（概念シグネチャ）：`kupiec(hit_series, alpha=0.05) -> BacktestTestResult 断片` / `christoffersen(hit_series) -> ...`。ストップ被覆・独立性の尤度比検定 p 値。
  - **SpaTestPort**（概念シグネチャ）：`spa(f_k_matrix, B=5000, seed, block_len="politis_white") -> p 値`。Hansen SPA（定常ブート・PW 自動ブロック長・再センタリング）。
- **依存方向の確定（DIP・C4）**：新規ポート抽象は usecase 層（`weekly_vol_ports.py`）に Protocol/ABC として置く（既存 optimize_ports.py が Protocol を usecase に置く先例に整合）。具象実装は adapter 層（numpy）。tools（Composition Root）が具象を UC へ注入する。これにより UC（WV1/WV3）は domain＋ポート抽象のみ依存し、numpy/pandas を import しない（NFR-S3）。
  - 代替案：ポート抽象を adapter に置く → usecase が adapter を import＝依存方向違反（C4）。よって不採用。出典：公式設計原則（DIP・依存方向内向き）＋既存規約（optimize_ports.py）。
- **WV2 の IF（新 Input Boundary なし）**：週次執行は既存 `StrategyPort`（on_init/on_new_bar/on_position_check/on_tick）を実装する新規 adapter で実現（依頼確定・`stop_entry_probe.py` 手本）。新規 Input Boundary は追加しない。

### 6.2 画面構成・遷移

- 該当なし（本機能は UI を含まない。オフライン分析・バッチ実行）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| committed バックテストエンジン（`simulator.main`/`simulator.usecase`） | プロセス内関数呼び出し（無改変・部品利用） | RunBacktestRequest / BacktestResult（dataclass） | WV3 で候補数×区間回数 | エンジン例外（ConfigError/BacktestError）を UC が捕捉し中断・明示報告（無音禁止） |
| run_is_oos（既存 UC） | プロセス内関数呼び出し（無改変・再利用） | RunIsOosRequest / RunIsOosResult | WV3 で IS/OOS 分割 1 回 | IsOosValidationError（区間空・範囲不正）を WV3 が捕捉し中断 |
| 価格データセット（日足/5分/配当） | ファイル読み取り（読み取り専用） | CSV（既存 repository 経路） | WV1 で1回ロード | 読込失敗は DataError で中断 |
| ブローカー（OCO・現物指数CFD） | 設計上の外部前提（バックテストでは Order/config で模擬） | 約定価格はブローカー提示値（取引所単一正本なし・仕様 §5） | 週次 | ギャップ突破は寄り値約定・差をスリッページ計上（§2.6） |

- **OCO・同一バー両到達の表現**：既存 Order（kind/price/sl/tp/volume）と pending_oco config（`stop_entry_probe.py` の OCO 経路）で表現。同一バー両到達は「ストップ優先」を約定順規約として設計（仕様 §2.3・§2.6）。詳細な約定順機構は内部設計で確定。

### 6.4 通信プロトコル・データ形式

- **プロトコル**：なし（プロセス内）。
- **出力データ形式**：
  - 機械可読：JSON（週次ログ・BacktestTestResult）。`asdict` で dataclass を dict 化（既存パターン）。
  - 人間可読：Markdown（検証レポート＝採否・SPA p・選択候補・各検定 p・OOS 全閾値）。
  - 整形は tools 層で行い、既存 presenter は改変・流用しない（adapter 無改変・C2）。
- **週次予測ストア形式**：CSV または JSON（小規模時系列・新規パス）。詳細は §9.3 TBD-4。

### 6.5 出力先検証（データ非波及の機構・NFR-S2・C1）

- **検証関数（新規・概念）**：書込先パスを引数に取り「指定 OUT ディレクトリ配下のみ許可」を判定する純関数を tools 層に設ける。
- **拒否条件**：解決済み絶対パスが `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/` のいずれかのプレフィクスに該当する書込を拒否する（C1/NFR-S2）。許可は新規 OUT 配下のみ。
- **検証手段（計測可能）**：(1) 検証関数の単体テスト（禁止プレフィクス拒否）、(2) 結合テストで「実行前後に既存データディレクトリ配下ファイルの mtime 不変」を assert する（既存 ISOOS 設計 §6.5 の手段を踏襲）。

---

## 7. 非機能設計

> 出力元：S-4 品質特性の担保

### 7.1 性能設計・スケーラビリティ対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-D1 算出一意性 | 同一入力で S/T/N 完全一致（数値例：S=37,501・T=39,663・N=6.67） | S/T/N を §2.5 の決定論式で算出（浮動小数演算順序を固定）。z(p_tp) は定数表 | 数値例の単体テスト（誰が計算しても一致を assert） |
| NFR-D3 seed 固定再現 | 同一データ・同一 seed で SPA p 値完全一致 | SpaTestPort 実装で numpy.random.Generator(seed) を使い乱数状態を seed から完全再現。B=5000 のブート列が seed 固定で同一 | 同一 seed で2回実行し p 値の完全一致を assert |
| NFR-P1 推定計算量 | HAR 推定＝260週×説明変数3の OLS（小行列）。週数 W に対し O(W·260) | 260週ローリングの逐次更新。OLS は正規方程式（3×3）で軽量 | wall-clock 計測 |
| NFR-P2 SPA 計算量 | B=5000 ブート×20候補×IS週数。決定論・並列化不要 | numpy ベクトル化（候補×ブートを行列演算）。block 長は PW で1回算出 | wall-clock 計測 |
| スケーラビリティ | WV3 のバックテスト実行回数＝IS全20候補＋OOS選択候補1（最大21区間run）。同期実行で可 | 最適化ループは20候補固定（探索空間が小・多重性抑制済）。並列化は後段拡張余地 | 実行回数の assert |

### 7.2 可用性設計・障害対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-A1 実行完了 | オフライン分析ツールのため SLA/稼働率対象外 | 推定不能（σ̂ 算出不可・窓<260）・区間空・SPA 棄却は明示的に「ノートレード/棄却」として出力（無音禁止）。エンジン/検証の例外は UC が捕捉し中断・報告 | 単体テストで例外伝播・ノートレード/棄却パスを検証 |

- 本機能はオフラインの分析・検証ツールであり、常時稼働サービスの可用性（99.x%・MTTR）は適用対象外（該当なし）。

### 7.3 セキュリティ設計

- **認証・認可**：該当なし（プロセス内ローカル実行・公開エンドポイントなし）。
- **入力検証**：探索グリッド（e 5×p_tp 4＝20）・推定窓260・α_stop=0.05・f_risk=0.01・config 数値（c_spread/c_comm/r_fund 既定0.0）を UC/tools 入口で検証。look-ahead 排除（NFR-D4）を依存グラフ検証（σ̂・S・T・N が当日以降 H/L/C を参照しないこと）で担保。
- **データ保護（最重要・C1/NFR-S2）**：既存データ（marketdata/・fixtures/・confirmation/）への書込を §6.5 出力先検証関数で構造的に禁止。読み取りは read-only、書込は新規 OUT 配下のみ。
- **監査ログ**：該当なし（オフライン。週次ログは分析用標準出力/新規ファイル）。
- 詳細脅威分析は security スキル成果物を参照（本書では扱わない）。

### 7.4 運用・保守性設計

- **ログ設計**：週次ログ（§4.2 の14項目）を新規パスへ記録。イベント週（event_flag）を別集計可能にする。検証結果（採否・SPA p・選択候補・各検定 p）を BacktestTestResult として記録。
- **観測可能性（DoD 連動）**：採用時は OOS で §4.1 全しきい値（Kupiec/Christoffersen/利確校正/期待値/サンプル下限）を満たすことをログで確認可能にする。棄却時は SPA p 値と最良 f_k を記録（仕様 §6 DoD）。
- **構成管理**：新規ファイルのみ追加（usecase: weekly_vol_ports.py / weekly_vol_estimate.py / weekly_vol_band_validate.py、adapter: estimator/log_semivariance_har.py / stats/backtest_tests.py / stats/hansen_spa.py / repository/vol_band_repo.py / strategy/weekly_vol_band.py、domain: weekly_vol/*.py、tools: run_weekly_vol_band_cli.py）。既存ファイル差分 0（NFR-S1）。
- **保守性（拡張点）**：(1) 外部コスト c_spread/c_comm/r_fund・配当調整・seed を config 注入（既定0.0・設定外部化）。(2) 統計検定を Port 抽象化し実装差替可能（バリア分布の経験分布版差替＝仕様 §5 改版手続きへの拡張余地）。(3) RS 週次集計・f_k 抽出を独立関数化し WV3 が再利用。
- **バックアップ・リストア**：該当なし（出力は再生成可能・入力は読み取り専用）。

---

## 8. 開発・運用方針

> 出力元：S-4 品質特性の担保

### 8.1 開発方法論・プロセス

- `.claude/CLAUDE.md` のフロー（分析→計画→実行→品質管理→報告）に準拠。指示範囲（週次ボラバンド戦略の基本設計）に限定。乱数 seed は params 固定（決定論再現）。

### 8.2 品質保証方針

- 既存エンジンの MT5 bit-exact 突合資産・既存UC を無波及で保護（NFR-S1/S2）。新規ファイルのみ追加。
- 回帰テスト方針（user memory「bugfix-pair-with-regression-test」）：S/T/N 算出（数値例一致）・look-ahead 排除・seed 固定 SPA 再現の各々に「その間違いを禁止する回帰テスト」を添える。
- 既存規約整合：usecase は domain のみ依存（pandas 不可）・pandas は adapter 限定を CI/grep で担保。

### 8.3 テスト方針

- **単体（domain VO）**：VolatilityBand 不変条件（O−S>0・T>O）・VarianceForecast 推定可否・TradingWeek 境界・OcoOrderPair 択一決済。
- **単体（UC）**：(1) RS 週次集計の符号別二乗和、(2) S/T/N の数値例一致（NFR-D1）、(3) エントリ規則 E0/E1(θ) 真偽、(4) S4/S6 採否一意判定の全分岐（棄却/採用/不採用）、(5) ノートレード/区間空パス。
- **単体（adapter ポート実装・numpy）**：(1) HAR OLS+NW(lag4) の係数・予測、(2) SPA p 値の seed 固定再現（NFR-D3）・PW block 長、(3) Kupiec/Christoffersen 尤度比 p 値、(4) look-ahead 依存グラフ検証（当日以降 H/L/C 不参照・NFR-D4）。
- **結合**：WV1→WV2→WV3 のパイプライン決定論（同一入力で選択候補・採否が一意・NFR-D2）。既存データディレクトリ mtime 不変 assert（NFR-S2）。
- **テスト戦略概要**：合成小データで決定論・境界・look-ahead を検証。参照論文の既知数値例で検定実装を校正。

### 8.4 リリース・デプロイメント方針

- 環境構成：開発＝ローカル（staging/本番なし）。
- デプロイ戦略：該当なし（ライブラリ/ツールとしてリポジトリに追加）。外部コスト確定後に採否結論を再評価（TBD-1）。

---

## 9. リスク・課題

> 出力元：S-1 リスク列挙 ／ S-5 設計検証

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 対策の出典／根拠 |
|---|---|---|---|---|
| R-WV1 look-ahead 混入（σ̂・S・T・N に当日以降 H/L/C） | 高 | 中 | WV1 は「前週金曜引けまでの確定データのみ」で推定。依存グラフ検証（NFR-D4）を単体テストで担保 | 仕様 §3.1・§4.1・§6 |
| R-WV2 SPA seed 固定再現の失敗（B=5000 ブート） | 高 | 中 | numpy.random.Generator(seed) で乱数状態を seed から完全再現。同一 seed 2回実行で p 値一致を assert（NFR-D3） | 仕様 §4.1・§6 |
| R-WV3 統計検定 numpy 配置層の解釈分岐（依頼 vs 既存規約） | 中 | 確 | adapter ポート実装へ配置（DIP・依頼意図尊重）。既存 compute_stats.py 先例で usecase 実装も既存規約に反しないことを §10.2 に明記し詳細設計余地を残す | upstream-input-validation（実証）・§3.4 課題-WV2 |
| R-WV4 金曜引け強制手仕舞いの既存機構不在 | 中 | 中 | on_position_check 曜日判定 close を第一候補。既存 trading_end 相当の有無は詳細設計で確認（§9.3 TBD-3） | 仕様 §3.1-7・prompt-validation F3 |
| R-WV5 OCO・同一バー両到達の表現精度 | 中 | 中 | 既存 Order/pending_oco config で OCO を表現。同一バー両到達はストップ優先を約定順規約化 | 仕様 §2.3・§2.6・stop_entry_probe.py |
| R-WV6 既存ファイル無改変違反 | 高 | 低 | 新規ファイルのみ追加。既存ポート/UC/domain 差分 0 を CI/差分確認で担保（NFR-S1） | C2・依頼制約 |
| R-WV7 既存データ書込波及 | 高 | 低 | 読み取り専用＋出力先検証関数（§6.5）＋mtime 不変 assert | C1・NFR-S2・user memory「no-ripple」 |
| R-WV8 技術スタック無断追加（statsmodels 等） | 高 | 低 | pure numpy 実装に固定。新規ライブラリ追加 0 を requirements 差分確認で担保 | C3・CLAUDE.md 禁止事項 |

### 9.2 スケジュール・リソースリスク

- 本機能は新規ファイル群（usecase 3・adapter 4・domain 1パッケージ・tools 1）に限定され、既存改修を伴わないため波及リスクは限定的。主リスクは統計検定 numpy 実装の数値正確性（参照論文の既知数値例で校正）と seed 固定再現性。

### 9.3 今後の検討課題（TBD 一覧）

| 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|
| TBD-1：外部コスト実数値（c_spread/c_comm/r_fund・配当調整） | 仕様 §7 TBD-1。アルゴリズムの曖昧性ではなく数値入力。既定0.0・config 注入で検証は実行可だが採否結論はコスト確定後に再評価 | ブローカー確定後／config 注入 |
| TBD-2：検証の経験的結果（採否・選択候補(e,p_tp)） | 仕様 §7 TBD-2。手続きが返す値でありデータ実行まで未確定（設計の曖昧性ではない） | 検証実行時（WV3 出力） |
| TBD-3：金曜引け強制手仕舞いの既存機構表現 | 既存 Interactor に時間ベース強制決済（trading_end 相当）が無い可能性。on_position_check 曜日判定 close を第一候補とするが、既存約定/決済機構との整合を要確認 | 詳細設計（既存 Interactor/SessionCalendarPort/on_position_check 経路の確認） |
| TBD-4：週次予測ストア・出力レポートの新規パス規約 | 既存生成物を上書きしない出力先の命名規約・形式（CSV/JSON）・保持期間 | 運用方針として確定（§6.5 検証関数の許可ディレクトリ具体パス） |
| TBD-5：統計検定 numpy 実装の最終配置層（adapter vs usecase） | adapter 配置を採用するが、既存 compute_stats.py 先例（usecase numpy）も既存規約に反しない。実装者の最終選択余地 | 詳細設計（§10.2 の判断根拠を踏まえ確定） |
| TBD-6：イベント週判定データ（日銀/FOMC/SQ/主要指標）の供給元 | event_flag の別集計（§2.6・§4.2）に必要なイベントカレンダーの取得元・形式が未定 | 詳細設計／データ供給元確認 |

---

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| 半実現分散 RS⁺/RS⁻ | 5分リターンの符号別二乗和（正リターン＝RS⁺、負リターン＝RS⁻）の週次集計（仕様 §2.3） |
| Garman-Klass | OHLC を用いた全体ボラ推定量（週次集計・仕様 §2.3） |
| log-semivariance-HAR | log 半分散を被説明変数、{1週,4週平均,12週平均} を説明変数とする HAR 回帰（Corsi 2009 系） |
| Newey-West(lag=4) | 系列相関・不均一分散に頑健な HAC 共分散推定（lag=4） |
| Hansen SPA | Superior Predictive Ability 検定（データスヌーピング統制・Hansen 2005） |
| 定常ブートストラップ | Politis-Romano(1994) のブロック・ブートストラップ（ブロック長は幾何分布） |
| Politis-White block | Politis-White(2004) によるブロック長自動選択 |
| Kupiec 検定 | VaR/ストップ被覆率の尤度比検定（Kupiec 1995） |
| Christoffersen 検定 | 例外の独立性（クラスタリング不在）のマルコフ尤度比検定（Christoffersen 1998） |
| OCO | One-Cancels-the-Other（一方約定で他方取消・S 成行ストップと T 指値の択一） |
| IS / OOS | In-Sample（前半70%・学習）／ Out-of-Sample（後半30%・封印検証） |
| z(p_tp) | 利確分位点係数 z(p)=−Φ⁻¹(p/2)。z(0.40)=0.842 等（仕様 §2.5） |
| ノートレード | σ̂ 算出不可・窓<260週・規則偽でその週エントリしないこと |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| アーキテクチャ | 3関心3アクター分離＋新規ポート隔離 | 単一巨大UC / tools 直書き | 独立ライフサイクル・C2 無改変・C4 依存方向・DIP を同時充足 | 公式（SRP/DIP）＋仮説＋規約 |
| WV2 執行 IF | 既存 StrategyPort 実装（新 IB なし） | 新規 Input Boundary 追加 | 依頼確定・stop_entry_probe.py 手本で OCO/再アーム/SL-TP 表現可 | 規約＋実証 |
| 統計検定ライブラリ | pure numpy | statsmodels / scipy / arch | C3 新規ライブラリ追加禁止・SPA/PW block 非提供・seed 完全制御 | 規約（CLAUDE.md）＋仮説 |
| 統計検定の配置層 | adapter ポート実装（numpy 局所化） | usecase に numpy 直書き（compute_stats.py 先例） | 依頼「numpy/pandas は adapter に限定」を実証検証の上で意図尊重＋DIP。既存規約では usecase numpy も可のため詳細設計余地を §9.3 TBD-5 に明記 | 公式（DIP）＋既存規約（実証）＋仮説 |
| ポート抽象の配置 | usecase 層（Protocol/ABC） | adapter 層 | usecase→adapter の逆依存回避（C4）。optimize_ports.py が先例 | 公式（DIP）＋規約 |
| 新規ポート/VO の配置 | 新規ファイル（ports.py/domain 無改変） | 既存 ports.py/domain に追記 | C2 既存無改変。optimize_ports.py が新規ファイル追加の先例 | 規約（C2）＋実証 |
| 金曜引け手仕舞い | on_position_check 曜日判定 close | trading_end 機構新設 | 既存 StrategyPort 契約内で表現を第一候補。既存機構有無は TBD-3 | 仮説＋TBD |
| IS/OOS 分割 | 既存 run_is_oos 再利用 | WV3 で再実装 | DRY・既存決定論分割の部品再利用 | 公式（DRY）＋実証 |
| 外部コスト/配当 | config 注入・既定0.0 | ハードコード | 仕様 §7 TBD-1 が既定実行を許容・設定外部化 | 仕様＋仮説 |
| 乱数 seed | params 固定 | 実行時生成 | 決定論再現（NFR-D3・仕様 §4.1） | 仕様 |

### 10.3 参考資料

- `/workspaces/app/.doc/WEEKLY_VOL_BAND_SPEC_v1_0.md`（仕様 v1.0・一次情報）
- `simulator/usecase/ports.py`（既存ポート：StrategyPort L97-121 / MarketDataPort / SessionCalendarPort L146-158・無改変対象）
- `simulator/usecase/optimize_ports.py`（Protocol を usecase に置く新規ファイル先例・「committed ports.py は編集しない（C2）」L5）
- `simulator/usecase/run_is_oos.py`（IS/OOS 分割：WV3 再利用元・slice_is_bars 純関数・usecase は domain のみ依存 L7）
- `simulator/usecase/optimize.py` / `walk_forward.py`（run_is_oos 部品再利用の先例）
- `simulator/usecase/compute_stats.py`（L36 `import numpy as np`＝usecase で numpy 使用の既存先例・課題-WV2 の証拠）
- `simulator/adapter/strategy/stop_entry_probe.py`（StrategyPort 実装手本：on_new_bar/on_tick で Order・SL/TP・OCO config）
- `simulator/domain/bar.py`（L19-50 frozen VO 様式・「domain 層は numpy のみ依存可」L9＝新規 VO の手本・C4 の証拠）
- `simulator/usecase/models.py`（BacktestStats L91-142）
- `/workspaces/app/.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md`（体裁の手本・出力先検証 §6.5 の手段）
- 仕様 §関連ドキュメント主要論文：White(2000)/Hansen(2005)/Sullivan-Timmermann-White(1999)/Politis-Romano(1994)/Politis-White(2004)/Corsi(2009)/Barndorff-Nielsen-Kinnebrock-Shephard(2010)/Garman-Klass(1980)/Kupiec(1995)/Christoffersen(1998)

### 10.4 関連する標準・規格

- クリーンアーキテクチャ（依存方向の内向き規律・DIP）：本リポジトリの既存規約（run_is_oos.py L7・ports.py L7・bar.py L9「domain は numpy のみ依存可」）。
- SRP / DRY / YAGNI（公式設計原則）：3関心分離・既存資産（run_is_oos/StrategyPort/Interactor）の複製回避・最適化ループを20候補固定に限定。
- 統計検定の原典：仕様 §関連ドキュメントの主要参照論文（pure numpy 実装の根拠）。
