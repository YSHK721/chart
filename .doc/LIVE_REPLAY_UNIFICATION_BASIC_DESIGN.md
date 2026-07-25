# ライブ / リプレイ 一本化（ルータ方式）基本設計

- ブランチ: `feature/live-replay-unify-router`
- 開発フロー: 6フェーズ（git → architecture → tdd → programmer → code-review → git）
- **絶対制約（依頼者厳命）**: この仕様追加の変更が**その他モジュールへ波及することを一切許可しない**。
  → 既存モジュール（`indigators/indicator_ui/**`・`simulator/replay_ui/**`・共有JS・`marketdata`・`simulator` core）は **byte 不変**。実装は **新規ファイルのみ**（ISOOS 最適化設計と同型の「committed 資産無改変・新規ファイル追加」パターン）。

---

## 1. 目的と方式

**目的**: 公開 URL を 8000 単一化し、同一ページ上でライブ⇄リプレイを**再ナビゲーションなしで切替**（状態保持）。かつライブ本番の障害/負荷分離を維持する。

**方式**: 薄いルータ（リバースプロキシ）を新設し、公開 8000 の内側で **2つの既存 core を別プロセスのまま維持**する。

```
[ブラウザ] ─8000─> [router :8000 (新規)]
     ├─ 静的 /            → 統合エントリ web（新規）
     ├─ /live/*   （静的+API）→ 127.0.0.1:8001（ライブ core = indicator_ui・無編集）へ prefix 除去プロキシ
     ├─ /replay/* （静的+API）→ 127.0.0.1:8281（リプレイ core = serve_replay・無編集）へ prefix 除去プロキシ
     └─ Service Worker が同一オリジンの API 要求をアクティブモードで /live/* or /replay/* へリライト
       ※内部ポートは loopback 限定・外部非公開
```

**重要（architecture 検証で確定）**: `/live/*`・`/replay/*` は**直 FS 配信せずコアへプロキシ**する。理由: リプレイの共有 JS（chart_renderer 等 20 本超）は `simulator/replay_ui/web` に**物理不在**で、`serve_replay` の `shared_js_root` フォールバック（`static_file_server.py:90-103`）が実行時に indicator_ui から配信している。直 FS 配信では共有 JS が 404 し replay 全損。プロキシならフォールバックが効き無波及を保てる。

- ライブ core / リプレイ core は **コード無編集**。起動バインド先が 8000→8001 / 8280→8281 に変わるだけ（両サーバとも `--port` 既存対応＝serve.sh 引数のみ・コード不変）。
- 障害分離: リプレイ core がクラッシュしてもライブ core は別プロセスで無傷。ルータはリプレイ系のみ 502。
- 負荷分離: 重い全期間ロード/parquet 直読はリプレイ core プロセスに閉じる（nice/cgroup 可）。

---

## 2. API ルーティング（ルータの振り分け規則）

**振り分けは path prefix で一意化する（Service Worker がモード別 prefix を付与）**。ルータは prefix を除去してコアへ渡す（コアは自分の素のパスを受ける＝無編集）。

| ブラウザが出す URL | ルータ → コア | 備考 |
|---|---|---|
| `/live/compute` `/live/candles` `/live/live_ticks` `/live/forming_bar` `/live/tf_period_profile` `/live/catalog` `/live/market_profile*` | 8001 の `/compute` … | prefix 除去 |
| `/replay/compute` `/replay/candles` `/replay/intraday` `/replay/market_profile*` | 8281 の `/compute` … | prefix 除去 |

- **曖昧性解消（architecture 検証で Cookie から差替）**: 新規 **Service Worker**（統合層が登録・既存無編集）が、既存フロントの root 相対 fetch（`/compute` 等・全クライアントで実証済み・ホスト/ポートのハードコード皆無）を、アクティブモードに応じ `/live/*`・`/replay/*` へ**リライト**する。
- Cookie 方式は単一オリジンのグローバル性ゆえマルチタブ/in-flight で誤ルートし得るため不採用。Service Worker はタブ単位・要求単位で確実に prefix 付与でき、既存 core は自分宛の素パス要求のみ受ける。

---

## 3. 統合エントリ（新規フロント層）とモード切替

- 新規 `web/index.html`（統合）: ヘッダに **モードトグル**（Live / Replay）を置く。
- 新規 `web/js/unified_root.js`:
  - `import { bootstrap as bootstrapLive } from '/live/js/adapter/front/composition_root_front.js'`
  - `import { bootstrap as bootstrapReplay } from '/replay/js/adapter/front/composition_root_front.js'` ＋ `setupReplay`
  - ES Modules の相対 import は **各モジュール自身の URL 基準**で解決されるため、`/live/*`・`/replay/*` 配下の既存相対 import はそのまま成立（既存ファイル無編集）。
