# 技術スタック

## 言語・ランタイム
- Python 3.10 以上 (仕様書 C1)
- Linux / macOS / Windows 対応（Windows は SIGTERM 制限あり）

## 必須依存ライブラリ（仕様書 C2）
- `numpy >= 1.24` — 数値計算（Domain 層に許容される唯一の外部依存、Section A.2）
- `tqdm >= 4.65` — プログレスバー表示（Interface 層）

## 標準ライブラリのみで完結（仕様書 C3）
- `argparse`, `hashlib`, `json`, `os`, `sys`, `signal`, `pathlib`, `dataclasses`, `platform`, `abc`, `typing`, `ast`

## 開発依存（仕様書 C5）
- `pytest >= 7.0`

## 数値型ポリシー（仕様書 C9）
- 戦略・regret・strategy_sum はすべて `float32` で統一
- Domain DTO の `__post_init__` で dtype 検証 + writeable=False 強制

## requirements 構成（2026-04-28 修正済み、コミット 05183ef）
- `requirements.txt`: production 依存のみ
  - `numpy>=1.24`（C2、Domain 層唯一の外部依存）
  - `tqdm>=4.65`（C2、Interface 層プログレスバー）
- `requirements-dev.txt`: 開発依存
  - `-r requirements.txt`（production 依存を継承）
  - `pytest>=7.0`（C5）

## インストール
```bash
# production
python -m pip install -r requirements.txt
# 開発時
python -m pip install -r requirements-dev.txt
```

## 環境構築
- DevContainer: `.devcontainer/` 配下に Docker ベースの開発環境定義あり
- MCP: Serena MCP を使用してセマンティックコード解析
