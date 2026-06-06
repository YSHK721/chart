# 設計パターンとガイドライン

## クリーンアーキテクチャの実装方針
- **依存方向**: 外側の層 → 内側の層への一方向のみ
- **Protocol 定義**: 各層の境界に `typing.Protocol`（C8）
- **DTO**: 層間データ受け渡しは frozen dataclass
- **Domain 層の例外的依存**: numpy のみ許容（Section A.2、実用主義判断）

## SRP（単一責任の原則）の現代的解釈
> A module should be responsible to one, and only one, actor

- 古典的「do one thing」だけでは不十分
- 「誰が変更を要求するか」をアクター単位で識別
- 例: Phase 1.0 の `protocols.py` は 6 アクター混在 → SRP 違反として検出
- 仕様書 A.3 の構造でアクター毎にファイル分離

## Protocol vs Abstract Base Class
- 本プロジェクトでは Protocol を採用（C8）
- 理由: 構造的型付け、明示的継承不要、ダックタイピングと型安全性の両立
- `@runtime_checkable` で `isinstance` チェック可能
- `assert_implements_protocol` ヘルパーで依存性注入時の早期検証

## 不変性の徹底
- frozen dataclass で Python レベルの不変性
- numpy 配列は `writeable=False` で要素レベルの不変性
- `__post_init__` で copy + writeable=False を強制（指摘 10-1）

## 拡張ポイント（Stage 2 以降の先取り設計、D4-A）
6 種の Protocol で抽象化:
| Protocol | Stage 1 | Stage 2+ |
|---------|--------|---------|
| Game | KuhnPokerGame | HeadsUpPushFoldGame, SixMaxGame |
| Solver | VanillaCFRPlusSolver | MCCFRSolver, LinearCFRSolver |
| Bucketing | IdentityBucketing | Strategic169Bucketing |
| EquityCalculator | KuhnEquityCalculator | TexasHoldemEquityTable |
| PayoffCalculator | KuhnPayoffCalculator | AntePayoff, ICMPayoff, PKOPayoff |
| RangeSerializer | KuhnNPZSerializer | Texas169NPZSerializer |

## H1 予備検証手順（仕様書 Section 11.5）
Stage 2 着手前のドライラン:
1. `MockSecondGame` 作成（Game Protocol 実装、Heads-Up 簡略版）
2. 既存 `VanillaCFRPlusSolver` で実行
3. ImportError / TypeError / AttributeError なしを確認
4. 失敗時は Stage 1 のリファクタリング実施

## CFR+ 数値型ポリシー（C9）
- 戦略・regret・strategy_sum: float32 統一
- Stage 6 の性能ボトルネック時に Algorithm 層内で固定小数点演算検討
- Domain 層の Strategy.probabilities は float32 維持

## NPZ シリアライズ（仕様書 Item 7 Entity 1）
- 原子的書き込み: `{path}.tmp` → `os.rename`
- スキーマバージョン管理（schema_version=1）
- termination_reason 値域: target_exploitability_reached / max_iterations_reached / signal_received

## エラーハンドリングプロセス（ErrorHandler-workflow）
1. 問題特定（再現条件、影響範囲）
2. 原因分析（仮説、解決策提案）
3. 解決（TDD: Red-Green-Refactor）
4. ブランチ作成 → 実装 → テスト → コミット

## セキュリティガイドライン（リスクベース）
- 高リスク: 認証/認可/暗号化 → 完全レビュー必須
- 中リスク: ビジネスロジック/API → 選択的深度レビュー
- 低リスク: UI/ドキュメント/テスト → 軽量レビュー

## ドキュメント戦略（条件付き有効性）
- 複雑度高 + 規模大 + 長期 → 高優先度（API/関数 docstring 必須）
- 複雑度低 + 規模小 + 短期 → 低優先度（最小限）
- 「Why over What」: 理由と背景を重視
