# タスク完了時チェックリスト

## CLAUDE.md ワークフロー（プログラマー role）

タスク実装時、以下の順序で各管理者の承認を得る:

1. **Git 戦略**: `<type>/<feature>` ブランチを develop から作成 → Git 管理者承認
2. **テスト設計** (TDD): 機能要件ベースのテストケース作成 → TDD 管理者承認
3. **Red-Green サイクル**: 失敗するテスト → 最小実装 → Green 確認
4. **アーキテクチャ設計**: クリーンアーキテクチャ管理者承認
5. **コーディング**: SOLID 原則準拠で実装 → コーディング管理者承認
6. **コードレビュー**: リスクベースレビュー（高/中/低） → レビュアー承認
7. **ドキュメント**: docstring + 設計記録 → ドキュメント管理者承認
8. **ソースコード管理**: コミット → Git 管理者承認

## 承認要求時の必須項目
- 実施内容の明確な説明
- 実証的証拠の提示（コード実行結果、検証ログ等）
- 期待される結果
- リスクと対策

## コード品質チェック項目

### SOLID（現代的 SRP 解釈で評価）
- [ ] SRP: 各モジュールが単一アクターに対して責任を持つ
- [ ] OCP: 拡張に開放、修正に閉鎖
- [ ] LSP: 派生型は基底型と置換可能
- [ ] ISP: クライアントは未使用メソッドへの依存を強制されない
- [ ] DIP: 抽象に依存、具象に依存しない

### Clean Architecture（仕様書 Section A.2）
- [ ] Domain 層: 他層 import なし、numpy + stdlib のみ
- [ ] Algorithm 層: Domain のみ import
- [ ] Application 層: Domain, Algorithm を import
- [ ] Interface 層: Domain, Algorithm, Application を import
- [ ] 依存方向自動テスト (SC6) パス

### TDD
- [ ] テストファースト原則を遵守
- [ ] Red → Green → Refactor サイクルを実行
- [ ] テスト名が機能を正確に表現
- [ ] Arrange-Act-Assert 構造
- [ ] 100% コードカバレッジ

### ドキュメント
- [ ] 関数・クラスに docstring（役割/パラメータ/戻り値/例外/使用例）
- [ ] 仕様書の指摘番号を参照
- [ ] 実装と整合

### セキュリティ
- [ ] 機密情報のハードコーディングなし
- [ ] 入力検証（V1〜V9 ルール）
- [ ] エラーハンドリング適切

## Phase 1.0 完了時に確認したコマンド
```bash
# import 検証
python -c "from src.domain.info_set import Action; ..."

# 依存方向検証
python -c "AST 解析スクリプト"  # SC6 検証

# 動作検証
# - Strategy writeable=False 強制
# - dtype=float32 検証
# - assert_implements_protocol 動作
# - SolverResult termination_reason 値域検証
```

## 仕様書準拠状況（2026-04-28 時点）
- ✅ requirements.txt は仕様書 C2 準拠（numpy>=1.24, tqdm>=4.65）
- ✅ requirements-dev.txt は仕様書 C5 準拠（pytest>=7.0）
- ✅ Phase 1.0 Protocol 定義は仕様書 A.3 のレイヤー構造で完成
- ✅ Domain 層依存ポリシー（Section A.2、numpy + stdlib のみ）遵守
- ✅ GitFlow 構造確立（main / develop / `<type>/<feature>`）
