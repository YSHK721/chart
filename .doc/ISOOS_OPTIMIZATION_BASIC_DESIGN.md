# IS/OOS 最適化（Optimization）基本設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v0.2.0
- 作成者：system-basic-design エージェント
- 承認者：（未承認・レビュー待ち）
- 上位計画：`.doc/ISOOS_BROWSER_PLAN_WIP.md`（Phase 1・アクター E＝最適化）
- 前段成果物（再利用元）：`.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md` v0.2.0／`.doc/ISOOS_SIMPLE_SPLIT_DETAILED_DESIGN.md` v1.0.0／`simulator/usecase/run_is_oos.py`／`simulator/tools/run_is_oos_cli.py`
- 変更履歴：
  - v0.1.0 (2026-06-20) 初版。IS/OOS サブフェーズ2「最適化」の基本設計。サブフェーズ1（単純分割）の `run_is_oos`／`run_segment` 契約・`slice_is_bars`・`RunIsOosRequest/Result`・`DegradationReport` を再利用・拡張。
  - v0.2.0 (2026-06-20) spec-reviewer 指摘を決定論的に解消。C-1（非有限スコア除外＋有限のみ argmax＋tie 基準順先勝ち＋best 0 件で明示中断・`math.isfinite` 規約化）／High-1（性能記述の矛盾修正：build_interactor 内部 CSV 再ロード N_cand+1 回を正直に記載・虚偽の「1 回ロード」根拠を撤回・再ロード削減を TBD-O2 へ降格）／High-2（best_is_stats を TrialRecord に保持し再 run しない・OOS は別 build 1 回で N_cand+1 run）／High-3（random を離散候補集合からの整数インデックス非復元抽出 `random.Random(seed).sample(range(N_space), k)` として定義・辞書順序規約確立）／M-1（失敗候補方針本文昇格・`ConfigError`/`BacktestError`/`MarginCallError` を UC/tools が捕捉＝execute 直叩きで MarginCallError 翻訳が掛からない事実を明記）／M-2（理論候補数 > max_candidates で拒否＝単一動作）／M-3（max_candidates 必須引数化）／L-1（小さいほど良い指標の符号反転を YAGNI で既定外）を反映。

---

## 2. プロジェクト概要

> 出力元：S-1 要件分析と設計方針決定

### 2.1 システム概要

- **位置付け**：committed バックテストエンジン（`simulator/`・MT5 bit-exact 突合済）の上に載るオーケストレーション層。サブフェーズ1（単純分割）が「固定パラメータ 1 組を IS/OOS で並列評価する」のに対し、本サブフェーズ2は **パラメータ探索空間と目的関数を入力に、IS 区間でパラメータを探索・最適化し、最良パラメータを凍結して OOS で検証する**（過剰最適化検出）。
- **解決する業務課題**：戦略パラメータを「IS でのみ機能し OOS で劣化する」過剰最適化（カーブフィッティング）から守るため、IS で目的関数を最大化する best params を**自動探索**で確定し、その同一 params を未知区間 OOS で評価して劣化（IS best vs OOS）を定量化する。単純分割が「人手で決めた 1 組の堅牢性確認」であるのに対し、本サブフェーズは「複数候補から IS 最良を機械選択し OOS で検証する」段である。
- **3 サブフェーズ中の位置**：「①単純分割 → ②最適化 → ③ウォークフォワード」の第 2 段。本書は②のみを対象とする。③ウォークフォワードは本サブフェーズの「IS 探索→best 凍結→OOS 検証」を窓ごとに反復する土台（§9 拡張余地）。

### 2.2 開発目的・背景

- **背景**：サブフェーズ1で `run_is_oos(*, request, full_bars, run_segment)`（`simulator/usecase/run_is_oos.py:113`）が「`run_segment` コールバックを IS/OOS 2 回呼んで両 `BacktestStats` と `DegradationReport` を返す」プリミティブとして確立済（同ファイル実装で実証）。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 のレイヤリング図は `usecase/optimize.py` が `ParameterSearchPort`／`ObjectivePort` を持ち「探索空間×目的関数で IS を探索・best params 返却」する設計を構想済（同 §3 L48-52 で実証）。
- **達成したい目標**：committed エンジンを無改変のまま、サブフェーズ1の公開 IF（`run_is_oos`／`run_segment` 契約・`slice_is_bars`）を**壊さず再利用**し、(1) パラメータ探索空間を定義する、(2) 探索アルゴリズム（grid/random・差し替え可能）で IS を走査する、(3) 目的関数（PF/NetProfit 等・差し替え可能）で IS best を確定する、(4) best params を凍結して OOS を 1 回評価し劣化レポートを返す、新規 UC `simulator/usecase/optimize.py` を追加する。本 UC は後段③ `usecase/walk_forward.py` が「窓ごとの最適化単位」として再利用する。

### 2.3 適用範囲・制約条件

#### 機能要件サマリー（要件 ID 一覧）

| 要件 ID | 概要 |
|---|---|
| FR-O1 | パラメータ探索空間（可変パラメータ名→候補値集合）を入力として受け取る |
| FR-O2 | 探索アルゴリズム（grid／random 等）を差し替え可能な抽象（`ParameterSearchPort`）として注入する |
| FR-O3 | 目的関数（PF／NetProfit／Sharpe 等）を差し替え可能な抽象（`ObjectivePort`）として注入する |
| FR-O4 | 探索空間の各候補について IS 区間でエンジンを実行し `BacktestStats` を得る |
| FR-O5 | IS で目的関数を最大化する best params を確定する（IS 探索→best 確定） |
| FR-O6 | best params を凍結し OOS 区間で 1 回評価して `BacktestStats` を得る |
| FR-O7 | IS best と OOS の劣化（過剰最適化レポート）を `DegradationReport`（サブフェーズ1再利用）で算出・出力する |
| FR-O8 | 探索ログ（候補ごとの params・IS 目的値・採否）を出力する |
| FR-O9 | 探索空間の**理論候補数**（grid＝直積 N_space／random＝min(n_samples, N_space)）が必須上限 `max_candidates` を超える場合は無音切り捨てせず件数をログ明示して**拒否**する（M-2 単一動作）。非有限スコア候補・失敗候補の探索除外も件数・理由をログ明示する（C-1／M-1） |

#### 非機能要件サマリー（数値目標を含む）

| 区分 | 数値目標 |
|---|---|
| 性能（NFR-OP1） | エンジン実行回数＝**探索候補件数 N_cand × 1（IS run）＋ 1（OOS run）**。total run = N_cand + 1。各 run の前に `build_interactor` が **1 回ずつ実行**され、その内部で `data_path` から CSV を**再ロード**する（registry 構築に `_load_dataframe`／`market_data.load`＝`main/__init__.py:335,344` で実証）。したがって **CSV ロードは N_cand+1 回発生**する（候補ごとに build_interactor 内部で再ロード・High-1）。劣化算出は `BacktestStats` 上の O(1) 算術。計算コストは N_cand に線形比例（grid では各パラメータ候補数の直積）。 |
| 性能（NFR-OP2） | 探索空間サイズ上限を **必須引数 `max_candidates`（既定なし・M-3）** で明示する。**上限判定は理論候補数 > `max_candidates` で拒否（既定かつ単一動作・M-2）**。理論候補数の定義：**grid＝各値リスト長の直積 `N_space`／random＝`min(n_samples, N_space)`**。超過時は FR-O9 に従い**件数をログ出力した上で**拒否（無音切り捨て禁止）。 |
| 決定論性（NFR-OD1） | 同一入力（データ・split・探索空間・探索アルゴリズム・目的関数・config）に対し best params・IS/OOS `BacktestStats`・探索ログがバイト同一で再現される。**辞書順序規約**：パラメータキーを辞書順昇順で固定し、全直積を辞書順で列挙したインデックス列を基準順序とする（FO-02）。grid はこの順序で全列挙＝決定論。random は `random.Random(seed).sample(range(N_space), k)` で**整数インデックスを非復元抽出**＝seed 固定で決定論（float 等価比較に依存しない・High-3）。tie-break（同一最大目的値の複数 best）は基準順序の先勝ち。argmax は**有限スコアのみ**で取る（非有限スコアは除外・C-1）。 |
| 既存データ非波及（NFR-OS1） | `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`・既存生成物への書き込み 0 件。サブフェーズ1の出力先検証関数（`assert_safe_output_dir`・`run_is_oos_cli.py:67`）を再利用して計測可能に担保。 |
| committed 無改変（NFR-OS2） | `simulator/domain`・`simulator/usecase`（既存ファイル：`run_backtest.py`・`models.py`・`run_is_oos.py` を含む）・既存 `simulator/adapter`・`simulator/main` の差分 0 行。新 UC／新 Port は新規ファイルのみ。 |

#### 制約条件（技術 / 運用 / プロジェクト規約）

| 区分 | 制約 |
|---|---|
| 絶対制約 C1（運用） | 既存データの改変・波及を禁止する。`marketdata/`・`fixtures/`・`confirmation/`・既存生成物は読み取り専用。出力は新規パスのみ。 |
| 絶対制約 C2（技術・アーキテクチャ） | committed エンジン無改変。`simulator/domain`・既存 `simulator/usecase`・既存 `simulator/adapter`・`simulator/main` を編集しない。**サブフェーズ1の `simulator/usecase/run_is_oos.py` も編集せず再利用**（公開 IF＝`run_is_oos`／`run_segment` 契約／`slice_is_bars`／`RunIsOosRequest/Result`／`DegradationReport` を壊さない）。新 Port／UC は `ports.py` 等を編集せず**新規ファイル**へ置く。 |
| 絶対制約 C3（技術） | 技術スタック追加禁止（純 Python＋既存依存のみ）。usecase 層は pydantic 非依存（`models.py:1-9` docstring で実証）。 |
| プロジェクト規約 C4 | `.claude/CLAUDE.md`：指示範囲外の変更・破壊的変更を禁止する。 |

#### 設計上の課題と技術的リスク

