# 詳細設計書 — チャート上の売買ポイント可視化（Trade Markers）

> 基本設計 `CHART_TRADE_MARKERS_BASIC_DESIGN.md`（v2・spec/clean-arch レビュー反映済）を実装可能水準へ
> 詳細化する。方式A（ブラウザUI重畳）確定。記録日 2026-06-20。

---

## 0. データ安全ガードレール（実装時の絶対則）
- 既存データ（`marketdata/`・`fixtures/`・`confirmation/`・既存生成物）は**読み取り専用**。書込/上書/削除しない。
- committed **simulator コード（usecase/domain/既存adapter/main）を 1 行も編集しない**。
  → 新 Port は `ports.py` を編集せず**新規ファイル** `simulator/usecase/marker_ports.py` に置く（C3 厳守）。
- 既存ファイル編集は**フロント DI 1 ファイルのみ**（`composition_root_front.js`・追加3行・try/catch 隔離）。

---

## 1. ファイル一覧（確定）

### 1.1 新規ファイル（既存編集ゼロ）
| パス | 役割 |
|---|---|
| `simulator/usecase/marker_ports.py` | `TradeMarkerPresenterPort`（新規 Port・domain のみ依存） |
| `simulator/adapter/presenter/trade_markers.py` | `TradeMarkersPresenter`（trades→Marker DTO→JSON） |
| `simulator/tools/export_trade_markers.py` | 実行スクリプト（run→presenter→`web/data/trade_markers.json`） |
| `simulator/tests/unit/test_trade_markers_presenter.py` | presenter 単体テスト（TDD 中心） |
| `indigators/indicator_ui/web/js/adapter/front/trade_markers_renderer.js` | フロント renderer（JSON→lwc マーカー） |
| `indigators/indicator_ui/web/tests/trade_markers_renderer.test.js` | renderer 単体テスト（node:test） |
| `indigators/indicator_ui/web/data/trade_markers.json` | 生成物（新規・static 配信） |

### 1.2 追加編集（フロント DI・1 ファイルのみ・挙動保存）
| パス | 追加内容 |
|---|---|
| `indigators/indicator_ui/web/js/adapter/front/composition_root_front.js` | import 1・instantiate 1・初回 load 1（try/catch で既存 candles 描画に非干渉） |

---

## 2. バックエンド詳細

### 2.1 Port（`simulator/usecase/marker_ports.py`）
```python
from __future__ import annotations
import abc
from typing import Any

class TradeMarkerPresenterPort(abc.ABC):
    """確定トレードのマーカー表現を出力する境界（ReportPresenterPort とは別 Port＝ISP）。"""
    @abc.abstractmethod
    def present_markers(self, result: Any, path: Any, *, symbol: Any, ea_name: Any) -> None:
        """result.trades → Marker JSON を path へ書き出す。"""
        raise NotImplementedError
```

### 2.2 Marker DTO（JSON・lwc フィールドとメタを分離＝M-2）
`trade_markers.json`:
```jsonc
{
  "ok": true,
  "symbol": "JP225",
  "ea_name": "...",
  "count": 1234,                 // 全件数（無音切り捨て禁止＝H-4）
  "markers": [
    {
      "lwc": { "time": 1339670100, "position": "belowBar", "shape": "arrowUp",
               "color": "#26a69a", "text": "BUY 8568.9" },   // createSeriesMarkers へ渡す純フィールド
      "meta": { "kind": "entry", "side": "buy" }              // 由来トレース（描画では未使用）
    }
  ]
}
```
- markers は **time 昇順**で出力（lwc setMarkers は昇順要求）。entry/exit を時刻順にマージソート。

### 2.3 変換規則（presenter・確定）
時刻（B-1 確定式・candles と同一）:
```python
import pandas as pd
def _unix(t) -> int: return int(pd.Timestamp(t).timestamp())   # dataset.py:_to_unix_seconds と同一式
```
配色・position・shape（H-1 確定・presenter 内定数）:
```python
C_BUY, C_SELL = "#26a69a", "#ef5350"
# 建て:  buy → (belowBar, arrowUp, C_BUY) / sell → (aboveBar, arrowDown, C_SELL)
# 決済:  buy 玉 → (aboveBar, circle) / sell 玉 → (belowBar, circle)
#        色は勝敗: pnl()>0 → C_BUY / それ以外 → C_SELL
```
text（H-3 確定）:
```python
digits = symbol_spec.digits   # 価格表示桁。symbol 引数経由で渡す（後述）
# 建て:  f"{side.upper()} {entry_price:.{digits}f}"        例 "BUY 8568.9"
# 決済:  f"{reason.upper()} {exit_price:.{digits}f} ({pnl:+.0f})"  例 "TP 8600.0 (+312)"
```
exit_reason（H-2・6 種＋fallback）: `sl/tp/reverse/expire/stop_out/end_of_test` を許容、未知は reason 文字列をそのまま text に出して描画継続。

