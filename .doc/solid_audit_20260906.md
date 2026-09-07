# リポジトリ全体 SOLID 精査台帳（2026-09-06）

読み取り専用精査。8 系統の並列エージェントが全実装コードを実読・実測（grep / AST / inode / Test Spy 注入）して検出。
コード変更は一切未実施。各指摘は「原則 / ファイル:行 / 内容 / 重大度」を実コード確認付きで記録。

**対象**: common, api_shared, common_view, datawindow, marketdata, indigators, simulator, dashboard_ui, unified_ui, tools, .claude/scripts
**対象外**: node_modules・lightweight-charts-python-main（vendored 第三者コード）、prototype_*（使い捨てスパイク）、sample/design/docs（非コード）、テストコード自体の SOLID（証拠参照のみ）

**総計**: 違反 119 件（高 48・中 60・低 11）。うち行番号まで独立再検証済みの高重大度は各系統の検証記録参照。

---

## 総括 — リポジトリ横断の構造所見

### 健全（実測で確認）
- **レイヤー/パッケージ依存方向はほぼ全域でクリーン**。共有 4 パッケージ（common/common_view/api_shared/datawindow）の出次数 0、simulator domain の外向き import 0、dashboard_ui domain/usecase の逆流 0、marketdata の上位層逆依存 0 — すべて grep/AST 実測。
- **依存規律の多くが機械的検査で強制済み**（marketdata の AST 依存宣言検定、indigators の構造ガード 16 本、JS の js_layer_guard、common の子プロセス純度検定）。
- **列挙 factory（if/elif 型分岐）は本番 Python にほぼ不在**。indigators は宣言表 `_TABLE` へ是正済み、marketdata は 0 件。

### 循環依存（本番・4 件）
| # | 辺 | 証拠 |
|---|---|---|
| C-1 | tools ⇄ simulator | `simulator/sim_ui/main/composition_root_jobs.py:68` ↔ `tools/verify_*` 等 7 本 |
| C-2 | simulator.main ⇄ simulator.main.tester_settings | `main/__init__.py:683` ⇄ `run_from_settings.py:49`（遅延 import で回避のみ） |
| C-3 | dashboard_ui framework ⇄ main | `main/composition_root.py:62` ⇄ `framework/serve_dashboard.py:168` |
| C-4 | indicator_ui ⇄ market_profile（JS） | symlink 27 本 ↔ `market_profile_primitive.js:13,21` の逆 import |

### 単一ソース違反（手書き複製・既に乖離あり＝最重要テーマ）
| # | 事実の複製 | 箇所 | 乖離 |
|---|---|---|---|
| D-1 | API エンドポイント集合 | `unified_ui/web/js/sw_rewrite.js:21` ⇔ `op_log.js:191` | **既に食い違い**（`tf_period_profile` 欠落・実在しない `compute_seq`） |
| D-2 | 曜日配列 Mon..Sun | `report_ui heatmap.js:16`(export済) ⇔ `graphs.js:17` ⇔ `usecase/derive.py:13` | 3 重・手動同期を自認 |
| D-3 | hold バケット境界 7 組 | `report_ui graphs.js:20-23` ⇔ `derive.py:16-24` | JS↔Python 跨ぎ・突合検定 0 |
| D-4 | 報告キー語彙 ~45 件 | `glossary.js` 3 構造 + `build_report_payload.py:326-369` | 1 キー追加＝4 箇所改変 |
| D-5 | 因果ローリング分位スパン 4 関数 84 行 | `profit_rmm_macd/src/core.py:99-182` ⇔ `profit_rmm/src/core.py:77-160` | verbatim 複製をコード自身が明記 |
| D-6 | 8択ソース解決手続き | `btlm_trail/core.py:50`・`ma_marod/core.py:150`・`moving_averages/lwc_chart.py:85`・`call_binding.py:242` | 4 重実装 |
| D-7 | `resolve_times` | `common_view/lwc_adapter.py` ⇔ 自前 3 ファイル | `str(c).lower()` vs `c.lower()` で例外型が分岐 |
| D-8 | 表示遅延 12 秒 | `indicator_ui_compute_gateway.py:43` ⇔ `live_tick_player.js:27` | 「変えるときは両方」とコメント自認 |
| D-9 | 時間足順序 | `dashboard_ui web/js/.../timeframes.js:12` ⇔ `domain/horizon.py:17` | 突合検定 0 |
| D-10 | 末尾読み上限 50,000 | `rollup_store.py:42` ⇔ `dataset.py:103`（＋`tickvol_profile.py:41` の出典コメントは誤り） | コメント同期のみ |
| D-11 | core 集合列挙の第 3 の写し | `tools/js_layer_guard.mjs:36`（唯一源は `mode_table.js`） | モード追加で新 core が無音で無検査化 |
| D-12 | `_quantile_series_name` | 4 パッケージ複製（q=0.995/0.99 同名衝突ごと複製） | — |
| D-13 | volume_step 量子化 | `domain/volume_step.py:52` ⇔ `partial_close_rule.py:64` ⇔ `order.py:93` | 3 重 |
| D-14 | ブローカー時間座標（NY+7h） | `marketdata/resample.py:76` ⇔ `session_day.py:41` | 2 実装 |
| D-15 | UTC 日帰属規則 | `mt5_ticks/ingest.py:131` ⇔ `archive_ingest.py:416` | 2 実装 |
| D-16 | ロールアップ保存配置 | 3 パッケージ 8 箇所が各自構築（`rollup_store.py:38` ほか） | 権威モジュール不在 |
| D-17 | パイプライン実行器 | `tools/acquire_marketdata.py:327` ⇔ `build_tick_rollup.py:265` | docstring まで同一の丸ごと二重実装 |

