# IS/OOS ウォークフォワード（Walk-Forward）基本設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v0.2.0
- 作成者：system-basic-design エージェント
- 承認者：（未承認・レビュー待ち）
- 上位計画：`.doc/ISOOS_BROWSER_PLAN_WIP.md`（Phase 1・アクター D＝検証方法論／窓設計）
- 前段成果物（再利用元・無改変）：
  - サブフェーズ1（SP1）：`simulator/usecase/run_is_oos.py`／`simulator/tools/run_is_oos_cli.py`／`.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md`／`.doc/ISOOS_SIMPLE_SPLIT_DETAILED_DESIGN.md`
  - サブフェーズ2（SP2）：`simulator/usecase/optimize.py`／`simulator/usecase/optimize_ports.py`／`simulator/usecase/optimize_strategies.py`／`simulator/tools/optimize_cli.py`／`.doc/ISOOS_OPTIMIZATION_BASIC_DESIGN.md` v0.2.0／`.doc/ISOOS_OPTIMIZATION_DETAILED_DESIGN.md`
- 変更履歴：
  - v0.1.0 (2026-06-20) 初版。IS/OOS サブフェーズ3「ウォークフォワード」の基本設計。SP1（区間スライス／区間定義／劣化レポート）と SP2（`optimize`／探索 Port／目的 Port）を**部品として再利用**し、新規ファイルのみで WF を実現。SP2 再利用前提として `optimize_cli.py` のレビュー指摘 🟡-2（`--search-algo random` の `--seed`/`--n-samples` 未指定時 TypeError＋非決定論）を入力検証で解消する方針を含む。
  - v0.2.0 (2026-06-20) spec-reviewer 指摘（B-1/B-2/C-1/C-2/H-1/H-2/H-3/M-1/L-1/L-2）を決定論的に解消。主な確定事項：(B-1) `make_run_segment_factory` 戻り値 `full_bars`（全期間）は破棄し `window_bars_provider(is_start, oos_end)` の当窓 full のみを `optimize(full_bars=...)` に渡す（§4.2 WF-F2・§6.1）。(B-2) `search_space` キーを `build_interactor` 受理キーワードの部分集合に限定し未知キーを入口で明示中断（§4.5）。(C-1) WF 効率/劣化集約の対象指標を `profit` に一意固定し `ratio is None` 窓を集約から除外＋件数ログ（§4.2 WF-F5）。(C-2) 🟡-2 入口検証を `main()` 内 `parser.error()`（終了コード2）で一意確定（§4.5・§7.2）。(H-1) `theoretical_count == 実 IS run 数` を Port 契約化し総 run 式を明記（§7.1）。(H-2/H-3) 窓生成終了条件を `<=` 式で確定し最終窓端数（部分 OOS）を切り捨て（§4.2 WF-F1・§5.2）。(M-1) `BacktestStats` 全フィールドを加法総和可/母数再計算可/連結不能の 3 分類で列挙（§5.2）。(L-1/L-2) span 既定型＝時刻型を明示し時刻型比較の引用行を `run_is_oos.py:35` へ修正（§5.2）。SP1/SP2/committed 公開 IF は無改変（C-W2 維持）。

---

## 2. プロジェクト概要

> 出力元：S-1 要件分析と設計方針決定

### 2.1 システム概要

- **位置付け**：committed バックテストエンジン（`simulator/`・MT5 bit-exact 突合済）の上に載るオーケストレーション層の**第 3 段**。SP1 が「固定 1 組を IS/OOS 並列評価」、SP2 が「探索空間×目的関数で IS を探索し best を OOS 検証（1 窓のみ）」であるのに対し、本サブフェーズ3（WF）は **IS/OOS の窓を前方へ step ずつ転がしながら、各窓で SP2 の `optimize`（IS 探索→best 確定→OOS 検証）を反復し、各窓 OOS の結果を連結（stitch）して通期のアウトオブサンプル成績＝ロバスト性を評価する**。
- **解決する業務課題**：SP2 の単一 split は「学習区間と検証区間の取り方が 1 通りに固定」されており、その 1 分割でたまたま OOS が良好／不良だった可能性を排除できない。WF は窓を転がして「各時点で最新 IS から最適化したパラメータを直後の未知 OOS で検証する」手続きを通期反復し、**時間方向に複数の独立した OOS 検証を積み重ねる**ことで、過剰最適化に対する戦略の頑健性（OOS 成績の安定性・WF 効率）を定量化する。
- **3 サブフェーズ中の位置**：「①単純分割 → ②最適化 → ③ウォークフォワード」の**最終段**。WF は SP1（区間スライス・区間定義）と SP2（窓内最適化）を**統合する土台**であり、本書をもって IS/OOS 機能の方法論レイヤ（アクター D/E）が完結する。

### 2.2 開発目的・背景