| ID | 課題／リスク | 備考 |
|---|---|---|
| 課題-O1 | **パラメータごとに `run_segment` を再生成する必要がある**。サブフェーズ1の `make_run_segment(controller, request)`（`run_is_oos_cli.py:38`）は単一 `(controller, request)` を閉包するが、`controller`／`request` は `build_interactor(...)` の引数（`lot_size`/`stop_loss_points`/`take_profit_points`/`entry_offset_points`/`ma_period` 等＝探索対象パラメータの実体・`main/__init__.py:256-285` で実証）に依存する。よって**候補パラメータが変わるたびに `build_interactor` を再構築して run_segment を生成する必要がある**。サブフェーズ1の固定 1 組前提では現れない要件。 |
| 課題-O2 | `run_is_oos` は IS と OOS を**同一 `run_segment`（同一 params）**で 2 回呼ぶ契約（`run_is_oos.py:134-135`）。最適化では「IS は候補ごとに run、OOS は best のみ 1 回 run」と非対称になるため、`run_is_oos` をそのまま 1 回呼ぶだけでは表現できない。`run_is_oos` の**部品（`slice_is_bars`・`build_degradation_report`・`run_segment` 契約）を再利用しつつ、IS 探索ループは新 UC `optimize` が統括する**必要がある。 |
| リスク-O1 | 探索空間爆発。grid は各パラメータ候補数の直積で N_cand が急増し、エンジン実行が N_cand 回走る（NFR-OP1）。上限制御（NFR-OP2・FR-O9）を欠くと長時間実行・暗黙打ち切りを招く。 |
| リスク-O2 | 探索対象パラメータが committed の振る舞いに正しく反映されるか。`build_interactor` の引数のうち、各 ea_name（戦略）が実際に参照するパラメータは異なる（`main/__init__.py:295-337`：TC24051901 は lot_size/stop_loss_points/take_profit_points/point_size を参照、MaSlope は slope_shift/slope_min_points 追加、MaSlopePending/StopEntryProbe は digits/stops_level/entry_offset_points/entry_type 追加）。探索空間に「当該戦略が参照しないパラメータ」を入れると no-op 候補で計算を浪費する。 |
| リスク-O3 | 決定論性。random 探索は seed 未固定だと再現不能（NFR-OD1）。tie（同一目的値）の best 選択順も決定論規則が必要。 |

---

## 3. システムアーキテクチャ

> 出力元：S-1 採用パターン選定 ／ S-2 アーキテクチャ設計

### 3.1 全体構成図

```
┌────────────────────────────────────────────────────────────────────┐
│ [新規] 実行入口層（tools）  simulator/tools/optimize_cli.py          │
│   - CLI 引数解釈（探索空間・探索アルゴリズム・目的関数・split 等）  │
│   - build_interactor_factory（params→(controller,request) の閉包）  │
│   - 候補ごとに build_interactor を再構築し run_segment を生成（O1）  │
│   - assert_safe_output_dir（サブフェーズ1再利用）→ 新規 OUT 書込    │
└───────────────────────────────┬────────────────────────────────────┘
                                │ 呼出（依存方向：tools → usecase）
                                ▼
┌────────────────────────────────────────────────────────────────────┐
│ [新規] オーケストレーション UC  simulator/usecase/optimize.py        │
│   - OptimizeRequest（探索空間・split・is_trading_start・metric_names）│
│   - ParameterSearchPort.candidates(search_space) で候補列挙（FR-O2） │
│   - 各候補 params に対し make_run_segment(params) を呼び IS run（O1） │
│   - ObjectivePort.score(is_stats) で IS 目的値算出（FR-O3）          │
│   - argmax で best params 確定（FR-O5・tie は探索順先勝ち）          │
│   - best params で OOS run（FR-O6）                                  │
│   - build_degradation_report(is_best_stats, oos_stats)（SP1再利用）  │
│   - OptimizeResult（best_params・IS/OOS stats・degradation・探索ログ）│
└──────┬──────────────────────────────────┬──────────────────────────┘
       │ 抽象を注入（DIP）                  │ 部品再利用（SP1・無改変）
       ▼                                   ▼
┌──────────────────────────┐   ┌──────────────────────────────────────┐
│ [新規] Port 抽象          │   │ [既存・無改変] simulator/usecase/      │
│ simulator/usecase/        │   │   run_is_oos.py（サブフェーズ1）       │
│   optimize_ports.py       │   │   - slice_is_bars(bars, split)         │
│  - ParameterSearchPort    │   │   - build_degradation_report(...)      │
│    .candidates(space)     │   │   - extract_metrics(...)               │
│  - ObjectivePort          │   │   - DegradationReport / MetricDegrad.  │
│    .score(stats)->float   │   │   - run_segment 契約（型エイリアス）   │
│ [新規] 既定実装           │   └──────────────────────────────────────┘
│   optimize_strategies.py  │                  │ 部品（run_segment）として
│  - GridSearch / RandomSrch│                  ▼
│  - PfObjective/NetProfit..│   ┌──────────────────────────────────────┐
└──────────────────────────┘   │ [既存・無改変] committed エンジン       │
                                │   build_interactor(... params ...)→     │
                                │     (controller, request)               │
                                │   controller._interactor.execute(req)→  │
                                │     BacktestResult.stats                │
                                └──────────────────────────────────────┘
```

- **データフロー**：探索空間（読み取り）→ `ParameterSearchPort.candidates` が候補 params 列を決定論的に列挙 → tools が候補ごとに `build_interactor(**base_kwargs, **params)` を再構築し `run_segment`（`controller._interactor.execute` 閉包）を生成（課題-O1）→ UC が候補ごとに `slice_is_bars` で IS bars を head 切りし `run_segment(is_bars, is_trading_start)` で IS run → `ObjectivePort.score(is_stats)` で目的値 → argmax で best params → best の run_segment で `run_segment(full_bars, split)` の OOS run → `build_degradation_report`（サブフェーズ1再利用）で IS best vs OOS 劣化 → 探索ログ＋結果を新規 OUT へ。

### 3.2 アーキテクチャパターン選択理由

- **採用パターン**：クリーンアーキテクチャ準拠「オーケストレーション UC ＋ Strategy パターン（探索／目的関数を Port 抽象で差し替え）」。新 UC は committed の公開 IF とサブフェーズ1の部品を呼ぶだけでエンジン内部に手を入れない。探索アルゴリズム・目的関数は Port 抽象（DIP）で外部注入し UC から独立して差し替える。

| 評価軸 | 案A：新規 UC `optimize.py`＋Port 抽象（探索/目的を差替）（採用） | 案B：サブフェーズ1 `run_is_oos` を拡張（IS ループ引数を追加改修） | 案C：tools スクリプトに探索ループ直書き（UC・Port なし） |
|---|---|---|---|
| 要件適合（IS 探索→best→OOS 検証・差替可能性） | ○ UC が探索統括・Port で grid/random・PF/NetProfit を差替 | △ IS/OOS 対称契約（`run_is_oos.py:134-135`）に非対称ループを後付け＝IF 肥大 | △ 再利用不能・後段③が呼べない |
| C2 committed・サブフェーズ1 無改変 | ○ 既存・SP1 ファイル 0 行差分。SP1 を部品再利用 | ✕ `run_is_oos.py` を改修＝C2 違反（SP1 公開 IF 破壊） | ○（だが Port 抽象がない） |
| 後段③（ウォークフォワード）の土台化 | ○ `walk_forward` が `optimize` を窓ごとに反復呼出 | △ 拡張した `run_is_oos` に WF 都合が混入 | ✕ tools にロジック閉塞で再利用不能 |
| 探索アルゴリズム・目的関数の差し替え | ○ Port 抽象（DIP）で grid↔random・PF↔NetProfit を注入 | ✕ `run_is_oos` に探索ロジックを埋め込むと差替不能 | △ tools 内 if 分岐で差替も再利用不能 |
| クリーンアーキ依存方向（usecase→domain のみ） | ○ 新 UC・新 Port は domain/同階層 model のみ依存 | ○（既存層内だが IF 肥大） | △ tools は main 依存可だが責務肥大 |
| MT5 突合資産・SP1 資産の保護 | ○ エンジン・SP1 無波及 | ✕ SP1 の bit-exact 経路を改変するリスク | ○ |

- **採用根拠**：案 A は C2（committed＋サブフェーズ1 無改変）と「探索/目的関数の差し替え（FR-O2/FR-O3）」と「後段③土台化」を同時に満たす唯一案。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 のレイヤリング図（`optimize.py` が `ParameterSearchPort`／`ObjectivePort` を持つ構想）に整合。
- **棄却理由**：案 B はサブフェーズ1 `run_is_oos` の改修が必須となり C2 違反かつ SP1 公開 IF（IS/OOS 対称 2 回呼び契約）を破壊する。案 C は Port 抽象を欠き探索/目的関数を差し替え不能、かつ後段③ `walk_forward` が呼べず土台化要件を満たさない。
- 出典：公式設計原則（クリーンアーキ・DIP・Strategy パターン〔GoF〕）＋プロジェクト規約（C2・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3）＋（実務的推奨／仮説）。

### 3.3 技術スタック詳細

| 層 | 採用技術 | バージョン | 代替候補 | 採用根拠 |
|---|---|---|---|---|
| オーケストレーション UC | 純 Python（`@dataclass`・標準ライブラリ・`typing.Protocol` または ABC） | 既存リポジトリと同一（追加なし） | pydantic 等の検証層 | C3 技術スタック追加禁止。usecase 層は pydantic 非依存（`models.py:1-9` で実証）。Port 抽象は標準 `typing.Protocol`／`abc.ABC` で表現可（依存追加なし） |
| 探索アルゴリズム | 純 Python（grid＝`itertools.product`／random＝`random.Random(seed)`） | 既存／標準ライブラリ | scipy.optimize・optuna 等 | C3 追加禁止。grid/random は標準ライブラリのみで決定論的に実現（seed 固定）。最適化ライブラリ導入は C3 違反 |
| 部品（サブフェーズ1） | `simulator/usecase/run_is_oos.py`（`slice_is_bars`/`build_degradation_report`/`extract_metrics`） | 既存 | — | C2 によりサブフェーズ1を無改変で再利用 |
| 部品（エンジン） | committed `simulator.main.build_interactor`／`controller._interactor.execute` | 既存 | — | C2 により無改変で再利用 |
| 入力前処理（tools） | `pandas`（main/tools 層内に閉じる既存利用） | 既存 | — | 先例 `run_is_oos_cli.py:21`／`normalize_time`（同 L54）が tools での pandas 利用を確立済。usecase へ漏らさない |

