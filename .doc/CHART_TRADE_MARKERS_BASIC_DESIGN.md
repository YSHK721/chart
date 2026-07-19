# 基本設計書 — チャート上の売買ポイント可視化（Trade Markers）

> 対象機能: シミュレーション結果の売買ポイント（建て/決済）を、ブラウザ UI の
> ローソク足チャート上にマーカーとして可視化する。
> 記録日: 2026-06-20 / ステータス: 基本設計（レビュー前）。

---

## 1. 目的・スコープ・制約

### 1.1 目的
バックテスト 1 run の確定トレード（`BacktestResult.trades`）を、既存ブラウザ UI
（`indigators/indicator_ui/web/`・lightweight-charts）のローソク足チャート上に
**建て/決済マーカー**として重畳表示し、戦略の売買タイミングを視覚的に確認可能にする。

### 1.2 スコープ
- IN: 確定トレードの建て/決済マーカー描画（方向・価格・勝敗・決済理由）。
- OUT: IS/OOS・最適化・ウォークフォワード（別機能）。インタラクティブ発注・編集。
  リアルタイム追従（live tick でのマーカー更新）。

### 1.3 制約（最優先・絶対遵守）
- **C1 データ安全ガードレール**: 既存データ（`marketdata/`・`simulator/tests/fixtures/`・
  `simulator/tests/confirmation/`・既存生成物）と **committed エンジン挙動を一切変更しない**。
  本機能は **新規ファイル追加を原則**とし、既存ファイルへの変更は「追加のみ・既存経路の
  挙動を 1 バイトも変えない」registration 行に限定する（§9 に全件列挙）。
- **C2 技術スタック不変**: 新規依存を追加しない。バックエンドは既存の純 Python
  （numpy/pandas 等）＋ stdlib HTTP サーバ、フロントは既存 ES Modules（バンドル不要）＋
  同梱 lightweight-charts v5 のみ。
- **C3 エンジン無改変**: `simulator/usecase`・`domain`・`adapter`（既存）・`main` の
  committed コードは変更しない。`BacktestResult` の生成経路に手を入れない。

---

## 2. 全体アーキテクチャ

### 2.1 データフロー（追加要素は ★）

```
[既] run_backtest → BacktestResult.trades (list[TradeRecord])
          │
          ▼
★[adapter] TradeMarkersPresenter        ← result.trades を Marker DTO 列へ純変換
          │   (新規 Port: TradeMarkerPresenterPort 実装)
          ▼
★ trade_markers.json  (Marker DTO 配列 + メタ)   ← 生成物（served 可能な場所へ出力）
          │
          ▼  GET（既存 static 配信 or 追加 read-only endpoint）
★[front/adapter] TradeMarkersRenderer    ← JSON を取得し lwc マーカーへ変換
          │
          ▼
[既] mainSeries (CandlestickSeries)       ← createSeriesMarkers(mainSeries, markers)
```

### 2.2 レイヤ責務（クリーンアーキ）
| レイヤ | 要素 | 責務 | 依存方向 |
|---|---|---|---|
| usecase（境界） | ★`TradeMarkerPresenterPort`（新規 Port） | trades→マーカー表現の出力境界を抽象化 | domain のみ参照 |
| adapter（変換） | ★`TradeMarkersPresenter` | `BacktestResult`→Marker DTO 列→JSON | usecase + domain + stdlib のみ |
| framework（殻） | 既存 `http.server`（配信） | 生成 JSON の配信（static or 追加 route） | adapter へ |
| front/adapter | ★`trade_markers_renderer.js` | JSON 取得 + lwc マーカー変換・付与 | 既存 ChartRenderer と同層（`adapter/front/`）・上流 API 隔離 |
| front/adapter（合成根） | 既存 `composition_root_front.js`（`adapter/front/` 内の DI 結線ファイル） | DI 結線（renderer 注入・初回取得トリガ） | 追加 import/instantiate のみ。※独立レイヤではなく `adapter/front/` 内に同居 |

---

## 3. 機能要件（FR）

- **FR-1 建てマーカー**: 各 TradeRecord の `entry_time`/`entry_price`/`side` から建てマーカーを
  生成する。buy=ローソク下・上向き矢印・買い色／sell=ローソク上・下向き矢印・売り色。
- **FR-2 決済マーカー**: `exit_time`/`exit_price`/`exit_reason`/`pnl()` から決済マーカーを生成する。
  勝敗（pnl>0 / ≤0）で配色し、`exit_reason`（sl/tp/reverse/stop_out/end_of_test）をラベルに含める。
