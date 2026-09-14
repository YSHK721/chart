# SOLID 全体監査 — 全 116 件の精査結果（2026-08-05）

アーキテクチャエージェント 7 体が 9 スライス・約 125,000 行に対して挙げた **116 件**の指摘を、
**全件、実コードで裏取り**した記録。エージェント出力をそのまま採用したものは 1 件も無い。

## 検証の定義（重要）

- **事実確認済み** = 「指摘が引用した file:line が、主張どおりの内容である」ことを grep / Read /
  実行で確認したもの。**「修正すべき欠陥である」という意味ではない**。
- 実害（利用者に見える不具合・データ欠損）まで確認したものは「実害確認」と明記する。
- 本監査は**読取と局所実行のみ**。実 UI での再現は ISSUE 化した 5 件を除き未実施。

## 集計

| スライス | 指摘数 | 事実確認済み | 誤り |
|---|---|---|---|
| indicator_ui/api | 21 | 21 | 0 |
| indicator_ui/web | 13 | 12 | **1** |
| market_profile | 19 | 19 | 0 |
| replay_ui | 18 | 18 | 0 |
| 共有核（marketdata/common/common_view/api_shared） | 14 | 14 | 0 |
| unified_ui / tools | 17 | 17 | 0 |
| スライス間境界 | 14 | 14 | 0 |
| **合計** | **116** | **115** | **1** |

**誤りだった 1 件**: 「`js/usecase` に非ソース成果物 137 件」→ 実測 **12 件**（同ディレクトリ直下）。
エージェントがリポジトリ直下の `.playwright-mcp` を合算していた。

**引用の軽微な不正確（主張の substance は成立）**: 3 件。
`set-mode` の送信元は `unified_root.js` でなく `sw_client.js:33`／
`IndicatorStateStore` の host 依存メンバー数 20 に対し実測 31（過小申告）／
`marketdata.api_contract` の本番参照は 0 でなく 1。

## 実害まで確認したもの（5 件・ISSUE 化済み）

| ID | 内容 | 状態 |
|---|---|---|
| ISSUE-257 | `/live_ticks` の同時要求が無制限に積み上がる | RESOLVED |
| ISSUE-258 | 全件 rewrite が up/dn 列を落とす（**実行で証明**） | RESOLVED |
| ISSUE-259 | MP 3 経路が単一ワーカー内で HTTP 応答を書く | OPEN |
| ISSUE-260 | `va` 設定が 2 経路に届かず 0.70 固定（効かないツマミ） | OPEN |
| ISSUE-261 | 時間足台帳の第 2 定義（7 箇所） | 一部 RESOLVED |

## 分類（事実確認済み 115 件）

### A. 未起票の実害候補（10 件・要判断）
1. **VA アルゴリズムが src で異なる**: `/tf_period_profile` の `va_low/va_high` は
   src 省略時 `_value_area_sparse`（連続 VA）、`src=zp` 時 `_value_area`（非連続集合の min/max）。
   フロントは src 非依存で同じフィールドを読む（`tf_period_columns.py:165,225`）。
2. **`sessions[].date` の日境界が src で異なる**: `market_profile.py:95` のみ UTC 暦日、
   他 3 src はセッション日。現行 UI からは `src=candle` に到達しないが HTTP 直叩きでは到達する。
3. **`compute_id` 別名がスレッド親和宣言を迂回**（`server.py:265`）。rpy2/R がプールに載る。
   実クライアントは `indicatorId` のみ送出のため**潜在**。
4. **SW のモード権威が揮発変数**（`sw.js:13`）。SW 回収時にリプレイ中でもライブ core へ流れる。
   実ブラウザでの再現は未実施。
5. **形成中バーの畳み込みが 4 実装**（replay.js / forming_plan.js / live_tick_player.js /
   serve_live_tick_tails.py）。ずれると「ローソクと指標が別の値」になる（ISSUE-232 で発生済み）。
6. **mid/tz 正規化が 3 実装**（tick_m1.py:80 / MP gateway:80 / tools）。MP の価格と 1 分足が別コード。
7. **生 tick 列定義が 3 実装**（ingest_ticks / live_tick_watch:282 / verify_tick_immutability:35）。
8. **tick tree レイアウトが 3 実装**（tick_m1 が「単一権威」と宣言しているが走査側が未移行）。
9. **`_tail_points` が 5 実装で既に乖離**（3 つは `int(times[i])`、profit_rsi のみ `_to_unix_seconds`）。
10. **`stream_loop` の起動シーケンスが無防御**（`live_tick_watch.py:363-374` が try の外）。
    ISSUE-252 の実障害（watch プロセス死）と同じ構造。

### B. 設計負債（動作は正しいが変更が局所化しない・約 45 件）
god object（`replay.js` の `setupReplay` 799 行 28 関数 31 let／`call_binding.py` 813 行／
`server.py` 535 行 6 役／`market_profile_primitive.js` 616 行 3 モード）、
host 全渡し（協働子が host private 20〜31 メンバーへ依存）、
太いインターフェース（`ChartRenderer` public 53・`typeof` ガード 12）、
合成根の二重化（live 766 行 / replay 360 行・既に 5 機能が乖離）、
Port に HTTP status 露出、MP 11 パラメータが 6 層素通し、など。

### C. 宣言と実装の乖離（8 件）
`chart_renderer.js:4-7`「lwc を呼ぶのは本ファイルだけ・grep 0 件強制」→ **11 ファイルが呼び、強制テストは不在**／
`series_render_router.js:43`「台帳追記で完結」→ **4 ファイル改変が必要**／
`tools/__init__.py`「ロジック重複を持たない合成点」→ **5 種の重複**／
`market_profile_actor.js:266`「TF_BAR_SEC 二重宣言で bundle 破損するから複製する」→
**宣言は tf_meta.js の 1 箇所のみで前提が不成立**／`resample.py:8`「pandas のみに依存」→ csv_schema にも依存、など。

### D. 衛生（約 30 件）
未使用 import（`forming_bar.py` の os/time/_sys ほか）、docstring の陳腐化、
死にコード（`view_state.js` は本番参照 0・`replay.js:36` の 30m プリセットは到達不能）、
`js/usecase` 直下の非ソース 12 件、`sys.path.insert` 7 ファイル残存、など。

### E. 違反ではないもの（2 件）
Port を plain dict に保つことによる DataFrame 変換コスト（エージェント自身が
「違反というより設計上の帰結」と記載）。`common/api_shared` の中立配置。

## 構造的な結論

115 件が事実として成立する一方、**依存方向の逆流は Python 全スライスで 0 件**であり、
骨格は健全である。壊れているのは骨格ではなく**規律の締め方**で、
C（宣言と実装の乖離）8 件がそれを最も端的に示す。

過去の SOLID 是正（ISSUE-087/094/133/134/155/156/179/183/254 …）は実際に行われていた。
効かなかったのは、多くが**「コメントに正しいことを書く」「ISSUE を RESOLVED にする」で終わり、
ずれたときに落ちる検定を残さなかった**こと。時間足台帳だけは生成物＋双方向 parity 検定を
持っており、そこは実際に守られていた。差はそれだけである。