- 技術スタックの追加・バージョン変更は 0 件（C3 遵守）。

### 3.4 レイヤー構成・責務分担

| レイヤー | 責務 | 依存先 | 出典／根拠 |
|---|---|---|---|
| tools（実行入口・新規 `optimize_cli.py`） | CLI 引数解釈・読み取り専用ロード・**候補 params ごとの `build_interactor` 再構築＋`run_segment` 生成**（課題-O1）・出力先検証（SP1 再利用）・新規 OUT 書込・探索アルゴリズム/目的関数の具体実装の選択・注入 | usecase（新 UC・新 Port・SP1）・main（`build_interactor`） | プロジェクト規約（`run_is_oos_cli.py:38-51` の `make_run_segment` パターンを params 可変へ拡張） |
| usecase（オーケストレーション・新規 `optimize.py`） | 探索候補ループ統括・IS run／目的値算出／argmax best 確定／OOS run／劣化算出（SP1 `build_degradation_report` 再利用）。domain・同階層 model・SP1・新 Port のみ依存 | domain・`usecase.models`・`usecase.run_is_oos`（SP1・無改変）・`usecase.optimize_ports` | 公式設計原則（クリーンアーキ：usecase は domain のみ依存。`run_is_oos.py:7-8` の既存規約を継承） |
| usecase（Port 抽象・新規 `optimize_ports.py`） | `ParameterSearchPort`（探索戦略の抽象）・`ObjectivePort`（目的関数の抽象）の IF 定義 | （標準ライブラリのみ） | 公式設計原則（DIP・Strategy パターン） |
| usecase（既定実装・新規 `optimize_strategies.py`） | GridSearch／RandomSearch・PfObjective／NetProfitObjective 等の具体実装 | `usecase.optimize_ports`・`usecase.models`（`BacktestStats` 参照） | 公式設計原則（OCP：新アルゴリズム追加で既存無改変） |
| usecase（サブフェーズ1・無改変） | `slice_is_bars`／`build_degradation_report`／`extract_metrics`／`run_segment` 型契約 | domain・`usecase.models` | C2（サブフェーズ1 無改変） |
| usecase（既存・無改変） | 1 run プリミティブ（`RunBacktestInteractor.execute`） | domain | C2 |
| domain（無改変） | Bar / TradeRecord / BacktestStats 等の VO | （なし） | C2 |

- **依存方向**：tools → usecase（新 UC・新 Port・既定実装・SP1）→ domain。新 UC は committed `build_interactor`（main 層）を**直接 import せず**、サブフェーズ1と同様に「候補 params に対し 1 区間を実行する手段（run_segment）を生成するファクトリ」を**tools 層からコールバックとして注入**する（DIP・課題-O1 の解決）。これにより新 UC は domain／同階層のみ依存（main 非依存）を保つ。
  - 出典：公式設計原則（依存方向の内向き規律・DIP）。

---

## 4. 機能設計

> 出力元：S-3 機能設計

### 4.1 機能一覧・優先度

| 機能 ID | 機能名 | 概要 | 優先度 | 対応要件 ID |
|---|---|---|---|---|
| FO-01 | 探索空間定義 | 可変パラメータ名→**有限候補値リスト**（および random 用 seed／n_samples・**必須の max_candidates**）を入力として受領。max_candidates は既定なし＝未指定は入力検証エラー（M-3） | 高 | FR-O1, FR-O9 |
| FO-02 | 候補列挙（探索アルゴリズム抽象） | `ParameterSearchPort.candidates(space)` が候補 params 列を決定論的に列挙（grid／random 差替可能） | 高 | FR-O2, FR-O9 |
| FO-03 | IS 候補実行 | 候補ごとに `build_interactor` 再構築→run_segment 生成→`slice_is_bars` で IS bars→IS run→`BacktestStats` | 高 | FR-O4 |
| FO-04 | 目的関数評価（目的関数抽象） | `ObjectivePort.score(is_stats)->float`（PF／NetProfit／Sharpe 差替可能）で IS 目的値算出 | 高 | FR-O3 |
| FO-05 | best params 確定 | IS 目的値の argmax で best params を確定（tie は探索順先勝ち） | 高 | FR-O5 |
| FO-06 | OOS 検証 | best params を凍結し OOS（full_bars + trading_start=split）で 1 回 run | 高 | FR-O6 |
| FO-07 | 過剰最適化レポート | IS best stats と OOS stats から `build_degradation_report`（SP1 再利用）で劣化算出 | 高 | FR-O7 |
| FO-08 | 探索ログ出力 | 候補ごとの params・IS 目的値・best 採否を一覧化して出力 | 中 | FR-O8 |

### 4.2 機能詳細仕様（主要機能のみ）

#### FO-02 候補列挙（`ParameterSearchPort`）

- **入力**：探索空間（`search_space`：可変パラメータ名→**有限の候補値リスト**の写像。grid／random とも同一の離散候補集合空間）、探索アルゴリズム固有設定（grid＝なし／random＝`seed` と `n_samples`）、`max_candidates`（**必須引数・既定なし**＝M-3）。
- **辞書順序規約（決定論の基礎・NFR-OD1）**：
  - **パラメータキーは辞書順昇順で固定**する（`sorted(search_space.keys())`）。
  - grid／random とも、固定キー順に各値リストを並べた **全直積を辞書順（左キーほど上位）で列挙したインデックス列 0..N_space-1** を基準順序とする（`N_space = Π(各値リスト長)`）。
- **処理**：
  - grid：上記辞書順インデックス列を**そのまま全列挙**（`itertools.product` を固定キー順で適用相当）。N_cand = N_space。
  - random（High-3 確定方式）：**離散候補集合からの非復元抽出**として定義する。手順＝① キーを辞書順昇順で固定 ② 全直積を辞書順で列挙したインデックス列 `range(N_space)` に対し `random.Random(seed).sample(range(N_space), k=min(n_samples, N_space))` で **インデックスを選ぶ**（float 値そのものの等価比較に依存せず**整数インデックスで非復元抽出**するため、float の等価比較に起因する重複判定の不安定を回避）③ 選ばれたインデックスを ParamSet へ復号。`n_samples > N_space` の場合は**全件（N_space 件）を採用しログに明示**する。これにより `seed` 固定で抽出が決定論（NFR-OD1）。
  - **上限制御（FR-O9・NFR-OP2・M-2）**：列挙前に**理論候補数**を算出する（grid＝`N_space`／random＝`min(n_samples, N_space)`）。**理論候補数 > `max_candidates` の場合は件数をログに明示出力した上で拒否する（既定かつ単一動作・M-2）**。**無音切り捨ては禁止**。
- **後条件**：候補 params の決定論的順序付きリスト（各要素は build_interactor 引数へマージ可能な部分写像。grid は辞書順全列挙順／random は seed 固定で選ばれたインデックスの昇順）。
- **抽象 IF（概念）**：`ParameterSearchPort.candidates(search_space) -> Iterable[ParamSet]`。実装差し替えで grid↔random を切替（FR-O2・OCP）。

#### FO-03 IS 候補実行（run_segment 再生成・課題-O1）

- **入力**：候補 params（FO-02 の 1 要素）、`base_kwargs`（build_interactor の固定引数：data_path/symbol/ea_name/config_overrides 等）、`split`、`is_trading_start`、`full_bars`。
- **処理（課題-O1 の解決方式）**：候補 params ごとに **tools 層のファクトリ `make_run_segment(params)`** が `build_interactor(**base_kwargs, **params)` を**再構築**して新しい `(controller, request)` を得（候補 params が build_interactor 引数＝`lot_size`/`stop_loss_points`/`take_profit_points`/`entry_offset_points`/`ma_period` 等の実体・`main/__init__.py:256-285` で実証）、サブフェーズ1の `make_run_segment(controller, request)`（`run_is_oos_cli.py:38`）相当の閉包で `run_segment` を生成。UC は `slice_is_bars(full_bars, split)`（SP1・`run_is_oos.py:26`）で IS bars を head 切りし、`run_segment(is_bars, is_trading_start)` を呼んで IS の `BacktestStats` を得る。
- **呼出経路（SP1 継承・B-1）**：run_segment 内部は `controller._interactor.execute(request)`（`run_is_oos_cli.py:48`）を直接呼ぶ。`controller.run()` は使わない（data_path 再ロードで IS truncation が無効化されるため。SP1 基本設計 §6.1 で実証済）。
- **後条件**：候補ごとの `BacktestStats`（IS 区間）。
- **例外条件**：空区間（IS バー数 < 1 または OOS バー数 < 1）は SP1 の検証（`run_is_oos.py:128-131` の `IsOosValidationError`）相当を**探索ループ開始前に 1 回**実施（split・is_trading_start は全候補共通のため候補ごとに再検証しない）。

#### FO-04 目的関数評価（`ObjectivePort`）

- **入力**：IS の `BacktestStats`。
- **処理**：`ObjectivePort.score(is_stats) -> float` が **大きいほど良い** 単一スカラを返す（argmax 規約）。既定実装は `BacktestStats` の実在フィールド（`models.py:97-105` で実証）に限定：
  - `PfObjective`：`profit_factor` を返す。
  - `NetProfitObjective`：`profit` を返す。
  - `SharpeObjective`：`sharpe_ratio` を返す。
  - `RecoveryObjective`：`recovery_factor` を返す。
- **有限性規約（C-1）**：`BacktestStats` の `profit_factor`／`sharpe_ratio`／`recovery_factor` は float であり、約定 0 件・gross_loss=0 等の境界で **NaN／±inf を取り得る**（`models.py:100-103` で float 型を実証。例：`profit_factor = gross_profit / gross_loss` が gross_loss=0 で非有限）。**`score` が返す値の有限性は `math.isfinite(score)` で判定することを規約化**する。**非有限スコア（NaN／±inf）を返した候補は探索対象（argmax 母集合）から除外**する。除外は無音にせず探索ログに **除外件数と理由（非有限スコア）を明示**する（FR-O8／FR-O9 の無音切り捨て禁止と整合）。
- **後条件**：IS 目的値（float）。非有限値は best 選定の母集合に含めない（FO-05 へ渡さない）。
- **抽象 IF（概念）**：`ObjectivePort.score(stats) -> float`。実装差し替えで PF↔NetProfit↔Sharpe を切替（FR-O3・OCP）。SP1 の `extract_metrics`（`run_is_oos.py:91`）を内部利用可（同関数は「`BacktestStats` から name→値 抽出」で `ObjectivePort` 前身と docstring に明記・L92）。