- 切替動作（**無波及順守版**）:
  1. トグル押下 → 現在の閲覧状態（timeframe・表示指標構成・可視レンジ/スクロール）を **capture**。
  2. Cookie `ui_mode` を更新。
  3. 現 core 配線を teardown → 反対モードの bootstrap を同一ページ・同一 DOM ホストで再構築。
  4. capture した状態を **restore**。
  - ＝ページ再ナビゲーションは発生せず（同一オリジン・同一 URL）、状態は復元される。チャート実体は再生成されるが、既存モジュールの改変は不要。

### 無波及を保つ実装制約（architecture 検証で追加確定）
- **localStorage 名前空間分離（R4）**: 単一オリジン化で live/replay が同一 localStorage を共有しキー衝突する。統合層が注入する `LocalStorageGateway` をモード別 prefix（例 `live:` / `replay:`）でラップして注入する（注入は bootstrap 引数＝既存キー・既存モジュール不変）。
- **teardown のタイマ停止（R5）**: 切替時の旧モード停止は、既存 bootstrap が受ける `setInterval`/`clearInterval` 注入口を統合層がラップし、切替時に一括 clear する（既存無編集で成立）。

### 設計上のトレードオフ（明示）
- 「**チャート実体ゼロ破棄の完全シームレス**」は、既存 composition root にモード差替 seam を開ける＝**既存モジュール改変＝波及**が必須。→ 厳命により**不可**。
- よって本設計は「同一ページ内・状態復元付き再構築」を採る。再ナビゲーション/オリジン跨ぎ（＝現状のポート切替）の初期化・localStorage 非共有は解消される。完全ゼロ破棄は将来別承認課題。

---

## 4. 影響ファイル（全て新規・既存編集ゼロ）

新規ディレクトリ `unified_ui/`（名称は architecture 承認で確定）:
| 新規ファイル | 役割 |
|---|---|
| `router.py` | 8000 リバースプロキシ＋`/live/*`・`/replay/*` prefix 除去プロキシ（静的+API 両方） |
| `serve.sh` | router(8000)＋**既存 `indicator_ui/serve.sh 8001`＋既存 `replay_ui/serve.sh 8281`** を起動・監視。**生 python 起動は禁止**：既存 serve.sh はデータ watch（毎分 M1 追記・当日 tick 再取得）を併走させ、これが無いと確定足が伸びず指標が止まる（memory: fixed-ports-and-serve-scripts）。既存 serve.sh は無編集で port 引数のみ渡す |
| `web/index.html` | 統合エントリ（モードトグル・Service Worker 登録） |
| `web/js/unified_root.js` | 両 bootstrap の import・切替・状態 capture/restore・localStorage/timer ラップ注入 |
| `web/sw.js` | Service Worker：API 要求をアクティブモードで `/live/*`・`/replay/*` へリライト |
| `tests/**` | router prefix 除去・SW リライト・状態復元・localStorage 分離の単体/結合テスト |

**編集ゼロを保証する対象**: `indigators/indicator_ui/**`・`simulator/replay_ui/**`・`indigators/market_profile/**`・共有 JS・`marketdata/**`・`simulator/{domain,usecase,adapter,main}`・既存各 `serve.sh`。

---

## 5. byte 不変・無波及の検証計画

| # | 検証 | 合格基準 |
|---|---|---|
| 1 | `git diff develop --stat` が **新規ファイルのみ**（既存行の変更 0） | 既存追跡ファイルの改変 0 件 |
| 2 | ライブ既存 vitest／api テスト全緑（無編集ゆえ不変のはず） | pass 数不変 |
| 3 | リプレイ既存テスト全緑 | pass 数不変 |
| 4 | router 新規テスト（振り分け表・Cookie 分岐・502 隔離） | 全緑 |
| 5 | 実 UI（8000 統合）で Live 起動→操作→トグル→Replay で状態復元→トグル戻し | 依頼者確認（実HTTP・実UI） |
| 6 | 直 8001/8281 でも各 core が従来同一挙動 | 依頼者確認 |

---

## 6. 6フェーズ実行計画

| Phase | 担当 | 内容 | 破壊性 |
|---|---|---|---|
| 1 git | 済 | `feature/live-replay-unify-router` 作成 | 無 |
| 2 architecture | architecture-executor | 本設計の依存方向・境界・無波及の妥当性検証（レイヤ・OCP） | 無 |
| 3 tdd | tdd-executor | router 振り分け・Cookie 分岐・状態 capture/restore の Red テスト設計 | 無 |
| 4 programmer | programmer-executor | 新規ファイル群のみ実装（既存無編集）・Green 化 | 追加のみ |
| 5 code-review | code-review-executor | 無波及（既存編集ゼロ）・隔離・境界の査読 | 無 |
| 6 git | git-executor | 原子的コミット（push しない） | 無 |

- 各 core 起動ポート変更は serve.sh（新規 `unified_ui/serve.sh`）内のみ。既存 serve.sh は温存。
- 削除は本スコープで一切行わない（旧 index/root は温存）。将来の整理は別承認。