- **FR-3 時刻整合**: マーカー `time` は描画中ローソクの時間軸（UNIX 秒・UTC）と一致させる。
  整合しない時刻のマーカーは描画されない/ズレるため、presenter で時刻を UNIX 秒へ正規化する。
- **FR-4 重畳・非破壊**: マーカーは `mainSeries` に重畳する READ-ONLY 表示。既存の candles 描画・
  indicator compute フロー・live updater に干渉しない。
- **FR-5 クリア/再描画**: 別 run のマーカーへ差し替え可能（既存マーカーを除去して再付与）。

---

## 4. データ設計

### 4.1 Marker DTO（presenter 出力・JSON 1 要素）
```jsonc
{
  "time": 1775000000,          // UNIX 秒・UTC（ローソク時間軸と一致）
  "position": "belowBar",      // belowBar | aboveBar
  "shape": "arrowUp",          // arrowUp | arrowDown | circle
  "color": "#26a69a",          // 方向/勝敗で決定（定数表）
  "text": "BUY 50699.0",       // 種別 + 価格（決済は + 理由/pnl）
  "kind": "entry",             // entry | exit（用途識別・描画側で未使用でも保持）
  "side": "buy"                // buy | sell（由来トレースのため保持）
}
```
出力ファイル `trade_markers.json`:
```jsonc
{ "ok": true, "symbol": "JP225", "ea_name": "...", "count": N, "markers": [ ...上記... ] }
```

### 4.2 配色・形状規約（定数・presenter 内に集約）
| 用途 | position | shape | color |
|---|---|---|---|
| 建て buy | belowBar | arrowUp | 買い色（例 #26a69a） |
| 建て sell | aboveBar | arrowDown | 売り色（例 #ef5350） |
| 決済 勝ち（pnl>0） | 反対側 | circle | 勝ち色（例 #26a69a） |
| 決済 負け（pnl≤0） | 反対側 | circle | 負け色（例 #ef5350） |

### 4.3 時刻契約（FR-3 詳細・最重要リスク）
- `TradeRecord.entry_time/exit_time` は `bar.time`（numpy.datetime64 もしくは epoch int）。
- 既存フロントの candles は `GET /candles` の UNIX 秒。**両者の軸を一致**させるため、
  presenter は時刻を「UTC UNIX 秒（整数）」へ正規化して出力する。
- マーカー描画対象のローソクデータセットは、シミュレーション入力と**同一銘柄・同一足・同一時間軸**
  であることを前提とする（不一致時はマーカーが対応ローソクに載らない）。前提を満たす確認は
  詳細設計の検証フェーズで端点（最初/最後のトレード時刻 ⊂ candles 範囲）で担保する。

---

## 5. インターフェース設計

### 5.1 バックエンド（新規 Port + 実装）
```python
# usecase/ports.py に追加（新規 Port・既存 Port は不変）
class TradeMarkerPresenterPort(abc.ABC):
    @abc.abstractmethod
    def present_markers(self, result: Any, path: Any, *, symbol: Any, ea_name: Any) -> None: ...
    # 確定シグネチャ（§12.7 M-1）。symbol/ea_name は引数注入（_present_outputs の setattr 非依存）。
    # presenter は TradeRecord の時刻を「読み取り変換」するのみで domain へ書き戻さない
    # （trade_record.py の「pd.Timestamp 禁止」は domain 保持の制約・変換読取は抵触しない）。
```
```python
# adapter/presenter/trade_markers.py（新規ファイル）
class TradeMarkersPresenter(TradeMarkerPresenterPort):
    def present_markers(self, result, path) -> None:
        # result.trades → Marker DTO 列 → JSON 書き出し（JsonPresenter と同型の I/O）
```
- 既存 `ReportPresenterPort` は**拡張しない**（ISP: マーカーは別クライアント＝別 Port）。
  既存 3 presenter・`_BasePresenter` は不変。

### 5.2 配信（2 案・§8 で選定）
- 案A（推奨）: 既存 static 配信で `trade_markers.json` をそのまま返す（**server.py 無改変**）。
- 案B: `GET /trade-markers?runRef=...`（whitelist・`/candles` と同型）を追加（server.py へ
  additive route 1 本）。

### 5.3 フロントエンド（新規モジュール）
```js
// web/js/adapter/front/trade_markers_renderer.js（新規）
export class TradeMarkersRenderer {
  constructor({ lwc, mainSeries }) {...}
  async load(url) { /* fetch JSON */ }
  setMarkers(markers) { /* lwc v5: createSeriesMarkers(mainSeries, markers) */ }
  clear() { /* 既存マーカーを除去 */ }
}
```
- `composition_root_front.js` に **import + instantiate + 初回 load** を additive 追加（§9）。
- 既存 `chart_renderer.js`・`indicator_controller.js`・`live_updater.js` は**無改変**。