- **背景（実コードで実証）**：
  - SP2 `optimize(*, request, full_bars, make_run_segment, search_port, objective_port) -> OptimizeResult` が `simulator/usecase/optimize.py:91-206` で確立済。`OptimizeResult` は `best_params`／`best_is_stats`／`best_is_score`／`oos_stats`／`degradation`（`DegradationReport`）／`trials`／`excluded_count`／`total_candidates`／`finite_candidates` を保持する（同ファイル `:76-89`）。WF は**窓ごとに `optimize` を 1 回呼ぶ**。
  - SP1 `slice_is_bars(bars, split)`（`run_is_oos.py:26-39`）が「`bar.time < split` の head-prefix を打ち切り返却・副作用なし・時刻昇順前提」の純関数として確立済。WF の窓スライス（IS_i／OOS_i の区間切り出し）に再利用する。
  - `.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 L48 のレイヤリング図が `usecase/walk_forward.py` を「窓ごとに IS最適化→凍結→OOS評価 を反復・統合」として構想済（同 §3 で実証）。
- **達成したい目標**：committed エンジンと SP1／SP2 の公開 IF を**壊さず再利用**し、(1) 窓スケジュール（anchored／rolling・窓幅・step・OOS 幅）を入力に取り、(2) 各窓 i について SP1 区間定義で IS_i／OOS_i 境界を決定論的に算出し、(3) 各窓で SP2 `optimize` を呼んで窓別 best／IS／OOS を得て、(4) 各窓 OOS を連結して通期 OOS 成績と窓別レポート・WF 効率を出力する、新規 UC `simulator/usecase/walk_forward.py` と純関数の窓スケジューラを追加する。

### 2.3 適用範囲・制約条件

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 概要 |
|---|---|
| FR-W1 | 窓スケジュール入力（方式＝anchored/rolling・IS 幅・OOS 幅・step・全期間境界）を受け取り、窓列 [(IS_i 区間, OOS_i 区間)] を決定論的に生成する。窓 i は終了条件式（H-3）を満たす限り i=0,1,… を採番し、`oos_end_i <= global_end` を満たさなくなった時点で生成を打ち切る（H-2 端数切り捨て） |
| FR-W2 | 各窓 i について IS_i／OOS_i 区間境界（時刻）を SP1 区間定義に整合する形で算出する（anchored＝IS 起点固定・拡張／rolling＝IS 固定幅・移動）。全境界は半開区間 [start, end) で表現し SP1 `slice_is_bars` の `bar.time < split` 比較（`run_is_oos.py:35`）と整合させる |
| FR-W3 | 各窓 i について SP2 `optimize` を 1 回呼び、窓別 `OptimizeResult`（best_params／best_is_stats／oos_stats／degradation 等）を得る |
| FR-W4 | 各窓 OOS（OptimizeResult.oos_stats）を窓順に連結（stitch）し、通期 OOS 成績集約を算出する |
| FR-W5 | 窓別レポート（窓 ID・IS_i/OOS_i 境界・窓別 best_params・IS best 値・OOS 値・劣化）を出力する |
| FR-W6 | WF 効率（窓別 OOS/IS の集約指標・通期 OOS 集約）を出力する。集約対象指標は `profit` に一意固定し（C-1）、`ratio is None`（IS 値=0）の窓は集約から除外し除外件数をログ出力する |
| FR-W7 | 窓スケジュールが 1 窓も生成できない（全期間 < IS 幅+OOS 幅）／窓境界が不正（IS_i 空・OOS_i 空）の場合は無音継続せず明示中断する |
| FR-W8 | SP2 再利用の前提として、探索アルゴリズム入力（`random` 時の seed／n_samples）の未指定を**入力検証で明示中断**し、決定論性を担保する（🟡-2 解消） |
| FR-W9 | `search_space` のキーが `build_interactor` 受理キーワードの**部分集合**でない（未知キーを含む）場合は tools 入口で明示中断する（B-2・`make_run_segment_factory` 内 `build_interactor(**{**base_kwargs, **params})` の TypeError 必発を前置回避） |

#### 非機能要件サマリー（数値目標を含む）

| 区分 | 数値目標 |
|---|---|
| 性能（NFR-WP1） | エンジン実行回数＝**多段 N 比例**。WF 全体の engine run 回数 = Σ_i (N_cand_i + 1)。各窓 i で SP2 が「N_cand_i 回の IS run ＋ 1 回の OOS run」を行う（`optimize.py:133-188` で実証：候補ごと IS run、best で OOS run 1 回）。窓数を W、各窓の探索候補数を N_cand とすると総 engine run = **W × (N_cand + 1)**。SP2 詳細設計（NFR-OP1）に従い各 run の前に `build_interactor` が 1 回ずつ実行され内部で CSV を再ロードするため、**CSV ロードも W × (N_cand + 1) 回**発生する。WF はこの乗算コストを**無音で切り捨てず**、窓数・候補数・総 run 見積りをログ明示する。 |
| 性能（NFR-WP2） | 窓数 W の上限を**必須入力**で明示する（既定なし）。W × (N_cand + 1) の総 run 見積りを実行前に算出し、上限超過時は件数をログ出力した上で**拒否**する（SP2 `max_candidates`・M-2 単一動作の WF 版・無音切り捨て禁止）。 |
| 決定論性（NFR-WD1） | 同一入力（データ・窓スケジュール・探索空間・探索アルゴリズム・目的関数・config）に対し、窓列・窓別 best params・通期 OOS 集約・WF レポートがバイト同一で再現される。窓境界算出は整数/`numpy.datetime64` 上の決定論的算術（§3.4・§5.2）。各窓の `optimize` 決定論性（SP2 NFR-OD1：辞書順序規約＋seed 固定 random）を**そのまま継承**する。 |
| 既存データ非波及（NFR-WS1） | `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`・既存生成物への書き込み 0 件。SP1 出力先検証 `assert_safe_output_dir`（`run_is_oos_cli.py:67`）を再利用して計測可能に担保。 |
| committed/SP1/SP2 無改変（NFR-WS2） | `simulator/domain`・`simulator/usecase`（既存：`run_backtest.py`／`models.py`／`run_is_oos.py`／`optimize.py`／`optimize_ports.py`／`optimize_strategies.py`）・既存 `simulator/adapter`・`simulator/main`・SP1/SP2 既存 tools の差分 0 行。WF は**新規ファイルのみ**で実現する。 |

#### 制約条件（技術 / 運用 / プロジェクト規約）

| 区分 | 制約 |
|---|---|
| C-W1（絶対制約・最優先） | 既存データの改変・波及を絶対禁止する。`marketdata/`・`fixtures/`・`confirmation/`・既存生成物は**読み取り専用**。出力は新規 OUT ディレクトリのみ（`assert_safe_output_dir` で担保）。 |
| C-W2（絶対制約） | committed エンジン無改変。SP1（`run_is_oos.py`／`run_is_oos_cli.py`）・SP2（`optimize.py`／`optimize_ports.py`／`optimize_strategies.py`／`optimize_cli.py`）の**公開 IF を壊さない**。既存 `ports.py` 等は編集しない。WF は SP1/SP2 を部品として再利用し、新規ファイルのみで実現する。 |
| C-W3（絶対制約） | 技術スタック追加禁止。WF は標準ライブラリ＋既存 domain／usecase のみで構成する（pandas・新規依存を usecase に持ち込まない）。 |
| C-W4（プロジェクト規約） | クリーンアーキ依存方向。新 UC `walk_forward.py` は domain のみ依存（adapter/framework/main・pandas を import しない）。tools 入口のみ Composition Root として `simulator.main`／pandas を許容（SP1/SP2 tools と同層）。 |

#### 設計上の課題と技術的リスク

| ID | 課題 / リスク | 設計上の扱い |
|---|---|---|
| 課題-W1 | SP2 `optimize` は `oos_stats` を**窓集約済 `BacktestStats`**で返すのみで、**per-trade／equity_curve は返さない**（`run_segment` が `result.stats` のみ返す＝`run_is_oos_cli.py:49`／`make_run_segment_factory` も同 `make_run_segment` を閉包＝`optimize_cli.py:52`）。したがって既存公開 IF のみでは「連結 OOS のトレード列・連結エクイティ曲線」は構築できない。本書は連結（stitch）を**窓別 OOS `BacktestStats` の集約**として定義する（§4.2 FR-W4）。トレード列レベルの連結は §9.3 TBD-W1 として後段に委譲する。 |
| 課題-W2 | 🟡-2：`optimize_cli.py:59-66` `_build_search_port` は `RandomSearch(seed=args.seed, n_samples=args.n_samples, ...)` を呼ぶが、`--seed`/`--n-samples` は default=None（`optimize_cli.py:192-193`）。`RandomSearch.candidates` は `k = min(self.n_samples, n_space)`（`optimize_strategies.py:97`）で `min(None, int)` → **TypeError**、かつ `random.Random(None)`（同 `:105`）→ **非決定論**。WF は窓ごとに同機構を再利用するため、再利用前に解消必須。本書 §4.5・§7.2 で **WF tools 入口の入力検証として明示中断**を規定する（SP2 既存ファイルは無改変・C-W2 維持）。 |
| 課題-W3 | 窓境界の時刻型整合。`slice_is_bars` は `bar.time < split` の比較に依存（`run_is_oos.py:35`）。WF が窓境界を算術生成する際、時刻は `numpy.datetime64 \| int`（domain 規約・`optimize.py:10`）で扱い、CLI 文字列→時刻型正規化は SP1 `normalize_time`（`run_is_oos_cli.py:54`）を tools 層で再利用し、UC には正規化済時刻のみ渡す。 |
| 課題-W4 | anchored の OOS 直前接続。anchored は IS 起点固定で IS_i が窓ごとに拡張する。各窓の OOS_i が「直前 OOS_{i-1} の終端＝当窓 OOS_i の始端」となるよう step を OOS 幅と一致させる構成を既定とし、非一致時の重なり/隙間挙動を明示する（§6.2）。 |
| 課題-W5（B-2） | `search_space` のキー制約。`make_run_segment_factory` の `factory` は `build_interactor(**{**base_kwargs, **params})`（`optimize_cli.py:51`）を呼ぶが、`build_interactor`（`main/__init__.py:256-286`）は固定キーワード引数のみで `**params` を受理しない。`search_space` に未知キーがあると **TypeError 必発**。WF 入口で `search_space` キーを `build_interactor` 受理キーワードの部分集合に制約し、未知キーを明示中断する（§4.5 FR-W9）。SP2 既存ファイルは無改変（C-W2）。 |

---

## 3. システムアーキテクチャ

> 出力元：S-1 採用パターン選定 ／ S-2 アーキテクチャ設計

### 3.1 全体構成図

```
[tools 入口（新規）] simulator/tools/walk_forward_cli.py   ← Composition Root（main/pandas 許容）
   1. CLI 引数解釈（窓スケジュール・探索空間・探索アルゴリズム・目的関数・base_kwargs）
   2. 🟡-2 入力検証（random 時 seed/n_samples 必須・未指定で明示中断）
   3. normalize_time（SP1 再利用）で全期間境界/窓パラメータを時刻型へ正規化
   4. make_run_segment_factory（SP2 再利用・課題-O1）で params->run_segment を構成
        （戻り値 full_bars＝全期間は window_bars_provider のスライス元に使うのみ・UC へ渡さない＝B-1）
   4'. B-2 search_space キー検証（build_interactor 受理キーワード部分集合・未知キー→parser.error）
   5. assert_safe_output_dir（SP1 再利用）で出力先ガード
   6. walk_forward(...) を呼ぶ → 窓別/連結レポートを新規 OUT へ書込
        │
        ▼
[UC（新規）] simulator/usecase/walk_forward.py            ← domain のみ依存
   - schedule_windows（純関数・窓スケジューラ）で窓列を決定論生成（FR-W1/W2）
   - 各窓 i: optimize(request=OptimizeRequest(...窓 i 境界...), ...) を呼ぶ（SP2 再利用・FR-W3）
   - stitch_oos（純関数）で窓別 oos_stats を連結集約（FR-W4）
   - WalkForwardResult（窓別 OptimizeResult 列＋連結 OOS 集約＋WF 効率）を返す
        │
        ▼ （SP2 を窓ごとに呼ぶ・既存無改変）
