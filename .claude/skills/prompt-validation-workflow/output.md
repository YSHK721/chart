# Prompt Validation Workflow - Self Review Output

**対象**: indicator-ui B方式 — (1)Refactor ＋ (2)HTTPサーバ本体/静的配信/起動/フロント配線切替
**実施日**: 2026-06-07
**努力レベル**: xhigh
**ブランチ**: feature/indicator-ui
**主張内容**: Python 128 passed（既存 113 ＋ 新規 dataset7/server smoke8）/ JS 170 passed（既存 162 ＋ composition5/note3）/ Refactor 振る舞い不変 / stdlib のみ・新規依存ゼロ / IndicatorComputeAdapter・指標 src 不接触 / 実通信で param 反映実証（maxbars 40↔120 で系列長・値・時間範囲が変化）/ upstream 系列 API は chart_renderer.js のみ

---

## 視点固定: 擁護 → 死因究明

本成果物が **本番（served B方式）で失敗した** と仮定し、最も可能性の高い失敗原因を判定前に実証で検証する。

## A. 軽量検証の落とし穴チェック（強制適用）

| 自己質問 | 答え |
|---|---|
| 「全テスト緑」を結論ありきにしていないか | 緑を後方互換の短絡にせず、(a) Refactor 後 Python113 再緑を git 機構差替え後に実行、(b) param 反映を curl 実値（40点 time=1676505600 vs 120点 time=1666310400）で死因究明 |
| 「N 辺検証」で N 辺すべて実施したか | candles↔compute 時間軸整合 / Refactor 振る舞い不変 / 静的配信 traversal の 3 辺を後述 checklist で実施 |
| Specification 射程外拡大はないか | nested error=§6.3.4・traversal=§7.3・解像度非依存 UNIX秒=§6.3 の射程内。本番デプロイ/認証/range は対象外と明示 |
| ユーザー承認語彙に承認超過結論を付与していないか | 「サーバ殻はスモーク可」の承認範囲を守り、純ロジックは handle_compute/dataset 単体で網羅。スモークを「全数検証」と称さない |

## B. 用語拡大解釈チェック

- 「Refactor 振る舞い不変」= 既存 Python113/JS162 が機構差替え後も緑。新規振る舞いを足していない（module_loader/dataset/ComputeError 集約は委譲のみ）。
- 「same-origin 静的配信」= web/ ルート内に正規化後パスを限定。`..` で抜けたら 404（curl --path-as-is で 404 実証）。
- 「param 実反映」= /compute が params で実再計算（series 長・値・時間範囲が変化）。SAMPLE_DATA 固定エコーではない。

---

## 検証する 3 辺（事前列挙: Triangulation Checklist）

| # | 辺 | 検証内容 | 実施状態 | 証拠強度 |
|---|----|---------|----------|---------|
| 1 | /candles 時間軸 ↔ /compute series 時間軸 | 両者 full CSV 由来・同一 UNIX秒変換式で揃う（compute time が candle range 内） | ☑ done | ★★★ |
| 2 | Refactor 前後の振る舞い不変 | module_loader/dataset/ERROR_STATUS/ComputeError 集約後に Python113・JS162 再緑 | ☑ done | ★★★ |
| 3 | 静的配信 ↔ traversal 防止 ↔ same-origin module 読込 | `/` 200 html・module 200 js・`..` 404 を curl 実証 | ☑ done | ★★★ |

3 辺完了・全辺 ★★★。結論を全面採用可。本番デプロイ/認証/range はスコープ外→残存リスクへ転記。

---

## Pre-mortem: 最も可能性の高い失敗原因

| # | 失敗原因 | リスク帯 |
|---|---|---|
| 1 | **/candles（full CSV）と /compute（別スライス）で時間軸が乖離**し B方式でラインが画面外へ浮く | 高（B方式核心） |
| 2 | **Refactor（importlib 機構/whitelist/ERROR_STATUS/ComputeError 集約）が振る舞いを変え**既存緑を破壊 | 高（後方互換核心） |
| 3 | **パストラバーサルで web/ ルート外を配信**（`..` 解決漏れ）→ 情報漏洩 | 高（セキュリティ） |
| 4 | **restore() が pairs を ComputeHttpClient に渡し JSON 破壊**（B方式の保存復元失敗） | 中（B方式復元） |
| 5 | **A方式注記が B方式でも表示**（出し分け漏れ）または null append で DOM 例外 | 低（UI 出し分け） |
| 6 | **dataset.py の workspace ルート parents 深さ誤り**で CSV 解決失敗 | 中（パス算出） |

---

## 証拠先行: 実証的証拠

### 証拠 E1: 時間軸整合（#1 死因究明）
```
GET /candles?datasetRef=sample → count 2981 first time 1277769600 (2010-06-29)
POST /compute maxbars=40  → 40点 first time 1676505600
POST /compute maxbars=120 → 120点 first time 1666310400
両 compute time は candle range(1277769600..) 内。両者とも dataset.load_dataframe("sample")＋int(pd.Timestamp(idx).timestamp())
```
**判定**: #1 棄却。candles と series は同一 full CSV・同一変換式で時間軸一致。