### 2.4 presenter アルゴリズム
```
present_markers(result, path, *, symbol, ea_name):
    markers = []
    for tr in result.trades:                       # TradeRecord（読み取り専用・domain 非書戻し）
        # 建てマーカー
        markers.append(entry_marker(tr, symbol.digits))
        # 決済マーカー
        markers.append(exit_marker(tr, symbol.digits))
    markers.sort(key=lambda m: m["lwc"]["time"])   # time 昇順
    payload = {"ok": True, "symbol": symbol.name, "ea_name": ea_name,
               "count": len(markers), "markers": markers}
    write_json(path, payload)                        # JsonPresenter と同型 I/O（stdlib json）
```
- `symbol` は `SymbolSpec`（name/digits を持つ・`usecase/models.py`）を引数注入。`ea_name` も引数注入
  （`_present_outputs` の setattr 非依存＝M-1）。

### 2.5 実行スクリプト（`simulator/tools/export_trade_markers.py`）
責務＝結線（Composition Root 利用側・main 無改変）。
```
1. marketdata/data/jp225_m1.csv を読み取り専用で pandas ロード（列 date,open,high,low,close,volume）。
2. 列ブリッジ（既存データ非改変・新規 tmp へ書く）:
     rename date→time, add spread=0, （必要列 time/open/high/low/close/volume/spread）
   → tempfile に engine 形式 CSV を書き出す（NamedTemporaryFile・実行後削除）。
3. build_interactor(data_path=tmp, ea_name=<既定 TC24051901>, symbol="JP225",
     point_size=0.1, digits=1, contract_size=10, ...既定値) で controller/request 構築。
4. result = controller._interactor.execute(request)   # committed IF のみ使用
5. TradeMarkersPresenter().present_markers(result, OUT, symbol=spec, ea_name=ea)
     OUT = indigators/indicator_ui/web/data/trade_markers.json（新規・web/ ルート内）
6. 検証ログ: trades 件数・markers 件数・時刻範囲を stdout に出す（B-2 集合包含は別検証）。
```
- CLI 引数（任意・既定あり）: `--rows N`（先頭 N 本に限定・既定で直近寄り）/ `--ea` / `--out`。
- 大容量（約3億バイト）対策: `--rows` で範囲限定（既定で UI の直近窓に対応する範囲）。
- 例外時は非ゼロ終了＋メッセージ（既存データには一切触れない）。

---

## 3. フロントエンド詳細

### 3.1 renderer（`web/js/adapter/front/trade_markers_renderer.js`）
```js
// 上流 lwc API（createSeriesMarkers）を隔離する adapter（chart_renderer.js と同層・同規約）。
export class TradeMarkersRenderer {
  constructor({ lwc, mainSeries }) { this._lwc = lwc; this._series = mainSeries; this._handle = null; }

  setMarkers(lwcMarkers) {                       // lwcMarkers: [{time,position,shape,color,text}]（昇順）
    if (!this._handle) this._handle = this._lwc.createSeriesMarkers(this._series, lwcMarkers);
    else this._handle.setMarkers(lwcMarkers);     // v5: ハンドル方式（C-3）
  }
  clear() { if (this._handle) this._handle.setMarkers([]); }

  async load(url, fetchFn = fetch) {              // 失敗は warn + 0 件（candles 非干渉＝M-3）
    try {
      const res = await fetchFn(url);
      if (!res.ok) { console.warn(`[trade-markers] fetch ${res.status}`); return 0; }
      const json = await res.json();
      const lwc = (json.markers || []).map(m => m.lwc);   // lwc サブセットのみ抽出（M-2）
      this.setMarkers(lwc);
      if (json.count != null) console.info(`[trade-markers] ${json.count} markers`);  // H-4 明示
      return lwc.length;
    } catch (e) { console.warn('[trade-markers] load failed', e); return 0; }
  }
}
```