[UC（既存・SP2）] simulator/usecase/optimize.py          ← optimize(...) -> OptimizeResult
   ├─ ParameterSearchPort（GridSearch/RandomSearch・optimize_strategies.py）
   └─ ObjectivePort（PF/Net/Sharpe/Recovery・optimize_strategies.py）
        │
        ▼ （make_run_segment で 1 区間実行・既存無改変）
[UC（既存）] run_backtest 系 / controller._interactor.execute（committed・無改変）
   - 1 run プリミティブ。窓×候補回呼ばれるだけ。
        │
        ▼
[既存・SP1] slice_is_bars / 区間定義 / DegradationReport（run_is_oos.py・無改変で再利用）
```

### 3.2 アーキテクチャパターン選択理由

| パターン | 内容 | 評価 |
|---|---|---|
| **採用：オーケストレーション UC ＋ 純関数スケジューラ（SP1/SP2 委譲・クリーンアーキ準拠）** | WF を新 UC `walk_forward.py` に置き、窓生成を副作用なし純関数 `schedule_windows` に分離、窓内最適化は SP2 `optimize` へ委譲、連結は純関数 `stitch_oos` に分離。tools 入口で DI を結線。 | 採用。SP2（`optimize.py`）が同型の「UC＋Port＋tools 結線」で確立済（実証済パターンの踏襲＝学習コスト・整合コスト最小）。窓設計変更（D）も探索法変更（E）もエンジンに波及せず MT5 突合資産を保護（C-W1/C-W2 整合）。決定論は純関数化で担保。 |
| 代替1：SP2 に窓ループを追加（`optimize` を拡張） | `optimize` に「窓スケジュール引数」を足し内部で反復させる。 | 棄却。SP2 公開 IF（`optimize(*, request, full_bars, make_run_segment, search_port, objective_port)`）の破壊＝C-W2 違反。単一窓 SP2 利用者（`optimize_cli.py`）にも波及。関心（窓設計＝D／最適化＝E）の混在で `.doc/ISOOS_BROWSER_PLAN_WIP.md` §2 のアクター分離原則に反する。 |
| 代替2：tools 層に窓ループを置く（UC を作らない） | `walk_forward_cli.py` が窓を回し `optimize` を直接反復。UC を新設しない。 | 棄却。窓スケジュール算出（FR-W1/W2）と連結集約（FR-W4）はドメインロジックであり、tools（Composition Root）に置くと pandas/main へ依存しない純粋ロジックがテスト困難な結線層に埋没。クリーンアーキ依存方向（C-W4）に反する。UC 化で domain 依存のみの純関数として単体テスト可能になる。 |

出典区分：採用は SP2 実装パターン（`optimize.py`／`optimize_cli.py`）の踏襲＝プロジェクト内既存規約＋（実務的推奨／仮説）。クリーンアーキ依存方向は公式設計原則（Clean Architecture・依存は内向き）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| UC（`walk_forward.py`） | Python 標準ライブラリ（dataclasses／typing）＋既存 domain／SP1 SP2 usecase | 既存リポジトリ準拠（追加なし） | 外部ライブラリでの窓分割 | C-W3（技術スタック追加禁止）。SP2 UC（`optimize.py`）が標準ライブラリ＋domain のみで成立済（同ファイル import 群で実証）を踏襲。 |
| 窓スケジューラ | 純関数（標準 `numpy.datetime64`/`int` 上の算術） | 既存準拠 | pandas 期間生成（`pd.date_range`） | pandas は usecase に持ち込まない（C-W4・SP1/SP2 と同方針）。窓境界は domain 時刻型上の決定論的算術で表現。 |
| tools 入口（`walk_forward_cli.py`） | `argparse`／`json`／`pathlib`＋`simulator.main`＋pandas（時刻正規化のみ） | 既存準拠 | 新規 CLI フレームワーク | SP1/SP2 tools（`run_is_oos_cli.py`／`optimize_cli.py`）と同層・同スタック。pandas は tools に閉じる（`normalize_time` 再利用）。 |

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| tools（`walk_forward_cli.py`・新規） | CLI 解釈／🟡-2 入力検証／時刻正規化／DI 結線／出力ガード／レポート書込 | `simulator.main`・pandas・SP1 tools（`normalize_time`／`assert_safe_output_dir`）・SP2 tools（`make_run_segment_factory`・port builder）・新 UC | Composition Root（SP1/SP2 tools と同層）。プロジェクト規約より。 |
| usecase（`walk_forward.py`・新規） | 窓スケジュール生成（`schedule_windows`）／窓ごと `optimize` 呼出／OOS 連結集約（`stitch_oos`）／WF 結果構築 | domain のみ＋SP2 `optimize`／SP1 `slice_is_bars`・区間定義（内向き・usecase 内同士） | クリーンアーキ依存方向（公式：依存は内向き）。C-W4。 |
| usecase（SP2・既存・無改変） | 窓内の IS 探索→best 確定→OOS 検証（`optimize`） | domain／SP1 純関数 | C-W2（SP2 無改変）。`optimize.py` 実証。 |
| usecase（SP1・既存・無改変） | 区間スライス（`slice_is_bars`）・区間定義・劣化レポート（`DegradationReport`） | domain | C-W2（SP1 無改変）。`run_is_oos.py` 実証。 |
| domain（committed・無改変） | 1 run のエンジン実行・`BacktestStats` | なし（最内核） | C-W2（committed 無改変）。 |

依存方向：tools → usecase(WF) → usecase(SP2/SP1) → domain。循環なし。逆向き依存なし（domain は usecase を知らない）。

---

## 4. 機能設計

> 出力元：S-3 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID |
|---|---|---|---|---|
| WF-F1 | 窓スケジューラ（純関数） | 方式・IS 幅・OOS 幅・step・全期間境界から窓列 [(IS_i, OOS_i)] を決定論生成 | 高 | FR-W1, FR-W2 |
| WF-F2 | 窓内最適化（SP2 結線） | 各窓 i で `optimize` を 1 回呼び窓別 `OptimizeResult` を得る | 高 | FR-W3 |
| WF-F3 | OOS 連結（stitch・純関数） | 窓別 `oos_stats`（BacktestStats）を窓順に連結集約し通期 OOS 成績を算出 | 高 | FR-W4 |
| WF-F4 | 窓別レポート | 窓 ID・境界・best_params・IS/OOS 値・劣化の表を出力 | 高 | FR-W5 |
| WF-F5 | WF 効率集約 | 窓別 OOS/IS 比の集約・通期 OOS 集約を出力 | 中 | FR-W6 |
| WF-F6 | 窓不成立の明示中断 | 窓 0 件／窓境界不正を無音継続せず中断（`WalkForwardError`） | 高 | FR-W7 |
| WF-F7 | 探索入力検証（🟡-2 解消） | random 時 seed/n_samples 未指定を明示中断（`parser.error`・終了コード2）、決定論担保 | 高 | FR-W8 |
| WF-F8 | 探索キー検証（B-2 解消） | `search_space` キーが `build_interactor` 受理キーワードの部分集合でない場合に明示中断（TypeError 前置回避） | 高 | FR-W9 |

### 4.2 機能詳細仕様（主要機能のみ）

**WF-F1 窓スケジューラ `schedule_windows`（純関数・H-2/H-3 決定論化）**
- 入力：`mode`（"anchored" | "rolling"）／`global_start`・`global_end`（全期間境界・時刻型）／`is_span`（IS 幅）／`oos_span`（OOS 幅）／`step`（前進量）。幅・step の単位は窓境界算出方針（§5.2）に従う（既定＝時刻 span・L-1）。
- 出力：窓列 `[WindowSpec(index, is_start, split, oos_end)]`（`split`＝IS_i 終端＝OOS_i 始端、半開区間 [is_start, split) が IS_i、[split, oos_end) が OOS_i）。
- 前提：`global_start <= global_end`、`is_span >= 1`、`oos_span >= 1`、`step >= 1`、全期間が IS 幅+OOS 幅以上。
- 後条件：各窓は SP1 区間定義と整合（IS_i は `bar.time < split` の head-prefix＝`run_is_oos.py:35`、OOS_i は `bar.time >= split` 側）。
- 窓境界の定義（i = 0, 1, 2, …）：
  - rolling：`is_start_i = global_start + i×step`、`split_i = is_start_i + is_span`、`oos_end_i = split_i + oos_span`。
  - anchored：`is_start_i = global_start`（固定）、`split_i = global_start + is_span + i×step`、`oos_end_i = split_i + oos_span`。
- **窓生成終了条件（H-3・`<=` 明記）**：窓 i は次式を満たす場合**のみ**生成する。満たさなくなった最初の i で生成を打ち切る（以降の i も生成しない＝単調性）。
  - rolling：`global_start + i×step + is_span + oos_span <= global_end`
  - anchored：`global_start + is_span + i×step + oos_span <= global_end`
  - 両式とも `oos_end_i <= global_end` と等価（**H-2 端数規約**：`oos_end_i <= global_end` を満たさない端数窓〔OOS 幅が `oos_span` 未満になる部分窓〕は採用しない＝決定論的に切り捨て）。
- **global_end クリップ規約（半開区間・H-2）**：採用窓は全て `[is_start_i, oos_end_i) ⊆ [global_start, global_end)` を満たす（`oos_end_i <= global_end`）。`global_end` は半開区間の右端（exclusive）。最終窓の右余り区間（`oos_end_last < global_end` の残バー）は当該 WF 実行では未使用となる（決定論・無音切り捨てではなく規約による設計上の不採用）。
- 例外：上記前提違反・終了条件を満たす窓が 1 つも存在しない（窓 0 件）で `WalkForwardError`（無音禁止・FR-W7）。空窓拒否（窓 0 件→error）は終了条件と整合（i=0 で終了条件不成立なら窓 0 件＝error）。

**WF-F2 窓内最適化（SP2 結線・B-1 二重 full_bars 調停）**
- 各窓 i について `OptimizeRequest(search_space, split=split_i, is_trading_start=is_start_i)` を構築し `optimize(request=..., full_bars=window_bars_i, make_run_segment, search_port, objective_port)` を呼ぶ。
- **B-1 確定方針（二重 full_bars の調停）**：WF tools 入口は SP2 `make_run_segment_factory`（`optimize_cli.py:26-54`）を呼んで `make_run_segment` ファクトリを得るが、その戻り値タプルに含まれる **`full_bars`（＝`base_request.bars`・全期間 CSV ロード結果・`optimize_cli.py:45,54`）は破棄する**。`optimize(full_bars=...)` に渡すのは `window_bars_provider(w.is_start, w.oos_end)` が返す**当窓 full（IS_i ∪ OOS_i・[is_start_i, oos_end_i)）のみ**である。
- **当窓 full のみで機能上正しい理由（実証付き）**：(1) `optimize` は引数 `full_bars` を IS/OOS 母集合として扱う＝IS は `slice_is_bars(full_list, split)`（`optimize.py:111`）、best の OOS は `rs_best(full_list, split)`（`optimize.py:188`）。よって母集合を「当窓 full」に絞れば SP2 が窓内 IS/OOS を正しく分割する。(2) `make_run_segment_factory` の `factory` が内部で呼ぶ `build_interactor`（`optimize_cli.py:51`）は候補ごとに全期間 CSV をロードするが、ファクトリが返す `run_segment` は run 時に `request.bars = bars`（`run_is_oos_cli.py:47`）で **`request.bars` を呼出引数（当窓 full / IS slice）で上書き**してから `controller._interactor.execute(request)` を呼ぶ（`run_is_oos_cli.py:45-49`）。したがって `build_interactor` の全期間ロード結果は run に波及せず、二重ロードが発生しても **エンジン実行は常に当窓 full／IS slice に対して行われ機能上正しい**。
- **性能コスト（正直記載）**：上記の二重ロード（factory の base build＋候補ごと build_interactor の全期間 CSV ロード）は機能には無害だが、当窓 full のみを使うにもかかわらず全期間 CSV をロードする無駄が残る。この再ロード削減は SP2 `build_interactor` 機構無改変（C-W2）の制約下で §9.3 TBD-W4 に降格する（虚偽の「1 回ロード」根拠は記載しない・SP2 High-1 教訓継承）。
- `is_trading_start=is_start_i`（当窓 IS 始端）。SP2 の事前検証 `is_trading_start <= split`（`optimize.py:125`）を満たす。
- 窓ごとの `OptimizeError`（有効候補 0 件・上限超過）は WF が捕捉し、窓 ID 付きで集計・中断方針（§4.5）に従う。

**WF-F3 OOS 連結 `stitch_oos`（純関数・M-1 指標別連結 3 分類）**
- 入力：窓別 `oos_stats` 列（`BacktestStats` 列）。
- 出力：通期 OOS 集約（`StitchedOosSummary`）。
- 連結方式（課題-W1 の制約下）：SP2 公開 IF が返すのは窓集約済 `BacktestStats`（per-trade／equity 非公開・`run_is_oos_cli.py:49` が `result.stats` のみ返す）であるため、本書の連結は**窓別 OOS `BacktestStats` の集約**として定義する。
- **M-1 指標別連結 3 分類（`models.py:91-143` 全フィールド列挙）**：`BacktestStats` の各フィールドを、連結時の扱いで以下 3 区分に明示分類する。連結不能区分は**通期スカラとして出力しない**（窓別系列としてのみ提示・虚偽連結禁止・TBD-W1 整合）。
  - **(A) 加法総和可（Σ で通期値を算出）**：`profit`／`gross_profit`／`gross_loss`／`trades`／`profit_trades`／`loss_trades`／`long_trades`／`short_trades`／`profit_long_trades`／`profit_short_trades`。窓別値を単純総和して通期値とする。
  - **(B) 母数再計算可（A の総和母数から再計算）**：`profit_factor`＝`Σgross_profit / Σgross_loss`（`Σgross_loss==0` で None）／`expected_payoff`＝`Σprofit / Σtrades`（`Σtrades==0` で None）／`average_profit_trade`＝`Σgross_profit / Σprofit_trades`／`average_loss_trade`＝`Σgross_loss / Σloss_trades`。窓別比率の単純平均ではなく**通期母数（A の総和）から再計算**する。
  - **(C) 連結不能（窓別系列のみ・通期スカラ非出力）**：`initial_deposit`（窓定数＝総和に意味なし）／`recovery_factor`／`sharpe_ratio`／`z_score`／`ahpr`／`balance_min`／`balance_dd`／`balance_dd_percent`／`balance_dd_relative`／`balance_ddrel_percent`／`balance_dd_abs`／`max_profit_trade`／`max_loss_trade`／`max_con_wins`／`max_con_profit_trades`／`max_con_losses`／`max_con_loss_trades`／`con_profit_max`／`con_profit_max_trades`／`con_loss_max`／`con_loss_max_trades`／`profit_trades_avg_con`／`loss_trades_avg_con`／`equity_dd_abs`／`equity_dd_max`／`equity_dd_max_percent`。これらは窓内 equity/balance 系列・連勝連敗系列・口座状態に依存し、`BacktestStats` だけでは通期連結再計算不能（連結エクイティ／連結トレード列が必要）。
- 出力構造：(A) 通期総和・(B) 通期再計算比率・(C) 窓別 OOS 指標の系列（各窓スカラの列）を保持。トレード列レベルの連結エクイティ（(C) の通期再計算を可能にする）は §9.3 TBD-W1。

**WF-F5 WF 効率（C-1 指標固定＋None 集約）**
- **対象指標の一意固定（C-1）**：`DegradationReport` は 6 指標（`profit`／`profit_factor`／`recovery_factor`／`expected_payoff`／`sharpe_ratio`／`trades`・`optimize.py:53-60` の `metric_names`）各々に `MetricDegradation.ratio` を持つため「the ratio」は曖昧である。本書は **WF 効率／劣化集約の対象指標を `profit` に一意固定**する（窓別 `OptimizeResult.degradation.by_name("profit").ratio` を採用・実務的推奨／仮説）。`profit` は加法総和可（M-1 区分 A）で連結時の意味が明確なため選定する。
- **None 窓の集約除外（C-1）**：各窓の ratio は `ratio = (ov / iv) if iv != 0.0 else None`（`run_is_oos.py:106`）で算出される。**`ratio is None`（IS `profit` が 0）の窓は集約から除外**し、除外窓件数をログ出力する。中央値・最小値は**有限 ratio を持つ窓のみ**から算出する（None を 0 や欠損として混入させない）。全窓が None（有限 ratio 窓 0 件）の場合は集約値を None とし件数を明示する。
- 出力：窓別 `profit` ratio 列（None 窓を明示）＋有限 ratio の集約統計（中央値・最小値）＋除外件数。WF 効率の単一スカラ定義（例：通期 OOS profit / 全窓 IS best profit 合計）は §9.3 TBD-W2 で定義候補を残す（本書は `profit` 固定の窓別 ratio 列＋集約統計の提示に留め、単一スカラ定義は実務的推奨／仮説）。

### 4.3 処理フロー図

```
walk_forward(request, window_bars_provider, make_run_segment, search_port, objective_port):
  1. windows = schedule_windows(mode, global_start, global_end, is_span, oos_span, step)
        └ 窓 0 件 → WalkForwardError（FR-W7）
  2. 総 run 見積り = Σ_i theoretical_count(search_port, search_space)   # IS run（H-1 Port 契約：theoretical_count == 実 engine IS run 数）
                   + len(windows)                                      # 各窓 OOS 1 回
        └ 上限超過 → WalkForwardError（NFR-WP2・件数ログ明示）
  3. for i, w in enumerate(windows):
        a. bars_i = window_bars_provider(w.is_start, w.oos_end)   # 当窓 full
        b. result_i = optimize(
              request=OptimizeRequest(search_space, split=w.split, is_trading_start=w.is_start),
              full_bars=bars_i, make_run_segment, search_port, objective_port)
              └ OptimizeError → 窓 ID 付与し中断方針へ（§4.5）
        c. window_results.append((w, result_i))
  4. stitched = stitch_oos([r.oos_stats for _, r in window_results])
  5. wf_efficiency = aggregate_efficiency(window_results)
  6. return WalkForwardResult(windows, window_results, stitched, wf_efficiency)