### 神クラス・多責務（SRP 上位）
- `simulator/usecase/run_backtest.py:164` — 940 行/18 メソッド（約定・SL/TP・記帳・証拠金・建玉変更・run 制御）【高】
- `simulator/main/__init__.py:620` `build_interactor` — 38 引数・235 行。3 本番モジュールが `inspect.signature` 反射＝事実上の HTTP スキーマ【高】
- `indigators/.../call_binding.py` — 986 行 6 責務。指標固有 hook の抽出は 26 指標中 1 件のみ【高】
- `dashboard_ui/web/js/.../reach_sheet_view.js` — 1,170 行に 6 責務【高】
- `indicator_ui/.../properties_dialog.js:44` — 829 行 5 変更軸（自ファイル宣言と矛盾）【高】
- `common/marod_bands.py:1-399` — 6 責務（numpy 分位のビット等価再実装 91 行が同居）【高】
- `marketdata/mt5_ticks/archive_ingest.py:318-455` — 138 行 9 関心・nonlocal 9【高】
- `tools/live_tick_watch.py:474-543` — 7 関心融合【高】

### 抽象の未宣言・半適用（DIP/ISP/LSP 上位）
- `marketdata/rollup.py:652-761` — `RollupWriter` 抽象が増分経路で迂回され CSV 直書き【高】
- `replay_ui/web/js/replay.js` — `typeof` 能力探査 18 箇所＝宣言 port の代用【高】
- `replay_ui/usecase/replay_ports.py:214,245,270,286` — usecase Port の戻り値が (HTTP status, body)【高】
- `__getattr__` 透過委譲ラッパ 11 件（replay_ui/sim_ui framework）— 契約未宣言・欠落はリクエスト時まで露見せず【中】
- `api_shared/json_get_routes.py:95-103` — `fallback`/`handler` が `Any`、要求 5 メンバが型に現れない【高】
- `dashboard_ui` F-1 — series 二重発行（実測 3 instance→6 発行）を具象 gateway の memo だけが救い、Port 契約は冪等性を未宣言。controller 経路の計算量テストに穴【高】
- `dashboard_ui/adapter/series_role_table.py:112` — `sys.path` 副作用依存の裸 `adapter` import【高】
- `chart_renderer.js:135-152` — 6 協働子へ host 全体（~85 メソッド）を渡す（`host_view.js` の契約射影が未適用）【高】
- `sim_frame_view.js:71-118` — iframe 親子契約不在（postMessage 0 件、子 global/DOM id へ reach-in）【高】
- LSP: 同名 `_parse_int` の負値契約分岐（`market_profile_controller.py:191` vs `tf_period_profile_controller.py:428`）、`ReplayIndicatorController` の基底 private 21 個依存、simulator usecase 例外 6 本が `BacktestError` 階層外。