---

## 6. 非機能要件

- **性能**: マーカー数は trades 件数（数千規模・例 IS 5224）。一括 setMarkers は O(N) で実用範囲。
  ただし lightweight-charts のマーカー描画は件数増で重くなるため、上限（例 表示範囲内 or 上限 N）
  を詳細設計で定義し、超過時は間引き or 範囲限定（**無音の切り捨て禁止**＝件数をログ/メタに明示）。
- **セキュリティ**: 配信は既存方針（localhost バインド・パストラバーサル防止）を踏襲。案B採用時は
  runRef を whitelist 解決（`/candles` と同様）。生成 JSON にユーザ入力を生挿入しない。
- **依存**: 追加ゼロ（C2）。

---

## 7. クリーンアーキ整合（自己点検・正式確認は architecture-executor）

- 依存方向: front renderer→lwc（上流）を隔離、backend presenter→domain のみ参照、
  Port は domain のみ依存。**内向き依存**を維持（framework→adapter→usecase→domain）。
- 単一責任: presenter は「trades→マーカー表現」、renderer は「JSON→lwc 付与」に限定。
- ISP: 既存 `ReportPresenterPort` を汚さず新 Port を分離。
- OCP: 既存経路へ分岐を足さず、新クラス/新ファイルで拡張。

---

## 8. アーキテクチャ判断と代替案

| 判断点 | 採用 | 代替 | 理由 |
|---|---|---|---|
| 描画先 | **ブラウザ UI（mainSeries）** | HtmlPresenter（撤去済み・2026-07-18）（equity 線） | HtmlPresenter は bars 非保持で価格チャート不可。candles を既に持つ UI が自然。設計判断は歴史記録として保存 |
| 出力境界 | **新規 `TradeMarkerPresenterPort`** | `ReportPresenterPort` に method 追加 | ISP・既存 presenter/Base を不変に保つ |
| 配信 | **案A static（推奨）** | 案B 追加 route | C1 最尊重（server.py 無改変）。一貫性重視なら案B（review 判断） |
| 時刻 | **UTC UNIX 秒へ正規化** | bar.time 生値 | candles 軸（UNIX 秒）に一致させないと描画ズレ |

---

## 9. 既存ファイルへの変更一覧（追加のみ・データ波及ゼロの根拠）

| ファイル | 変更種別 | 内容 | 既存挙動への影響 |
|---|---|---|---|
| `simulator/usecase/ports.py` | 追加 | 新 Port クラスを 1 つ追記 | 既存 Port 不変＝import 互換・挙動不変 |
| `web/js/adapter/front/composition_root_front.js` | 追加 | import 1・instantiate 1・初回 load 1 | 既存 DI/描画経路は分岐せず、失敗時も candles 描画に非干渉（try/catch 隔離） |
| （案B 採用時のみ）`indigators/.../api/framework/server.py` | 追加 | read-only route 1 本 | 既存 route 群は不変 |

**新規ファイル**: `adapter/presenter/trade_markers.py`、`web/js/adapter/front/trade_markers_renderer.js`、
各テスト、`trade_markers.json`（生成物）。

**データ波及ゼロの根拠**: 既存データファイル（marketdata/fixtures/confirmation/既存生成物）への
書き込み・上書き・削除は**一切行わない**。生成物 `trade_markers.json` は**新規パス**にのみ出力する。
`BacktestResult` 生成（engine）には触れず、presenter は result を**読み取り専用**で消費する。

---

## 10. リスク・申し送り

- R1 時刻軸不一致（§4.3）: シミュレーション入力 CSV と `/candles` データセットの時間軸が
  異なるとマーカーがズレる。詳細設計で端点検証＋同一データセット前提を明文化。
- R2 lightweight-charts v5 のマーカー API は v4 の `series.setMarkers` から
  `createSeriesMarkers(series, markers)` へ移行。詳細設計で同梱版（v5.2.0）の正確な API を確定。
- R3 大量マーカーの描画性能（§6）。上限と間引き方針を詳細設計で定義（無音切り捨て禁止）。
- R4 run→JSON の生成トリガ（誰が `trade_markers.json` を出力するか）は本機能では
  「既存 run の出力に presenter を 1 つ足す」想定。ブラウザからの run 起動（/simulate）は別機能
  （WIP Phase 2）で、本機能の前提にしない（事前生成 JSON を可視化する）。

---