### 3.2 DI 結線（`composition_root_front.js`・追加3行・挙動保存）
```js
import { TradeMarkersRenderer } from './trade_markers_renderer.js';   // (1) import
// … 既存 mainSeries 生成後 …
const tradeMarkers = new TradeMarkersRenderer({ lwc, mainSeries });    // (2) instantiate
try { await tradeMarkers.load('/data/trade_markers.json'); }          // (3) 初回 load（try で隔離）
catch (e) { console.warn('[trade-markers] init skipped', e); }
```
- 既存の candles 描画・compute・live updater の経路に**分岐を足さない**。load 失敗時も既存描画は継続。

---

## 4. 検証条件（B-2 軸一致の担保）
- **集合包含検証**（Phase 3/6）: 全マーカー `time` ⊆ candles `time` 集合（同一 datasetRef・M1）。
  包含しないマーカー件数を**ログに明示**（無音にしない）。0 件であることを合格条件とする。
- 利用前提（ドキュメント明記）: UI を **M1 表示**に切替え、candles は sim と同一の marketdata jp225_m1。
  既定 1D・直近1500本では可視範囲が限られる（範囲制限は本機能のスコープ外＝既知の利用上の制約）。

---

## 5. 6 フェーズ実装計画（各フェーズ TDD・Red→Green→Refactor・完了時に検証）

| Phase | 内容 | 主なテスト（Red 先行） | 完了条件 |
|---|---|---|---|
| **P1** | `marker_ports.py`（Port）＋ presenter 用定数/ヘルパの骨格 | Port が import 可・抽象契約 | import OK・既存テスト不変 |
| **P2** | `TradeMarkersPresenter` 実装 | 合成 BacktestResult（buy/sell・勝敗・各 exit_reason・expire 含む）→ 時刻式/position/shape/color/text/件数/昇順/lwc・meta 分離を assert | presenter 単体テスト緑 |
| **P3** | `export_trade_markers.py`（列ブリッジ＋run＋出力＋集合包含検証） | 小さな合成 CSV で run→JSON 生成・schema 検証・time⊆candles | スクリプト実行で JSON 生成・包含0件 |
| **P4** | `trade_markers_renderer.js` 実装 | fake `createSeriesMarkers`/fetch で setMarkers/clear/handle 保持/失敗時 warn+0 | node:test 緑 |
| **P5** | `composition_root_front.js` 追加結線 | 既存 web/tests 全通過（回帰）＋結線が候補描画を壊さない | 既存フロントテスト緑 |
| **P6** | 結合検証＋回帰＋ドキュメント | simulator 全テスト緑（回帰）／git で既存データ・committed simulator 無改変を確認 | 全緑・データ波及0 |

- 各 Phase は前 Phase 緑を前提に進む。問題は自律解決（原因特定→修正→再検証）。
- P6 で `git status` により「既存追跡ファイルの変更＝フロント DI 1 ファイルのみ」「既存データ変更0」を実証。

---

## 6. リスク対応（詳細設計時点で確定）
- 時刻式不一致 → §2.3 で `int(pd.Timestamp(t).timestamp())` に固定（candles と同一）。
- v5 マーカー API → §3.1 ハンドル方式に固定。
- 生成トリガ/出力先 → §2.5 スクリプト＋ `web/data/trade_markers.json` に固定（main 無改変・web/ 内）。
- comma-CSV time 実型 → §2.5 ブリッジで `time` 列に date 文字列を載せ、presenter 側 `pd.Timestamp` で吸収。
- 件数性能 → 全件描画・件数明示。実測で重い場合は `--rows` で範囲限定（サイレント切り捨てなし）。

---

## 8. 動作確認フィードバック対応（Fix v2・export スクリプト堅牢化）

実機動作確認で、機能の中核（presenter/renderer/配線/時間軸整合＝393件一致）は正常だが、
**export スクリプトの既定動作**に 2 つの実用障害が判明。本節で確定する。

### 8.1 Fix-A: 既定生成窓を「直近（tail）」にする（UI 可視窓と整合）
- 問題: 既定 `--rows N` は marketdata の**先頭 N 本（最古=2012年）**を読む。UI 既定の `/candles?limit=1500`
  は**直近**を返すため、マーカーが UI 可視窓と重ならず**画面に出ない**。