```

### 4.4 業務フロー・ユースケース

- アクター：D（検証方法論＝窓設計）＋ E（最適化）。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §2 のアクター分離に整合。
- ユースケース：分析者が「全期間・IS 幅・OOS 幅・step・方式・探索空間・目的関数」を指定 → WF が窓を転がして各窓 OOS を積み上げ → 通期 OOS 成績と窓別劣化を返す → 分析者が戦略の時間方向頑健性を評価する。

### 4.5 機能横断仕様：入力検証と明示中断（無音禁止）

| 検証項目 | 条件 | 動作 | 出典 |
|---|---|---|---|
| 🟡-2 探索入力（WF-F7・FR-W8・C-2 一意化） | `args.search_algo == "random"` かつ（`args.seed is None` または `args.n_samples is None`） | **WF 入口 CLI `main()` 内で `_build_search_port` 呼出前に** `parser.error(<固定メッセージ>)` を呼ぶ（`argparse` の `parser.error` ＝**終了コード 2**で中断）。固定メッセージ：`"--search-algo random requires both --seed and --n-samples (omitting either is non-deterministic)"`。`optimize_cli`（SP2 既存）は無改変＝WF 入口 CLI で実施し C-W2 整合。UC へ到達させない。 | 課題-W2。`optimize_cli.py:59-66,192-193`。SP2 既存ファイル無改変のまま WF 入口で前置検証。 |
| B-2 未知探索キー（FR-W9） | `search_space` のキー集合が `build_interactor` 受理キーワード集合の部分集合でない（未知キーを含む） | **WF 入口 CLI `main()` 内で `make_run_segment_factory`／`optimize` 呼出前に** 未知キーを列挙して `parser.error(<未知キー名を含むメッセージ>)`（終了コード 2）。`build_interactor(**{**base_kwargs, **params})`（`optimize_cli.py:51`）の TypeError 必発を発生前に遮断。 | B-2。`main/__init__.py:256-286`（固定キーワード・`**params` 非対応）。 |
| 窓 0 件（FR-W7） | `schedule_windows` が窓を 1 つも生成できない（i=0 で H-3 終了条件不成立） | `WalkForwardError`（context に global 境界・is_span・oos_span・step） | 無音禁止（C-1 系・SP2 M-1 踏襲） |
| 総 run 上限（NFR-WP2・H-1） | `Σ_i theoretical_count_i + W > max_total_runs`（必須入力） | `WalkForwardError`（context に W・窓別 theoretical_count 内訳・総 run 見積り） | SP2 M-2 拒否方針の WF 版。H-1 Port 契約（theoretical_count==実 IS run 数）。 |
| 窓内最適化失敗 | ある窓で `OptimizeError`（有効候補 0 件等） | 窓 ID を付して `WalkForwardError` へ昇格（既定＝厳格中断）。窓スキップ継続は §9.3 TBD-W3 として方針候補を残す | SP2 `OptimizeError`（`optimize.py:30`）の伝播 |

**B-2 許容キー集合（`build_interactor` 受理キーワードの探索対象部分集合）**：`search_space` のキーは `build_interactor`（`main/__init__.py:256-286`）の固定キーワード引数のいずれかでなければならない。探索対象として実用的な許容キー例：`lot_size`／`stop_loss_points`／`take_profit_points`／`entry_offset_points`／`ma_period`／`ma_method`／`entry_type`／`stop_out_level`／`slope_shift`／`slope_min_points`（いずれも `main/__init__.py:271-285` の受理キーワード）。`build_interactor` は `**params` を受理しない（catch-all なし）ため、上記集合外のキーを `search_space` に与えると `make_run_segment_factory` の `build_interactor(**{**base_kwargs, **params})`（`optimize_cli.py:51`）が **TypeError** を送出する。WF 入口はこれを発生前に検証し明示中断する。

🟡-2 の決定論担保（C-2 一意化）：random 探索は `random.Random(seed).sample(range(N_space), k)`（`optimize_strategies.py:105-108`）で seed 固定時のみ決定論。WF は全窓で同一 seed・n_samples を用い（窓ごとに seed を変えない既定）、未指定を入口で拒否することで NFR-WD1（決定論性）を全窓に渡って担保する。検証位置は **WF 入口 CLI `main()` 内 `_build_search_port` 呼出前**に一意確定し、`parser.error()`（終了コード 2）で中断する（`optimize_cli` 無改変・C-W2 整合）。これにより `RandomSearch` の `min(None, n_space)` TypeError（`optimize_strategies.py:97`）と `random.Random(None)` 非決定論（同 `:105`）の双方を発生前に遮断する。

---

## 5. データ設計

> 出力元：S-3 データ設計

### 5.1 データモデル概要（概念・実装非依存）

WF は「窓スケジュール（入力）→ 窓列 → 窓別最適化結果 → 連結 OOS 集約 → WF レポート（出力）」のデータ変換系。窓は時刻区間の対であり、SP2 結果（`OptimizeResult`）を窓数分保持し、その OOS 部分を集約する。

### 5.2 主要エンティティ定義

| エンティティ | 概要 | 主要属性 | 関連エンティティ |
|---|---|---|---|
| WalkForwardRequest（入力） | WF の入力一式 | mode（anchored/rolling）・global_start・global_end・is_span（時刻 span・既定型 L-1）・oos_span・step・search_space（キーは build_interactor 受理キーワードの部分集合・B-2）・metric_names・max_total_runs | WindowSpec（生成元）, OptimizeRequest（窓ごと） |
| WindowSpec（中間） | 1 窓の区間定義 | index・is_start・split（IS 終端＝OOS 始端）・oos_end | WalkForwardRequest, OptimizeRequest |
| OptimizeResult（窓別・SP2 既存・再利用） | 窓 i の最適化結果 | best_params・best_is_stats・oos_stats・degradation・trials・excluded_count・total_candidates・finite_candidates | WindowSpec, BacktestStats |
| StitchedOosSummary（出力） | 通期 OOS 連結集約（M-1 3 分類） | (A) 加法総和（profit・gross_profit・gross_loss・trades・profit_trades・loss_trades 等）・(B) 母数再計算比率（profit_factor＝Σgross_profit/Σgross_loss・expected_payoff＝Σprofit/Σtrades・average_profit_trade・average_loss_trade）・(C) 連結不能指標の窓別系列（sharpe_ratio・z_score・balance_dd_*・max_con_*・equity_dd_* 等＝通期スカラ非出力） | OptimizeResult.oos_stats 列 |
| WalkForwardResult（出力） | WF 全結果 | windows・window_results（WindowSpec×OptimizeResult 列）・stitched（StitchedOosSummary）・wf_efficiency | 上記全て |

**窓境界の決定論的算出（NFR-WD1・課題-W3・H-2/H-3/L-1/L-2）**：
- **span の既定型（L-1）**：`is_span`／`oos_span`／`step` の既定型は**時刻 span**（`numpy.timedelta64`〔`bar.time` が `datetime64` の場合〕／epoch 秒の整数差分〔`bar.time` が `int` の場合〕）とする。バー本数指定（インデックス基準）は需要を §9.3 TBD-W5 に残す（本書既定は時刻 span 単一型）。
- 時刻型は `numpy.datetime64 | int`（domain 規約・`optimize.py:10`・`run_is_oos.py:8`）。CLI 文字列→時刻型は SP1 `normalize_time`（`run_is_oos_cli.py:54`）を tools で再利用し、`is_span`/`oos_span`/`step` も同型整合の差分（`int` epoch 秒なら整数加算、`datetime64` なら `numpy.timedelta64`）で表現する。
- 窓境界は全期間境界＋窓パラメータの**決定論的算術**で生成し、SP1 `slice_is_bars` の `bar.time < split` 比較（**`run_is_oos.py:35`**＝時刻型比較の実行行・L-2 引用行修正）に渡せる時刻型を保つ。浮動小数等価比較に依存しない（SP2 High-3 の決定論方針を継承）。
  - rolling：`is_start_i = global_start + i×step`、`split_i = is_start_i + is_span`、`oos_end_i = split_i + oos_span`。
  - anchored：`is_start_i = global_start`、`split_i = global_start + is_span + i×step`、`oos_end_i = split_i + oos_span`。
- **窓生成終了条件（H-3・`<=` 明記）と端数切り捨て（H-2）**：窓 i は `oos_end_i <= global_end`（⇔ rolling: `global_start + i×step + is_span + oos_span <= global_end`／anchored: `global_start + is_span + i×step + oos_span <= global_end`）を満たす場合**のみ**生成し、満たさなくなった最初の i で打ち切る。`oos_end_i <= global_end` を満たさない端数窓（OOS が `oos_span` 未満になる部分窓）は採用しない（決定論的切り捨て）。
- 区間半開規約：IS_i = [is_start, split)、OOS_i = [split, oos_end)。`global_end` は半開区間の右端（exclusive）でありクリップ基準は `oos_end_i <= global_end`。SP1（`run_is_oos.py:35` head-prefix 打ち切り）と整合。

### 5.3 データフロー図

```
[入力] WalkForwardRequest（窓スケジュール＋探索空間＋目的関数）
   │ schedule_windows（純関数・決定論）
   ▼