### OCP（拡張が既存改変を強制）
- EA 追加＝`simulator/main/__init__.py` 内 8 種の編集点（実測 10 行＋1 ファイル）【高】
- MP 特別扱い分岐 8 箇所散在（`replay_indicator_controller.js` ほか）【高】
- `dashboard_ui/adapter/series_role_table.py:94` `_OSCILLATORS` literal dict に差し替え口なし（＋`breakpoints/__init__.py:40`）【高】
- `marketdata/tick_m1.py:637-678` 形成中バー経路に `price_basis` 拡張点なし（`jp225_mt5.tick=False` が代替）【高】
- `.claude/scripts/declaration_integrity.py:432` 検査 C1-C3 固定連鎖、`tools/codescan/dependencies.py:43` 言語 2 分岐で seam 迂回【高】

---

## 系統別詳細

### 1. common / api_shared / common_view / datawindow（26 ファイル 2,990 行・全読）
違反 14（高 3・中 7・低 4）。
- 高: marod_bands.py 6 責務同居 / json_get_routes.py の `Any` 協調者 / common `__init__` の遅延公開名が単一モジュール固定（OCP）
- 中: `_KIND_AGGREGATORS` 位置タプル＋呼出側分岐残存、event_quantiles ステッパ 7 引数固定（ダミー 2 リスト強制）、`step_events` がループ内で毎回正規化を発行し捨てる（「作ってから捨てる」規約該当）、`__init__` 3 責務、`_LastBarTime` の宣言なきダックタイプ、9 引数素通しファサード、`resolve_times` 一本化未完（3 ファイル残存・docstring の「7」は実測 3 と不一致）
- 低: datawindow 独立化根拠の一部が実測で失効、状態なしクラスの具象直接生成、関数内 import、公開面の二重規約
- 遵守: 依存方向の機械検査（AST＋子プロセス 2 段）/ `applied_price` の単一表 / writer 注入の加法拡張

### 2. marketdata（実装 45 ファイル 7,281 行）
違反 15（高 5・中 10）＋枠外 9。逆依存 0・循環 0・列挙 factory 0 を実測。
- 高: ロールアップ配置権威の不在（8 構築点）/ `RollupWriter` の半分適用 / `ingest_months` 9 関心 / 形成中バーの `price_basis` 欠落 / `"JP225"` 2 台帳別値（tick=1.0/digits=0 vs 0.1/1、突合検定 1 本のみ）
- 中: `incremental_update` 6 変更軸、`tail_gap_report` 列名直書き、`RollupState` の値+永続化兼務、`CandleSource` 半開契約の担保者非対称（LSP）、UTC 日帰属 2 実装、ブローカー時間座標 2 定義、journal の NDJSON+parquet 兼務、`serving_cache` の `(*args,**kwargs)` 公開面、`dataset` の私有属性再輸出、50,000 の 2 定義
- 遵守: tf_ledger の台帳 OCP（時間足追加 1 行）/ AST 依存宣言検定（未使用許可も失敗）/ 依存ゼロの規則核

### 3. indigators 計算部（非テスト 317 ファイル 43,866 行）
違反 15（高 4・中 9・低 2）。elif 5 段以上 0 件・指標名分岐 0 件・本番計算モジュールの I/O 0 件を実測（想定違反 3 種は棄却）。
- 高: call_binding.py の非閉性（指標固有 hook 抽出 1/26）/ profit_rmm 系 84 行 verbatim 複製 / `_value_area` 等 private 跨モジュール import 8 箇所 / 同名 `_parse_int` の契約分岐（LSP）
- 中: call_binding 6 責務、8択ソース解決 4 重、`resolve_times` 自前 2+1、`tickvol→btlm_trail.src.*` 内部貫通（facade 不在）、store_port のサービスロケータ解決、`_quantile_series_name` 4 重、server.py の MP クエリ直保持（非対称）、tf_period_profile_controller 560 行 5 責務、data_prep.py 7 責務（研究層）
- 遵守: 28 パッケージ一貫の層分離 / Protocol 53・ABC 0 の構造的部分型 / 構造ガード検定 16 本（AST で「invoke に if 0 件」等を固定）