- 修正: 既定を**直近 N 本（tail）**生成にする。297MB を丸読みせず、行数を数えて
  `pd.read_csv(src, skiprows=range(1, total-N+1))` で**末尾のみ**読む `read_recent_marketdata(src, n)` を追加。
  既定 N は UI の `RECENT_BARS=1500` を内包する余裕（例 5000）。明示の先頭指定も残す（後方互換オプション）。
- 受入: 既定実行で生成したマーカー time が `/candles?datasetRef=jp225_m1&timeframe=1m&limit=1500` の
  candle time 集合と**重なり >0**（UI に描画される）。

### 8.2 Fix-B: 既定 run config を堅牢化（高価格でクラッシュしない）
- 問題: 直近の高価格（JP225≈71700）×`_meta` 既定（lot=1.0 / leverage=100 / 証拠金10000 / fail_stop）で
  `MarginCallError` が送出され**クラッシュ**（exit≠0・JSON 未生成）。
- 修正: `_meta` に `config_overrides={"stop_out_action": "close_and_halt"}` を付与し、証拠金割れ時も
  **強制決済して完走**（例外送出しない）。加えて持続可能なサイジング（`lot_size=0.1`）へ既定変更し、
  直近窓を通してトレードが分布するようにする（早期 halt を避ける）。
- 受入: 既定実行が**非ゼロ終了せず**完走し、trades>0・JSON 生成。

### 8.3 受入（動作確認の完全完了基準）
1. 既定 `python simulator/tools/export_trade_markers.py` がクラッシュせず JSON 生成。
2. B方式サーバ配信下で、マーカー time が UI 直近 M1 candle 窓と**重なり >0**。
3. 既存テスト全緑（回帰）＋ 新規テスト（tail 読み・堅牢 config・重なり>0）緑。
4. committed simulator（domain/usecase/既存adapter/main）無改変は維持。

---

## 7. 実装完了記録（2026-06-20・6 フェーズ全完了）

| Phase | 成果物 | テスト |
|---|---|---|
| P1 | `simulator/usecase/marker_ports.py`（Port） | import OK |
| P2 | `simulator/adapter/presenter/trade_markers.py` | `test_trade_markers_presenter.py` 12 件緑 |
| P3 | `simulator/tools/export_trade_markers.py` | `test_export_trade_markers.py` 4 件緑 |
| P4 | `web/js/adapter/front/trade_markers_renderer.js` | `trade_markers_renderer.test.js` 6 件緑 |
| P5 | `composition_root_front.js`＋`index.html`＋`build.mjs`（追加結線） | web 全 **259 件緑**（回帰） |
| P6 | 結合検証・回帰・データ安全 | simulator 全 **588 件緑** |

- 結合検証（実 marketdata 8000 本）: trades=134 / markers=268 / **集合外=0**（時間軸整合実証）。
- データ安全: committed simulator（domain/usecase/adapter既存/main）**無改変**、
  marketdata/fixtures/confirmation **無改変**。既存編集はフロント 3 ファイルの追加のみ。
- 利用手順: `PYTHONPATH=/workspaces/app python3 simulator/tools/export_trade_markers.py --rows N`
  で `web/data/trade_markers.json` を生成 → B方式サーバ起動 → UI を **M1 表示**にすると mainSeries に
  売買マーカーが重畳される。

---

## 9. 動作確認フィードバック対応 v3（可視範囲外マーカーの非描画＝左端クランプ列の除去）

- 症状: UI 既定（直近1500本 M1）表示で、描画中ローソク範囲より**前の時刻**を持つマーカーが
  lightweight-charts により**左端へクランプ**され、ラベルが縦に積層する（左側のマーカー列）。
- 原因: JSON のマーカー（直近5000本窓・約1326件）のうち、UI が読む直近1500本ローソクの
  **範囲外**の時刻（窓の左外）が左端へ寄る。集合包含は export の5000窓基準であり、UI の1500窓とは別。
- 修正: `TradeMarkersRenderer` を「**現在の可視時間範囲内のマーカーのみ描画**」に変更。
  - constructor に `chart` を追加。`chart.timeScale().subscribeVisibleTimeRangeChange(range => _apply(range))` を購読。
  - `load()` は全マーカーを `this._all` に保持。可視範囲が確定したら `from<=time<=to` のマーカーのみ
    `createSeriesMarkers`/`setMarkers` で適用。範囲変更（時間足切替・パン・ズーム）で再適用。範囲 null（初期）は空。
  - 既存の `setMarkers`/`clear`/`load` の公開挙動は互換維持（range 未購読時のフォールバックを明確化）。
