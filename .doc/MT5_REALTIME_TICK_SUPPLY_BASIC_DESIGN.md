# MT5 リアルタイムティック増分供給 基本設計書（ISSUE-447 段階 1・再定義版）

- 作成: 2026-09-01（architecture-executor 工程 2 成果物）
- 依頼者裁定: バッチとリアルタイムを分けず「最後に保存したティック以降の増分を引き続ける」単一供給ループとする（2026-09-01 承認）。最終ゴールは **MT5 のリアルタイムデータをチャート（統合 UI 8000 ライブ側）で受信・表示するまで**の端から端の結線。発注 API は射程外。
- 確定原則（変更不可）: **VM 側は定義を持たない**。tick 木レイアウト・列定義・時刻変換（DST）の知識を VM 側ファイルに置かない。定義を要する処理はコンテナ側が既存権威（`marketdata.tick_m1` 等）を import して行う。
- 制約: 新規ファイルのみ（既存ファイル改変は §13 承認事項として分離）・既存データ改変 0・ライブラリ追加禁止・認証必須（ISSUE-446 段階 2 制約 5 点）・計算量テスト絶対命令。

## 1. 実読済みの根拠（設計の拘束条件）

ISSUE-447（T1〜T12・12h ずれ罠・COPY_TICKS_INFO 一択・DST 冬 UTC+2/夏 UTC+3）、ISSUE-446（橋渡し 5 制約・コンテナ→VM 172.16.162.129 疎通・送信元 172.16.162.1）、`tools/capture_mt5_symbol_spec.py`（VM 単体配布の規律）、`tools/live_tick_watch.py`（様式は踏襲・記憶域方式は棄却＝毎ポーリング当日全量再直列化のため）、`marketdata/tick_m1.py`（木レイアウト単一権威・`_TICK_COLUMNS` 3 列。`append_m1_from_ticks` は当日累積比例のため当日 M1 化には使わない）、既存単一権威検定 2 本（`test_tick_tree_layout_authority.py` は marketdata/tools/… を rglob、`test_tools_composition_declaration.py` は tools/*.py 非再帰）、`scratchpad/probe_mt5_ticks.py`（structured array・time_msc・tobytes()）。

前提検証の要点:
- P4 棄却: `live_tick_watch.py:392-399` は rows 到着ごとに当日全量 concat→再直列化＝CX-d 違反の実例。ジャーナル追記＋日次確定を新設する。
- P5 棄却: `tick_m1.append_m1_from_ticks`（:443-465）は当日 parquet を丸ごと再計算＝当日 M1 化には使えない。新着分のみ畳む `m1_chain` を新設。
- P6 棄却: 表示は「追加のみ」で到達しない（§13 A-1〜A-3）。

## 2. アクター×責務マトリクス（SRP・アクター単位）

| アクター | 関心事 | モジュール |
|---|---|---|
| A ブローカー（OANDA） | サーバ時刻ラベルと DST 規則 | `marketdata/mt5_ticks/server_clock.py`（純粋） |
| B 供給連続性の運用者 | 増分カーソル・境界 ms・再開点 | `marketdata/mt5_ticks/cursor.py`（純粋） |
| C MT5 端末 | API の呼び方・戻り形 | `tools/mt5_tick_feed.py`（VM 側単体） |
| D セキュリティ運用 | 認証・bind・転送契約 | `marketdata/mt5_ticks/wire.py`（純粋）＋ VM 側 |
| E marketdata 台帳所有者 | 列・木配置・M1 規約 | `journal.py` / `ingest.py` / `m1_chain.py`（既存権威へ委譲） |
| F 運用者 | 起動・停止・周期 | `tools/mt5_tick_watch.py`（Composition Root） |
| G チャート UI | データセット選択・描画 | 表示結線（§13 承認事項） |

## 3. コンポーネント構成（新規ファイルのみ）

| パス | 層 | 責務 |
|---|---|---|
| `tools/mt5_tick_feed.py` | framework（VM 単体配布） | MT5 読み取り＋生応答 HTTP 配信。定義を持たない |
| `marketdata/mt5_ticks/__init__.py` | — | 依存宣言 docstring |
| `marketdata/mt5_ticks/server_clock.py` | domain (A) | サーバラベル→UTC（依存ゼロ） |
| `marketdata/mt5_ticks/cursor.py` | domain (B) | 増分カーソル規約（依存ゼロ） |
| `marketdata/mt5_ticks/wire.py` | domain (D) | 転送契約・HMAC 署名正準化・応答検証（依存ゼロ） |
| `marketdata/mt5_ticks/port.py` | usecase | `IncrementalTickSource` Protocol・例外型 |
| `marketdata/mt5_ticks/journal.py` | adapter (E) | 追記記録・原子確定。パスは `tick_m1.day_parquet_path` から派生 |
| `marketdata/mt5_ticks/ingest.py` | adapter (E) | 検証・UTC 変換・日分割・列整形（列は `tick_m1` から import） |
| `marketdata/mt5_ticks/m1_chain.py` | adapter (E) | 閉じた分のみの M1 追記＋rollup 差分更新 |
| `marketdata/mt5_ticks/usecases.py` | usecase | UC-01 PollOnce / UC-02 FinalizeDay / UC-03 PublishDataset / UC-04 RestoreCursor |
| `marketdata/mt5_ticks/http_source.py` | framework | 実 HTTP（urllib のみ） |
| `marketdata/mt5_ticks/fakes.py` | test support | Fake / Spy（本番から import されない） |
| `tools/mt5_tick_watch.py` | main (F) | CLI・常駐ループ・DI 組み立て |
| テスト | `marketdata/tests/test_mt5_*.py`（7 本）・`tools/tests/test_mt5_tick_feed.py`・`tools/tests/test_mt5_tick_watch.py` | §10 |

import ルール（新規検定 `test_mt5_module_dependency_declarations.py` で AST 施行）: `server_clock`/`cursor`/`wire`/`port` → stdlib のみ／`journal`/`ingest`/`m1_chain` → pandas＋`marketdata.tick_m1`＋`marketdata.rollup`＋同パッケージ下位／`usecases` → 同パッケージのみ／`http_source` → stdlib＋`wire`/`port`／`tools/mt5_tick_watch.py` → 全層。規則を tools に置かない（`test_tools_composition_declaration` の非再帰の穴を突かない）。

DIP 適用点: `IncrementalTickSource`（http/fake/spy の 3 実装）と `Clock`（now 注入）。他の永続化ポートは YAGNI で置かない（monkeypatch 差替＝既存様式）。

## 4. 主要契約

### server_clock（A・純粋）
- `offset_seconds(server_label_ms:int)->int`（7200 or 10800。EU DST 算術規則: 3 月最終日曜 01:00Z〜10 月最終日曜 01:00Z が夏。**規則自体は仮説・V-3 で確定**。固定点 T1b: 2020-12/2021-01/2026-01=+2h、2021-04/2026-08=+3h）
- `to_utc_ms(server_label_ms)->int` = `server_label_ms - offset*1000`。逆変換（UTC→ラベル）は多価のため**実装しない**。
- `utc_day_of(server_label_ms)->date` / `is_dst_transition_day(day)->bool`（記録のみ・緩和しない）

### cursor（B・純粋）
- `Cursor(cursor_ms:int, boundary_rows:tuple[(ms,bid,ask),...])`＝永続化済み最大 ms とその ms の全行。
- `request_window(cursor)->(cursor_ms, None)`：**下端は含む**（同一 ms 複数ティックのため境界 1 ms 再取得は正しさに必要な入力）。
- `absorb(cursor, rows)->{new_rows, dropped, next_cursor}`：rows 昇順・`rows[0].ms>=cursor_ms` 必須。`ms==cursor_ms` の先頭 `len(boundary_rows)` 行が**値まで一致**した場合のみ落とす（不一致は `CursorContractError`＝Fail-Stop。同一 ms 内順序の安定性は未検証 V-2）。dropped ≤ 1 ms 内ティック数（セッション長・回数に非比例）。
- `from_journal_tail(tail)->Cursor`：復元の唯一経路（ジャーナルが正）。
- コールドスタートは `--from` 必須。暗黙既定（now−30 分等）を作らない。cursor_ms は単調非減少・巻き戻し禁止。

### wire（D・純粋）— 転送プロトコル
- 要求: `GET /ticks?symbol=&from_msc=&to_msc=&max_rows=`＋`Authorization: MT5B1 key=,ts=,nonce=,sig=`（HMAC-SHA256。正準文字列 `"GET\n/ticks\n<sorted-query>\n<ts>\n<nonce>"`。ts 差>120s・nonce 再使用・不一致は 401）。
- 成功応答: `200 application/octet-stream`＋`X-MT5-Count`/`X-MT5-Dtype`（numpy dtype.descr JSON）/`X-MT5-Latest-Msc`/`X-MT5-Truncated`/`X-MT5-Server`（account_info().server 生値）。body=`ndarray.tobytes()` 無加工。
- エラー: `auth`401（SupplyUnavailable・バックオフ）/`terminal`502（last_error 添付）/`argument`400（Fail-Stop・再試行しない）/`busy`429。
- `parse_response`：Count×itemsize と body 長の整合・`time_msc`/`bid`/`ask` の存在・Latest-Msc 必須。違反は `WireError`。

### mt5_tick_feed（C・VM 単体配布）
- API 許可集合（AST 施行）: initialize/shutdown/last_error/version/terminal_info/symbol_select/symbol_info/symbol_info_tick/account_info/copy_ticks_range/copy_ticks_from/COPY_TICKS_INFO。`order_*` 参照 0。トップレベル `import MetaTrader5` 0。
- エンドポイントは `/ticks` と `/health` の 2 本のみ。stdlib `http.server` 単一スレッド。特定 IF bind（既定 `172.16.162.129:8771`・`0.0.0.0` 禁止）。秘密は環境変数のみ。ファイル配信機構を使わない。
- epoch(server label int)→MT5 が要求する datetime への変換は `read_tick_window` 1 関数のみが知る（12h ずれ罠の閉じ込め・V-1 で確定）。
  ※当初案の関数名 `read_ticks` は market_profile 既存宣言との全域名衝突で C1 を誘発したため改名（2026-09-01 実測）。
- 持たないもの（機械検査）: `"ticks"`/`YYYY/MM/DD`/`_ticks.parquet`/`bidPrice`/`askPrice`/`timestamp` 列名/DST・UTC 変換/配信ディレクトリ。
- 直列化は `tobytes()`+`dtype.descr`（`.npy` は決定性未検証のため使わない）。

### journal / ingest / m1_chain（E）
- `journal_path(day,*,symbol,data_dir)` = `tick_m1.day_parquet_path(...).with_suffix(".ndjson")`（派生 1 箇所・自前レイアウト禁止）。
- `append` は 1 回 write+flush+fsync＝O(新着)。末尾 torn 行は読み手が捨てる。**ジャーナルは消さない**。
- `finalize(day)`：全行→DataFrame（列は `tick_m1._TICK_COLUMNS` import・dtype は timestamp=datetime64[ms, UTC]/float64＝`test_live_tick_watch.py:268-273` と同一）→tmp→`os.replace`。1 UTC 日 1 回。既存と内容一致なら書かない（unchanged）。0 行かつ走査済の日のみ `.empty`。
- `validate`：非単調・窓外・ask<bid・bid<=0・dtype 不一致 → `Mt5SupplyError`（部分書込を残さない）。
- `m1_chain.append_m1_for_closed_minutes`：**新着分のみ** `tick_m1.ticks_to_m1` で畳み、`until` で形成中分を除外、書式は `tick_m1._format_m1_for_csv` を import（private 依存は A-5 の承認まで M-3 検定で防波堤）。`update_rollups`：`marketdata.rollup.incremental_update`（`rollups/<ref>/`・ref_prefix=ref）。
- SymbolToken: `token_for(symbol, server)` = `capture_mt5_symbol_spec.sanitize_path_component` を **import**（ミラー実装禁止）。例 `JP225@OANDA-Japan-MT5-Live`。VM 側はトークンを作らない。

### mt5_tick_watch（F・Composition Root）
- 引数: `--symbol`(JP225)/`--endpoint`(http://172.16.162.129:8771)/`--key-id`/`--interval`(既定 5.0・下限 2.0)/`--data-dir`/`--ref`(既定 jp225_mt5)/`--from`(コールドスタート必須)/`--once`/`--no-publish`/`--quiet`。秘密は `MT5_BRIDGE_SECRET` 環境変数のみ。
- 1 周期 = fetch 1 回 → absorb → journal 追記 →（分が閉じたら）M1/rollup →（日が変わったら）finalize。失敗は指数バックオフ（→×2・上限 60s・連続 8 回で 600s ブレーカ）。

## 5. 記憶域の増分構造

| 段 | 実体 | 書込コスト | 原子性 |
|---|---|---|---|
| 受信 | `<木>/<token>_ticks.ndjson` | O(新着) | 追記のみ・torn 行は読み手が捨てる |
| 表示 | `<DATA_DIR>/jp225_mt5_m1.csv` | O(閉じた分のティック数) | 1 回 write |
| 表示 | `rollups/jp225_mt5/…` | O(新 M1 行) | 既存 rollup の原子化 |
| 確定 | `<木>/<token>_ticks.parquet` | 1 UTC 日 1 回 | tmp→os.replace |

日跨ぎ: `split_by_utc_day` で昇順分割。日 D の確定は「D+1 の行を観測」or「now≥D+1+300s」。

## 6. テスト設計（引き渡し・アサーション後工程で変更禁止）

### 正常系
- N-1 `to_utc_ms`：冬 3 点（2020-12/2021-01/2026-01 の T1b 先頭行）＝+2h、夏 2 点（2021-04/2026-08）＝+3h に一致（実測値を固定点にする）
- N-2 `absorb`：新着のみ返り、`next_cursor` が最終行を指す
- N-3 1 応答 → ジャーナル追記 → `finalize` → parquet の列・dtype が `datetime64[ms, UTC]`＋float64（`test_live_tick_watch.py:268-273` と同一主張）
- N-4 `finalize` 後に `tick_m1.day_parquet_files(symbol=token)` が当該日を列挙する
- N-5 分確定 → M1 CSV 追記 → rollup 差分更新 → `dataset.load_candles("jp225_mt5","5m",10)` が 10 本返す（registry 未登録の間は同等の直接読込で検証）
- N-6 署名往復：`wire.sign` → VM 側 `verify` が通る

### 異常系（すべて Fail-Stop・書込 0）
- E-1 非単調 `time_msc` → `Mt5SupplyError`・ジャーナル未生成
- E-2 窓外の行（`ms < from_msc`）→ 同上
- E-3 `ask < bid` / `bid <= 0` / NaN → 同上
- E-4 dtype に `time_msc` が無い → `WireError`
- E-5 `X-MT5-Count` と body 長の不整合 → `WireError`
- E-6 境界行の値不一致 → `CursorContractError`
- E-7 署名不正 / ts 120 秒超 / nonce 再使用 → 401・`SupplyUnavailable`・書込 0
- E-8 端末 None（`copy_ticks_*` が None）→ 502・書込 0・`last_error` が detail に載る
- E-9 ジャーナル末尾 torn → 末尾行を捨てて復元成功（例外にしない）
- E-10 コールドスタートで `--from` 未指定 → 非 0 終了・書込 0

### 境界
- B-1 同一 ms に 3 行 → 境界再取得で 3 行落ち、新着だけ残る
- B-2 UTC 日境界を跨ぐ 1 応答 → 2 日のジャーナルへ昇順で分かれる
- B-3 DST 切替日（3 月/10 月最終日曜）の変換：切替前後で UTC が単調
- B-4 応答 0 行・`truncated=1`・`max_rows` ちょうどの 3 ケース
- B-5 週末（0 行の UTC 日）→ `.empty` 1 個・parquet 無し

### AST/機械検査（`tools/mt5_tick_feed.py` ほか）
- A-1 トップレベル `import MetaTrader5` が 0
- A-2 `order_` で始まる属性参照が 0
- A-3 `mt5.*` 属性参照が許可集合（§4 の一覧）の部分集合
- A-4 `"ticks"`・`%Y`+`%m`・`*_ticks.parquet`・`bidPrice`・`askPrice` リテラルが 0（既存 2 検定と同じ正規表現の独立検定。既存検定は無改変）
- A-5 ファイル配信（`SimpleHTTPRequestHandler`/`translate_path`）不使用
- A-6 `--help` が MetaTrader5 不在環境で returncode 0（subprocess 実測）
- A-7 `marketdata/mt5_ticks/*.py` の宣言依存と実 import の一致（既存 `test_module_dependency_declarations.py` と同方式の新設検定）
- A-8 秘密リテラル 0（`MT5_BRIDGE_SECRET` 以外の 32 文字以上 hex リテラル 0）

### ミラー／単一権威
- M-1 sanitize は `capture_mt5_symbol_spec.sanitize_path_component` の呼び出し（`_SAFE_CHARS` 相当の別実装が無いことを AST 確認）
- M-2 ジャーナル/parquet パスが `tick_m1.day_parquet_path` から派生（monkeypatch 反映で実証）
- M-3 M1 CSV 追記の byte 出力が `tick_m1._format_m1_for_csv` と一致
- M-4 ジャーナル畳み（intraday）の M1 == 確定 parquet からの M1（同値性）
- M-5 既存 2 検定（layout authority / tools composition）が緑のまま
- M-6 Dukascopy 非干渉：着手前後で `day_parquet_files(symbol="JP225")` の列挙が完全一致

### 計算量スパイ（Test Spy・発行−使用=0）

- **CX-a** 受信行−保存行==境界 ms 重複行数、境界重複はポーリング回数・セッション長に非比例（2×2 点で「増加なし」を固定。回数を焼き込まない）
- **CX-b** 新着 0 の周期で journal/parquet/M1/rollup/cursor の全書込 0（finalize の内容一致時も 0）
- **CX-c** 1 ポーリングの fetch 発行数がカーソル位置・保存済み日数に非依存（2×2 点）
- **CX-d** 直列化行数が新着 k に比例し当日累積に非比例（累積 1,000/100,000 の 2 点＋k を 2 倍で 2 倍）。finalize は 1 日 1 回
- **CX-e** Fail-Stop 経路（E-1〜E-8）で全 writer 呼出 0
- **CX-f** M1 畳みへ渡る行数==閉じた分のティック数（`ticks_to_m1` Spy・2 点。既存 `append_m1_from_ticks` を使うと赤になる主張）

## 7. 端から端の検証点（ISSUE-291 規約）

H1 MT5→VM feed（V-1〜V-4・/health）／H2 VM→コンテナ（署名 curl 200・鍵無し 401・送信元限定）／H3 wire→ingest（E 系・CX-e）／H4 →ジャーナル（1 営業日 ≈123,110 行 ±1%）／H5 →日 parquet（M-4 同値・T4b bit 一致）／H6 →M1（M-4・CX-f・N-5）／H7 →rollup（実在・増加）／H8 →core 配信（`/candles?datasetRef=jp225_mt5` 200・A-1 承認後）／H9 →front 表示（**実 UI**・A-1〜A-3 承認後）／H10 常設運用（serve.sh・Ctrl-C 停止・A-4 承認後）。

## 8. 実装着手順序

1. domain（server_clock/cursor/wire）＋検定 → 2. port/fakes/usecases＋CX 検定 → 3. journal/ingest/FinalizeDay＋同値検定 → 4. VM 側 feed＋AST 検定 → **5. VM 実測 V-1〜V-5（通過まで実端末へ向けない）** → 6. http_source＋watch＋実 HTTP 疎通 → 7. m1_chain/PublishDataset＋rollup 実測 → 8. 表示結線（A-1〜A-3 承認後）。

## 9. VM 実測項目（V）と承認事項（A）

| # | 未検証事項 | 測り方 |
|---|---|---|
| V-1 | copy_ticks_range/from の意味論（端点包含・datetime 解釈 3 通り） | 既知窓で 3 方式比較 |
| V-2 | 同一 ms 内の返却順序安定性 | 同一窓 3 回取得の bit 比較 |
| V-3 | DST 切替日の time_msc 挙動 | 3 月/10 月最終日曜 24h の単調性検査 |
| V-4 | tobytes()+dtype.descr 往復一致 | VM/コンテナ双方 sha256 |
| V-5 | ポーリング周期下限と端末負荷 | 5/3/2 秒×30 分・失敗率 0・所要<周期 50% |
| V-6 | zoneinfo/tzdata 実在 | 双方で import 実測（既定は算術規則） |
| V-7 | 3 列 parquet の往復 | 書き→`columns=_TICK_COLUMNS` 読み |

| # | 承認事項 | 最小改変案 | 裁定（2026-09-01 依頼者） |
|---|---|---|---|
| A-1 | `dataset_registry.py` へ `jp225_mt5` 1 エントリ追加（既存改変） | `tick=False` で足内更新経路に触れない。1 行削除で可逆 | **承認** |
| A-2 | `symbol_spec_generated.js` 再生成（A-1 と同時必須） | 生成器実行のみ・手編集しない | **承認** |
| A-3 | front の ref 選択（**UI 変更**） | **案 U1 で承認**: `datasetRef` を URL クエリで上書き（既定挙動不変）。案 U2（セレクタ UI）は不採用 | **承認（U1）** |
| A-4 | serve.sh へ常駐ループ組込 | 既存 `--stream` と同型 1 ブロック＋trap 追記 | **承認** |
| A-5 | `tick_m1` へ M1 追記公開 API 追加 | private 依存の恒久解消（追加のみ） | **承認** |
| A-6 | 足内更新（forming_bar）の MT5 対応 | 別段階（本段階は tick=False で回避） | 未裁定（別段階） |

ref 名 `jp225_mt5` は仮（A-1 と同時確定）。ISSUE-448 の裁定（置換/併存）は本設計に影響しない（別 ref・別トークンで完全同居）。

## 10. M1 浄化の裁定（2026-09-01・依頼者 y/n）

日内増分 M1 には日次統計を要する外れ値除去 `_clean_m1_day`（ISSUE-107）が原理的に適用できない
（TDD 工程で実測: 外れ値日で増分 10 バー対 全量 8 バー。清浄日はバイト一致＝M-4）。

**裁定: 日次確定時に再構築（案 b）**。日中は暫定値として表示し、UTC 日が閉じた時点
（FinalizeDay 後）に権威経路（`_clean_m1_day` あり）で当日分の M1 を再計算し、
既存追記分と**差分がある日だけ**是正する。清浄日は書込 0（CX-b 整合・Spy で固定）。
確定記録は既存権威（全量経路）と完全一致する。

**実装形（2026-09-01・TDD 工程の実測による条件付き採用）**: ロールアップの是正は
`rollup.incremental_update` では不可能（合流が high=max/volume=sum の合算で、除去された
分バーを消せない＝rollup.py:596-599 実測。区間置換の公開 API も無い）。よって是正が要る日だけ
権威 `stream_build` で当該 ref 配下（M1＋rollups）を再生成する（`marketdata/mt5_ticks/rebuild.py`）。
代償は外れ値日のみ O(M1 全体)。清浄日（大多数）は書込 0。