### 4. simulator 本体（非テスト Python、レイヤー構造）
違反 15（高 7・中 8）＋枠外 6。domain 純度は完全（外向き 0・numpy 0）。experts に Python 実装なし（.mq5 のみ）。
- 高: RunBacktestInteractor 940 行 / build_interactor 38 引数（3 モジュールが signature 反射）/ EA 追加 8 編集点 / main⇄tester_settings 循環 / replay_ports の HTTP 語彙侵食 / ReplayApp 251 行 8 Port（API 追加＝4 箇所同期）/ AccountEngine と domain/account の証拠金規則 2 所有
- 中: submit_job 9 協力者＋直列連鎖、ini_codec 802 行 3 アクター、validation.py 871 行同居、run_job 479 行、本番実装 0 の Port 4 本（YAGNI）、`BacktestError` 階層外例外 6 本（LSP）、volume_step 量子化 3 重、`__getattr__` 委譲 11 件
- 枠外: run_features 12 スイッチ、SPREAD_DEPENDENT_EA_NAMES 二重宣言、serve_sim_jobs の手書き if/elif（兄弟 10 ファイルはルート表）、INITIAL=10000.0 二重ソース、Port 抽象の `Any` 35 箇所、submit_job 検証 3 本到達不能
- 遵守: domain 純度実測完全 / Null オブジェクト 6 件（同一例外・沈黙させない）/ 宣言駆動選択＋起動時 RuntimeError・AST 検定

### 5. simulator UI（replay_ui/report_ui/sim_ui、非テスト 174 ファイル 21,477 行）
違反 15（高 7・中 8）。神ファイル不在（中央値 80 行・最大 700 行）。
- 高: `typeof` 能力探査 18 箇所（ISP）/ `_isMarketProfile` 分岐 8 箇所散在 / 基底 private 21 個依存（LSP・基底は symlink 先）/ 曜日 3 重複製 / hold 境界 JS↔Py 複製（言語跨ぎ突合なし）/ 報告語彙 3 構造+Py 約 30 ラベル / iframe 無契約結合（postMessage 0）
- 中: `_renderCharts` 176 行、`renderChart`、`boot()` の直接 fetch、private 直代入（`_recentBars`）、Strategy が actor 内部 19 個へ無制限アクセス、Strategy 選択 if/else 2 箇所重複、route App の `__getattr__` 透過、Interactor 具象直接構築
- 遵守: ルート連鎖（Handler 分岐ゼロ・追加＝連結 1 行）/ v5 再入規律の `write()` 単一集約 / 設定 schema 語彙値のサーバ単一ソース＋fail-stop

### 6. dashboard_ui（非テスト py 37・js 23）
違反 15（高 5・中 8・低 2）。レイヤー逆流 0（例外は C-3 のみ）。発行回数は Spy 注入で実測。
- 高: series/bars 二重発行（Port 契約に冪等性なし・controller 経路の計算量テスト欠落）/ reach_sheet_view.js 1,170 行 / composition_root_front.js 682 行（宣言「結線だけ」と乖離・表示系統追加＝4 箇所）/ `_OSCILLATORS` literal 差し替え口なし / RSI 固有数式が汎用役割表に同居
- 中: `SeriesRolePort` 5 メソッド束（架空 instance 組立を強制）、3 キャッシュ具象への層跨ぎ結合、`_level_price_of` の数値ポリシー所有、MP bin 帰属 2 所有（ISSUE-500 2b で顕在化）、3 poller の戻り値契約分裂＋ゲートロジック 3 複製（LSP）、framework⇄main 循環、`main()` の本番方針保持、DELAY 12 秒手写し
- 低: 到達不能 `_closes_by_time`、P-1/P-2 兼務 gateway
- 遵守: 依存方向クリーン＋JS 機械ゲート / 無改変デコレータ 2 例 / 規則所有者と描画者の注入分離