## 11. 詳細設計でのフェーズ分割（プレビュー・6 フェーズ）
1. Port + Marker DTO 定義（usecase 境界・配色/時刻規約の定数化）
2. `TradeMarkersPresenter` 実装 + 単体テスト（trades→DTO・時刻正規化・配色）
3. 生成物 JSON 出力配線（既存 run 出力に presenter を足す・static 配置）
4. `trade_markers_renderer.js` 実装 + 単体テスト（JSON→lwc マーカー・clear）
5. `composition_root_front.js` への additive 結線（初回 load・try/catch 隔離）
6. 結合検証（端点時刻整合・描画確認）＋ ドキュメント整備・回帰確認（既存テスト全通過）

---

## 12. レビュー指摘対応・設計改訂（v2・spec-reviewer 精査結果を反映）

本章は §3〜§11 の該当箇所を**上書き（supersede）**する確定決定。Blocker/Critical/High を解消する。

### 12.1 B-1 解決 — 時刻変換式を candles 生成と完全一致させる
- presenter は時刻を **`int(pd.Timestamp(value).timestamp())`** で UNIX 秒（整数）化する。これは
  candles 生成（`indicator_ui/api/adapter/compute/dataset.py` の同名変換）と**同一式**。両者が同一式で
  naive 値を換算するため、サーバ TZ に依らず**相対的に一致**する（オフセットが相殺）。
- 設計 §4.3 / FR-3 の「UTC へ正規化」という曖昧語は**削除**。確定式のみを正とする。
- 適用対象は `TradeRecord.entry_time/exit_time`（MA_Slope 系=np.datetime64）。comma 形式
  （TC24051901 既定経路）の `time` 列実型は CSV 依存のため、詳細設計で fixture を Read 実証する。

### 12.2 B-2 解決 — 「同一データセット・同一足（M1）」を成立条件として明文化
- 本機能は **candles と同一 datasetRef・同一 timeframe（M1）** のときのみ有効。マーカーを載せる
  ローソクは、**シミュレーション入力と同一の M1 ソース**（例 `marketdata/data/jp225_m1.csv`）であること。
- 検証条件を端点から**集合包含**へ格上げ：全トレード時刻（UNIX秒）が candles の time 集合に**完全包含**。
- 現 UI 既定（`jp225_m1` を `1D`・1500本表示）では非成立。よって本機能は「UI を M1 表示に切替え、
  かつ sim 入力＝当該 M1 candles と同一ソース」を**利用前提**として要求する（詳細設計の検証で担保）。
- MT5 テスター export（confirmation/fixtures の bars）と marketdata jp225_m1 は別ソースのため**混用不可**。
  可視化は marketdata 系 M1 で run したケースを対象とする。

### 12.3 C-1 解決 — 生成トリガを新規スクリプトに確定（main 無改変＝C3 順守）
- `trade_markers.json` の生成は **新規スクリプト**（例 `simulator/tools/export_trade_markers.py`・新規ファイル）が担う。
- 同スクリプトは committed IF だけを使う：`run_backtest(...) -> (exit_code, result)` の戻り `result` を受け、
  `TradeMarkersPresenter().present_markers(result, path, symbol=..., ea_name=...)` を呼ぶ。
- `simulator/main/__init__.py`（`_present_outputs`/`run_backtest`）は**一切改変しない**。§9 変更一覧に main は
  追加されない（C3 維持）。

### 12.4 C-2 解決 — 出力先を web/ ルート内の固定新規パスへ
- 出力先は **`indigators/indicator_ui/web/data/trade_markers.json`**（新規ファイル）に固定。
  `web/data/` は既存（`sample_data.js` が在る）。同名既存ファイルが無いことを実装時に確認する。
- フロント取得 URL は **`/data/trade_markers.json`**（既存 static 配信・server.py 無改変＝案A 確定）。
- C1 根拠：既存データ・既存生成物を上書きせず、**新規ファイルのみ**を web/data/ に作成する。

### 12.5 C-3 解決 — v5 マーカー API をハンドル方式で確定
- 付与：`const handle = LightweightCharts.createSeriesMarkers(mainSeries, markersArray)` を保持。
- 差し替え：`handle.setMarkers(newArray)`。クリア：`handle.setMarkers([])`（恒久除去は `handle.detach()`）。
- renderer は handle を内部に保持し、`setMarkers()`/`clear()` をこの手順で実装（§5.3 を上書き）。