[中間] WindowSpec 列  ──→  各窓: window_bars_provider → 当窓 full bars
   │                          │ optimize（SP2・窓ごと N_cand+1 run）
   ▼                          ▼
[中間] OptimizeResult 列（窓別 best/IS/OOS/劣化）
   │ stitch_oos（純関数・OOS BacktestStats 集約）
   ▼
[出力] StitchedOosSummary（通期 OOS）＋ WF 効率 ＋ 窓別レポート
   │ tools: to_json_dict / to_markdown（SP1/SP2 形式踏襲）＋ assert_safe_output_dir
   ▼
[永続] 新規 OUT ディレクトリのみ（既存データ非波及・NFR-WS1）
```

### 5.4 データライフサイクル

- 入力データ（`marketdata/` 等）：読み取り専用。WF は改変しない（C-W1）。
- 中間データ（WindowSpec・OptimizeResult 列）：プロセスメモリ内のみ。永続化しない。
- 出力データ（WF レポート JSON/Markdown）：新規 OUT ディレクトリへ書込。保持期間・削除は運用者裁量（本書は規定しない）。既存生成物の上書き禁止（`assert_safe_output_dir` で repo_root 外・禁止プレフィクス配下を拒否）。

---

## 6. インターフェース設計

> 出力元：S-3 インターフェース設計

### 6.1 API 設計概要（UC 公開 IF）

新 UC 公開関数（概念シグネチャ・SP2 `optimize` の呼出規約に整合）：

- `walk_forward(*, request: WalkForwardRequest, window_bars_provider, make_run_segment, search_port, objective_port) -> WalkForwardResult`
  - `window_bars_provider(is_start, oos_end) -> bars`：当窓 full（[is_start, oos_end)・IS_i ∪ OOS_i）バー列を返すコールバック（tools 層が全期間 full_bars をスライスして注入。pandas を UC に持ち込まないための DI・SP2 `make_run_segment` と同じ注入思想）。**B-1**：UC が `optimize(full_bars=...)` に渡すのは本コールバックが返す当窓 full のみ。tools 層は `make_run_segment_factory` が返す全期間 `full_bars`（`optimize_cli.py:54`）を `window_bars_provider` のスライス元として保持しつつ、**UC へは渡さない**（全期間 full_bars は `optimize` 引数として直接使わない）。
  - `make_run_segment`・`search_port`・`objective_port`：SP2 と同一の Port／ファクトリをそのまま転送。WF は SP2 `optimize` へ素通しする。
- `schedule_windows(*, mode, global_start, global_end, is_span, oos_span, step) -> list[WindowSpec]`（純関数・単体テスト可能）。
- `stitch_oos(oos_stats_list) -> StitchedOosSummary`（純関数）。

SP1/SP2 公開 IF は**変更しない**（`optimize(...)` シグネチャ・`run_is_oos`・`slice_is_bars`・各 Port Protocol を無改変で呼ぶ）。C-W2 維持。

### 6.2 画面構成・遷移

- 本書スコープ（基本設計）では UI を対象外。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5 Phase 2 で `POST /walkforward`（非同期＋ポーリング）＋ web 比較ビューを別途設計予定。WF UC は CLI（`walk_forward_cli.py`）入口で完結し、HTTP 化は後段で同 UC を委譲先として再利用する。
- anchored の OOS 接続（課題-W4）：`step == oos_span` を既定とし窓 OOS が連続（隙間・重なりなし）。`step < oos_span`（OOS 重なり）／`step > oos_span`（OOS 隙間）は警告ログを出した上で許容（無音禁止）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| committed エンジン（`controller._interactor.execute`） | SP2 `make_run_segment` 経由の関数呼出（無改変） | bars＋trading_start → BacktestStats | W×(N_cand+1) 回 | `ConfigError`/`BacktestError`/`MarginCallError` は SP2 `optimize` が窓内で捕捉・除外・継続（`optimize.py:138`） |
| marketdata（CSV） | `build_interactor` 内部の CSV ロード（SP2 経由・無改変） | OHLC CSV | W×(N_cand+1) 回（NFR-WP1） | 読み取り専用。書込なし（C-W1） |
| 出力先 OUT | ファイル書込（SP1 `assert_safe_output_dir` で検証） | JSON／Markdown | WF 1 実行で 1 セット | 禁止プレフィクス配下・repo_root 外で `OutputGuardError`（`run_is_oos_cli.py:67`） |

### 6.4 通信プロトコル・データ形式

- レポート出力：JSON（機械可読・SP1/SP2 `to_json_dict` の `asdict` パターン踏襲）＋ Markdown（人間可読・SP1/SP2 `to_markdown` 形式踏襲）。WF 固有として「窓別表（窓 ID・IS_i/OOS_i 境界・best_params・IS/OOS 値・劣化）」＋「連結 OOS 集約表」＋「総 run 見積り・窓数」を追加。
- プロトコル選定理由：既存 SP1/SP2 tools が JSON＋Markdown の二出力で確立済（`optimize_cli.py:283-294`）。同形式踏襲で技術スタック追加なし（C-W3）・分析者の既存ワークフロー互換。代替（CSV 単独）は窓別ネスト構造の表現に不利のため非採用。

---

## 7. 非機能設計

> 出力元：S-4 品質特性の担保

### 7.1 性能設計・スケーラビリティ対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-WP1（計算量・H-1） | 総 engine run = **Σ_i theoretical_count_i（IS run）+ W（各窓 OOS 1 回）**。均一探索時は Σ_i (N_cand_i + 1) = W×(N_cand+1) に縮約。CSV ロードも同回数（SP2 NFR-OP1 継承）。劣化・連結算出は `BacktestStats` 上の O(1) 算術×W。計算量は窓数 W × 候補数 N_cand の**多段 N 比例**。 | 窓ループは SP2 `optimize` を W 回呼ぶ素直な反復（隠れた追加 run なし）。WF 自体は engine run を増やさない（窓内 run は SP2 既存分のみ）。 | 実行前に総 run 見積り（Σ_i theoretical_count_i + W）をログ出力（無音切り捨て禁止・NFR-WP2）。実測 run 回数を `make_run_segment` 呼出カウントで突合可能。 |
| NFR-WP2（上限ガード・H-1 Port 契約） | `max_total_runs`（必須入力）。`Σ_i theoretical_count_i + W > 上限`で拒否。**総 run = Σ_i theoretical_count_i（IS run）+ W（各窓 OOS 1 回）**。 | tools 入口で全窓の理論候補数（`search_port.theoretical_count`・`optimize_ports.py:23`）を合算し W を加えて事前判定。超過時 `WalkForwardError`（件数ログ明示）。**H-1 Port 契約**：`search_port.theoretical_count` ＝ 実 engine IS run 数を Port 契約として要求する（grid＝`_space_size`＝`itertools.product` 列挙数・`optimize_strategies.py:69-78`／random＝`min(n_samples,n_space)`＝`rng.sample` 抽出数・`optimize_strategies.py:89-108`、いずれも理論数と実 IS run 数が一致）。 | 上限超過ケースで非ゼロ終了＋件数メッセージを確認。grid/random とも theoretical_count 回の IS run が実行されることを結合テストで突合（§8.3）。 |
| スケーラビリティ（TBD-W4） | CSV 再ロード W×(N_cand+1) 回が支配的（SP2 High-1 と同構造）。 | 本書では SP2 同様、再ロード削減（full_bars キャッシュ・並列化）は §9.3 TBD-W4 へ降格（C-W2 で SP2 build_interactor 機構は無改変）。WF 層での虚偽の「1 回ロード」根拠は記載しない（SP2 High-1 教訓継承）。 | 後段測定対象。 |

### 7.2 可用性設計・障害対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| 決定論性（NFR-WD1） | 同一入力で出力バイト同一（再現率 100%） | 窓境界＝決定論的算術（§5.2）。窓内 `optimize` の決定論（辞書順序規約＋seed 固定 random・SP2 NFR-OD1）を全窓継承。🟡-2 解消で random の seed 未指定（非決定論）を入口で排除（§4.5・FR-W8）。 | 同一入力 2 回実行で JSON バイト一致を確認。 |
| 明示中断（無音禁止） | 窓 0 件・上限超過・窓内最適化失敗・🟡-2 未指定（C-2）・B-2 未知探索キーで必ず非ゼロ終了（入口検証は `parser.error`＝終了コード2） | `WalkForwardError`＋tools 入口検証（§4.5 表）。SP2 `OptimizeError` を窓 ID 付きで昇格。 | 各異常ケースで終了コード・context 内訳を確認。`parser.error` 経路は終了コード2を確認。 |

🟡-2（課題-W2）解消の確定方針（C-2 一意化）：WF tools 入口 `walk_forward_cli.py` の `main()` 内で、`_build_search_port` 呼出**前に** 次を一意実行する：`if args.search_algo == "random" and (args.seed is None or args.n_samples is None): parser.error("--search-algo random requires both --seed and --n-samples (omitting either is non-deterministic)")`。`argparse` の `ArgumentParser.error()` は **終了コード 2** で中断するため、`RandomSearch` 構築前に確実に遮断される。これにより `min(None, n_space)` の TypeError（`optimize_strategies.py:97`）と `random.Random(None)` の非決定論（同 `:105`）の双方を発生前に遮断する。SP2 既存ファイル（`optimize_cli.py`・`optimize_strategies.py`）は無改変（C-W2）＝WF 入口 CLI で実施。grid 時は seed/n_samples 不要のため検証対象外。

B-2 解消の確定方針：同じく `main()` 内 `make_run_segment_factory`／`optimize` 呼出前に、`search_space` のキー集合が `build_interactor` 受理キーワード集合（`main/__init__.py:256-286`）の部分集合でない場合、未知キーを列挙して `parser.error(f"unknown search-param key(s): {unknown} (must be a subset of build_interactor keywords)")`（終了コード 2）で中断する。これにより `build_interactor(**{**base_kwargs, **params})`（`optimize_cli.py:51`）の TypeError 必発を発生前に遮断する。SP2 既存ファイルは無改変（C-W2）。

### 7.3 セキュリティ設計

- 認証・認可：本 UC/CLI スコープ外（ローカルツール）。HTTP 化（Phase 2）時に別途設計。
- 入力検証：窓スケジュール前提（global_start<=global_end・各 span/step>=1・全期間>=IS+OOS）／🟡-2 探索入力（C-2）／B-2 探索キー部分集合／総 run 上限を tools・UC で検証し明示中断（§4.5・入口は `parser.error`＝終了コード2）。
- 出力先ガード：`assert_safe_output_dir`（`run_is_oos_cli.py:67`）再利用で `marketdata/`・`fixtures/`・`confirmation/` 配下・repo_root 外への書込を拒否（C-W1・NFR-WS1）。
- 監査ログ：総 run 見積り・窓数・除外件数（SP2 `excluded_count` 窓別）をレポートに記録。
- （詳細脅威分析は security スキル成果物を参照。本書スコープでは該当なし。）

### 7.4 運用・保守性設計

- ログ設計：実行前に窓数・総 run 見積りを出力。各窓完了時に窓 ID・best_params・OOS 主要指標を記録。除外候補（非有限・失敗）件数を窓別に記録（SP2 `excluded_count` 再利用）。
- 監視ポイント：総 run 上限超過拒否・窓内 `OptimizeError` 発生窓・OOS 隙間/重なり警告（§6.2）。
- 構成管理：WF は新規ファイル（`walk_forward.py`／`walk_forward_cli.py`）のみ。SP1/SP2/committed の差分 0 行（NFR-WS2）で既存資産と独立にレビュー可能。
- バックアップ・リストア：本書スコープ外（出力は再生成可能・決定論性により再現可）。

---

## 8. 開発・運用方針

> 出力元：S-4 品質特性の担保

### 8.1 開発方法論・プロセス

- SP2 の確立パターン（UC＋Port＋tools 結線＋JSON/Markdown 二出力）を踏襲し、新規ファイルのみ追加。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5 Phase 1 の最終要素として実装。
- 修正には回帰テストを 1 本添える（プロジェクト方針・MEMORY: bugfix-pair-with-regression-test）。特に 🟡-2 解消は「random 時 seed 未指定で明示中断する」回帰テストを必須とする。

### 8.2 品質保証方針

- 純関数（`schedule_windows`／`stitch_oos`）は domain 依存のみで単体テスト可能（pandas/main 非依存・C-W4）。
- 決定論性：同一入力 2 回実行のバイト一致テスト。
- 既存無改変：SP1/SP2/committed の差分 0 行を CI/レビューで確認（NFR-WS2）。

### 8.3 テスト方針

- 単体：`schedule_windows`（anchored/rolling・窓 0 件・**H-3 終了条件境界**〔`oos_end == global_end` ちょうどは採用／超過は不採用〕・**H-2 端数切り捨て**・境界整合）、`stitch_oos`（M-1 区分 A 加法総和・区分 B 母数再計算・区分 C 通期スカラ非出力の確認）、`aggregate_efficiency`（C-1 `profit` 固定・None 窓除外＋件数）、🟡-2 入口検証（`parser.error`＝終了コード2）、B-2 未知キー検証（終了コード2）。
- 結合：WF→SP2 `optimize`→`make_run_segment`→engine の窓ループ（小窓数・小探索空間で **Σ_i theoretical_count_i + W** run 回数突合＝H-1 Port 契約検証）。当窓 full のみを `optimize` に渡し factory 全期間 full_bars を破棄しても窓内 IS/OOS が正しく分割されること（B-1）を確認。
- システム：既存 confirmation fixture（読み取り専用）に対し WF を回し、窓別 OOS が SP2 単一窓結果と当該窓で一致することを確認（SP2 再利用の正当性検証）。

### 8.4 リリース・デプロイメント方針

- 環境構成：ローカル CLI（既存 SP1/SP2 tools と同形態）。HTTP 化は Phase 2（別書）。
- デプロイ戦略：新規ファイル追加のみ。既存 IF 無改変のため SP1/SP2 利用側に後方互換影響なし。

---

## 9. リスク・課題

> 出力元：S-1 リスク列挙 ／ S-5 設計検証

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 対策の出典／根拠 |
|---|---|---|---|---|
| 課題-W1：連結エクイティ/トレード列が SP2 公開 IF で取得不可（`run_segment` は `stats` のみ返す） | 中 | 確実（IF 制約） | 本書は連結を窓別 OOS `BacktestStats` 集約として定義。トレード列連結は TBD-W1 で後段に委譲（公開 IF 拡張不要な範囲に限定し C-W2 維持） | `run_is_oos_cli.py:49`・`optimize_cli.py:52`・`models.py:146` で実証 |
| 課題-W2：🟡-2（random の seed/n_samples 未指定で TypeError＋非決定論） | 高 | 高（再利用で必発） | WF tools 入口 `main()` 内 `_build_search_port` 呼出前に `parser.error()`（終了コード2・固定メッセージ）で明示中断（§4.5・§7.2）。回帰テスト必須 | `optimize_cli.py:59-66,192-193`・`optimize_strategies.py:97,105` で実証 |
| 課題-W5：B-2（search_space 未知キーで `build_interactor` TypeError 必発） | 高 | 高（再利用で必発） | WF tools 入口で `search_space` キーを `build_interactor` 受理キーワードの部分集合に制約・未知キー→`parser.error()`（終了コード2）で前置中断（§4.5）。回帰テスト必須 | `main/__init__.py:256-286`（`**params` 非対応）・`optimize_cli.py:51` で実証 |
| 計算量爆発（W×N_cand の多段乗算＋CSV 再ロード同回数） | 高 | 中 | 総 run 上限 `max_total_runs` 必須・事前見積りログ・超過拒否（NFR-WP2） | SP2 NFR-OP1/M-2 継承 |
| 窓境界の時刻型不整合（`datetime64`/`int` 混在で比較失敗） | 中 | 低 | `normalize_time` 再利用＋窓算術を同型 timedelta で（§5.2・課題-W3） | `run_is_oos_cli.py:54`・`optimize.py:10` |
| anchored OOS の隙間/重なり（step≠oos_span） | 低 | 中 | step==oos_span 既定・不一致は警告ログで許容（無音禁止・§6.2） | 課題-W4 |

### 9.2 スケジュール・リソースリスク

- WF は SP1/SP2 完了が前提（両者は実装済・本書時点で公開 IF 確定）。新規ファイルのみのため既存改修リスクは低い。最大の工数リスクは大窓数×大探索空間の実行時間（NFR-WP1）であり、上限ガードと小規模テストで緩和。

### 9.3 今後の検討課題（TBD 一覧）

| 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|
| TBD-W1：トレード列レベルの連結エクイティ曲線 | SP2 公開 IF が `BacktestStats` のみ公開（課題-W1）。連結エクイティには新 run_segment 変種（BacktestResult 全体返却・新規ファイル）が必要。C-W2 を維持しつつ実現可否を要判断 | 後段（presenter/is_oos）設計・親会話確認 |
| TBD-W2：WF 効率の単一スカラ定義 | 「OOS/IS 集約」の正準定義（通期 OOS profit / Σ IS best profit 等）が未確定（実務的推奨／仮説） | 分析方法論レビュー（アクター D） |
| TBD-W3：窓内最適化失敗時の継続 vs 中断 | 既定は厳格中断（§4.5）。一部窓スキップ継続を許すかは方針未確定 | 親会話確認 |
| TBD-W4：CSV 再ロード削減（full_bars キャッシュ・並列化） | W×(N_cand+1) 回ロードが支配的（SP2 High-1 同構造）。SP2 build_interactor 機構無改変（C-W2）の範囲で WF 層キャッシュ可否を要検討 | 性能改善フェーズ |
| TBD-W5：窓幅・step の単位（バー本数 vs 時刻 span） | 時刻型 span（datetime64/int）かバー本数かで窓算出が変わる。本書は時刻 span を既定としたが、バー本数指定の需要を要確認 | 分析者要件確認 |

---

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| ウォークフォワード（WF） | IS/OOS 窓を step ずつ前進させ各窓で IS 最適化→OOS 検証を反復する手続き |
| anchored | IS 起点を固定し IS 終端を窓ごとに拡張する方式 |
| rolling | IS を固定幅で step ずつ移動させる方式 |
| 窓（window） | 1 つの IS_i／OOS_i 区間の対。split_i を境界とする半開区間 |
| step | 窓を前進させる量（時刻 span） |
| stitch（連結） | 窓別 OOS 成績を窓順に集約し通期 OOS 成績を得る操作 |
| WF 効率 | 窓別/通期の OOS/IS 比に基づく頑健性指標 |
| SP1/SP2/SP3 | サブフェーズ1（単純分割）／2（最適化）／3（WF・本書） |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| WF の配置 | 新 UC `walk_forward.py`＋純関数スケジューラ | SP2 拡張／tools 直書き | C-W2（SP2 無改変）・クリーンアーキ依存方向・SP2 パターン踏襲 | 規約＋公式（Clean Arch） |
| 窓内最適化 | SP2 `optimize` を窓ごとに素通し呼出 | WF 内に探索再実装 | DRY・SP2 公開 IF 再利用・決定論継承 | 公式（DRY）＋規約 |
| 窓境界の表現 | 時刻型（datetime64/int）上の決定論的算術＋半開区間 | バー本数インデックス | SP1 `slice_is_bars` の `bar.time<split` 比較と整合・時刻型統一 | 実証（run_is_oos.py:35）＋（実務的推奨／仮説） |
| 連結方式 | 窓別 OOS `BacktestStats` 集約（加法総和＋比率再計算） | トレード列連結エクイティ | SP2 公開 IF が stats のみ公開（課題-W1）・C-W2 維持 | 実証（models.py:146・run_is_oos_cli.py:49） |
| 🟡-2 解消位置（C-2） | WF tools 入口 `main()` 内 `_build_search_port` 前で `parser.error()`（終了コード2・固定メッセージ） | SP2 既存ファイル修正 | C-W2（SP2 無改変）・WF が再利用前に決定論担保 | 実証（optimize_cli.py:59-66,192-193・optimize_strategies.py:97,105） |
| search_space キー制約（B-2） | build_interactor 受理キーワードの部分集合に限定・未知キーは parser.error | 任意キー許容（`**params` 期待） | build_interactor は `**params` 非対応・未知キー TypeError 必発 | 実証（main/__init__.py:256-286・optimize_cli.py:51） |
| WF 効率の対象指標（C-1） | `profit` に一意固定・None 窓除外＋件数ログ | 6 指標横断の「the ratio」曖昧定義 | DegradationReport は 6 指標各々に ratio・None 混入回避 | 実証（optimize.py:53-60・run_is_oos.py:106） |
| 指標別連結（M-1） | 加法総和可/母数再計算可/連結不能の 3 分類・連結不能は通期スカラ非出力 | 全指標を一律に総和/平均 | BacktestStats だけでは equity/連勝系列依存指標は通期連結不能 | 実証（models.py:91-143・run_is_oos_cli.py:49） |
| 窓生成終了条件・端数（H-2/H-3） | `oos_end_i <= global_end` を満たす窓のみ生成（`<=`）・端数 OOS 切り捨て | 端数窓を部分 OOS で採用 | 半開区間 [start, split) 規約と整合・決定論性 | 実証（run_is_oos.py:26-39,35） |
| 総 run 見積りの基礎（H-1） | `theoretical_count == 実 IS run 数` を Port 契約化・総 run=Σ theoretical_count_i + W | N_cand を別途数える | grid/random とも theoretical_count が candidates 生成数と一致 | 実証（optimize_ports.py:23・optimize_strategies.py:69-108） |
| anchored OOS 接続 | step==oos_span 既定・不一致は警告許容 | 重なり/隙間を禁止 | 無音禁止・分析者の窓設計自由度確保 | （実務的推奨／仮説） |

### 10.3 参考資料

- `simulator/usecase/optimize.py`（SP2 `optimize`／`OptimizeResult`／`OptimizeError`）
- `simulator/usecase/optimize_ports.py`（`ParameterSearchPort`／`ObjectivePort`）
- `simulator/usecase/optimize_strategies.py`（`GridSearch`／`RandomSearch`・🟡-2 該当箇所 `:97,105`）
- `simulator/tools/optimize_cli.py`（`make_run_segment_factory`・🟡-2 入力箇所 `:59-66,192-193`）
- `simulator/usecase/run_is_oos.py`（SP1 `slice_is_bars`／区間定義／`DegradationReport`）
- `simulator/tools/run_is_oos_cli.py`（`normalize_time`／`assert_safe_output_dir`／`make_run_segment`）
- `simulator/usecase/models.py`（`BacktestStats`／`BacktestResult`）
- `.doc/ISOOS_BROWSER_PLAN_WIP.md`（全体計画・WF 構想 §3 L48）
- `.doc/ISOOS_OPTIMIZATION_BASIC_DESIGN.md` v0.2.0／`.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md`

### 10.4 関連する標準・規格

- Clean Architecture（依存は内向き・UC は framework/adapter を知らない）：UC `walk_forward.py` の domain のみ依存方針の出典。
- DRY（Don't Repeat Yourself）：SP1/SP2 部品再利用方針の出典。
- 本リポジトリ規約（`.doc/ISOOS_*_DESIGN.md` 章構成・無音切り捨て禁止・既存データ非波及）：プロジェクト規約。

---

## 完結性ノート

WF（本書）は IS/OOS 3 サブフェーズの**最終段**であり、SP1（区間スライス・区間定義・劣化レポート）と SP2（窓内最適化）を**統合する土台**である。WF を新 UC＋純関数スケジューラとして新規ファイルのみで実装し、SP1/SP2/committed の公開 IF を無改変で再利用することで、IS/OOS 機能の方法論レイヤ（`.doc/ISOOS_BROWSER_PLAN_WIP.md` アクター D/E）が本書をもって完結する。Phase 2（HTTP/UI）は本 WF UC を委譲先として再利用する。
