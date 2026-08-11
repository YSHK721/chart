# prototype_260811-01 — 口座状態エンジン（証拠金・ロスカットの参照実装／ISSUE-369）

発注計画（方向・建値・ロット・損切り・利確）を **実 tick（bid/ask）へ順に適用**し、決済までの
口座状態（残高・有効証拠金・必要証拠金・証拠金維持率）とイベント（約定・損切り・利確・
ロスカット）をティック粒度で再現する。本プロトタイプが **OANDA 証券 JP225 CFD の証拠金・
ロスカット計算の参照実装（権威）**であり、ポジションサイズ計算機
（`integrated_position_sizing_calculator.html`）と将来のチャート統合（ISSUE-368）の式は
ここの実測へ一致させる。

## アクター構成（SRP・Phase 2 で simulator/ へ恒久化済み）

| アクター | ファイル（恒久化後） | 責務 |
|---|---|---|
| 口座状態エンジン | `simulator/usecase/account_engine.py` | 口座状態の計算のみ（標準ライブラリのみ・データ読込もレポートも持たない） |
| レポート提示 | `simulator/adapter/presenter/account_report_build.py` | 状態時系列 JSON → グラフ入り単体 HTML（計算しない・サーバ不要） |
| データ供給 | `simulator/tools/run_account_scenario.py` | 発注計画 JSON ＋ 期間 → `marketdata.tick_m1`（tick tree レイアウト単一権威）で tick を読みエンジンへ流す CLI |
| 式の検定 | `verify.py`（本ディレクトリ・検証記録） | 公式閉形式・修正前式とエンジン実測の数値突き合わせ |
| アクター突合 | `parity_check.py`（本ディレクトリ・検証記録） | simulator 既存口座アクター（MT5 規約）との同一 tick 実測比較 |
| 回帰ゲート | `simulator/tests/unit/test_account_engine{,_regression}.py` | 挙動単体＋移設 byte 一致ゲート（fixture: `simulator/tests/fixtures/account_engine/`） |

## 使い方（リポジトリ直下から）

```bash
VENV=/workspaces/app/lightweight-charts-python-main/.venv/bin/python
export MARKETDATA_DATA_DIR=/workspaces/app/data/marketdata   # worktree から実行する場合

# 1) シナリオ実行（発注計画 → 口座状態時系列 JSON）
$VENV simulator/tools/run_account_scenario.py --plan prototype_260811-01/plans/long_stop.json \
    --start 2026-08-06 --end 2026-08-06 --out prototype_260811-01/out/long_stop.json --sample 20

# 2) レポート生成（ブラウザで開くだけの単体 HTML）
$VENV simulator/adapter/presenter/account_report_build.py \
    --in prototype_260811-01/out/long_stop.json --out prototype_260811-01/out/long_stop.html

# 3) 式の検定・アクター突合（本ディレクトリから）
$VENV prototype_260811-01/verify.py
$VENV prototype_260811-01/parity_check.py

# 4) 回帰テスト
$VENV -m pytest simulator/tests/unit/test_account_engine.py \
    simulator/tests/unit/test_account_engine_regression.py -q
```

発注計画 JSON の形式は `simulator/tools/run_account_scenario.py` の docstring 参照
（`plans/` に代表 4 シナリオ）。

## 口座モデル（出典: `docs/oanda_indices_cfd_about.md` ＝ OANDA 証券公式ページの再構成）

- **必要証拠金 ＝ 約定代金 × 証拠金率**（§3(2)「約定代金に必要証拠金率を乗じて算出」・
  既定 10%）。既定 `margin_basis="entry"`（建値固定）。時価基準 `"mark"` は比較用に残す。
- 有効証拠金 ＝ 口座残高 ＋ 評価損益（§3(3) 値洗い）。評価価格はロング＝bid／ショート＝ask（§2(5)）。
- 証拠金維持率 ＝ 有効証拠金 ÷ 必要証拠金 × 100（§1-2）。**100% 以下でロスカット・
  マージンコールなし**（§1）。「損失の大きい建玉から順に、維持率が 100% を上回るまで継続」
  （§1-2＝実装と一致）。**ロスカットは逆指値より優先**（§2(9)③＝tick 内判定順に反映済み）。
- 指値は指値価格で約定（ロング: ask≤price ／ ショート: bid≥price 到達時）。
  成行は当該 tick のロング＝ask／ショート＝bid。

## 確定した式（実測 2026-08-11・verify.py 全 10 検定合格）

採点の基準（正解）は公式文書由来の式（`official_required_margin` / `official_losscut_price`）。
修正前の計算機の式（`superseded_mark_based_losscut_price`）は正解ではなく記録用。