### 12.6 High 解決
- **H-1（配色・position 確定）**: 色は**確定16進**。決済 position は `side` から一意決定。
  | 用途 | position | shape | color(確定) |
  |---|---|---|---|
  | 建て buy | belowBar | arrowUp | `#26a69a` |
  | 建て sell | aboveBar | arrowDown | `#ef5350` |
  | 決済（buy 玉） | aboveBar | circle | 勝=`#26a69a` / 負=`#ef5350` |
  | 決済（sell 玉） | belowBar | circle | 勝=`#26a69a` / 負=`#ef5350` |
- **H-2（exit_reason 6 種）**: `sl/tp/reverse/expire/stop_out/end_of_test` を全列挙（`expire` 追加）。
  未知 reason は text にそのまま reason 文字列を出すフォールバック（描画は継続）。
- **H-3（text 形式確定）**: `text = f"{KIND} {price:.{digits}f}"`（建て）／決済は
  `f"{REASON} {price:.{digits}f} ({pnl:+.{pd}f})"`。`digits`=`SymbolSpec.digits`、`pd`=`profit_round_digits`
  （None 時は 0 桁表示）。価格・pnl の桁を一意化。
- **H-4（件数方針）**: **間引きしない・全件描画**を既定とし、件数を JSON メタ（`count`）と
  フロント console に明示（無音切り捨て禁止）。性能上の上限値は詳細設計で計測のうえ決定（超過時は
  描画継続＋警告、サイレント truncation はしない）。

### 12.7 Medium 解決
- **M-1（symbol/ea_name 供給）**: presenter 引数で明示注入（`present_markers(result, path, *, symbol, ea_name)`）。
  `setattr` 経由（`_present_outputs` 内付与）に依存しない＝main 無改変と整合。
- **M-2（lwc フィールド分離）**: JSON は lwc 用フィールド（`time/position/shape/color/text`）と
  メタ（`kind/side`）を**別キー階層**に分離。renderer は lwc 用サブセットのみを `createSeriesMarkers` へ渡す。
- **M-3（失敗時挙動）**: fetch 失敗・JSON 不正・time 不一致時は **console.warn + マーカー0件で続行**。
  既存 candles 描画へ例外を伝播させない（FR-4 / try-catch 隔離の可視挙動を確定）。

### 12.8 改訂後の判定
上記で Blocker 2 / Critical 3 / High 4 / Medium 3 をすべて確定解決。残存はいずれも詳細設計で
実証・数値確定する委譲事項（comma-CSV time 型・v5 マーカーの未存在 time 挙動・件数上限値）。

## 13. 追加機能（ISSUE-026）: 売買マーカー hover 時の取引明細ポップアップ

- 機能: 売買マーカー（矢印 / 円グリフ）に hover した際、当該ペアの取引明細ポップアップを表示する。
  ISSUE-025 で marker の `text`（価格ラベル）を除去済みのため、ヒット領域は**グリフのみ**であり、本機能は
  既存 v8 の `hoveredObjectId` 駆動経路（§12「v8 減光」基盤）に**相乗り**する。新規の購読・新規 fetch は増やさない。
- 表示 9 項目: 利益 / 取引日時（YYYY/MM/DD）/ 取引時間（HH:MM:SS）/ 取引価格 / 取引数量 /
  決済日時 / 決済時間 / 決済価格 / 決済数量。
- 確定仕様（ユーザー決定）:
  - 日時表示は **JST（UTC+9）** 固定（実行環境 TZ 非依存）。
  - 利益は**数値のみ**を表示し、正は緑・負は赤で着色する（通貨記号・単位は付さない）。
  - ポップアップ配置は**マーカー固定**（カーソル非追従）。§12 不変ガード配下で highlight 変化時に 1 回だけ
    配置し、同一マーカー hover 中は追従しない＝hover 開始位置に固定（実装挙動の詳細は詳細設計 §15）。
  - 取引数量＝決済数量＝**volume**（当エンジンに部分決済は無いため往復同量）。
- データ供給: presenter の pair record（`_pair_record`）が `pairs` 各要素に `profit`（pnl）と `volume` を追加出力する
  （§9「既存ファイルへの変更一覧」の追加のみ・データ波及ゼロ方針と整合。pair record の構造詳細は詳細設計 §10.3。
  committed simulator は無改変）。
- DI: フロントの `TradeMarkersRenderer` は `{document, container}` を注入される。両者不在時（SSR / 未注入）は
  ポップアップ生成を **no-op** とし後方互換を保つ（詳細は詳細設計 §15）。
- 既存節との関係: §12（v8 減光）の発火経路・§12.7（presenter 追補）のデータ契約に追補で乗る変更であり、
  既存の範囲フィルタ・ペア識別・減光連動・配色は不変。

