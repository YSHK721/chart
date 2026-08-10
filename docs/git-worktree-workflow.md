# git worktree 並行作業ノート

別案件を「いまの作業を止めず・ファイル衝突なく」並行で進めるための手順メモ。
worktree は同一リポジトリの作業ツリーを別ディレクトリに複製する仕組み（各ツリーは別ブランチを持てる）。

## 1. いつ使うか

- いまの作業（例：試作ブランチ）と**別案件**を同時に進めたい。
- 変更対象（ファイル・状態）が現作業と**重複しない**＝安全に並列。重複するなら直列化する。

## 2. 派生元の決め方（重要）

新案件が**現ブランチの変更に依存するか**で分ける。

| ケース | 派生元 | マージ先 |
|---|---|---|
| **A. 独立した別案件**（依存しない）★既定 | `develop` | 完了後 `develop` へ直接 |
| **B. 現ブランチの成果物の上に積む**（依存する） | 現ブランチ | 現ブランチ → 最後に `develop` |

- **独立案件を現ブランチ経由にしない**こと。試作の未確定コミットが新案件に巻き込まれ、レビュー・マージ単位が混ざる。
- 独立案件は独立にマージできる形（A）が GitFlow の正道。

## 3. 作成コマンド

```bash
# A: develop 起点（推奨）。リモート最新から切るなら origin/develop を起点に。
git fetch origin
git worktree add -b feature/<新案件名> /workspaces/app-<名前> origin/develop

# B: 現ブランチ起点（依存する場合のみ）
git worktree add -b <新ブランチ名> /workspaces/app-<名前> <現ブランチ>
```

- 配置パスは**既存ツリーと重複しない**こと（`git worktree list` で確認）。
- このセッションからは `! git worktree add ...` で実行できる。

## 4. 作業・片付け

```bash
git worktree list                       # 現在のツリー一覧
cd /workspaces/app-<名前>               # 作業開始
# …開発・コミット…
git worktree remove /workspaces/app-<名前>   # 片付け（変更が無ければ自動削除もされる）
```

ツリーの切り替えは **`cd` でディレクトリを移動するだけ**。`git switch` のような「同じフォルダ内での切替」ではなく、各ブランチが**別ディレクトリとして同時に存在**する（フォルダごと別物で、それぞれが自分のブランチ・作業状態を保持）。

```bash
cd /workspaces/app          # 現ブランチ（proto/...）の作業ツリー
cd /workspaces/app-<名前>   # 新案件の作業ツリー（feature/...）
git branch --show-current   # 現在地のブランチを確認
```

ターミナルのタブ/ウィンドウごとに `cd` 先を変えれば、両ツリーを同時に開いて並行作業できる。

## 5. 注意

- worktree は**コミット済み HEAD のみ**を引き継ぐ。現ブランチの**未コミット変更は持ち込まれない**。必要なら先にコミットする。
- 最新を起点にしたいときは `git fetch` 後に `origin/develop` を派生元にする（ローカルが古い場合の取りこぼし防止）。
- 同一ブランチは2つのツリーで同時 checkout できない（必ず別ブランチを切る）。

## 補足：サブエージェントの worktree 隔離との違い

`isolation: "worktree"` はアシスタントが**サブエージェントを別ツリーで並列起動**する際の指定で、本ノートの「自分で作業ツリーを増やす」操作とは別物。独立案件をエージェントに並列で任せたい場合に使う。

## 6. アシスタント（Claude）への依頼の仕方

- **特別なコマンドは不要**。自然言語で「どのパスで」「直列か並列（裏で）か」を伝えれば、その形で起動する。
- **ターミナルの `cd` はアシスタントの作業位置に連動しない**（別プロセス）。手元で `cd` しても無効なので、**対象パスを明示**して依頼する（例：「`/workspaces/app-practice` で 〇〇 して」）。git は `git -C <パス>`、ファイル編集は絶対パスで当該ツリーを対象にできる。
- **直列／並列を決めるのは「パス指定」ではなく「誰が実行するか」**。

  | 依頼の仕方 | 実行 |
  |---|---|
  | パス明示 → アシスタントが直接作業 | 直列（その場・1件ずつ） |
  | パス明示 → バックグラウンド・サブエージェントへ委譲 | 並列可（裏で・複数同時可） |
  | `isolation:"worktree"` で隔離起動 | 並列可（自前の隔離ツリー） |

- 「**新しいワークツリーで並列で実行して** ＋ 作業内容」で隔離並列起動できる。**作業内容（何をするか）は必須**。派生元/パス未指定なら既定（`develop` 起点・`/workspaces/app-<名前>`）で用意する。