1. **【公式】ロスカット価格（V1）**: 約定代金固定の閉形式
   **X = avgP·(1+mr) − E/U（ロング）／ avgP·(1−mr) + E/U（ショート）** が実測と一致。
   - ロング（2026-08-06・25 単位・E=172,000）: X=65,188.19 / 実測 65,186.32（差 −1.87pt）
   - ショート（2026-08-10・同）: X=66,433.33 / 実測 66,435.48（差 +2.15pt）
   - 差は tick 間ギャップ由来。**これが公式仕様準拠の正式（golden fixture の基準）**。
2. **【公式】必要証拠金（V2）**: 公式式（約定代金×証拠金率）と実測が**完全一致**・
   保有中の変動 **0**（公式仕様どおり建値固定）。
3. **mark 基準の内部整合（V3/V4・比較用）**: 時価連動モデル自体はシミュレーションと
   閉形式が一致（ロング差 −0.34pt／ショート差 +1.11pt）。公式仕様ではない。
4. **U1 の感度（V5）**: mark 基準における時価の mid/trade-side 解釈差はトリガー価格 0.0pt。
5. **【記録】修正前の式の代数同値（V6）**: 2 段式（lcDistCore×mFactor）はそれ自身の閉形式と
   同値（最大差 2.9e-11）＝誤りは実装ではなく**前提（時価連動）**だった。
6. **【記録】式差（V7・ISSUE-370 の根拠）**: 公式式と修正前の式の差は同一条件で
   **ロング +36.5pt（修正前は危険側＝実際より遠く表示）／ショート +24.0pt（保守側）**。
   計算機 HTML は 2026-08-11 に公式式へ修正済み（ISSUE-370 RESOLVED）。

## 実測済みシナリオ（plans/ → out/*.html・margin_basis="entry" 公式基準）

| シナリオ | 期間 | 結果 |
|---|---|---|
| `long_stop` | 08-06 | 損切り 65,100 指定 → 65,097.0 で約定（3pt すべり＝tick ギャップ）・−8,391 円 |
| `long_tp_split` | 08-06 | 難平 3 本（成行/65,300/65,000）全約定 → 65,801.5 で利確 +13,737 円。**維持率最小 105.9%**（難平が証拠金危険域へ近づく実測） |
| `long_losscut` | 08-06 | 維持率 100% 到達 → 65,186.3 で強制決済 −8,255 円（時価基準比で早く・浅く発動） |
| `short_weekend_stop` | 08-07〜10 | 金曜建て → 週末跨ぎ → 月曜 66,803.5 で損切り −15,483 円 |

## 公式文書との突き合わせで解決・確定した事項

- **U2（解決）**: ロスカット執行は「損失の大きい建玉から順に、維持率が 100% を上回るまで
  継続」（§1-2）＝実装と一致。
- **U4（解決・実装修正済み）**: 「ロスカット取引が発生した場合は、同取引が優先される」
  （§2(9)③）＝tick 内判定順をロスカット→損切り→利確へ変更。
- **必要証拠金の基準（解決・実装修正済み）**: 約定代金固定（§3(2)）。時価連動は公式記載に
  ない＝計算機 HTML の mFactor は要修正（ISSUE-370）。

## 未反映事項（推測で断定しない）

- **[U1]** `margin_basis="mark"`（比較用）における時価の bid/ask/mid 解釈は公式記載なし
  （V4 で実務影響 0.0pt を確認済み。既定の "entry" では無関係）。
- **[U3]** 損切り・ロスカットの約定価格はトリガー tick の評価価格（成行）。公式もスリッページ・
  価格非保証を明記（§1-3/§3(3)）。板・実スリッページ分布は未反映（tick 間ギャップのみ再現）。
- **[U5]** ファイナンシングコスト（金利相当額・§2(8)）・配当相当額は未実装。複数日保有の
  実損益はこれらの日次受払分だけずれる（率は公式 financing ページ・Phase 2 で実装検討）。
- **[U6]** 公式は「一定の時間間隔で値洗い」（§3(3)）。本実装は毎 tick 判定＝発動が最速側。

## 次の段階（Phase 2・恒久化）

market_profile と同じ 2 フェーズ方式で `simulator/` 配下へ取り込む：

1. byte 一致回帰ゲート（代表シナリオの出力 JSON 固定）を先に置く。
2. `git mv` ＋ import retarget を 1 コミットで（エンジン純ロジック → `simulator/usecase/`、
   レポート生成 → `simulator/adapter/presenter/`）。
3. **golden fixture 出力**：ISSUE-368（チャート UI 統合）の JS 側証拠金・ロスカット検定に
   使う JSON を吐くスクリプトを含める。
