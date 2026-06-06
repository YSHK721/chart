# コードスタイル・規約

## 共通方針
- `from __future__ import annotations` をすべての .py ファイル先頭に記載
- 型ヒントは厳密に付ける（Protocol、Literal、TypedDict、NDArray[np.float32] 等）
- `Optional[T]` を使用（`T | None` 構文と混在させない、現状のスタイル）

## Docstring
- Google スタイル準拠。`Attributes`, `Args`, `Returns`, `Raises`, `Example`, `Note` セクション
- 日本語と英語混在 OK（仕様書も日本語ベース）
- 必須項目: 役割と責務 / 関数の目的 / パラメータ / 戻り値 / 例外 / 使用例（複雑な場合）

## クラス設計
- 値オブジェクト・DTO は `@dataclass(frozen=True)`
- 不変性が重要な numpy 配列は `__post_init__` で writeable=False を強制
- Protocol は `@runtime_checkable` で装飾（assert_implements_protocol で利用）

## 命名規則
- クラス: `PascalCase` (例: `KuhnPokerGame`, `SolverResult`)
- 関数・変数: `snake_case`
- Protocol: そのままの名詞（接尾辞なし、例: `Game`, `Solver`, `Bucketing`）
- 具象クラス: 実装の特徴を反映（例: `VanillaCFRPlusSolver`, `KuhnEquityCalculator`）
- ファイル: 役割を表す単数形 `snake_case.py`（例: `info_set.py`, `range_serializer.py`）

## ファイル構成パターン
各ファイル冒頭の docstring に以下を含める:
1. レイヤー名
2. 含まれる構造（クラス/関数）の一覧
3. 仕様書の関連指摘番号（M1, L2 等）
4. 依存（標準ライブラリ / 外部 / プロジェクト内モジュール）

## Type Annotation の特殊用法
- `NDArray[np.float32]` を numpy 配列の型ヒントに使用
- `Literal["chance", "decision", "terminal"]` で値域を制約
- `dict[str, "TreeNode"]` のような前方参照は文字列で

## SOLID 原則の遵守
- SRP: 現代的解釈「one actor」を採用（古典的「do one thing」は不十分）
- OCP: Protocol で拡張ポイントを抽象化、新ゲーム追加で既存修正不要
- LSP: Protocol の構造的型付けで担保
- ISP: 各 Protocol は 1〜3 メソッドの最小インターフェース
- DIP: Algorithm 層は Domain Protocol に依存、具象クラスへの直接依存なし

## コミットメッセージ
- Conventional Commits 形式: `feat:`, `fix:`, `refactor:`, `docs:` 等
- 日本語の説明で OK（既存コミット履歴で確認）
- 末尾に `Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>` を付与