### 証拠 E2: Refactor 振る舞い不変（#2 死因究明）
```
module_loader.load_package/load_module へ call_binding・dataset を委譲後:
  Python full → 128 passed（既存 113 緑維持）
ComputeError を domain/compute_error.js へ集約・両 adapter re-export 後:
  JS full → 170 passed（既存 162 緑維持）
ERROR_STATUS を adapter.compute へ単一定義化・controller 参照後: test_compute_controller 全緑
```
**判定**: #2 棄却。3 つの Refactor すべて後方互換（既存テスト無改変で緑）。

### 証拠 E3: param 実反映（#1 派生・主目的）
```
maxbars=40  btlm_mean: len40  first value 198.938...
maxbars=120 btlm_mean: len120 first value 178.013...
```
**判定**: params で series 長・値・時間範囲が変化＝実再計算。A方式の固定エコーでない。主目的達成。

### 証拠 E4: 静的配信・traversal（#3 死因究明）
```
GET /                                   → 200 text/html
GET /js/adapter/front/composition_root_front.js → 200 text/javascript
GET /../../../../etc/passwd (--path-as-is)       → 404
pytest test_server_smoke traversal/static       → 8 passed
```
`_resolve_static` は (_WEB_ROOT/rel).resolve() を relative_to(_WEB_ROOT) で検証、ValueError→None→404。
**判定**: #3 棄却。ルート外配信なし。

### 証拠 E5: restore pairs 正規化・注記出し分け（#4・#5 死因究明）
```
indicator_controller._paramsObject: Array(pairs)→Object.fromEntries / object はそのまま
properties_dialog._buildAMethodNote: mode==='b'→null（B方式非表示）/ 'a'→note要素
両 pane caller: if(note) pane.append(note)（null append 回避）
node --test composition/properties → 8 passed（mode b/a 双方・default a）
```
**判定**: #4・#5 棄却。pairs→object 正規化済み・注記 B方式非表示・null append ガード済み。

### 証拠 E6: パス算出（#6 死因究明）
```
dataset.py parents[5]=/workspaces/app（修正前 parents[4]=indigators で FileNotFound→修正）
GET /candles 200・/compute 200 で CSV 解決成功を実証
```
**判定**: #6 顕在化したが検出・修正済み（テストで実証）。棄却。

---

## 検証結果（判定）

| # | 推定原因 | 判定 | 根拠 |
|---|---|---|---|
| 1 | 時間軸乖離 | **棄却** | E1: 同一 CSV・同一変換式・compute time が candle range 内 |
| 2 | Refactor 破壊 | **棄却** | E2: 128/170 緑（既存 113/162 維持） |
| 3 | traversal 漏洩 | **棄却** | E4: `..` →404・relative_to 検証 |
| 4 | restore pairs 破壊 | **棄却** | E5: _paramsObject 正規化 |
| 5 | 注記出し分け漏れ | **棄却** | E5: mode b→null・null append ガード |
| 6 | parents 深さ誤り | **棄却（検出修正済）** | E6: parents[5] へ修正・/candles 200 実証 |

pre-mortem #1-#6 全棄却。#6 は修正反映済み。

---

## 反映: 判断の撤回または修正

- #6: dataset.py の workspace ルートを parents[4]→parents[5] へ修正（compute_controller が api/adapter/controller/ で parents[5] だったのに対し、dataset は api/adapter/compute/ で 1 段浅いため parents[5] が正）。テストで再緑を実証。
- restore() は B方式で保存 params を実再計算へ流すため _paramsObject で pairs→object 正規化を追加（A方式は params 無視のため無害）。
- index.html は B方式の /candles 取得（ready）を待ってから restore する順序に変更（candle 描画後に復元）。

---

## 残存リスク特定

1. **ThreadingHTTPServer + lru_cache の並行性**: load_dataframe/load_candles は lru_cache。GIL 下で初回同時アクセスは二重計算しうるが純関数で結果一貫。localhost プロトタイプでは許容。高並行が要件化したら明示ロック検討（後続）。
2. **サーバ殻は socket スモークのみ**: 純ロジックは handle_compute/dataset 単体で網羅。E2E（xvfb ブラウザ描画）は別工程。
3. **本番デプロイ/認証/ペイン分割/range スライス/Q-1..7**: スコープ外（実装しない）。
4. **Python スイートは lwc/.venv 必須**（default python pandas 不在・MEMORY lwc-headless-run）。サーバ起動も同 venv 前提を README に明記。
5. **upstream addCandlestickSeries は composition_root_front.js に既存**（チャート bootstrap の組み立て点・本タスクで新規追加せず）。系列 add/priceLine API は chart_renderer.js のみ（grep 0 件実証）。

---

## 最終判定

**合格**（pre-mortem #1-#6 を実証的に棄却。#6 は修正反映。3 辺 triangulation 全辺 ★★★）。

- ✓ Python 128 / JS 170 passed（既存 113/162 緑維持）: 実証（E2）
- ✓ param 実反映（maxbars 40↔120 で series 変化）: 実証（E3・主目的）
- ✓ /candles・/compute 時間軸整合: 実証（E1）
- ✓ 静的配信・traversal 404・same-origin module 読込: 実証（E4）
- ✓ Refactor 振る舞い不変・stdlib のみ・adapter/指標 src 不接触: 実証（E2・grep）
- ✓ A方式注記 B方式非表示・restore pairs 正規化: 実証（E5）

**追従性バイアス検出**: 「全テスト緑」を後方互換の短絡にせず、Refactor の機構差替えごとに再実行で実証。param 反映は SAMPLE_DATA エコーでないことを curl 実値の series 差分で死因究明した。