- 配線: `composition_root_front.js` で `new TradeMarkersRenderer({ lwc, mainSeries, chart })`（chart 追加）。
- 受入: 既定表示で**左端のマーカー列が消え**、可視ローソク上のマーカーのみ表示。可視範囲変更で追従。
  既存フロントテスト緑＋新規（範囲内のみ適用・範囲外除外・範囲変更で再適用）緑。committed simulator 無改変。

---

## 10. 追加機能 v4（売買ペアのライン結合 + ホバー時の他マーク減光）

v5 API 実在確認済: `attachPrimitive`/`detachPrimitive`・`paneViews`・`requestUpdate`・
`series.priceToCoordinate`・`timeScale().timeToCoordinate`・`subscribeCrosshairMove`・`param.hoveredObjectId`・
marker の `id`（createSeriesMarkers は id 受理）。

### 10.1 機能(1) 売買ペアをラインで結ぶ
- 各トレードの建て `(entry_time, entry_price)` → 決済 `(exit_time, exit_price)` を**線分**で結ぶ。
- 配色: 勝敗（`pnl>0`=緑 `#26a69a` / それ以外=赤 `#ef5350`）。
- 実装: 新規カスタム primitive `PairLinesPrimitive`（`web/js/adapter/front/pair_lines_primitive.js`）を
  `mainSeries.attachPrimitive(...)` で付与。pane view renderer が `pairs` を走査し、
  `timeScale().timeToCoordinate(time)` と `series.priceToCoordinate(price)` で座標化して canvas に線分描画。
  座標が null（可視範囲外）の点はスキップ（Fix v3 の可視範囲整合）。`requestUpdate()` で再描画。

### 10.2 機能(2) ホバー時に当該ペア以外を減光
- `chart.subscribeCrosshairMove(param)` の `param.hoveredObjectId` で、ホバー中マーカーの id を取得。
- id 規約: 各 marker に `id = "t{i}:entry" / "t{i}:exit"`（i=トレード通番）を付与（presenter）。
- ホバー時、当該トレード i の**ペア（entry+exit のマーカー＋線）を強調、それ以外を減光**:
  - マーカー: 非ハイライトを **alpha 低下色**（例 rgba 0.15）で再 `setMarkers`、ハイライトは通常色。
  - 線: primitive に `highlightIndex=i` を渡し `requestUpdate` で非ハイライト線を低 alpha 描画。
- ホバー解除（`hoveredObjectId` 無し）で全件通常表示へ復帰。可視範囲フィルタ（§9）と両立。

### 10.3 データ（presenter 追補）
- `simulator/adapter/presenter/trade_markers.py`（feature の新規ファイル・committed engine ではない）に:
  - 各 marker の `lwc` に `id`（`t{i}:entry`/`t{i}:exit`）を追加（v5 marker は id 受理）。`meta` に `pair=i`。
  - JSON に **`pairs`** 配列を追加: `[{ "i", "side", "win", "entry": {"time","price"}, "exit": {"time","price"} }]`。
- 既存 markers/lwc サブセット・時刻式・配色・昇順は互換維持。committed simulator は無改変。

### 10.4 配線・制約・受入
- 新規: `pair_lines_primitive.js`。`trade_markers_renderer.js` 拡張（pairs 保持・primitive attach・hover dimming・
  範囲フィルタ整合）。`composition_root_front.js` で chart/crosshair を renderer へ供給。
- committed simulator（domain/usecase/既存adapter/main）無改変。新規依存禁止。後方互換（chart省略・購読API非提供）維持。
- 受入: ブラウザで**ペア線が表示**され、マーカーに**ホバーすると当該ペア以外が減光**。range=null/フォールバックでも throw しない。
  既存フロントテスト緑＋新規（pairs生成・id付与・hover時の強調/減光集合計算・座標スキップ・範囲整合）緑。
  canvas 実描画は node:test 範囲外のためブラウザ結合確認で担保（ロジックは fake scale で単体検証）。

---

## 11. 追加機能 v5（ホバー時に当該ペア以外のローソク足も減光）

