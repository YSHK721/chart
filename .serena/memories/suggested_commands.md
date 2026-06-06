# 推奨コマンド集

## Python 環境
```bash
# 仮想環境作成 + アクティベート
python -m venv .venv
source .venv/bin/activate

# 依存関係インストール（仕様書 C2/C5 準拠済み）
pip install -r requirements.txt          # production: numpy, tqdm
pip install -r requirements-dev.txt      # 開発時: + pytest

# パッケージを開発モードで（Stage 1 実装後）
pip install -e .
```

## テスト・検証
```bash
# プロジェクト構造確認
find . -name "*.py" -not -path "*/.venv/*" -not -path "*/__pycache__/*" | head -40

# pytest（Stage 1 実装後）
pytest --collect-only          # テスト対象モジュール特定
pytest                          # 全テスト実行
pytest -v --tb=long             # 詳細トレース付き
pytest tests/integration/test_dependency_direction.py  # SC6 検証
pytest tests/integration/test_h1_predictive_check.py   # H1 予備検証

# キャッシュクリア
find . -name "__pycache__" -delete && pytest --cache-clear
```

## インポート + 依存方向検証（Phase 1.0 で使用したもの）
```bash
# 全モジュール import 検証
python -c "
from src.domain.info_set import Action, ActionSet, InfoSet
from src.domain.tree import TreeNode, GameTree
from src.domain.strategy import Strategy
from src.domain.game import Game
from src.domain.bucketing import Bucketing
from src.domain.equity import EquityCalculator
from src.domain.payoff import PayoffCalculator
from src.algorithm.solver import Solver, SolverResult, Checkpoint
from src.algorithm.exploitability import ExploitabilityCalculator
from src.interface.range_serializer import RangeSerializer
from src.application.protocol_assertion import assert_implements_protocol
print('OK')
"
```

## CLI 実行（Stage 1 実装後）
```bash
python kuhn_solver.py --solver cfr_plus --cfr-iterations 10000 \
  --target-exploitability-mbb 1.0 --output kuhn_range.npz
```

## Git 運用（GitFlow 準拠、2026-04-28 確立）
```bash
# ブランチ命名規則: <type>/<feature-name>
# type: feature / fix / refactor / docs / test / chore
git checkout develop                              # 必ず develop を基点に
git checkout -b feature/stage1-kuhn-implementation
git checkout -b fix/cache-corruption-detection
git checkout -b refactor/protocols-srp-split
git checkout -b chore/serena-memory-update

# コミット（Conventional Commits + Co-Authored-By）
git commit -m "feat: 機能の説明
Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"

# 作業完了後、develop に --no-ff マージ
git checkout develop
git merge --no-ff <branch-name>

# main へ直接マージは禁止（develop 経由必須）
```

## ブランチ構造
- `main` — production リリース版
- `develop` — 統合用、すべての feature/fix/refactor の合流先
- `<type>/<feature>` — 個別作業ブランチ（develop から分岐、develop へマージ）

## Linux 系標準コマンド
- `ls -la`, `find`, `grep -rn`, `wc -l`, `sed`, `awk`
- `git`, `python3`, `pip`, `pytest`
- `cd` は基本不要（絶対パス推奨）

## Serena MCP（推奨優先使用ツール）
- `mcp__serena__get_symbols_overview` — ファイルの全シンボル一覧
- `mcp__serena__find_symbol` — シンボル名で検索
- `mcp__serena__find_referencing_symbols` — 参照元検索
- `mcp__serena__search_for_pattern` — パターン検索（grep の代替）
- `mcp__serena__replace_symbol_body` — シンボル単位の編集
- `mcp__serena__list_memories`, `read_memory`, `write_memory` — メモリ管理