#### FO-05 best params 確定

- **入力**：候補ごとの `(params, is_stats, is_score)` 列。
- **処理（C-1 確定方式）**：
  1. **有限スコアのみで母集合を構成**する。`math.isfinite(is_score)` が真の候補のみを argmax 対象とする（非有限スコア候補は FO-04 で既に除外・除外件数はログ明示済）。
  2. 有限スコア母集合に対し **argmax** で best params を確定する。**tie（同一最大目的値が複数）は FO-02 の決定論的列挙順（grid 辞書順／random も §FO-02 の辞書順インデックス規約）で最初に出現した候補を採用（先勝ち）**。有限スコア集合は全順序が定義可能（float の `>` 比較で全順序、tie は列挙順で確定）であるため best は一意に決まる（NFR-OD1）。
  3. **全候補が非有限スコア（有限スコア母集合が空集合）の場合は best 0 件**となる。この場合は **無音で fallback せず明示エラー（`OptimizeError` 相当）で中断**する。エラーには「総候補数・非有限除外件数・有限候補 0 件」を context として載せる（M-1 の「best 0 件なら中断」と整合）。
- **後条件**：`best_params`・`best_is_stats`（best 候補の IS run 結果を保持値としてそのまま採用＝High-2。OOS は別途 FO-06 で再 run）・`best_is_score`。または best 0 件時は明示中断（成果物を出力しない）。

#### FO-06 / FO-07 OOS 検証・過剰最適化レポート

- **入力**：`best_params`、`full_bars`、`split`、`best_is_stats`（探索中に保持した best 候補の IS run 結果＝High-2）。
- **処理（High-2 確定方式）**：
  - **`best_is_stats` は探索ループ中に各候補の IS run 結果（`is_stats`）を `TrialRecord`／中間データに保持しておき（§5.1／§5.2／FO-05 後条件）、best 確定時にその保持値をそのまま採用する。best の IS を再 run しない**（同一 params の IS を 2 回走らせない＝決定論かつコスト無駄を回避。total run = N_cand+1 を厳守）。
  - OOS は best_params で `make_run_segment(best_params)` を **別途 1 回再構築**して `run_segment(full_bars, split)`（OOS：full bars 無改変＋trading_start=split・SP1 OOS 方式）で OOS の `BacktestStats` を **1 回**取得する（build_interactor 再構築は IS 候補ビルドとは別インスタンス＝差し替えで捨てられる。よって OOS は N_cand+1 番目の run）。
  - SP1 の `build_degradation_report(best_is_stats, oos_stats, metric_names)`（`run_is_oos.py:96`）で **IS best（保持値）vs OOS の `DegradationReport`（ratio+delta 両格納）** を算出。
- **後条件**：`oos_stats`・`DegradationReport`。run 回数＝IS（N_cand 回）＋OOS（best 1 回）＝N_cand+1。best の IS run は再実行されず保持値を用いる（High-2）。
- **判断（実務的推奨／仮説）**：IS で目的関数を最大化した best が OOS で大きく劣化（profit_factor/profit の ratio < 1・delta < 0）するほど過剰最適化の疑い。**合否閾値判定は本サブフェーズでは行わず劣化の提示のみ**（SP1 の方針を継承。合否ロジックは後段③で窓間の安定性として扱う）。

### 4.3 処理フロー図

```
[start] tools/CLI
  │ 入力: data参照, base_kwargs, search_space, search_algo(+seed), objective,
  │       split, is_trading_start, max_candidates, config
  ▼
(1) 入力検証（探索ループ前に 1 回）: start<=is_trading_start<=split<=end / 時刻型整合
  │   max_candidates 必須（未指定→検証エラー・M-3）
  │   空区間判定（SP1継承）: IS バー数>=1 かつ OOS(bar.time>=split) 数>=1
  │   探索空間上限: 理論候補数（grid=N_space / random=min(n_samples,N_space)）算出
  │              → max_candidates 超過なら件数ログ→拒否（単一動作・M-2/FR-O9）
  │  NG → 中断
  ▼
(2) candidates = ParameterSearchPort.candidates(search_space)  ← grid/random（辞書順序規約・決定論順）
  ▼
(3) for params in candidates:                                  ← N_cand 回ループ
  │     (3a) run_segment_p = make_run_segment(params)  ← build_interactor 再構築（CSV再ロード込・課題O1/High-1）
  │     (3b) is_bars = slice_is_bars(full_bars, split)（SP1・head切り・slice用full_bars保持）
  │     (3c) try: is_stats = run_segment_p(is_bars, is_trading_start)  ← IS run（execute直呼・B-1）
  │          except (ConfigError|BacktestError|MarginCallError): 失敗候補をログ除外し continue（M-1）
  │     (3d) score = ObjectivePort.score(is_stats)
  │          if not math.isfinite(score): 非有限候補をログ除外し argmax 母集合に入れない（C-1）
  │     (3e) 探索ログ TrialRecord へ {params, score, is_finite, is_stats(保持)} 追記（High-2）
  ▼
(4) finite = [t for t in trials if t.is_finite and not t.failed]   ← 有限スコアのみ（C-1）
      if finite == []: 明示エラーで中断（best 0 件・無音禁止・C-1/M-1）
      best = argmax_score(finite)  ← tie は基準順序先勝ち（NFR-OD1）
      best_is_stats = best.is_stats（保持値・再 run しない・High-2）
  ▼
(5) run_segment_best = make_run_segment(best.params)   ← OOS 用に別途 build 再構築（N_cand+1 番目の run）
      oos_stats = run_segment_best(full_bars, split)  ← OOS run（trading_start=split）
  ▼
(6) degradation = build_degradation_report(best_is_stats, oos_stats, metric_names)（SP1再利用）
  ▼
(7) OptimizeResult 構築（best_params|IS best stats|OOS stats|劣化|探索ログ）
      → assert_safe_output_dir（SP1再利用・出力先検証）→ 新規OUT へ JSON/MD 書込
  ▼
[end]
```

### 4.4 業務フロー・ユースケース

- **主アクター**：E. 最適化（パラメータ探索アルゴリズム＋目的関数の担当・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §2）。
- **ユースケース**：「分析者が、戦略パラメータの探索空間と目的関数を与え、IS で最良パラメータを機械探索させ、その同一パラメータを OOS で検証して過剰最適化の有無を 1 回の操作で評価する」。後段③ウォークフォワード（D＋E）は本 UC を窓ごとに反復呼出する上位ユースケース。

---

## 5. データ設計

> 出力元：S-3 データ設計

### 5.1 データモデル概要（概念・実装非依存）

- **入力**：`{ 価格データ参照, base_kwargs（build_interactor 固定引数）, 探索空間, 探索アルゴリズム指定(+seed), 目的関数指定, split, is_trading_start, max_candidates, config_overrides }`。
- **中間**：`{ 候補 params 列, 候補ごとの (params, is_stats, is_score, is_finite), best_params, best_is_stats }`。**各候補の IS run 結果 `is_stats` は `TrialRecord` に保持し、best 確定時にその保持値を `best_is_stats` として確定する（High-2：best の IS を再 run しない）**。`is_finite`＝`math.isfinite(is_score)` の判定結果（非有限候補は argmax 母集合から除外・C-1）。
- **出力**：`{ best_params, best_is_stats, oos_stats, DegradationReport, 探索ログ }`。

### 5.2 主要エンティティ定義

| エンティティ | 概要 | 主要属性 | 関連エンティティ |
|---|---|---|---|
| OptimizeRequest（新規・概念） | 最適化の入力一式 | search_space、search_algo（+seed/n_samples）、objective、split、is_trading_start（必須・SP1 H-1 継承）、max_candidates、metric_names | SearchSpace, SplitBoundary |
| SearchSpace（新規・概念） | 探索空間 | 可変パラメータ名→候補値集合の写像（パラメータ名は build_interactor 引数のサブセット・§5.5） | OptimizeRequest |
| ParamSet（新規・概念） | 1 候補のパラメータ組 | build_interactor 引数へマージ可能な部分写像（例：{lot_size, stop_loss_points, take_profit_points, ...}） | SearchSpace, TrialRecord |
| TrialRecord（新規・概念） | 探索 1 試行の記録 | params（ParamSet）、is_score（float）、is_finite（bool＝`math.isfinite(is_score)`・C-1）、is_stats（**保持・参照**＝best 確定時に再 run せず best_is_stats へ昇格・High-2）、is_best（bool） | ParamSet, BacktestStats |
| BacktestStats（既存・無改変） | 1 区間の成績（`models.py:91-142` で実在） | profit / profit_factor / recovery_factor / expected_payoff / sharpe_ratio / trades ほか | BacktestResult |
| DegradationReport（SP1・無改変・再利用） | IS best vs OOS の劣化（`run_is_oos.py:69-79`） | MetricDegradation の集合（各 {name, is_value, oos_value, ratio, delta}） | BacktestStats×2 |
| OptimizeResult（新規・概念） | 最適化の出力一式 | best_params、best_is_stats（保持値・High-2）、oos_stats、degradation、trials（探索ログ）、excluded_count（非有限スコア／失敗で除外した候補数・C-1/M-1） | ParamSet, BacktestStats, DegradationReport, TrialRecord |
| OptimizeError（新規・概念） | 探索が結果を出せない場合の明示中断（無音禁止・C-1/M-1） | 中断理由（有効候補 0 件＝全候補が非有限スコアまたは失敗）、context（総候補数・非有限除外件数・失敗除外件数） | OptimizeRequest |

- **抽象度維持**：物理テーブル・クラス名・DDL は内部設計に委譲（本書は概念定義のみ）。

### 5.3 データフロー図