- 目的: マーカー/線の減光（§10.2）に加え、ホバー中ペアの時間範囲 `[entry_time, exit_time]` **外のローソク足も減光**し、当該ペアへ視覚的フォーカスする。
- アプローチ候補（フェーズ2で実コード実証のうえ確定）:
  - **案A（推奨・自己完結）**: dimming オーバーレイ primitive。highlight 時、`[左端, entryX]` と `[exitX, 右端]` の x 帯に
    半透明の暗色矩形を `useBitmapCoordinateSpace` で描画（pane 全高）。ローソク・他マーカー・他線を一括減光し、
    ペア帯のみ素通し。candle データを改変しない。z 順は「ローソクの上・ペアマーカーを潰さない」配置を確認。
  - **案B**: ローソク per-bar 色上書き。`mainSeries.setData` で範囲外バーを暗色（color/borderColor/wickColor）に、
    範囲内は通常色に。hover 解除で復元。candle データ保持・復元責務が必要（ChartRenderer 連携）。
- 制約: committed simulator 無改変。フロント（primitive/renderer±composition_root）に閉じる。範囲フィルタ（§9）・
  hover マーカー減光（§10.2）と単一 `_render` 経路で両立。後方互換（chart/購読API/range null）維持。
- 受入: ホバー中、当該ペア以外のローソク足が減光し、ペアの足・マーカー・線が強調される。ホバー解除で復帰。
  既存テスト緑＋新規（範囲外帯の算出・highlight 連動・解除復帰）緑。canvas 実描画はブラウザ確認に委譲。

---

## 12. v6（ローソク足のみ減光・背景は減光しない／v5 オーバーレイを置換）

- 変更理由: v5（§11・案A dimming オーバーレイ矩形）は x 帯を**全高の半透明暗色矩形**で覆うため、
  ローソク足だけでなく**背景も減光**する。ユーザー要件は「**背景は絶対に減光させない・ローソク足だけを
  限りなく減光**」。よってオーバーレイ方式を**廃止**し、per-bar 着色（案B）へ置換する。
- 新方式（案B・per-bar 着色）: ホバー中、ペア `[entry_time, exit_time]` **外**のローソク足を、
  `mainSeries.setData` の per-bar `color`/`borderColor`/`wickColor` で**極めて暗い色（背景に近い・限りなく減光）**へ
  上書きする。ペア内バーは通常色。**背景ピクセルは一切変更しない**（ローソクの色だけが変わる）。ホバー解除で原色復元。
- candle データ保持/復元（フェーズ2で確定・**ChartRenderer 起点 observer 方式**）:
  - **基準 candles は ChartRenderer が単一所有**（`_baseCandles`）。`setCandles`（全置換）/`updateLastCandle`（差分）の
    全経路（timeframe 切替=controller・restore・live=live_updater）が ChartRenderer を通過するため、ここを唯一の同期点とする。
    （composition_root 単独供給＝`setBaseCandles` 方式は陳腐化するため**不採用**。）
  - hover: trade markers renderer が ChartRenderer の `dimCandlesOutsidePair({from,to})` を呼び、ChartRenderer が基準
    candles から「ペア外を per-bar 暗色化」した配列を `mainSeries.setData` する。解除: `restoreCandles()` で基準を `setData` 復元。
    **mainSeries への setData は ChartRenderer に閉じる**（upstream 隔離・grep0件規約）。renderer は直接 setData しない。
  - ChartRenderer は `setCandles`/`updateLastCandle` 時に observer（trade markers renderer の `onCandlesChanged`）へ通知。
    hover 中（dim 適用中）に通知が来たら **highlight 解除→基準復元**してから本来の書込みに委ねる（二重 setData 競合の回避）。
- `PairDimPrimitive`（v5 オーバーレイ）は**削除**。`PairLinesPrimitive`（ペア線・v4）と marker 減光（v4）は維持。
- 制約: committed simulator 無改変。フロント（renderer＋composition_root±ChartRenderer 連携）に閉じる。
  §9 範囲フィルタ・§10.2 marker 減光と単一 `_render` で両立。後方互換（chart/購読API/range null・基準candles未供給）維持。
- 受入: ホバーで当該ペア以外の**ローソク足のみ**が限りなく減光（**背景は不変**）、ペアの足・マーカー・線が強調。
  解除で完全復元。既存テスト緑＋新規（ペア外バー暗色化・ペア内保持・解除復元・基準candles未供給フォールバック）緑。
  実描画・実hoverはブラウザ確認。
