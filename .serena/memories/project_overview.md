# PokerSolverGTO プロジェクト概要

## 目的
GTOプリフロップ・レンジデータ取得 CLI ツール。Stage 1 として Kuhn Poker MVP を実装し、Stage 2 以降で Heads-Up Push/Fold、6-max などへ拡張する設計。

## 現在のステージ
- **Stage 1 (Kuhn Poker MVP)** の Phase 1.1 (Domain 層具象実装) を完了 (2026-04-28)
- Phase 1.0: 6 種の Protocol を仕様書 A.3 のレイヤー別構造で `src/` 配下に分割済み
- Phase 1.1: Domain 層 5 具象クラス実装完了
  - `AverageStrategy` (strategy.py)
  - `IdentityBucketing` (bucketing.py)
  - `KuhnEquityCalculator` (equity.py)
  - `KuhnPayoffCalculator` (payoff.py)
  - `KuhnPokerGame` (game.py): 12 情報セット + chance 1 + decision 24 + terminal 30
- 次フェーズ: Phase 1.2 Algorithm 層実装（VanillaCFRPlusSolver）

## 主要参照ドキュメント
- `.doc/preflop_range_getter_spec_v3_1.md` (80,085 bytes): 詳細仕様書 v3.1
- `.doc/conceptual-design-document.md`: 概念設計
- `.claude/CLAUDE.md`: AI 実行プロセス指示書
- `.claude/instructions/*.md`: 各役割の詳細指示

## 仕様書の主要構成
- Items 1-9: Objective / Scope / Assumptions / Constraints / Input / Processing / Entities / Output / Exception
- Sections A-G: Architecture / Domain / Algorithm / Application / Interface / Infrastructure / Tests
- Section 11.5: H1 予備検証手順（Stage 2 着手前のドライラン）

## 拡張ロードマップ（仕様書 Item 2.7）
| Stage | 追加対象 |
|-------|---------|
| Stage 1 | KuhnPokerGame + VanillaCFRPlusSolver + KuhnNPZSerializer |
| Stage 2 | HeadsUpPushFoldGame |
| Stage 4 | AntePayoffCalculator, ICMPayoffCalculator, PKOPayoffCalculator |
| Stage 5 | MCCFRSolver, LinearCFRSolver |
| Stage 6 | Strategic169Bucketing, SixMaxGame |

## テスト現状 (Phase 1.1 完了時)
- `tests/domain/`: 5 具象クラス × 計 71 ユニットテスト
- `tests/integration/test_dependency_direction.py`: SC6（AST 解析で Domain 層が他層を import しないことを検証）
- 合計 79 テスト全 pass

## 開発体制（CLAUDE.md ベース）
プログラマー + 各管理者（クリーンアーキテクチャ、TDD、コーディング、ドキュメント、コードレビュー、Git、エラーハンドリング、セキュリティ）の協働モデル。承認プロセスを経て段階的に実装を進める。