```
探索空間(SearchSpace・読取)
   │  ParameterSearchPort.candidates（grid/random・決定論順）
   ▼
候補 params 列(ParamSet×N) ──for each──► make_run_segment(params)[build_interactor 再構築]
                                              │
   full_bars ──slice_is_bars(bars,split)──► IS bars ─► run_segment_p(is_bars, is_trading_start) ─► is_stats
                                              │                                                      │
                                              │                                          ObjectivePort.score ─► is_score
                                              ▼                                                      ▼
                                       探索ログ(TrialRecord×N) ◄───────────────────────── {params, is_score}
                                              │ argmax (tie=先勝ち)
                                              ▼
                                       best_params ─► make_run_segment(best) ─► run_segment_best(full_bars, split) ─► oos_stats
                                                                                                                          │
                            build_degradation_report(best_is_stats, oos_stats)[SP1再利用] ◄────────────────────────────────┘
                                              ▼
                                       OptimizeResult → assert_safe_output_dir[SP1] → 新規OUT(JSON/MD)
```

### 5.4 データライフサイクル

- **入力データ**：読み取り専用（NFR-OS1・C1）。改変・移動・削除を行わない。
- **中間（候補 params・stats・探索ログ）**：プロセスメモリ上のみ（永続化は最終出力時のみ）。SP1 の IS truncation（option b＝`request.bars` 差し替え・in-memory）を継承し中間 CSV を生成しない。
- **build_interactor の再構築**：候補ごとに新規 `(controller, request)` を生成（課題-O1）。**この再構築のたびに build_interactor が内部で `data_path` から CSV を再ロードする**（registry 用 `_load_dataframe`／bars 用 `market_data.load`＝`main/__init__.py:335,344`）。registry は候補 params が `ma_period`/`ma_method` を含む場合に異なる値で再計算される（`main/__init__.py:312-337`）。SP1 の「単一 controller を IS/OOS 流用」とは異なり、最適化は候補ごとに full ビルド（CSV 再ロード込み）を行う点を明記（NFR-OP1／NFR-OP4 のコスト要因＝CSV ロード N_cand+1 回・High-1）。tools が保持する `full_bars` は **IS バー slice の入力**としてのみ使い、build 内部の CSV ロードを置換しない（NFR-OP3）。
- **出力レポート**：新規パスのみ（SP1 の `assert_safe_output_dir`・`run_is_oos_cli.py:67` を再利用して担保）。保持期間・アーカイブは運用判断（TBD・§9.3）。

### 5.5 探索対象パラメータの表現（build_interactor 引数のどれを可変にするか）

- **可変候補（build_interactor 引数のサブセット・`main/__init__.py:256-285` の strategy_params／registry 引数で実証）**：
  - 全戦略共通：`lot_size`、`stop_loss_points`、`take_profit_points`、`point_size`（`main/__init__.py:296-299` で strategy_params に格納）。
  - 指標系：`ma_period`、`ma_method`（registry 構築に渡る・`main/__init__.py:316/322/330/336`。可変にすると候補ごとに registry 再計算）。
  - MaSlope 系：`slope_shift`、`slope_min_points`（`main/__init__.py:301-302`）。
  - Pending／StopEntryProbe 系：`entry_offset_points`、`entry_type`、`digits`、`stops_level`（`main/__init__.py:304-307`）。
- **戦略別の有効パラメータ（リスク-O2 対策）**：探索空間に入れたパラメータが当該 `ea_name`（戦略）に参照されない場合は no-op 候補となり計算を浪費する（`main/__init__.py:312-337` で ea_name ごとに戦略・registry が分岐）。探索空間定義時に「当該戦略が参照するパラメータのみ」を選ぶことを設計上の前提とする（検証は探索ログの目的値が候補間で不変＝no-op を検出可能）。
- **探索空間の表現（概念）**：パラメータ名（build_interactor キーワード引数名と一致）→候補値の列（grid）または値域（random）の写像。`ParamSet` は build_interactor へ `**params` でマージ可能な部分写像（tools 層が `build_interactor(**base_kwargs, **params)` で結線）。
- **不変（探索対象外）**：`split`・`is_trading_start`・`config_overrides`（決定論 9 項目）・`symbol`・`ea_name`・`data_path`・口座初期値は全候補共通（base_kwargs）。決定論 config を可変にすると約定モデルが変わり比較が無意味化するため探索対象外とする（出典：実務的推奨／仮説）。

---

## 6. インターフェース設計

> 出力元：S-3 インターフェース設計

### 6.1 API 設計概要（内部 UC IF・概念レベル）

- **種別**：プロセス内の関数／UC 呼び出し（本サブフェーズに HTTP/REST は含めない。ブラウザ UI は `.doc/ISOOS_BROWSER_PLAN_WIP.md` の Phase 2）。
- **新規 UC（`simulator/usecase/optimize.py`・概念 IF）**：
  - 入力：`OptimizeRequest`（概念）＝`{ search_space, search_port, objective_port, split, is_trading_start, max_candidates, metric_names }` ＋ 注入される `make_run_segment: Callable[[ParamSet], run_segment]`（params→run_segment ファクトリ・課題-O1）と `full_bars`。
  - 出力：`OptimizeResult`（概念）＝`{ best_params, best_is_stats（保持値・High-2）, oos_stats, degradation: DegradationReport, trials, excluded_count }`。**有効候補が 0 件（全候補が非有限スコアまたは失敗）の場合は `OptimizeResult` を返さず `OptimizeError` で明示中断する（C-1/M-1・無音禁止）**。`ObjectivePort.score` の有限性は `math.isfinite` で判定し、非有限スコア候補は argmax 母集合から除外する。
- **新規 Port 抽象（`simulator/usecase/optimize_ports.py`・概念 IF）**：
  - `ParameterSearchPort.candidates(search_space) -> Iterable[ParamSet]`（FR-O2・差替可能）。
  - `ObjectivePort.score(stats: BacktestStats) -> float`（FR-O3・大きいほど良い規約・差替可能）。
- **依存方向の確定（クリーンアーキ遵守・SP1 継承）**：
  - `build_interactor`／`run_backtest` は **main 層**。usecase が main を import すると外向き依存（クリーンアーキ違反）。
  - **確定方式（課題-O1 の解決・SP1 のコールバック注入を拡張）**：新 UC は「候補 params に対し 1 区間を実行する run_segment を生成するファクトリ `make_run_segment(params)`」を**tools 層からコールバックとして注入**される。tools 層が候補ごとに `build_interactor(**base_kwargs, **params)` を再構築し、SP1 の `make_run_segment(controller, request)`（`run_is_oos_cli.py:38`）相当の閉包で run_segment を構成する。新 UC は `slice_is_bars`（SP1）で IS bars を導出し、注入された run_segment を IS（候補ごと N 回）・OOS（best 1 回）で呼ぶ。これにより新 UC は domain／同階層のみ依存（main 非依存）を保つ。
    - 呼出経路（B-1・SP1 継承）：run_segment 内部は `controller._interactor.execute(request)`（`run_is_oos_cli.py:48`）。`controller.run()` は使わない（IS truncation 無効化回避）。`_interactor` は private 属性だが読み取り利用のみ・改変なし（NFR-OS2／C2 と非矛盾・SP1 で実証済）。
    - 代替案：新 UC が `make_run_segment` を内部生成（`build_interactor` を usecase が import）。ただし usecase→main の逆依存（クリーンアーキ違反）かつ Port 実装組み立ての複製（DRY 違反）。よってファクトリ注入を採用。
    - 出典：公式設計原則（DIP・依存方向内向き）＋SP1 基本設計 §6.1 の確定方式継承。
- **サブフェーズ1 公開 IF の再利用（無改変・C2）**：
  - `slice_is_bars(bars, split)`（`run_is_oos.py:26`）：IS head 切りをそのまま使用。
  - `build_degradation_report(is_stats, oos_stats, names)`（`run_is_oos.py:96`）：IS best vs OOS 劣化算出をそのまま使用。
  - `extract_metrics(stats, names)`（`run_is_oos.py:91`）：`ObjectivePort` 既定実装の内部で再利用可。
  - `DegradationReport`／`MetricDegradation`（`run_is_oos.py:58-79`）：`OptimizeResult.degradation` の型として再利用。
  - `run_segment` 型契約（`RunSegment = Callable[[bars, trading_start], BacktestStats]`・`run_is_oos.py:23`）：注入コールバックの型として継承。
  - **`run_is_oos(...)` 関数自体は呼ばない**（IS/OOS 対称 2 回呼び契約のため非対称な最適化ループには不適合・課題-O2）。サブフェーズ1の**部品（純関数）のみ**を再利用し公開 IF を壊さない。

### 6.2 画面構成・遷移

- 該当なし（本サブフェーズは UI を含まない。ブラウザ UI は Phase 2・`.doc/ISOOS_BROWSER_PLAN_WIP.md` §5）。

### 6.3 外部システム連携仕様

| 連携先 | 連携方式 | データ形式 | 頻度 | エラー時動作 |
|---|---|---|---|---|
| committed バックテストエンジン（`simulator.main.build_interactor`／`controller._interactor.execute`） | プロセス内関数呼び出し（無改変・部品利用） | `RunBacktestRequest`／`BacktestResult`（dataclass） | **候補ごと（IS）N_cand 回＋OOS 1 回＝N_cand+1 回** | **UC/tools が候補ごとに次の例外型を網羅捕捉（M-1）：`ConfigError`／`BacktestError`／`MarginCallError`**。`MarginCallError` は `ExecutionError(BacktestError)` のサブクラスだが（`exceptions.py:92`）、最適化は `run_backtest` を経由せず `controller._interactor.execute(request)` を**直叩き**するため、`run_backtest` の `except ConfigError/BacktestError`（`main/__init__.py:436-438`＝build 段階のみ）の翻訳は **run 中の `MarginCallError` には掛からない**。よって **UC/tools 側で `MarginCallError` を明示捕捉する必要がある**。捕捉した候補は失敗候補としてログに params＋例外型／理由を残し**探索から除外**、**best 候補が 0 件なら明示中断**（部分結果の黙殺禁止・M-1） |
| サブフェーズ1 部品（`simulator.usecase.run_is_oos`） | プロセス内関数呼び出し（無改変・部品利用） | `slice_is_bars`／`build_degradation_report`／`DegradationReport`（dataclass） | IS スライス N_cand 回・劣化算出 1 回 | 純関数のため例外は入力前提違反時のみ（IsOosValidationError 相当は探索前検証で防止） |
| 価格データセット | ファイル読み取り（読み取り専用） | CSV（comma／tab・`build_interactor` ea_name 分岐 `main/__init__.py:312-337`） | **build_interactor 内部で候補ごとに再ロード＝N_cand+1 回**（`main/__init__.py:335,344`・High-1）。別途 tools が slice 用 full_bars を 1 回保持（NFR-OP3） | 読込失敗は `DataError`（`main/__init__.py:166-174`）で中断 |