## 7. 環境依存物のセットアップ（**必読**・ISSUE-363 / ISSUE-365）

### 7.1 なぜ必要か

worktree が展開するのは **git が追跡しているファイルだけ**である。作業ディレクトリの複製ではない。
したがって **gitignore 済みの実体は 1 つも来ない**。

| | worktree に来るか |
|---|---|
| ソースコード（追跡済み） | 来る |
| `data/marketdata`（gitignore） | **来ない** |
| `lightweight-charts-python-main/.venv`（gitignore） | **来ない** |

一方コードはこれらを**ツリー相対**で探す（`indigators/indicator_ui/serve.sh` の `VENV_PY`、
`marketdata/paths.py` の既定値）。よって worktree を作った直後は core が起動しない。

### 7.2 やること（1 コマンド）

```bash
cd <worktree>
./tools/setup_worktree.sh
```

git の common-dir から本チェックアウトを特定し、`dev_paths.local.sh`（gitignore 済み）へ
**絶対パスの環境変数**を書き出す。`tools/dev_paths.sh` がこれを読むため、以後
`./unified_ui/serve.sh` がそのまま動く。

```bash
export VENV_PYTHON="/workspaces/app/lightweight-charts-python-main/.venv/bin/python"
export MARKETDATA_DATA_DIR="/workspaces/app/data/marketdata"
```

### 7.3 やってはいけないこと — **symlink を張る**

**worktree 内に `data/marketdata` や `.venv` の symlink を張ってはならない。**

2026-08-10 に実際の事故が起きた（ISSUE-363）。経緯は以下のとおり。

```
1. worktree で core を起動するため、手で symlink を 2 本張った
     data/marketdata -> /workspaces/app/data/marketdata
     lightweight-charts-python-main/.venv -> /workspaces/app/lightweight-charts-python-main/.venv
2. .gitignore が `.venv/` と末尾スラッシュ付きだったため、symlink（ファイル扱い）に掛からなかった
3. `git add -A` が未追跡ファイルとして拾い、マージコミットへ入った
4. 本番（/workspaces/app）へマージ
5. checkout により symlink が展開され、「自分自身を指す symlink」になった
     /workspaces/app/data/marketdata -> /workspaces/app/data/marketdata
6. venv とデータの実体参照が失われ、サーバが起動しなくなった
```

**症状は分かりにくい。** `Too many levels of symbolic links` は出るが、
サーバ側は「core の起動を待機中...」で無言のまま止まり、原因に辿り着くまで時間を要する。

### 7.4 なぜ「気をつける」では防げないか

- `.gitignore` は**既に追跡されたファイルには効かない**。一度入れば止まらない。
- `git add -A` は作業ディレクトリに在るものを**無差別に**拾う。
- パス名で守ろうとすると、次に別のパスで同じことが起きたとき素通しになる。

守るべきは特定のパスではなく「**絶対パスの symlink**」という形そのものである。
相対パス（`../../`）の symlink はツリー内で完結するため、どこへ展開しても自己参照にならない。

### 7.5 仕組みによる防御（3 段）

| 段 | 仕組み | 何を止めるか |
|---|---|---|
| 1 | `tools/setup_worktree.sh` | symlink を**張る理由**を消す |
| 2 | `tools/tests/test_no_absolute_symlinks.py` | 張っても**commit させない** |
| 3 | `.gitignore`（末尾スラッシュ無し） | 素の `git add` から守る |

段 2 の検査は **index**（`git ls-files -s`）を走査する。`git ls-tree HEAD` では
`git add` 済みの symlink を検出できず**コミット後にしか落ちない**（実測で判明・是正済み）。
検出器自身の自己検査も同梱しており、ISSUE-363 の実際の 2 値を検出できることを固定している。

### 7.6 コミット前の手順

**`git add -A` を使わない。** 使う場合も、コミット前に必ず次を読む。

```bash
git diff --cached --stat     # 意図しないファイルが入っていないか
```

今回の事故は、この 1 行を読んでいれば防げた（`.venv | 1 +` の 2 行が見えていた）。

### 7.7 事故後の復旧手順（同じ症状に遭遇したとき）

```bash
# 1. 自己参照になっていないか確認
ls -la /workspaces/app/data/marketdata
ls -la /workspaces/app/lightweight-charts-python-main/.venv

# 2. 実体の場所を確認（本リポジトリでは /app 配下がコンテナ側の実体）
ls /app/data/marketdata/ | head

# 3. 実体へ向け直す（壊れたリンクだけが消える。実データは無傷）
ln -sfn /app/data/marketdata /workspaces/app/data/marketdata
ln -sfn /app/lightweight-charts-python-main/.venv /workspaces/app/lightweight-charts-python-main/.venv
```
