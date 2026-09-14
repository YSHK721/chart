# =============================================================================
# Development Container Dockerfile
# =============================================================================
#
# 目的:
#   Python と Node.js を含む統合開発環境を提供します。
#   Playwright を使用した Web スクレイピング・スクリーンショット取得機能を
#   含むプロジェクト向けのコンテナイメージです。
#
# 主要コンポーネント:
#   - Python 3.14 (ベースイメージ)
#   - Node.js 24.x LTS
#   - Git
#   - GitHub CLI (gh)
#   - Playwright (Chromium ブラウザのみ)
#
# ビルド方法:
#   docker build -t myproject-dev .
#
# 実行方法:
#   docker run -it --rm -v $(pwd):/app myproject-dev bash
#
# 注意事項:
#   - Playwright のブラウザバイナリはイメージに含まれるため、イメージサイズが大きくなります
#   - 本番環境では必要なコンポーネントのみを含む軽量イメージの作成を推奨します
#
# =============================================================================

# ベースイメージ: Python 3.14 公式イメージ (Debian ベース)
# 理由: 最新の Python 機能を活用し、Debian の安定したパッケージ管理システムを利用
FROM python:3.14

# 作業ディレクトリを /app に設定
# すべてのファイル操作とコマンド実行はこのディレクトリで行われます
WORKDIR /app

# -----------------------------------------------------------------------------
# 開発ツールおよび Node.js のインストール
# -----------------------------------------------------------------------------
# 目的: Playwright の実行に必要な Node.js 環境と、リポジトリ運用に必要な
#       GitHub CLI (gh) を含む基本ツールチェーンを構築する
# バージョン: Node.js 24.x LTS / gh は Debian 13 リポジトリ版
#
# インストール手順:
#   1. apt パッケージを更新し、必要な証明書・curl・gnupg・git・gh をインストール
#   2. NodeSource の GPG キーを取得し、apt キーリングに追加
#   3. NodeSource リポジトリを apt ソースリストに追加
#   4. apt パッケージリストを更新
#   5. Node.js をインストール
#   6. npm を最新バージョンにアップグレード
#   7. キャッシュをクリーンアップしてイメージサイズを削減
#
RUN apt-get update && apt-get install -y jq ca-certificates curl gnupg git gh && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_24.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && apt-get install -y nodejs && \
    npm install -g npm@latest && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# R + tgp + gfortran（indicator_ui の tgp_btlm 指標 fitter="tgp" バックエンド）
# -----------------------------------------------------------------------------
# 目的: tgp_btlm で fitter="tgp"（R の tgp::btlm = ベイズ木構造線形モデル。非線形・
#       レジーム変化を表現）を使えるようにする。
# 内容:
#   - r-base-dev : R 本体 + 開発ヘッダ（rpy2 ビルド用）
#   - gfortran   : R パッケージ tgp の C/Fortran コンパイル用（既定イメージに無い）
#   - tgp        : CRAN から導入（依存 cluster/rpart/maptree を含む）
# 注意:
#   - Python 側ブリッジ rpy2（src/rbridge.py が使用）は、API を起動する venv
#     （lightweight-charts-python-main/.venv）へ別途 `pip install rpy2` が必要。
#     当該 venv は本リポジトリ管理外（.gitignore）のため Dockerfile では導入しない。
#   - 未導入でも fitter="ols"（numpy 参照実装）は動作し、tgp 経路のみ backend_unavailable。
RUN apt-get update && \
    apt-get install -y --no-install-recommends r-base-dev gfortran && \
    Rscript -e 'install.packages("tgp", repos="https://cloud.r-project.org")' && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# -----------------------------------------------------------------------------
# Python 依存関係のインストール
# -----------------------------------------------------------------------------
# requirements.txt をコピー (キャッシュ効率化のため、他のファイルより先にコピー)
# 理由: requirements.txt が変更されない限り、このレイヤーはキャッシュされます
COPY requirements.txt .

# Python パッケージをインストール
# --no-cache-dir: pip キャッシュを保存せず、イメージサイズを削減
RUN pip install --no-cache-dir -r requirements.txt

# -----------------------------------------------------------------------------
# Playwright ブラウザのインストール
# -----------------------------------------------------------------------------
# 目的: Web ページのスクリーンショット取得や自動化に必要なブラウザを準備
# 対象ブラウザ: Chromium のみ (Firefox, WebKit は不要なため除外)
# 
# インストール内容:
#   1. Chromium ブラウザバイナリ本体
#   2. Chromium の実行に必要な OS レベルの依存ライブラリ
#      (フォント、コーデック、グラフィックライブラリなど)
#
# 注意:
#   - このステップでイメージサイズが大幅に増加します (約 300-500 MB)
#   - Chromium のみに限定することでサイズを最小化しています
#   - CI/CD 環境では install-deps を省略し、必要な依存関係を個別指定することも検討可能
#
# RUN playwright install chromium && \
#     playwright install-deps chromium

# -----------------------------------------------------------------------------
# アプリケーションファイルのコピー
# -----------------------------------------------------------------------------
# プロジェクトの全ファイルをコンテナ内にコピー
# .dockerignore で除外されたファイル (node_modules, .git など) は含まれません
COPY . .

# -----------------------------------------------------------------------------
# デフォルトコマンド
# -----------------------------------------------------------------------------
# コンテナ起動時のデフォルトコマンド: Python インタラクティブシェル
# 実行例:
#   docker run -it myproject-dev              # Python シェルが起動
#   docker run -it myproject-dev bash         # bash シェルを起動
#   docker run -it myproject-dev python script.py  # スクリプト実行
#
CMD ["python"]
