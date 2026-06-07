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

## A方式（単一 HTML・file:// 単体）— params は variant のみ反映

新規依存・サーバなしで動かす自己完結 HTML。埋め込み事前計算データ（SAMPLE_DATA）を使う
プロトタイプで、`variant` 以外のパラメータ変更は描画へ反映されない（ダイアログ内に注記を表示）。

```bash
cd /workspaces/app/indigators/indicator_ui/web
node build.mjs   # → ../out/prototype.html を生成
# ブラウザで out/prototype.html を開く（file://）
```

## A方式と B方式の違い

| | A方式（file://・単一HTML） | B方式（served・http://） |
|---|---|---|
| 計算 | 埋め込み事前計算（EmbeddedComputeGateway） | ライブ API（ComputeHttpClient → `/compute`） |
| params 反映 | variant のみ | 全パラメータを実再計算・実反映 |
| ローソク | SAMPLE_DATA | `GET /candles`（full CSV と時間軸一致） |
| A方式注記 | 表示 | 非表示 |
| 起動 | `node build.mjs` → file:// | venv python で `framework/server.py` → http:// |

判定はフロントの `location.protocol`（http/https → B方式、file: → A方式）で自動切替する。
