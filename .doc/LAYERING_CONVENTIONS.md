# レイヤ規約（ISSUE-087 🟡-2・2026-07-15 明文化）

スライスごとにレイヤ構成が異なる現状（replay_ui=5層完備 / indicator_ui api=usecase 欠落 /
market_profile api=controller+compute の2層）に対し、**役割の対応関係**を明文化する。
新設スライスは replay_ui 型（domain/usecase/adapter/framework/main）を範とする。

## 役割対応表（既存スライスの読み替え）

| クリーンアーキテクチャ | indicator_ui api | market_profile api | 規約 |
|---|---|---|---|
| Frameworks & Drivers | framework/server.py | （indicator_ui の殻に同居） | HTTP 殻は「クエリ取り出し→handle_x→JSON 送出」のみ。業務分岐・フォールバック判断を書かない |
| Interface Adapters | adapter/controller/* | controller/* | `handle_x(...) -> (status, body)` の**純関数**。検証・翻訳・フォールバック順序はここ |
| Application Business Rules | （handle_x が兼務） | （handle_x が兼務） | usecase 層を新設する場合は handle_x から手順部分を抽出する |
| Enterprise Business Rules | domain/* | compute/* | 統計・集計・グリッド等の純計算。HTTP/クエリ形式を知らない |
| 共有最下層 | marketdata/* | marketdata/* | セッション日・resample・tf メタ・API 規約表の唯一の規則源。上流を import しない |

## 依存規則

1. 依存は常に「framework → controller(handle_x) → compute/domain → marketdata」の内向きのみ。
2. スライス間の裸パッケージ参照（例: market_profile → indicator_ui `adapter`）は禁止。
   共有が必要な純粋物は marketdata（tf_meta / api_contract / session_day / resample）へ降ろす。
3. Python/JS で同一規則を二重実装する場合、権威は Python とし、JS は
   `tools/gen_js_parity_golden.py` が生成する golden fixture で一致を検定する
   （規則変更 PR は fixture 再生成を必須とする＝`test_js_parity_golden_fresh.py` が強制）。
4. tf メタ（秒長・floor 可否・tick ref）は Python=marketdata/tf_meta.py・JS=domain/tf_meta.js の
   各 1 箇所のみに定義する。UI の時間足ボタン集合もこの集合から乖離させない。

## 残課題（承認待ち・ISSUE-087 🟡-3）

- sys.path 実行時 insert（server.py / _indicator_ui_bridge.py / dataset.py の 3 系統）の
  正規パッケージ化（pyproject packages）は技術スタック変更＝依頼者承認後に実施する。