### 6.4 通信プロトコル・データ形式

- **プロトコル**：なし（プロセス内）。
- **出力データ形式（SP1 L-1 方針継承）**：
  - 機械可読：JSON（best_params／best_is_stats／oos_stats／degradation／trials）。`asdict(stats)`（`run_is_oos_cli.py:88-92` の既存パターン）で `BacktestStats`／`MetricDegradation` を dict 化。
  - 人間可読：Markdown（best params 表＋IS best｜OOS｜劣化の並列表＋探索ログ上位表）。SP1 の `to_markdown`（`run_is_oos_cli.py:95`）の並列レポート形式を踏襲し探索ログ列を追加。
  - **整形は tools 層内で行い新規 presenter を追加しない**（SP1 継承）。committed presenter は改変も流用もせず C2 無改変を維持。
  - 出典：プロジェクト規約（SP1 出力形式に整合・tools 層内整形）。

### 6.5 出力先検証（データ非波及の機構・SP1 再利用）

- **検証関数**：SP1 の `assert_safe_output_dir(out_dir, repo_root)`（`run_is_oos_cli.py:67`）を**そのまま再利用**。`_FORBIDDEN_PREFIXES`（`marketdata`／`simulator/tests/fixtures`／`simulator/tests/confirmation`・`run_is_oos_cli.py:31-35`）配下と repo_root 外への書き込みを拒否（`OutputGuardError`）。
- **検証手段（計測可能・SP1 H-2 継承）**：NFR-OS1 は (1) `assert_safe_output_dir` の単体テスト（禁止プレフィクスへの書込み拒否）、(2) 結合テストで「最適化実行前後に既存データディレクトリ配下ファイルの mtime が不変」を assert、の 2 点で実証する。

---

## 7. 非機能設計

> 出力元：S-4 品質特性の担保

### 7.1 性能設計・スケーラビリティ対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-OP1 エンジン実行回数 | N_cand + 1 回（IS が候補数 N_cand 回・OOS が best 1 回） | 探索は単一ループで N_cand 回 IS run。grid の N_cand = Π(各パラメータ候補数)。OOS は best のみ 1 回（全候補で OOS を走らせない＝N_cand 回の OOS run を回避） | run 全体の wall-clock（SP1 先例 `reconcile.py` の `dt` 計測パターン）・候補数 N_cand のログ出力 |
| NFR-OP2 探索空間上限 | `max_candidates`（**必須引数・既定なし・M-3**） | 列挙前に理論候補数（grid=N_space／random=min(n_samples,N_space)・M-2）を算出し、**理論候補数 > max_candidates なら件数をログ明示して拒否（単一動作・M-2/FR-O9・無音切り捨て禁止）** | 上限超過時のログに候補件数が出力され拒否されること（単体テスト） |
| NFR-OP3 IS バー切り出し方針（性能ではなく正しさの担保） | (a) tools が `full_bars` を **slice 用に in-memory で 1 回保持**（IS バー head 切りの入力） (b) IS bars は `slice_is_bars` で in-memory head 切り（中間 CSV 再生成なし・SP1 option b 継承） (c) OOS は best 1 回 | (a)（b）は **UC へ渡す IS バー列を中間ファイル化せず in-memory で導出する** ための機構であり、**build_interactor 内部の CSV ロード（NFR-OP4）とは別経路**である。tools が保持する `full_bars` は slice の入力に使うのみで、候補ごとの `build_interactor(**base_kwargs, **params)` が内部で読む CSV を肩代わりしない（build が内部 load した bars は差し替えで捨てられ、UC は slice 由来 IS bars を `run_segment` の引数として渡す＝B-1 の execute 直叩き経路）／(c) FO-06 | tools 側 full_bars 保持回数＝1（slice 用・ログ）。**build_interactor 内部の CSV ロードは別途 N_cand+1 回発生する（NFR-OP4・NFR-OP1）** |
| NFR-OP4 build_interactor 再構築コスト（CSV 再ロード込み・High-1） | 候補ごとに build_interactor 再構築＝**内部で CSV を再ロード**（`main/__init__.py:335,344`）。total N_cand+1 回の CSV ロード（課題-O1・committed IF に起因し本サブフェーズでは不可避） | committed `build_interactor` は registry 構築のため `data_path` から CSV を毎回読む（`main/__init__.py:339-344` のコメントで「1 回読みへの統合は committed IF 変更が要るため範囲外＝申し送り」と明記）。本サブフェーズは committed 無改変（C2）が前提のため **再ロード削減は実施せず、N_cand+1 回ロードを正直なコストとして記す**。再ロード削減（build への bars 注入・registry キャッシュ等）は committed IF 変更を要し本サブフェーズ範囲外＝**後段最適化候補へ降格（TBD-O2）** | CSV ロード回数のプロファイル（N_cand+1 回であることを計測・TBD-O2） |
| スケーラビリティ | 後段③で窓数 × N_cand へ拡張時に非同期ジョブ化（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4） | 本サブフェーズは単一最適化（同期）。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4 が「探索空間上限・キャッシュ・並列」を明記 | — |

- **キャッシュ余地（実務的推奨／仮説）**：同一 ParamSet が探索順で重複出現する場合（random で重複サンプルが起きうる）は IS run 結果をキャッシュ可。ただし FO-02 の random は「重複なしサンプル」を規約とするため本サブフェーズではキャッシュ不要。後段③で窓間にまたがる重複が生じる場合のキャッシュは後段で検討（§9 拡張余地）。

### 7.2 可用性設計・障害対策

| 要件 | 数値目標 | 設計対策 | 測定方法 |
|---|---|---|---|
| NFR-OA1 実行完了率 | バッチ実行のため SLA/稼働率対象外（オフライン分析ツール） | **候補単位失敗方針を確定（M-1・旧 TBD-O3 を本文昇格）**：UC/tools が候補ごとに `ConfigError`／`BacktestError`／`MarginCallError`（`execute` 直叩きのため UC/tools が捕捉必須＝`main/__init__.py:436-438` は build 段階の Config/Backtest のみ）を捕捉し、**失敗候補は params＋例外型／理由を探索ログに残して探索から除外**する。**有限スコア（C-1）かつ失敗除外後の有効候補が 0 件＝best 0 件なら明示エラーで中断**する。**部分結果の黙殺をしない** | 単体テストで候補失敗時のログ（除外件数・理由）／best 0 件中断を検証 |

- 本機能はオフラインの分析ツールであり、常時稼働サービスの可用性（99.x%・MTTR）は適用対象外（該当なし）。

### 7.3 セキュリティ設計

- **認証・認可**：該当なし（プロセス内ローカル実行・本サブフェーズに公開エンドポイントなし）。
- **入力検証**：split・is_trading_start の時刻型・範囲（`start <= is_trading_start <= split <= end`）と空区間判定（SP1 継承：IS バー数 ≥ 1 かつ OOS で `bar.time >= split` のバー数 ≥ 1）を**探索ループ前に 1 回**検証。`max_candidates` は必須（未指定は入力検証エラー・M-3）。探索空間の理論候補数（grid=N_space／random=min(n_samples,N_space)・M-2）を `max_candidates` と照合し**超過は拒否**（FR-O9・単一動作）。データパスは Phase 2 の HTTP 化時に datasetRef ホワイトリスト方式（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §1）。
- **データ保護（最重要・C1/NFR-OS1）**：既存データへの書き込みを SP1 の `assert_safe_output_dir`（`run_is_oos_cli.py:67`）の再利用で構造的に禁止する。読み取りは read-only、書き込みは検証関数通過後の新規 OUT 配下のみ。
- **監査ログ**：該当なし（オフラインツール。探索ログは出力 OUT へ書き込み）。
- 詳細脅威分析は security スキル成果物を参照（本書では扱わない）。

### 7.4 運用・保守性設計

- **ログ設計**：標準出力／OUT に (1) 候補件数 N_cand（無音切り捨て禁止・FR-O9）、(2) 候補ごとの params・IS 目的値・有限性判定（非有限スコアで除外した件数・C-1）、(3) best params・IS best stats・OOS stats・劣化、(4) tools の slice 用 full_bars 保持回数＝1／build_interactor 内部の CSV ロード回数＝N_cand+1（High-1・NFR-OP4）、を出力。
- **監視ポイント**：該当なし（オフライン）。
- **構成管理**：新規ファイルのみ追加（`simulator/usecase/optimize.py`・`simulator/usecase/optimize_ports.py`・`simulator/usecase/optimize_strategies.py`・`simulator/tools/optimize_cli.py`・テスト群）。既存ファイル（SP1 `run_is_oos.py` 含む）への差分 0（NFR-OS2）。
- **バックアップ・リストア**：該当なし（出力は再生成可能・入力は読み取り専用）。
- **保守性（拡張点）**：
  - 探索アルゴリズム拡張：`ParameterSearchPort` 実装を追加（grid/random に加え将来 latin-hypercube 等）。既存無改変（OCP）。
  - 目的関数拡張：`ObjectivePort` 実装を追加（PF/NetProfit/Sharpe に加え複合目的）。既存無改変（OCP）。
  - 後段③再利用：`optimize` の「IS 探索→best 凍結→OOS 検証」一式を `walk_forward` が窓ごとに呼べるよう、`OptimizeRequest`／`OptimizeResult` を窓非依存（split/is_trading_start を引数）に保つ。

---

## 8. 開発・運用方針

> 出力元：S-4 品質特性の担保

### 8.1 開発方法論・プロセス

- `.claude/CLAUDE.md` のフロー（分析→計画→実行→品質管理→報告）に準拠。指示範囲（最適化のみ）に限定し、ウォークフォワード（③）の先行実装を行わない。

### 8.2 品質保証方針

