# 上流入力前提検証結果（indicator-ui B方式: Refactor + HTTPサーバ本体/静的配信/起動/フロント配線切替）

## 上流入力の整理
- 依頼者指示：あり（(1)controller/JS の機構重複 Refactor 振る舞い不変、(2)stdlib のみで HTTP サーバ本体 `api/framework/server.py`（POST /compute・GET /candles・web/ 静的 same-origin 配信・パストラバーサル防止）＋起動、フロント配線を served 時 B方式（ComputeHttpClient + GET /candles）へ切替・file:// 時 A方式フォールバック、A方式注記の出し分け。全テスト Python113/JS162 緑維持・push しない）
- 他者レビュー指摘：該当なし
- 前段成果物：内部設計書.md（§2 framework 構造・§3.3.5 ComputeController/ComputeHttpClient・§6.3 nested エラースキーマ・§6.6 generation・§7.3 セキュリティ）、内部設計_パラメータ設定ダイアログ.md（§9 B方式実反映）、既存実装一式（handle_compute / ComputeHttpClient / IndicatorComputeAdapter / フロント）
- 既存合意の引き継ぎ：feature/indicator-ui の Python113/JS162 緑（触らない）、upstream JS API は chart_renderer.js のみ（grep 0 件）

## 前提抽出
- P1：`handle_compute(body)->(status,dict)` は HTTP 殻非依存の純関数（サーバ本体はこれを呼ぶ薄殻）
- P2：`/compute` 応答は nested error ボディ `{ok,generation,error:{type,message,violations}}`（§6.3.4）
- P3：`/candles?datasetRef=sample` は同一ホワイトリスト CSV から candles JSON を返す。time は UNIX 秒（解像度非依存 `int(pd.Timestamp(v).timestamp())`）
- P4：web/ の ES Modules は http:// same-origin 配信ならブラウザがそのまま読める（CORS/バンドル不要）
- P5：パストラバーサル防止＝配信ルートを web/ に限定、正規化後ルート外は 404
- P6：served 判定は `location.protocol`（http/https → B方式、file: → A方式）
- P7：generation 競合破棄（accepts）は facade.recompute に既存集約済み（壊さない）
- P8：A方式注記 `prop-a-method-note` は served（B方式）で非表示、file:// A方式でのみ表示
- P9：Refactor 対象重複は実在（controller の loader 読み込み／call_binding の `_load_src_package` 機構、error_type→status 二重定義、JS 2 種 ComputeError）
- P10：stdlib のみ・新規依存禁止（http.server / json / pathlib / urllib で実装可能）

## 証拠先行検証
- P1 → Read compute_controller.py:91-134（`handle_compute(body,*,adapter)->tuple[int,dict]`・ソケット非依存）。実証取得
- P2 → Read compute_controller.py:81-88（`_error_body` が nested `{ok,generation,error:{type,message,violations}}`）。実証取得
- P3 → Read fake_chart.py:19-20（`int(pd.Timestamp(value).timestamp())`＝解像度非依存変換の正典）。Bash head CSV `,date,open,high,low,close,volume`。実証取得
- P4 → index.html:63-72（`<script type="module">` で composition_root_front を import）。http:// 配信で解決可能。実証取得
- P5 → 基本設計書.md §7.3（ホワイトリスト・生パス直送禁止）。実証取得（静的配信ルート限定で同方針）
- P6 → composition_root_front.js:20-44（bootstrap で SAMPLE_DATA + EmbeddedComputeGateway を固定注入。protocol 分岐は未実装＝切替が本タスク）。実証取得
- P7 → facade.js:131-156（recompute が next_generation→compute→accepts で破棄）。実証取得
- P8 → properties_dialog.js:24-26,615-622（A_METHOD_NOTE/`prop-a-method-note` 固定生成。出し分け未実装＝本タスク）。実証取得
- P9 → compute_controller.py:67-78（`_load_loader` importlib）vs call_binding.py:36-62（`_load_src_package` importlib）＝機構重複。compute_controller.py:46-53 `_ERROR_STATUS` ＝ status 表が controller 側にある（adapter は error_type のみ保持）。compute_http_client.js:15-21 と embedded_compute_gateway.js:10-16 ＝ ComputeError 2 定義。実証取得
- P10 → Python 3.13 stdlib に http.server/json/pathlib/urllib 在中。実証取得
- ベースライン → Bash：`node --test 'tests/**/*.test.js'` 162 pass、`lwc/.venv/bin/python -m pytest -q` 113 pass。実証取得

## 判定結果
- 指示（handle_compute を呼ぶ薄殻サーバ・stdlib のみ）：**採用**（P1/P10 実証取得）
- 指示（nested エラーボディ・例外時も nested）：**採用**（P2 実証取得・handle_compute を流用、殻の例外は server 側で nested 包装）
- 指示（/candles 解像度非依存変換・未知 datasetRef 400）：**採用**（P3 実証取得・fake_chart の変換式と同一式を使用）
- 指示（web/ same-origin 静的配信・traversal 404）：**採用**（P4/P5 実証取得）
- 指示（served→B方式 / file://→A方式フォールバック、protocol 判定）：**採用**（P6 実証取得・bootstrap に分岐追加）
- 指示（A方式注記 served 非表示・file:// 表示、サイレント化しない）：**採用**（P8 実証取得・PropertiesDialog に mode フラグ注入）
- 指示（Refactor 振る舞い不変・テスト緑維持）：**採用**（P9 実証取得・重複実在）
- 指示（generation 破棄維持）：**採用**（P7 実証取得・facade 不接触）

## 残存リスク
- error.type→status の単一定義化（P9）は adapter/controller 間でモジュール参照方向に注意（domain への集約は依存方向 domain←adapter を逆転させない範囲で実施。adapter.compute 内へ status 表を置き controller が参照する形に限定）。
- /candles と /compute の時間軸整合：served 時は両者とも full CSV（whitelist 解決）由来で揃う。SAMPLE_DATA（A方式の別スライス）は file:// 専用に残し、混在させない。
- サーバ殻の統合テストは socket スモークに留める（純ロジックは handle_compute で網羅済み）。実通信 param 反映は curl で実証（手動・本タスクで実施）。
- 本番デプロイ/認証/ペイン分割/range スライス/Q-1..7 は対象外（実装しない）。