### 7. フロント JS 共有層（indicator_ui 実体 25,546 行ほか・symlink 148 本＝複製ではない）
違反 15（高 6・中 9）。switch 0 件・グローバル結合ほぼ封鎖済みを実測（想定違反 2 種は棄却）。
- 高: properties_dialog 829 行 / chart_renderer の host 全体渡し＋`candle_feed.js` の private 直代入 10 箇所 / **API 集合二重定義の実乖離（D-1）** / `chart_app_wiring.js:462` の private 掘り（6 行下で契約機構を正用）/ composition_root への方針漏出＋時間足名直書き / indicator_ui⇄market_profile 循環（C-4）
- 中: catalog.js REGISTRY 手書き 28 列挙（3 箇所編集）、共有レジストリの in-place 破壊更新、catalog 3 変更軸、フラグ分岐+ハードコード選択肢、`globalThis.__opsPrev` 暗黙依存、setTimeframe monkeypatch＋feature detection、`LAYER_EXTRAS` モード名リテラル（コメントと矛盾）、葉モジュールの `document` 直掴み（非対称）、色検証の乖離済み写し
- 遵守: `createHostView` の Proxy 契約射影（実行時 ISP 強制）/ 能力台帳駆動 dispatch（router は経路名を知らない）/ Python 単一ソースからの生成台帳（tf_ledger_generated）

### 8. tools / .claude/scripts / パッケージ間依存
違反 15（高 11・中 4）。本番隣接表・循環 C-1 は総括参照。
- 高: `sys.path` 副作用依存の裸 import（dashboard_ui）/ core 列挙第 3 の写し（D-11）/ tools⇄simulator 循環 / 別製品 StaticFileServer 直 import（`serve_dashboard.py:30`）/ codescan の言語分岐 seam 迂回 / 検査 C1-C3 固定連鎖 / パイプライン実行器二重実装（D-17）/ stream_loop 7 関心 / 兄弟 CLI の私有名 import＋import 時 I/O / download_oanda_ticks の run() 多責務 / router.py の 2 モジュール同一性
- 中: quality_scope のグローバル破壊的設定、run_quality_gate の具象直 import＋私有関数呼出、`getattr(sys.modules)` 文字列遅延束縛、web/tests の public 迂回（層ガード対象外）
- 遵守: codescan の Protocol+レジストリ / dev_paths.txt 単一源＋機械検定 / common の遅延解決構造

---

## 是正の優先順位（推奨・段階分割）

いずれも読み取り専用精査の結論であり、着手は個別承認後。相互依存があるため順序が重要。

1. **段階 1（実バグ相当・小・可逆）**: D-1（op_log のエンドポイント集合を sw_rewrite と同一源へ）— 現に操作ログが欠落している唯一の「既に壊れている」乖離。
2. **段階 2（単一ソース回復）**: D-2〜D-17 を「生成物 or 単一台帳＋機械的突合検定」パターン（tf_ledger_generated で実証済み）へ順次収束。各件独立・小差分・bit 等価検証可能。
3. **段階 3（循環 4 件の除去）**: C-1〜C-4。共有カーネル（中立層）への所有権移動で消える（C-1=台帳読取の中立化、C-3=起動口の main 層移動、C-4=chrome トークン等の共有置き場新設）。
4. **段階 4（神クラス分割）**: run_backtest / build_interactor / call_binding / reach_sheet_view。相互依存あり（build_interactor 分割は `_EA_FACTORIES` 再設計が前提）。各系統の残存リスク欄に前提条件を記録済み。
5. **段階 5（抽象の宣言化）**: Port 冪等性宣言＋controller 経路の計算量テスト（dashboard F-1）、replay.js の port 宣言、`__getattr__` 委譲 11 件の明示面化、replay_ports の HTTP 語彙除去。

各エージェントの検証記録（prompt-validation-workflow / upstream-input-validation の棄却・撤回履歴を含む）は精査時の会話ログに保存。上流想定のうち「列挙 factory」「計算と I/O 混在」「グローバル結合」「domain→framework 逆流」は実測で棄却されており、これらを前提とした是正計画は立てないこと。