- committed エンジン・サブフェーズ1の bit-exact／公開 IF 資産を無波及で保護（NFR-OS2・C2）。新 UC・新 Port は committed／SP1 の振る舞いを変えない。
- **SP1 再利用の正当性**：`slice_is_bars`／`build_degradation_report`／`extract_metrics` は純関数（`run_is_oos.py` 実装で副作用なしを実証）であり、最適化からの再利用が SP1 の挙動を変えない。`run_is_oos` 関数自体は呼ばず部品のみ再利用するため SP1 公開 IF を壊さない（C2）。
- 回帰テスト方針（user memory「bugfix-pair-with-regression-test」に整合）：探索の決定論性破れ（grid 列挙順非決定・random seed 未固定・tie 非決定・非有限スコアの argmax 混入＝C-1）、「OOS を全候補で走らせる退行（N_cand 回 OOS run）」、「best の IS を再 run する退行（High-2）」、「`MarginCallError` の握り潰し（M-1）」を禁止する回帰テストを各 1 本添える。

### 8.3 テスト方針

- **単体（UC/Port）**：(1) `ParameterSearchPort`（grid＝辞書順全列挙が決定論／random＝`random.Random(seed).sample(range(N_space),k)` が seed 固定で再現・整数インデックス抽出／`n_samples>N_space` で全件＋ログ／**理論候補数 > max_candidates で件数ログ＋拒否（単一動作・M-2）**／max_candidates 未指定で入力検証エラー・M-3）、(2) `ObjectivePort`（PF/NetProfit/Sharpe/Recovery が `BacktestStats` から正しいスカラを返す・大きいほど良い規約）、(3) **非有限スコア（NaN/inf）候補が argmax 母集合から除外され除外件数がログに出ること・全候補非有限で `OptimizeError` 明示中断すること（C-1）**、(4) argmax の tie が基準順先勝ち、(5) **候補が `MarginCallError`/`BacktestError`/`ConfigError` で失敗した際に UC/tools が捕捉しログ除外・継続し best 0 件で中断すること（M-1・execute 直叩き経路）**、(6) **best_is_stats が探索中の保持値であり再 run されないこと（IS run 回数が N_cand を超えない）・OOS が best 1 回のみ（run_segment 呼出回数 = N_cand[IS] + 1[OOS]・High-2）**、(7) SP1 部品（`slice_is_bars`／`build_degradation_report`）の再利用が SP1 単体テストと同一結果を返すこと、(8) `assert_safe_output_dir` 再利用の拒否プレフィクス。
- **結合（SP1 先例との整合）**：探索空間を「単一候補（= SP1 の固定 params）」に縮退させた場合、best=その候補となり IS/OOS stats・劣化が SP1（`reconcile_is.py` IS net+11370/5224・`reconcile.py` OOS net-4020/2438）と一致すること（SP1 結合テストとの後方整合）。加えて既存データディレクトリの mtime 不変 assert で NFR-OS1 を実証。
- **テスト戦略概要**：合成小データ（実データ非依存・`main/__init__.py:89-91` の方針）で探索・目的・argmax・決定論を検証し、SP1 縮退ケースで end-to-end 整合を確認。
- **テストレベル**：単体（探索/目的/argmax/出力先検証）→結合（SP1 縮退整合・mtime 不変）。

### 8.4 リリース・デプロイメント方針

- 環境構成：開発＝ローカル（本サブフェーズに staging/本番なし）。
- デプロイ戦略：該当なし（ライブラリ/ツールとしてリポジトリに追加）。

---

## 9. リスク・課題

> 出力元：S-1 リスク列挙 ／ S-5 設計検証

### 9.1 技術的リスクと対策

| リスク | 影響度 | 発生確率 | 対策 | 対策の出典／根拠 |
|---|---|---|---|---|
| R-O1 探索空間爆発（N_cand 急増・長時間実行） | 高 | 中 | `max_candidates`（必須・M-3）上限＋理論候補数の事前算出＋**超過時の件数ログ明示拒否（単一動作・M-2）**（FR-O9・NFR-OP2）。**無音切り捨て禁止** | プロジェクト計画（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4 の探索上限明記要求） |
| R-O2 候補ごとの run_segment 再生成漏れ（固定 controller 流用で params が反映されない） | 高 | 中 | tools 層 `make_run_segment(params)` が候補ごとに `build_interactor(**base_kwargs, **params)` を再構築（課題-O1）。params が build_interactor 引数の実体であることを単体テストで固定 | `main/__init__.py:256-285`（params=build_interactor 引数）・`run_is_oos_cli.py:38`（SP1 閉包パターン） |
| R-O3 決定論性破れ（grid 順/random seed/tie/非有限混入） | 高 | 中 | grid＝辞書順全列挙・random＝`random.Random(seed).sample(range(N_space),k)` 整数インデックス非復元抽出（High-3）・tie＝基準順先勝ち・非有限スコアは `math.isfinite` で除外（C-1）（NFR-OD1）。回帰テストで固定 | 公式設計原則（決定論・SP1 NFR-D1 継承） |
| R-O4 探索パラメータが戦略に未参照（no-op 候補） | 中 | 中 | 探索空間定義時に当該 ea_name が参照するパラメータのみ選択（§5.5）。探索ログの目的値が候補間で不変＝no-op を検出可能 | `main/__init__.py:312-337`（ea_name 別戦略/registry 分岐） |
| R-O5 OOS を全候補で走らせる退行（N_cand 回 OOS run） | 高 | 低 | OOS は best 確定後 1 回のみ（FO-06）。run_segment 呼出回数 = N_cand+1 を単体テストで固定 | 過剰最適化検出の定義（best のみ OOS 検証） |
| R-O6 committed/SP1 への意図せぬ波及 | 高 | 低 | 新規ファイルのみ追加し既存（SP1 含む）差分 0 を CI/差分確認で担保（NFR-OS2）。`run_is_oos` は呼ばず部品のみ再利用 | プロジェクト規約 C2 |
| R-O7 既存データへの書き込み波及 | 高 | 低 | SP1 `assert_safe_output_dir` 再利用＋新規 OUT のみ＋mtime 不変 assert（NFR-OS1） | C1・`run_is_oos_cli.py:67`（SP1 先例） |

### 9.2 スケジュール・リソースリスク

- 本サブフェーズは新規 4 ファイル（UC・Port・既定実装・tools）＋テスト群に限定され、committed／SP1 改修を伴わないため波及リスクは限定的。後段③の前提依存（本 UC の `OptimizeRequest`／`OptimizeResult` IF 安定性）が主リスク。

### 9.3 今後の検討課題（TBD 一覧）

| 項目 | 確認が必要な理由 | 確認先／確認方法 |
|---|---|---|
| ~~TBD-O1：`max_candidates` の既定値~~（解消・M-3） | **確定済**：`max_candidates` は**必須引数（既定なし）**とし恣意的既定値を作らない。未指定は FO-01 入力検証エラー。本文 NFR-OP2／FO-01／FO-02 へ昇格 | 解消（呼出側が実行環境の許容 wall-clock × 1 run コストから値を指定する責務） |
| TBD-O2：CSV 再ロード／registry 再計算の削減可否（後段最適化候補・High-1 で降格） | 候補ごとの `build_interactor` 内部 CSV 再ロード（N_cand+1 回・`main/__init__.py:335,344`）と registry 再計算は committed IF（path 再読み）に起因し、削減には committed IF 変更が必要＝本サブフェーズ範囲外。本サブフェーズは正しさ優先で full ビルド（再ロード込み）を許容 | 後段で build への bars 注入／registry キャッシュ等を committed 改修込みで検討（プロファイルで CSV ロードが支配的コストか測定して判断） |
| ~~TBD-O3：候補単位失敗時の継続/中断方針~~（解消・M-1） | **確定済**：失敗候補（`ConfigError`／`BacktestError`／`MarginCallError`）はログに件数／理由を残して探索から除外、有効候補（有限スコア・非失敗）が 0 件なら明示中断。本文 NFR-OA1／§6.3／FO-04／FO-05 へ昇格 | 解消（運用方針として本文確定） |
| TBD-O4：出力レポートの新規パス命名規約 | 既存生成物を上書きしない出力先の命名（SP1 TBD-3 と共通・Phase 2 ブラウザ応答形式と整合） | 運用方針として確定（SP1 と統一） |
| TBD-O5：複数目的関数（多目的最適化）の扱い | 本サブフェーズは単一スカラ目的（argmax）に限定。PF と DD の同時最適化等の多目的は射程外 | 後段または別 cycle で `ObjectivePort` の複合化として検討（YAGNI：現段階は単一目的） |
| TBD-O6：「小さいほど良い」指標の符号反転（L-1・YAGNI で既定外） | drawdown 等「小さいほど良い」指標を argmax 規約へ正規化する符号反転は**既定実装から外す**（現状の既定目的関数は PF／NetProfit／Sharpe／Recovery＝いずれも大きいほど良い）。射程外の機構を先取り実装しない（YAGNI） | 将来「小さいほど良い」目的を追加する際に `ObjectivePort` 実装側で符号反転して「大きいほど良い」に正規化（argmax 規約は不変・既存無改変＝OCP） |

---

## 10. 付録

### 10.1 用語集

| 用語 | 定義 |
|---|---|
| IS（In-Sample） | 学習/探索対象の区間 `[start, split)`。本サブフェーズでは目的関数を最大化する best params を探索する |
| OOS（Out-of-Sample） | 検証区間 `[split, end)`。IS で確定した best params を凍結して評価し過剰最適化を測る |
| split | IS と OOS を分ける境界日時（半開区間：split は OOS 側） |
| 探索空間（SearchSpace） | 可変パラメータ名→候補値集合の写像。build_interactor 引数のサブセット（§5.5） |
| ParamSet | 1 候補のパラメータ組（build_interactor へ `**params` でマージ可能な部分写像） |
| 探索アルゴリズム | grid（直積全列挙）／random（seed 固定サンプル）等。`ParameterSearchPort` で差替可能 |
| 目的関数 | IS の `BacktestStats` から「大きいほど良い」スカラを返す関数（PF/NetProfit/Sharpe 等）。`ObjectivePort` で差替可能 |
| best params | IS で目的値が最大の候補（tie は探索順先勝ち） |
| 過剰最適化（カーブフィッティング） | IS best が OOS で劣化する現象。`DegradationReport`（IS best vs OOS の ratio/delta）で定量化 |
| run_segment | 1 区間を実行し `BacktestStats` を返すコールバック（SP1 `run_is_oos.py:23` の型契約を継承） |
| committed エンジン | MT5 bit-exact 突合済の既存バックテストエンジン（`simulator/`・無改変対象） |
| サブフェーズ1（SP1） | IS/OOS 単純分割（`simulator/usecase/run_is_oos.py`・無改変で部品再利用） |

