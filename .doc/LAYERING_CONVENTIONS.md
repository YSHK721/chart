# レイヤ規約（ISSUE-087 🟡-2・2026-07-15 明文化 / ISSUE-092 反映・2026-07-16 更新）

スライスごとにレイヤ構成が異なる現状（replay_ui=5層完備 / indicator_ui api=usecase あり・main なし /
market_profile api=controller+compute+gateway 構成）に対し、**役割の対応関係**を明文化する。
新設スライスは replay_ui 型（domain/usecase/adapter/framework/main）を範とする。

## 役割対応表（既存スライスの読み替え）

| クリーンアーキテクチャ | indicator_ui api | market_profile api | 規約 |
|---|---|---|---|
| Frameworks & Drivers | framework/server.py | （indicator_ui の殻に同居） | HTTP 殻は「クエリ取り出し→handle_x→JSON 送出」のみ。業務分岐・フォールバック判断を書かない |
| Interface Adapters | adapter/controller/* ・adapter/gateway/* | controller/* ・gateway/* | `handle_x(...) -> (status, body)` の**純関数**。検証・翻訳・フォールバック順序はここ。外部データ結線は gateway（下記慣行） |
| Application Business Rules | usecase/*（ISSUE-092 ①で新設・compute_indicators） | （handle_x が兼務） | 業務手順は usecase の純関数へ。Output Boundary（Protocol）は usecase/compute が所有する |
| Enterprise Business Rules | （usecase と同居・指標 src はプラグイン） | compute/* | 統計・集計・グリッド等の純計算。HTTP/クエリ形式を知らない |
| 共有最下層 | marketdata/* ・common/* ・common_view/* | marketdata/* | セッション日・resample・tf メタ・API 規約表の唯一の規則源。上流を import しない。計算カーネル=common・表示定数=common_view（ISSUE-092 ⑥で分離） |

## 依存規則

1. 依存は常に「framework → controller(handle_x) → usecase → compute/domain → marketdata」の内向きのみ。
2. スライス間の裸パッケージ参照（例: market_profile → indicator_ui `adapter`）は禁止。
   共有が必要な純粋物は marketdata（tf_meta / api_contract / session_day / resample）へ降ろす。
   例外はスライスが明示公開する Facade のみ（replay bridge → `adapter/compute/__init__.py`・
   ISSUE-092 ②。内部モジュール（latest_dispatch 等）への直接 import はガードテストで禁止）。
3. Python/JS で同一規則を二重実装する場合、権威は Python とし、JS は
   `tools/gen_js_parity_golden.py` が生成する golden fixture で一致を検定する
   （規則変更 PR は fixture 再生成を必須とする＝`test_js_parity_golden_fresh.py` が強制）。
   指標 param 既定値も同様（権威=`catalog_schema.PARAM_DEFAULTS`・契約=`golden/catalog_defaults.json`・
   front は /catalog 取得＋静的フォールバック。ISSUE-092 ③）。
4. tf メタ（秒長・floor 可否・tick ref）は Python=marketdata/tf_meta.py・JS=domain/tf_meta.js の
   各 1 箇所のみに定義する。UI の時間足ボタン集合もこの集合から乖離させない。

## gateway（外部データ結線）の配置慣行（ISSUE-092 統合レビュー 🔵-1・2026-07-16 明文化）

外部データ具象（marketdata の I/O・キャッシュ永続化）への結線モジュールは gateway と呼び、
**配置はスライスの既存ディレクトリ慣行に従う**（横断で強制統一しない）:

| スライス | gateway 配置 | 例 |
|---|---|---|
| indicator_ui api（adapter/ 配下にネストする慣行） | `api/adapter/gateway/` | `marketdata_dataset.py`（DatasetPort 実装） |
| market_profile api（フラット構成の慣行） | `gateway/` | `marketdata_tick_store.py`・`dwell_rollup_store.py`・`zp_store.py`・`tf_period_disk_cache.py` |

共通規律（配置に依らず不変）:
1. ポート（Protocol）は**内側**（usecase / compute）が所有し、gateway はその実装。
2. 内側は gateway を module-level import しない（未注入時の遅延既定合成のみ許容・
   `tick_store_port.py` / `dataset_port.py` の様式に従う）。
3. 内側→I/O 具象の直結禁止は grep ガードテストで固定する
   （`test_tick_store_port.py`・`test_no_usecase_dependency.py`・`test_store_gateway_layering.py`）。

## import 解決の前提（.pth）と単体実行（ISSUE-092 統合レビュー 🔵-2・2026-07-16 明文化）

- リポジトリ根と `indigators/market_profile/api` は venv の
  `jp225_chart_paths.pth`（`tools/install_dev_paths.py` で登録）が恒久解決する。
  **指標 src（indigators/*/src）・ライブラリモジュールは実行時 sys.path.insert を持たない**
  （ISSUE-092 ⑤で撤去済み）。したがって指標 src の単体実行・REPL からの import は
  **.pth 登録済み venv の python が前提**（未登録 python では `common` / `common_view` /
  `marketdata` が解決しない）。新しい環境では最初に
  `<venv>/bin/python tools/install_dev_paths.py` を 1 回実行する。
- 実行時 insert（自己完結フォールバック）を持ってよいのは entry point のみ:
  server.py・replay bridge・conftest・`__main__` スクリプト・mp_stats/__init__。
- sibling 指標解決（`parents[2]` = indigators/）の insert は .pth の対象外（indigators/ は
  登録しない方針）のため src 内でも温存する（ISSUE-092 ⑤の判定）。

## 残課題（承認待ち・ISSUE-087 🟡-3）

- 実行時 sys.path insert の残存は entry point のフォールバック（server.py / _indicator_ui_bridge.py 等・
  上記「import 解決の前提」参照）のみ（ライブラリ側は ISSUE-087〜092 で撤去済み）。
  正規パッケージ化（pyproject packages）は技術スタック変更＝依頼者承認後に実施する。
