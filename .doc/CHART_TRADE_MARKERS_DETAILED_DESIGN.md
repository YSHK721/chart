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