### 10.2 設計判断の根拠・トレードオフ

| 判断項目 | 採用 | 代替 | 根拠 | 出典区分 |
|---|---|---|---|---|
| アーキテクチャ | 新規 UC `optimize.py`＋Port 抽象（探索/目的を差替） | SP1 `run_is_oos` 拡張 / tools 直書き | C2（committed＋SP1 無改変）・差替可能性（FR-O2/O3）・後段③土台化を両立 | 仮説＋規約 |
| SP1 再利用方式 | 部品（純関数 `slice_is_bars`/`build_degradation_report`/`extract_metrics`）のみ再利用。`run_is_oos` 関数は呼ばない | `run_is_oos` を拡張して呼ぶ | `run_is_oos` は IS/OOS 対称 2 回呼び契約（`run_is_oos.py:134-135`）で非対称な最適化ループに不適合（課題-O2）。部品のみ再利用で SP1 公開 IF を壊さない | 公式（IF 実証）＋規約 |
| 探索アルゴリズム抽象 | `ParameterSearchPort.candidates(space)`（grid/random 差替・標準ライブラリ） | scipy/optuna 導入 / tools 内 if 分岐 | C3 技術スタック追加禁止。標準 `itertools.product`/`random.Random(seed)` で決定論実現。OCP で拡張 | 公式（DIP/OCP/Strategy）＋規約 |
| 目的関数抽象 | `ObjectivePort.score(stats)->float`（大きいほど良い・差替） | `run_is_oos` の劣化指標を流用 / UC 内ハードコード | FR-O3 差替可能性。`BacktestStats` 実在フィールド（`models.py:97-105`）に限定。`extract_metrics`（SP1）を内部利用 | 公式（DIP/OCP）＋実証 |
| run_segment 再生成（課題-O1） | tools が候補ごとに `build_interactor(**base_kwargs, **params)` 再構築→run_segment 生成（ファクトリ注入） | 固定 controller を流用 / UC が build_interactor を import | params=build_interactor 引数の実体（`main/__init__.py:256-285`）。固定流用では params 反映不可。UC import は usecase→main 逆依存（クリーンアーキ違反） | 公式（実証＋DIP）＋仮説 |
| OOS 実行回数 | best のみ 1 回（N_cand+1 total） | 全候補で OOS run（2×N_cand） | 過剰最適化検出は「IS best を OOS で検証」の定義。全候補 OOS は計算 2 倍かつ検出定義に不要 | 公式（過剰最適化定義）＋仮説 |
| 決定論性 | grid 辞書順全列挙・random `random.Random(seed).sample(range(N_space),k)` で整数インデックス非復元抽出・tie 基準順先勝ち | 順序非規定 / seed 任意 / float 値直接サンプル | NFR-OD1（再現性）。整数インデックス抽出で float 等価比較の不安定を回避（High-3）。SP1 NFR-D1 継承 | 公式（決定論）＋規約 |
| 非有限スコアの扱い（C-1） | `math.isfinite` で判定し非有限候補を argmax 母集合から除外（件数ログ明示）・有限のみで argmax・全候補非有限なら明示中断 | NaN/inf を argmax に混入（max が未定義化）/ 無音 fallback | `BacktestStats` の PF/Sharpe/Recovery は float で NaN/inf を取り得る（`models.py:100-103`・gross_loss=0 等）。非有限混入は argmax 全順序を破壊し決定論を壊す | 公式（全順序/決定論）＋実証 |
| 候補失敗の扱い（M-1） | `ConfigError`/`BacktestError`/`MarginCallError` を UC/tools が捕捉しログ除外・best 0 件で中断 | 例外を握り潰す / run_backtest 翻訳に依存 | `execute` 直叩きのため `run_backtest` の Config/Backtest 翻訳（`main/__init__.py:436-438`＝build 段階のみ）は run 中の MarginCallError に掛からない（`exceptions.py:92` で MarginCallError=ExecutionError(BacktestError) を実証）。UC/tools 捕捉が必須 | 公式（例外網羅）＋実証 |
| 探索上限（M-2/M-3） | `max_candidates` を**必須引数（既定なし）**化し、**理論候補数（grid=N_space/random=min(n_samples,N_space)）> max_candidates で拒否（単一動作）** | 恣意的既定値 / 拒否と打切の二択を残す | M-3 恣意的既定禁止・FO-01 入力検証一意化。M-2 判定分離（grid/random で理論候補数定義を分け、判定は拒否に統一） | 規約＋計画 |
| best_is_stats 保持（High-2） | 各候補の IS run 結果を TrialRecord に保持し best 確定時に保持値を採用（再 run しない）・OOS は別 build 1 回 | best の IS を再 run | total run = N_cand+1 厳守・同一 params の IS 二重 run 回避（コスト・決定論） | 公式（冪等/コスト）＋仮説 |
| CSV 再ロード記述（High-1） | build_interactor 内部の CSV 再ロード N_cand+1 回を正直に記載・削減は TBD-O2 へ降格 | 「1 回ロードで再ロード回避」と虚偽記載 | committed `build_interactor` は registry 構築で毎回 CSV を読む（`main/__init__.py:335,344`・同 339-342 のコメントで「1 回読み統合は committed IF 変更要＝範囲外」を実証）。C2 無改変前提では削減不可 | 実証＋規約 |
| 探索上限ログ | `max_candidates`＋件数ログ明示拒否 | 無音打ち切り / 上限なし | NFR-OP2・FR-O9（無音切り捨て禁止）。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4 の上限明記要求 | 規約＋計画 |
| データ非波及 | SP1 `assert_safe_output_dir` 再利用＋mtime 不変 assert | 新規検証関数 / コメント方針のみ | 計測可能手段で NFR-OS1 実証・SP1 資産再利用で重複回避（DRY） | 規約＋公式（検証可能性/DRY） |
| 出力形式 | JSON＋Markdown 並列・tools 層内整形（新規 presenter なし・探索ログ列追加） | 新規 presenter 追加 / JSON のみ | SP1 L-1 方針継承・adapter 無改変・新規ファイル最小 | 規約 |

### 10.3 参考資料

- `simulator/usecase/run_is_oos.py`（SP1・`slice_is_bars` L26 ／ `extract_metrics` L91 ／ `build_degradation_report` L96 ／ `DegradationReport`/`MetricDegradation` L58-79 ／ `run_segment` 型 L23 ／ `run_is_oos` L113＝IS/OOS 対称 2 回呼び L134-135）
- `simulator/tools/run_is_oos_cli.py`（SP1・`make_run_segment` L38 ／ `controller._interactor.execute` L48 ／ `assert_safe_output_dir` L67 ／ `_FORBIDDEN_PREFIXES` L31-35 ／ `to_json_dict` L86 ／ `to_markdown` L95 ／ `normalize_time` L54）
- `simulator/main/__init__.py`（`build_interactor` L256-394＝探索対象パラメータの実体／strategy_params L295-308／registry 構築・ea_name 分岐 L312-337／`ConfigError`/`BacktestError` 捕捉 L436-439／`DataError` L166-174／実データ非依存方針 L89-91）
- `simulator/usecase/models.py`（`BacktestStats` L91-142＝目的関数/劣化対象フィールド／pydantic 非依存 L1-9）
- `.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md` v0.2.0（SP1 基本設計・呼出経路 B-1／option b／区間定義 H-1／出力先検証 H-2）
- `.doc/ISOOS_SIMPLE_SPLIT_DETAILED_DESIGN.md` v1.0.0（SP1 詳細設計・IS=full の head-prefix 実証 §1.2）
- `.doc/ISOOS_BROWSER_PLAN_WIP.md`（IS/OOS 全体計画・アクター E＝最適化 §2／`optimize.py`＋`ParameterSearchPort`/`ObjectivePort` 構想 §3／探索上限・キャッシュ・並列 §4）

### 10.4 関連する標準・規格

- クリーンアーキテクチャ（依存方向の内向き規律・DIP）：本リポジトリ既存規約（`run_is_oos.py:7-8`・`CLEAN_ARCH` 参照）。
- Strategy パターン（GoF）：探索アルゴリズム・目的関数の Port 抽象による差し替え（FR-O2/O3）。
- OCP（公式設計原則）：探索アルゴリズム・目的関数の追加で既存無改変。
- DRY / YAGNI（公式設計原則）：SP1 部品・committed build ロジックの複製回避（DRY）／単一目的に限定し多目的を後段へ（YAGNI・TBD-O5）。

---

## 後段サブフェーズ③（ウォークフォワード）への拡張余地（本設計が土台になること）

- **③ウォークフォワード（`usecase/walk_forward.py`）**：本 UC の「IS 探索→best 凍結→OOS 検証」一式を、anchored/rolling 窓ごとに**反復**する基本単位として再利用する。各窓 i に対し `split_i`・`is_trading_start_i` を与えて `optimize` を呼び、窓ごとの best params・OOS stats・劣化を収集して連結する。
  - 本 UC の `OptimizeRequest`／`OptimizeResult` を**窓非依存**（split/is_trading_start を引数化）に保つことで、`walk_forward` が窓ループから `optimize` をそのまま呼べる（§7.4 保守性）。
  - SP1 の IS truncation（`slice_is_bars`）・OOS warmup 機構が窓ごとにそのまま適用できる（SP1 拡張余地を継承）。
  - 計算コストは窓数 W × (N_cand+1) run。`.doc/ISOOS_BROWSER_PLAN_WIP.md` §4 の非同期ジョブ化・探索上限・キャッシュ・並列はこの段で本格適用する。
- いずれも committed エンジン・サブフェーズ1は無改変のまま、新規 UC の「上」にさらに UC を重ねる構造（`.doc/ISOOS_BROWSER_PLAN_WIP.md` §3 レイヤリング図）に整合する。
```
