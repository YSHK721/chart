# コードベース構造

## ルートディレクトリ
```
/workspaces/PokerSolverGTO/
├── src/                  # 実装コード
├── tests/                # テストコード（Phase 1.1 で追加）
├── .doc/                 # 仕様書・設計書
│   ├── preflop_range_getter_spec_v3_1.md  (主要仕様書)
│   └── conceptual-design-document.md
├── .claude/              # Claude Code 設定 + 役割別指示書
├── .serena/              # Serena MCP 設定 + memories
├── .devcontainer/        # DevContainer 定義
├── .github/              # GitHub workflow
├── requirements.txt      # production 依存（numpy>=1.24, tqdm>=4.65、仕様書 C2）
├── requirements-dev.txt  # 開発依存（pytest>=7.0、仕様書 C5）
├── Dockerfile
└── docker-compose.yml
```

## src/ 配下（仕様書 A.3 準拠の Clean Architecture 5 層構造）

```
src/
├── __init__.py
├── domain/                          # Domain 層（最内層、Phase 1.1 で具象実装完了）
│   ├── __init__.py
│   ├── info_set.py                  # Action, ActionSet, InfoSet
│   ├── tree.py                      # TreeNode, GameTree, NodeType (Literal)
│   ├── strategy.py                  # Strategy, AverageStrategy
│   ├── game.py                      # Game Protocol, KuhnPokerGame
│   ├── bucketing.py                 # Bucketing Protocol, IdentityBucketing
│   ├── equity.py                    # EquityCalculator Protocol, KuhnEquityCalculator
│   └── payoff.py                    # PayoffCalculator Protocol, KuhnPayoffCalculator
├── algorithm/                       # Algorithm 層（Phase 1.2 で具象実装予定）
│   ├── __init__.py
│   ├── solver.py                    # Solver Protocol, SolverResult, Checkpoint
│   └── exploitability.py            # ExploitabilityCalculator (placeholder)
├── interface/                       # Interface 層
│   ├── __init__.py
│   └── range_serializer.py          # RangeSerializer Protocol
└── application/                     # Application 層
    ├── __init__.py
    └── protocol_assertion.py        # assert_implements_protocol(obj, protocol, name)
```

## tests/ 配下（Phase 1.1 で新設）

```
tests/
├── __init__.py
├── domain/
│   ├── __init__.py
│   ├── test_average_strategy.py     # 8 tests
│   ├── test_identity_bucketing.py   # 9 tests
│   ├── test_kuhn_equity.py          # 8 tests
│   ├── test_kuhn_payoff.py          # 13 tests (zero-sum 検証含む)
│   └── test_kuhn_poker_game.py      # 33 tests (info_sets / tree / Protocol)
└── integration/
    ├── __init__.py
    └── test_dependency_direction.py  # SC6 自動検証 (AST 解析)
```

合計 79 テスト全 pass (Phase 1.1 完了時)

## 依存方向（仕様書 Section A.2）
- Domain 層: 他層 import 禁止（numpy + stdlib のみ許容）
- Algorithm 層: Domain のみ
- Application 層: Domain + Algorithm
- Interface 層: Domain + Algorithm + Application
- Infrastructure 層: 全層（未作成、Stage 1 実装で追加予定）

## Phase 1.1 で内部依存追加
- `src/domain/game.py` → `src/domain/payoff.py` (KuhnPokerGame が KuhnPayoffCalculator を使用)
- これは Domain 層内部の依存であり、Section A.2 の依存方向ポリシー（外側 → 内側）に違反しない

## Phase 1.2 以降で追加予定のファイル
- `src/algorithm/cfr_plus.py` 新規（VanillaCFRPlusSolver）
- `src/algorithm/exploitability.py` に BestResponseCalculator 追加と本実装
- `src/application/compute_range.py` 新規（ComputeRangeUseCase）
- `src/application/evaluate.py` 新規
- `src/application/cache_key.py` 新規
- `src/application/input_params.py` 新規
- `src/interface/cli.py` 新規
- `src/interface/range_serializer.py` に KuhnNPZSerializer 具象追加
- `src/interface/progress_bar.py` 新規
- `src/interface/signal_handler.py` 新規
- `src/infrastructure/` 全体（npz_storage, checkpoint_store, filesystem, main）
- `tests/algorithm/`, `tests/application/`, `tests/interface/`, `tests/infrastructure/`
