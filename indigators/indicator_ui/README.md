# インジケーター管理 UI

lightweight-charts 上で 3 指標（tgp_btlm / profit_band / price_range_power）を管理する
プロトタイプ UI。2 つの起動方式がある。

## B方式（推奨・ライブ計算サーバ）— params が実反映される

stdlib のみの HTTP サーバ（`api/framework/server.py`）が `web/` を same-origin 配信し、
`POST /compute`・`GET /candles` でブラウザからの実計算を返す。OK 押下時に入力パラメータが
実際に再計算され、チャートへ反映される。

### 1 コマンド起動

```bash
# pandas を含む venv の python で起動（既定ポート 8000）
/workspaces/app/lightweight-charts-python-main/.venv/bin/python \
  -m framework.server   # ← api/ をカレントにして実行（下記参照）
```

実行手順（カレント = `indigators/indicator_ui/api`）:

```bash
cd /workspaces/app/indigators/indicator_ui/api
/workspaces/app/lightweight-charts-python-main/.venv/bin/python framework/server.py
# 例: ポート変更   → ... framework/server.py 9000
#     または       → ... framework/server.py --port 9000
```

起動すると stdout に次が表示される:

```
インジケーター管理 UI（B方式）を起動しました: http://127.0.0.1:8000/
```

ブラウザで `http://localhost:8000/` を開く。ES Modules は same-origin の http:// で
そのまま読まれる（CORS 不要・バンドル不要）。

> pandas 必須のため `venv`（`lightweight-charts-python-main/.venv`）の python で起動すること。
> 既定 python には pandas が無い。

### エンドポイント

| メソッド | パス | 内容 |
|---|---|---|
| POST | `/compute` | JSON ボディ（`indicatorId`/`variant`/`params`/`datasetRef`）→ 系列 JSON |
| GET | `/candles?datasetRef=sample` | candles JSON（`[{time(UNIX秒),open,high,low,close}]`） |
| GET | `/`, `/js/...`, `/css/...` | `web/` 配下の静的配信（パストラバーサル防止） |

### 動作確認（実通信・param 反映）

```bash
BASE=http://localhost:8000
# maxbars=40
curl -s "$BASE/compute" -H 'Content-Type: application/json' \
  -d '{"indicatorId":"tgp_btlm","variant":"default","params":{"fitter":"ols","maxbars":40,"q_low":0.05,"q_high":0.95},"datasetRef":"sample"}'
# maxbars=120（系列が変化する＝params 実反映）
curl -s "$BASE/compute" -H 'Content-Type: application/json' \
  -d '{"indicatorId":"tgp_btlm","variant":"default","params":{"fitter":"ols","maxbars":120,"q_low":0.05,"q_high":0.95},"datasetRef":"sample"}'
curl -s "$BASE/candles?datasetRef=sample" | head -c 200
```

## A方式（単一 HTML・file://）— **廃止**（2026-08-05・ISSUE-266）

`node build.mjs` で全 ES Modules を 1 つの IIFE スコープへ連結し、サーバ無しで開ける
自己完結 HTML（`out/prototype.html`）を生成する方式があったが、**廃止した**。

廃止の理由:

- **実際に使われていなかった**。現行ソースからの再ビルドが構文エラー
  （`Identifier 'facadeToggleVisible' has already been declared`）で起動不能な状態のまま
  誰も気付いていなかった（ISSUE-265）。追跡下の生成物は 2026-08-02 のもので陳腐化していた。
- **実運用コードの設計を歪めていた**。import を剥がして 1 スコープへ連結するため
  (a) 全モジュールのトップレベル名が衝突しうる (b) 新規モジュールは `MODULE_ORDER` への
  手動登録が要る、という制約が付き、その制約を理由に実運用側へ**規則の複製**が生まれていた
  （例: `market_profile_actor._sessionFrom` が `GrowthWindow` の規則を複製）。

以後の起動は B方式（served・http://）に一本化する。復元が必要になった場合は
`git log -- indigators/indicator_ui/web/build.mjs` から取得できる。

## 市場データ取得（JP225 1 分足の自動更新）

`tools/export_jp225_m1.py` が Dukascopy（無料・口座不要のヒストリカル）から JP225 1 分足を
取得し、原子データ `marketdata/data/jp225_m1.csv` を生成・更新する。上位足（5m〜1D）は
別途取得不要で、この 1 分足から `dataset.resample_ohlc` が自動で再集計する（原子＝1 分足）。

> 既存 CSV の末尾時刻以降のみを取得して**追記**する。取得境界は「数分前まで」（`--lag-minutes`）
> で、未確定足を除外し look-ahead / repaint を防ぐ。原子は UTC 素のまま保存する。

### 起動方法

リポジトリルート `/workspaces/app` から実行する（既定出力 `marketdata/data/jp225_m1.csv`）。

```bash
# ① 起動時ワンショット増分（既定）— 末尾以降を最新(数分前)まで取得・追記して終了
python indigators/indicator_ui/tools/export_jp225_m1.py

# ② 継続ポーリング — 起動時増分＋一定間隔で増分し続ける（Ctrl-C で停止）
python indigators/indicator_ui/tools/export_jp225_m1.py --watch
python indigators/indicator_ui/tools/export_jp225_m1.py --watch --interval 60   # 間隔指定（下限 60 秒）

# ③ 全期間上書き（バックテスト初期構築用）— --start/--end 両指定
python indigators/indicator_ui/tools/export_jp225_m1.py --start 2011-06-01 --end 2026-06-15
```

### モード判定（引数で自動分岐）

| 指定 | モード |
|---|---|
| `--start`/`--end` 両方 | 全期間上書き（従来挙動） |
| 両方省略・`--watch` なし | **ワンショット増分（既定）** |
| `--watch` | 継続ポーリング |
| 片側のみ | エラー（曖昧モード排除） |

### 主なオプション

| フラグ | 既定 | 意味 |
|---|---|---|
| `--output` | `marketdata/data/jp225_m1.csv` | 出力先 |
| `--interval` | 60 | ポーリング間隔（秒・下限 60。過剰アクセス抑止） |
| `--lag-minutes` | 3 | 「数分前まで」境界（未確定足を除外） |
| `--offer-side` | bid | 気配側（bid / ask） |
| `--no-repair` | — | 日内外れ値除去を無効化（既定は有効） |
| `--quiet` | — | 進捗ログを抑制 |

> データは Dukascopy の非商用ライセンス（再配布禁止）。取得 CSV は公開リポジトリへコミットしない。
> リアルタイム秒単位のライブ追従が必要になった場合は JForex API への切り替えを別途検討する。
