# テストと実装の設計差異 監査（2026-08-09）

全テストコードと実装コードを突き合わせ、**テストが宣言する設計と実装の実際の設計の差異**を全数調査した記録。

- 対象: 388 Python テストファイル / 153 JS テストファイル（10 領域に分割して走査）
- 検出: **142 件**（重大度 高 **42 件** / 中 71 件 / 低 29 件）
- 起票: 根本原因の単位で 22 件に束ね、`ISSUE-319` 〜 `ISSUE-340` として `ISSUE.md` へ登録済み
- branch: `develop`（調査時 HEAD `0ff136f` / 起票時 `d3e6448`）
- 本調査でコードは変更していない（読み取りと計測のみ）

## 目次

- [1. ベースライン実測](#1-ベースライン実測)
- [2. 結論](#2-結論)
- [3. 実行・実測で確定した 8 件](#3-実行実測で確定した-8-件)
- [4. 横断して繰り返される失敗形](#4-横断して繰り返される失敗形)
- [5. 領域別の差異（全 142 件）](#5-領域別の差異全-142-件)
- [6. 起票との対応表](#6-起票との対応表)
- [7. 未検証・本調査の限界](#7-未検証本調査の限界)

---

## 1. ベースライン実測

| 指標 | 実測値 |
|---|---|
| Python テスト | **4,400 passed / 8 skipped / 0 failed**（パッケージ単位で 37 グループ実行） |
| JS テスト | **1,812 passed / 0 failed**（`tools/run_web_tests.sh`・4/4 スイート） |
| リポジトリ根 `python -m pytest --collect-only` | 3,338 collected / **88 errors** |
| `python -m pytest indigators --collect-only` | 1,510 collected / **83 errors** |
| 空アサーションのテスト | 2,930 テスト関数中 **1 件**（意図的な「例外を出さない」スモーク） |
| `skip` / `xfail` | 20 件（すべて「実データ / fixture 未配置」の条件付き） |

つまり **全件緑**であり、以下はすべて「緑のまま潜んでいる差異」である。赤くなる差異は 1 件も無い。

---

## 2. 結論

テストスイートは **存在の網羅**としては高密度（空アサーションは実質ゼロ、恒久 skip も無い）だが、**照準**が実装からずれている箇所が体系的に残っている。

最も多い形は 3 つ:

1. 実装のソース文字列を grep して「実装がある」と主張する構造テスト
2. 期待値を被検査コードの式そのものから生成するトートロジー
3. 本番が通らない経路だけを fake で叩くテスト

いずれも **ミューテーションで赤くならない**ため、回帰の壁として機能していない。

さらに構造上の問題として、**スイート全体を 1 コマンドで回すことが不可能**（basename 衝突とグローバル名 `src` の奪い合いで 88 / 83 collection error）。「全テスト緑」はディレクトリ個別起動でのみ成立する。

---

## 3. 実行・実測で確定した 8 件

以下は静的読解ではなく、コードを実行するか両側のソースを直接照合して確定させたもの。他の findings は各領域担当の静的読解に基づく。

### V-1 ライブ足内更新は 19 指標が宣言され、実働は 6 指標

`indigators/indicator_ui/web/js/usecase/intrabar_forming_ids.js:13` が「足内更新で末尾点を追従させる指標の単一情報源」として 19 の id を宣言する。バックエンドは `indigators/indicator_ui/api/adapter/compute/live_tick_tails.py:64` で非増分の指標を `None` で落とし、増分器 factory は `indigators/indicator_ui/api/adapter/compute/incremental/__init__.py:61` に 6 件しかない。

実 variant（`call_binding._TABLE` のキー）で `is_incremental` を全 19 件評価した結果:

| 区分 | 指標 |
|---|---|
| 追従する（6 件） | `moving_averages` `profit_rsi` `btlm_trail` `btlm_trail_marod` `ma_marod` `tickvol` |
| **追従しない（13 件）** | `profit_mfi` `profit_stc` `profit_oscillator` `profit_oscillator2` `profit_osi_ma` `profit_hlband` `profit_mfi_macd` `profit_rsi_macd` `profit_rmm` `profit_rmm_macd` `profit_adx_needle` `profit_arctan` `profit_volatility` |

両者を突き合わせるテストは存在しない。→ `ISSUE-319`

### V-2 `common.forming_window` は「共有中立核」ではない

現行ツリー（worktree 除く）で `common.forming_window` を import しているのは `simulator/replay_ui/` の 2 ファイルのみ。ライブ側 `indigators/indicator_ui/api/adapter/compute/live_tick_tails.py:10` は docstring で「共有核 `apply_forming` の唯一の定義を通す」と宣言するが **import していない**。

実体は `indigators/indicator_ui/api/adapter/controller/live_tick_tails_controller.py:135-140` の `_set_last_bar` で、**time を一切見ずに末尾行を無条件上書き**する。共有核 `common/forming_window.py:44-56` は「過去→無視 / 一致→置換 / 未来→追加」の 3 分岐を持つ。この「同値」宣言を検証するテストは 0 件。→ `ISSUE-320`

### V-3 `MtfProjectionPort` の宣言が実注入具象と 1 引数も一致しない

| 位置 | シグネチャ |
|---|---|
| Port 宣言 `indigators/indicator_ui/api/usecase/compute_ports.py:127` | `__call__(series, df_chart, compute_tf, *, period_start_unix)` |
| 実呼び出し `indigators/indicator_ui/api/usecase/compute_indicators.py:250` | `project_mtf(df_chart=, df_source=, compute_tf=, fold_from=)` |
| 注入具象 `indigators/indicator_ui/api/adapter/controller/compute_controller.py:68` | `_run(*, df_chart, df_source, compute_tf, fold_from=None)` |

Port 適合検査 `test_usecase_compute_ports.py:118` の対象は 4 ポートのみで、このポートは対象外。宣言どおりに書いた代替実装は呼んだ瞬間 `TypeError` になる。→ `ISSUE-321`

### V-4 増分パリティテストの「増分経路を通った」証明が成立しない

`indigators/indicator_ui/api/adapter/compute/incremental_state.py:171` の `_cache_put` は `:174` の `emit` より**前**にある。したがって `stats()["states"] >= 1` は prepare が走ったことしか示さない。

`emit` が `None` を返して full 再計算へ縮退しても（`latest_dispatch.py:72-77`）、値は full と一致するのでテストは緑のまま。ISSUE-233 の性能保証が無言で失われる。同型の assertion が `tests/latest/` の 5 ファイルに存在。→ `ISSUE-321` の併発として記載

### V-5 `/tf_period_profile` の as-of 時計は本番で壁時計

本番呼出 `indigators/indicator_ui/api/framework/server.py:316-317` は `now` を渡さず、`tf_period_profile_controller.py:499` が `time.time()` に落ちる（同 `:464` の docstring は「テスト注入用」と明記）。tf-period テストは全件 `now=` を明示注入しており、本番が通る分岐を一度も実行していない。→ `ISSUE-322`

### V-6 `placement → pane` 変換は今も無検査（穴は移設されただけ）

`docs/testing-notes.md` が自ら記録した「カタログ定数だけ見て変換ロジックは無検査」の穴は塞がっていない。変換は `indigators/indicator_ui/web/js/adapter/front/series_render_router.js:81` の `pane: def.placement !== 'overlay'` 1 行のみで、`opts.pane` を assert するテストは 0 件。文書が名指しする `indicator_controller.js` には `placement` の出現すら 0 件（移設済み）。→ `ISSUE-334`

### V-7 存在しない `protocol` 分岐を 19 箇所で検証している

`protocol` は `indigators/indicator_ui/web/js/` 全体で **0 件**。一方 `composition_root_front.test.js` は 19 箇所で `protocol:` を渡し、「served over http なら ComputeHttpClient を注入」というテスト名を掲げる。実装 `chart_app_wiring.js:74` は無条件生成。反証不能なテスト。→ `ISSUE-336`

### V-8 profit_volatility の描画列とσ水準線は本番既定でも repaint する

`indigators/profit_volatility/src/core.py:484,487` は `levels = compute_sigma_levels(z[valid])` を**最新足を含む全バー**から算出し、`np.clip(z, lower, upper)` を描画列 `level_count_clamped` にする。`src/volatility.py:68-70` の docstring は「確定したバーは新データ追加でも値が変わらない（repaint しない）」と明記。

本番既定 `window=120`・400 本に 1 本追加した実測:

| 量 | 追加前 | 追加後 | 差 |
|---|---|---|---|
| σ水準線 `up_329` | 3.633520 | 3.627080 | −0.006440 |
| σ水準線 `dn_329` | −3.636440 | −3.635180 | +0.001260 |
| 確定バーの `level_count_clamped` | — | — | 275 本中 1 本が 0.00126 変化 |

差は小さいが機構は確実に存在し、W を小さくすると拡大する。テストは描画されない `raw_level_count` だけを見ている。→ `ISSUE-323`

---

## 4. 横断して繰り返される失敗形

142 件を形で束ねると、独立した不具合ではなく 5 つの反復パターンに収束する。個別修正より、この 5 形を検出する仕組みを 1 つずつ置くほうが効率が高い。

| # | パターン | 件数 | 内容 |
|---|---|---:|---|
| 1 | ソース文字列で「実装がある」と主張する | 31 | `src.includes('...')` / 正規表現 / AST の部分走査。改名・別表記・コメント内出現で無効化される。`chart_renderer.js:33-36` は「契約の固定点として import を維持する」と明記した**未使用 import** でテストを満たしている |
| 2 | 期待値を被検査コードの式から作る | 24 | 両辺が同時に動く恒等式。`_inline_hedged_margin_level` や `_inline_before()` のように「非トートロジーな独立参照」と docstring で自称しているものが複数ある |
| 3 | 本番が通らない経路を fake で叩く | 28 | 死んだ API（`computeSeq` / `compare_stats` / `protocol` / `overlayReadout`）や、本物に無いメソッドを持つ fake |
| 4 | 生成物の鮮度ガードが片方向 | 19 | JS→fixture の検定はあるが Python→fixture の再計算検定が無い。`tf_ledger` のみ両方向で正しく閉じている |
| 5 | 同じ規則の第 2 実装が検定の外に置かれる | 14 | `fold_bars` 2 実装、`effectiveTimeframe` 2 実装、ブローカー日写像 2 実装ほか。現時点ではいずれも値が一致＝**潜在** |

（1〜5 は重複してカウントされる項目を含む。合計は 142 と一致しない。）

---

## 5. 領域別の差異（全 142 件）

分類記号: **A**=経路相違 / **B**=トートロジー / **C**=無検査 / **D**=緩いアサーション / **E**=モック乖離 / **F**=非対称 / **G**=恒真・skip

### 5.1 marketdata（resample / rollup / tf 台帳）— 19 件

28 テストファイル・247 passed。

#### 高

**MD-1（C+F）本番素材の 8 列 CSV（up/dn 付き）で rollup を end-to-end に流すテストが 1 本も無い**

- テスト: `marketdata/tests/test_resample_rollup_s1.py:46-58,136,152` — `_synthetic_m1` が生成するのは 6 列のみ
- 実装: `marketdata/rollup.py:137-140,146-148,191,246-247` — up/dn の有無で列数が変わる分岐が 4 箇所に分散
- テストの主張: ロールアップ生成は 6 列 CSV を入出力する 1 本の経路
- 実装の設計: 6 列 / 8 列の 2 形態を持つ
- 隠すバグ: `rollup.py:234-238` が実害として記録済みの「本経路だけが up/dn 列を落とし、ヘッダ不一致で自己修復せず方向内訳が恒久的に失われる（jp225_tick_1M.csv 破壊）」の再発

**MD-2（B）「ヘッダ不一致なら追記を拒否する」テストが追記経路を 1 行も実行していない**

- テスト: `marketdata/tests/test_rollup_schema_guard.py:27-34` — `_header_of(path) != _header_for_bars(bars)` の純比較
- 実装: `marketdata/rollup.py:561-568` — 実際のガードは `incremental_update` 内の `if/else`
- 隠すバグ: 条件式を反転しても `if` ごと削除しても緑。`rollup.py:556-560` が明記する「その tf の /candles・rollup 読取・ライブ watch が全部落ちる」実障害を素通しする

**MD-3（B+D）`_merge_agg` のテストが列名だけを見て集約関数を見ていない**

- テスト: `marketdata/tests/test_rollup_schema_guard.py:14-24`
- 実装: `marketdata/rollup.py:163-164,596-598`
- 隠すバグ: up/dn が `last` へ退行しても緑。形成中 1W/1M バーの方向内訳が「最後の 1 本ぶん」に化け、tickvol_updown が例外なしで過小になる

**MD-4（F+C）ブローカー日写像が 2 実装あり、一致は docstring の宣言だけ**

- テスト: `marketdata/tests/test_tf_meta.py` に `bar_time_unix` の呼び出しが 0 件
- 実装: `marketdata/session_day.py:37,42,71-79`（zoneinfo）と `marketdata/resample.py:74,77-79,82-94`（pandas tz）が独立に再実装。`tf_meta.py:85-86` の docstring が同一性を宣言
- 隠すバグ: 乖離すると `/candles` の 1D 足と `/forming_bar` が別 time に載り、1D バーが二重に出る。1W/1M は間接的なパリティがあるが **1D はどちらにも無い**。現時点では実測一致＝潜在

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| MD-5 | 中 | A | rollup 再集計 spy が `resample_ohlc` を見張るが本番入口は `resample_ohlc_tf`。検体 tf が非セッションのみで、規則の再実装を検出できない（`test_resample_rollup_s1.py:167-182`） |
| MD-6 | 中 | D | `incremental_update` のテストが「ファイルが存在する」しか見ない。本番ホットパスの truncate+append（`rollup.py:387-421`）は内容無検査 |
| MD-7 | 中 | C | resample キャッシュだけ `mtime is None` をヒット条件に含む fail-open（`serving_cache.py:149`）。base 側の fail-close（`:89`）と逆規律で、同種テストが無い |
| MD-8 | 中 | B | 日中足パリティが `resample_ohlc(df, TIMEFRAME_RULES[tf])` ＝実装の else 分岐そのものを期待値にしている（`test_session_resample.py:70-79`） |
| MD-9 | 中 | E | fake ローダが str index を返す。実ローダは DatetimeIndex + 必須列欠落で KeyError（`ohlc_csv_loader.py:177-191`） |
| MD-10 | 中 | C | API 配信される `profile_threshold` のテストが 0 件。`concentration_bands` と同じ percentile 式を二重に持つ |
| MD-11 | 中 | C | `resolve_now_unix` の env デモ時計分岐と bool 除外が無検査（`tf_meta.py:115-132`）。全 tf の期間始端の基準時刻 |
| MD-12 | 中 | C | `session_offset_profile(sessions=0)` は `[-0:]` ＝全履歴。クランプは呼び出し側責務で、結合テストが無い |
| MD-13 | 低 | G+F | golden byte 比較がセッション tf（1D/1W/1M）を除外。golden ファイルは存在するが誰も参照しない |
| MD-14 | 低 | C | OHLC 列名が解決できない素材で `dropna(subset=[])` が無効化し、休場区間の空行が残る |
| MD-15 | 低 | C | `_write_rollup`（dict 版）は本番参照 0 件の死コード。「2 経路が byte 一致」の宣言が無意味 |
| MD-16 | 低 | D | `test_tf_meta.py` の 6 テストのうち 1 つは無関係な `api_contract.ERROR_STATUS` を見ている |
| MD-17 | 低 | D | `max(p["values"])` が dict のキー最大＝ビン index。集計値（中央値）は 1 つも検証していない |
| MD-18 | 低 | D | tick tree レイアウト権威テストが 3 正規表現のみ。`Path(data_dir,"ticks")` 等は素通し |
| MD-19 | 低 | B | tf 台帳の派生値テストが導出式の逐語コピー（真理値表の併置で実害は小） |

---

### 5.2 market_profile — 12 件

38 テストファイル・499 passed。

#### 高

**MP-1（D）`src=dwell/m1` のリプレイは `to` を最大 1 バー分超過してティックを読む**

- テスト: `test_market_profile_dwell.py:639-666` — assert は**日境界**までで、プロファイル内容を一切見ない（クラス docstring `:624-629` は「未来リーク無し」と称する）
- 実装: `market_profile_dwell.py:402,313,339` — `now` でクランプしない。`controller:416-420` が `now` を渡さず `time.time()` に落ちる
- 隠すバグ: リプレイ中の 1D/4h dwell が「まだ来ていない時間帯」の価格帯を描く＝ライブと不一致。`src=zp` だけが `now=to` でクランプされており、「リプレイはライブに厳密一致」は zp 限定でしか成立していない

**MP-2（A）`/tf_period_profile` の `now` は「テスト専用引数」で、全テストがそれを注入している** — V-5 参照

**MP-3（C）`zp_supported_tfs` に Python→fixture の鮮度ガードが無い**

- テスト: `test_js_parity_golden_fresh.py:24-54` が検査するのは 5 点で、`zp_supported_tfs` を含まない
- 実装: `tf_period_profile_controller.py:88` の `_ZP_TF_ALLOWED` が唯一源
- 隠すバグ: 生成器の再実行忘れで fixture と JS が揃って陳腐化したまま全緑。サーバは 400 を返すのにフロントは選択可能＝ISSUE-261 が防ごうとした無言の機能不全がそのまま再発する

**MP-4（C）byte-parity golden 26 ケースに `src=zp` が 1 件も無い（zp はフロント既定ソース）**

- テスト: `test_market_profile_byte_parity.py:15-19,70-77` — 網羅判定は `kinds=={"mp","forming"}` と 200/400 の両在のみで src を見ない
- 実装: `mp_source_capability.js` の `MP_DEFAULT_SOURCE='zp'`
- 隠すバグ: 最頻経路が回帰ゲートの外。`compute_zp_profile` の丸め・キー順・`z_max`/`poc_star`・sessions 形状の変化が byte 検査されない

**MP-5（F）リプレイ取得とライブ取得でクエリ合成が非対称。非対称側にテストが無い**

- テスト: `market_profile_actor.test.js:846-888` — `refresh` 経路 4 ケースのみ。リプレイ側 0 件
- 実装: `market_profile_actor.js:342-346`（`_periodExtra`/`_clockExtra` 不在）、`mp_fetch_params.js:94-96`（リプレイ中は `periodExtra` を `{}` へ）
- 隠すバグ: リプレイで `period=day` を選ぶと、ライブと同じ設定にもかかわらず数年分累積のプロファイルが出る

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| MP-6 | 中 | B | as-of クランプの「同値性テスト」の参照値 `_inline_before()` が実装 2 行の逐語コピー。実効は境界値 7 点のみ |
| MP-7 | 中 | B | 「複製再発防止」ガードがソース 800 文字の正規表現。`.from` を `.to` に変えても通る |
| MP-8 | 中 | C | `forming_fold` fixture も鮮度ガード対象外。ISSUE-232「ローソクと指標が別の値を指す」の再発余地 |
| MP-9 | 中 | G | 唯一の実データ統合テストのガードが `except Exception: return False`。shim 消失時にデータがあっても永久 skip し、理由に出ない |
| MP-10 | 低 | G | `min < poc < max or min <= poc <= max` は第 2 項が第 1 項を包含＝狭い検査が消えている |
| MP-11 | 低 | E | parity 用 fake の `load_candles` が `timeframe` を無視。tf 付きケースを足した瞬間 golden が実挙動から乖離する |
| MP-12 | 低 | B | 層分離ガードが `"np.savez" in src`。同ファイルが「行頭アンカを使う」と明記しているのにこの assertion だけ素の `in` |

---

### 5.3 言語横断（tools / front↔back 契約）— 9 件

8 テストファイル・96 passed。

#### 高

**XL-1（F）足内更新の対象集合が front 19 件・back 6 件で非対称** — V-1 参照

**XL-2（A）ロールアップ対象 tf の第 2 定義が検定の死角に置かれている**

- テスト: `test_tools_composition_declaration.py:21,25,53` — 走査は repo 根 `tools/*.py` のトップレベル関数本体のみ
- 実装: `indigators/indicator_ui/tools/export_jp225_m1.py:62` の `_ROLLUP_TIMEFRAMES`（別ディレクトリのモジュール定数）が `serve.sh:65-66` の `--watch` へ入る
- 隠すバグ: 台帳へ tf を追加すると `rollup_timeframes()` は増えるが `--watch` は旧 8 tf のまま。新 tf のロールアップだけが無言で凍結する（ISSUE-253 と同型）

**XL-3（B）`forming_fold` の「Python 一致」検定が Python を見ていない**

- `py_parity_golden.test.js:128-146` / `test_js_parity_golden_fresh.py:24-54` — JS→fixture の一方向のみ。Python `forming_states` を変えても再生成しなければ誰も落ちない（現 fixture は fresh＝潜在）

**XL-4（B）zp 対応 tf の生成物にも鮮度検定が無い** — MP-3 の言語横断側。`mp_param_defaults_generated.js` には鮮度検定があり `mp_capability_generated.js` には無い

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| XL-5 | 中 | A | `serve.sh` の毎起動経路にある `export_jp225_csv.py` が完全無検査（date 書式・外れ値補正・境界・戻り値）。テストは `lambda argv: 0` に差し替えて argv だけ見る |
| XL-6 | 中 | C | rollup 検定の tf が本番 8 tf の部分集合。`15m/30m/4h` 未検査、暦足 `1W/1M` は毎分走る `incremental_update` の検査が無い |
| XL-7 | 中 | F | `tickvol_bands` の既定値・受理範囲が front/back に手書き二重。`MAX_SESSIONS` を下げても front スライダ上限は 25 のまま黙って clamp される |
| XL-8 | 中 | F | 「front/back 対称」検定が back 契約に載る id しか反復しない。front 固有の `market_profile` / `tickvol_bands` は無検査領域 |
| XL-9 | 低 | F | `effectiveTimeframe` が 2 実装（`period_presets.js:120-123` / `timeframe_controller.js:175-177`）。等価性を突き合わせる検定が無い |

---

### 5.4 simulator コア（バックテストエンジン）— 15 件

95 テストファイル・1,356 passed。

#### 高

**SIM-1（F）`stop_out_at_open` は本番設定で常に無効、テストは無効にならない経路でしか見ていない**

- テスト: `simulator/tests/unit/test_run_backtest.py:741` — bar-mode（`pending_lifecycle` 無し）でのみ検証
- 実装: `simulator/usecase/run_backtest.py:204,232` が唯一の参照（ともに `execute()` 内）。`_execute_every_tick()`（431-930 行）には一切現れない。`:161-164` が every-tick へルーティング
- 隠すバグ: MT5 突合済みと称する設定で、週末ギャップの open ストップアウトが一度も発火していない

**SIM-2（A）UC-003 `compare_stats` は本番から呼ばれない（死んだ API を 10 テストが守っている）**

- テスト: `test_compare_stats.py:17-124`（10 件）+ `test_usecase_ports.py:103`
- 実装: `usecase/compare_stats.py:35` / `usecase/ports.py:36` — tests を除く呼出 0 件。Composition Root に import なし
- 隠すバグ: 突合は `tests/confirmation/*/reconcile.py` の手書きスクリプトで行われており、許容誤差判定のリグレッションが「緑」のまま本番に一切効かない

**SIM-3（G）WF・最適化・IS/OOS の結合テスト 5 モジュールが git 未追跡 fixture に gate されている**

- テスト: `test_walk_forward_determinism.py:43` ほか 4 ファイル — `skipif(not _FIXTURE.exists())`
- 実測: `git ls-files simulator/tests/confirmation` = **0 件**、`.gitignore:208` で除外。ローカルには 2.3 MB の実体が存在するため現環境では走る
- 隠すバグ: クリーンチェックアウト・別マシン・将来の CI では全件が無言 skip し、`walk_forward.json` の meta キー欠落も grid 非決定化も検出されない

**SIM-4（E）Tick の bid/ask 規約が 2 系統あり、片方は一度もテストされない**

- テスト: `test_tick_model.py:56-59` — 全 synthetic テストが `spread=0`
- 実装: `tick_model.py:22-23` は中心化かつ `point_size` 未乗算、`_execution.py:23` は `ask = bid + spread×point`。JP225（spread=100, point=0.1）で tick model は bid=price−50 / ask=price+50 を返す（正しくは ±10 相当・非中心）
- 隠すバグ: `run_backtest.py:725` が毎ティック `last_bid,last_ask` を保存し、`:886-890` のティック 0 件足で equity 評価に使う。MT5 規約と食い違う値が資金曲線・DD へ混入する

**SIM-5（C）`sltp_tie="tp"` 分岐が全テストで 0 回**

- テスト: `grep sltp_tie="tp"` → **0 件**。全テストが `"sl"`
- 実装: `_execution.py:180`、`config_loader.py:48` の `Literal["sl","tp"]`
- 隠すバグ: config で選べる値なのに、SL/TP 同時到達の決済価格・件数が未検証のまま変わる

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| SIM-6 | 中 | A | `fill_delay` / `spread_model` / `return_basis` / `legacy_quirks` は消費者ゼロの死に config。テストは値の往復だけを assert し挙動を規定しているかのように書く |
| SIM-7 | 中 | A | `StrategyPort.on_position_check` は Interactor から一度も呼ばれない。6 戦略が空実装を義務付けられている（ISP 違反を Port テストが正当化） |
| SIM-8 | 中 | F | usecase 依存方向テストが 4 サブシステム分のみ。実際の numpy 流入 2 件（`metrics_spec.py:19` / `mt5_parity.py:32`）を素通し。相対 import も検出できない |
| SIM-9 | 中 | D+B | WF stitch の `profit_factor` が `abs()` なしで単 run と符号が逆。テストが実装式をそのまま期待値にしているので追随しない |
| SIM-10 | 中 | B | `_inline_hedged_margin_level` は「非トートロジーな参照」と docstring で自称しつつ被テストメソッドの逐語コピー |
| SIM-11 | 中 | E | `StubTickModelPort` が bid=足の安値 / ask=足の高値を返す。どの実装もそうしない Port 契約の反例が「正しいダブル」として固定 |
| SIM-12 | 中 | C | ティック 0 件足の equity carry-forward（`run_backtest.py:885-890`）が未テスト。SIM-4 と合成して誤 equity が入る経路が丸ごと無検査 |
| SIM-13 | 中 | F | `hedged_margin` は every-tick 側のみ、`stop_out_at_open` は bar-mode 側のみ。「他方では無視される」を固定するテストが両方向とも無い |
| SIM-14 | 低 | C | `fill_pending_order` の未知 kind 分岐（`else: return None`）が未テスト。kind のタイプミスで発注が無言で消える |
| SIM-15 | 低 | G | `compare_stats(tolerances={})` は無条件 `passed=True`。突合表の読み込み失敗が「全項目一致」として報告される |

---

### 5.5 indicator_ui API（Python）— 15 件

68 テストファイル・922 passed。

> この節だけ findings 15 件に対して掲載エントリが 14 件になる。原調査の F6（「未検証の協調子が増えていない」検査が余剰を検出しない）は独立した差異ではなく IAPI-1 の成立要因なので、IAPI-1 に畳んで記載した。本書全体の掲載エントリは 141、findings 実数は 142。

#### 高

**IAPI-1（E）Port 宣言が実際の注入具象と全面不一致（MTF 投影）** — V-3 参照。加えて `test_usecase_compute_ports.py:141-148` の「未検証の協調子が増えていない」検査は 5 個の文字列が**含まれる**ことしか見ず、増えた 2 つ（`project_mtf` / `period_boundary`）を検出しない。この穴から入った。

**IAPI-2（C）`computeTimeframe`（上位足）経路が丸ごと未テスト**

- テスト: `test_usecase_compute_indicators.py:110` の `_kw()` は `project_mtf`/`period_boundary` を渡さない。api/tests に `compute_timeframe` 設定は 0 件
- 実装: `compute_indicators.py:155-262`（投影分岐・二重ロード・`mode=latest` での `tail(1)`）と `mtf_causal_frames.py:65`（テストからの import 0 件）
- 隠すバグ: 上位足ライブ計算の窓・畳み・limit 適用が壊れても全テスト緑

**IAPI-3（D）「増分経路が使われた」assertion が emit フォールバックを検出できない** — V-4 参照。同型の assertion が `tests/latest/` の 5 ファイルに存在。

**IAPI-4（D）ライブ毎ティック末尾値の形成中バー注入が参照実装と別規則、値の検証が無い**

- テスト: `test_live_tick_tails_controller.py:71-76` — 9 種の tf を parametrize しながら assert は `out is not None` と `tickMs` 一致だけ。tf=1h では 15m 整列の窓を渡していて前提自体が崩れている
- 実装: `live_tick_tails_controller.py:135-140` vs 参照 `common/forming_window.py:45-56`
- 隠すバグ: 保存データのフロンティア遅れで末尾確定足が 1 期間古いとき、確定足が破壊され新バーが追加されないまま指標が計算される

**IAPI-5（C）`do_POST` の実行器分岐のうち、本番 25/26 指標が通る側が HTTP 経路で未テスト**

- テスト: `test_server_smoke.py:70,98` — POST /compute は 3 本、すべて `tgp_btlm`
- 実装: `server.py:375-378` — `tgp_btlm` だけ `_COMPUTE_WORKER`、他は全部 `_COMPUTE_POOL`（呼出 0 回）

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| IAPI-6 | 中 | C | `PeriodBoundaryPort` は注入されるが呼び出し箇所が存在しない。未注入で RuntimeError を投げる死んだ契約 |
| IAPI-7 | 中 | D | param scope 検査が docstring で「==」を謳って実際は「⊆」しか見ない。26 エントリ中 **25 件**に「受理されるが未宣言」の param がある |
| IAPI-8 | 中 | C | 静的配信の dual-root（MP モジュール側 symlink 約 30 本）分岐が未テスト。削ると MP フロント全 404 だが Python 側は全緑 |
| IAPI-9 | 中 | C | 413・非 /compute POST・`/tickvol_profile` の HTTP 要求・`_parse_port` がいずれも未到達。殻のクエリ名の取り違えを誰も検出しない |
| IAPI-10 | 中 | B | 「重複実装なし」を `"def _tail_points" not in src` で主張。`_points_tail` へ改名するだけで二重定義が復活しても緑 |
| IAPI-11 | 中 | B | MP ワーカーの構造ガードが server.py 自身の AST 検査＋実装関数から期待値生成。実行時ガード（スレッド ident・バリア）だけが有効 |
| IAPI-12 | 中 | D | 増分器レジストリ検査が「既定 params 1 回評価」に依存。docstring が述べる「params 依存で分岐する指標」は実在せず、`except Exception: continue` が評価不能エントリを黙って落とす |
| IAPI-13 | 低 | B | `PARAM_DEFAULTS == indicator_param_defaults()` 等のトートロジー 3 件。値の正しさは golden 契約だけが担保 |
| IAPI-14 | 低 | D | `latest_meta_fields` の戻り型注釈が実装と食い違う（撤去済みの 3 要素タプル形が注釈にだけ残存） |

---

### 5.6 indicator_ui / unified_ui フロント（JS）— 15 件

#### 高

**IJS-1（C）`placement → pane` 変換が今も無検査（記録された穴は移設されただけ）** — V-6 参照。唯一の end-to-end 経路 `indicator_controller_styles.test.js:181,207` の fake は `renderLine: (id, payloads) => …` と**第 3 引数 `opts` を捨てている**。

**IJS-2（B）series_kind 台帳の「消費者」検定が装飾 import で満たされ、実消費者は検定対象外**

- テスト: `series_kind.test.js:70` — 消費者を `['series_render_router.js','chart_renderer.js','properties_dialog.js']` に固定
- 実装: リスト内の `chart_renderer.js:33-36` は `seriesKind` を**一度も呼ばない**（36 行の import のみ）で、コメントが「契約の固定点として import を維持する」と自認。実際に能力分岐を持つ `series_drawer.js`（`seriesKind()` 呼出 13 箇所）はリストに無い
- 隠すバグ: `series_drawer.js` に raw kind 比較を書き戻しても緑。※現状 `series_drawer.js` は台帳を正しく使っており、差異はガードの照準のみ

**IJS-3（A）存在しない `protocol` 分岐を 19 箇所で検証している** — V-7 参照

**IJS-4（C）`/live_ticks` のクエリ組み立て（req → URL）が完全無検査**

- テスト: `live_tick_player.test.js:389-398,562` — player が渡す req オブジェクトまで。URL は誰も見ていない
- 実装: `composition_root_front.js:90-109` — `since/timeframe/datasetRef/specs/limit/tailsWithinMs` をここで初めて文字列化。`specs` が空なら他も落とす条件付き結線
- 隠すバグ: キー名・条件を壊すとサーバが tails を返さず「指標が足内で動かない」に落ちるが全緑（ISSUE-291 と同型の無言死経路）

**IJS-5（C）唯一の自動ビュー介入点に回帰防止がゼロ（二重に到達不能）**

- テスト: `composition_root_front.test.js:27` — fake の `timeScale` が `setVisibleRange` を持たず `chart_renderer.js:303` のガードで黙って no-op。さらに candles 1 本で `lastT−firstT > 1年` 条件にも入らない
- 実装: `composition_root_front.js:449-461` — `/candles` 完了後にユーザーイベントなしで `focusTimeRange(lastT−365日, lastT)` を自動実行

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| IJS-6 | 中 | C | `overlayReadout` は消費者ゼロの死んだ能力なのに、検定が仕様として固定している（撤去記録は `series_drawer.js:245`） |
| IJS-7 | 中 | G | `expect(text.startsWith('[   1235]')).toBe(false)` は恒真（実装の出力は必ず `[   1235ms]`）。`.toBe(true)` の書き損じ |
| IJS-8 | 中 | A/E | IndicatorController に存在しない `mode:'b'` / `facade:{}` をテストが渡し続けている（A方式撤去の残骸） |
| IJS-9 | 中 | G | symlink 検定が構造的に 0 回評価（`walk()` が `statSync` を通ったパスしか返さないので `broken` は常に空） |
| IJS-10 | 中 | D | 「**全対象指標**で計算.時間足が先頭」を謳いながら 6 id のハードコード。`list()` 全走査なら決定点 1 箇所という主張と検定が一致する |
| IJS-11 | 中 | B | `RENDER_ROUTES.method` の実在検定がソース正規表現（インデント 2 前提）。実オブジェクトで `typeof` を見れば偽陽性・偽陰性とも消える |
| IJS-12 | 中 | C | `unified_root.js` の `main()`（フェイルクローズ 4 経路＋mount 順序不変条件）が node から到達不能で無検査。ヘッダコメントと実装で順序が逆 |
| IJS-13 | 低 | B | 「未宣言経路で例外」がソース文字列マッチのみ（テスト自身が「実行時再現は不可」と記載） |
| IJS-14 | 低 | F.I.R.S.T | `catalog_server_defaults.test.js:49-54` がモジュール singleton を復元せず終了。後続の「静的既定へフォールバック」検定が汚染値を見る |
| IJS-15 | 低 | B | `pair_dim_alpha_single_source.test.js` は動機が消えたと自認する構造検定＋冗長 assert。import しつつ別値で描画しても緑 |

---

### 5.7 replay_ui（Python）— 12 件

39 テストファイル。

#### 高

**RPY-1（C+D）`mode='latest'` + `computeTimeframe` で forming と mode が無言で捨てられる**

- テスト: `test_causal_compute_mtf.py:96` — `_req()` の基底が `mode=None, forming=None` 固定。7 テスト全部がこれを使う
- 実装: `causal_compute.py:86-90,124-160` — `_compute_projected` は `request.mode`/`forming`/`window_port` を一切参照しない
- 本番で飛ぶ根拠: `indicator_controller.js:290,1013,1016` が `mode` と `computeTimeframe` を同一ボディへ載せる
- 隠すバグ: 上位足指標の足内フォールバック計算で形成中バーが反映されず末尾点が跳ぶ。`truncate` が進行中 C 足を確定 OHLC のまま残すため足内の未来参照になる

**RPY-2（A+D）シーム比較テストの fake forming が本番に存在しない `volume` を持つ**

- テスト: `test_causal_compute_mtf_seam.py:103,107`
- 実装: `causal_compute.py:222,265,418` — `_fold_bars` は volume を合算、H 経路は実 tick 数を載せないと明記。本番 snapshot は OHLC のみ（`forming_plan.js:53` / `forming_fold.js:26-33`）
- 隠すバグ: 計算足を持つ volume 系指標（tickvol / profit_mfi 等）で、足内値とリビール値が同じ瞬間に別物になる

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| RPY-3 | 中 | B | Facade 縮約テストが陳腐化した 2 リテラルの grep のみ。実装は `mtf_causal` / `mtf_causal_memo` を内部パスから直 import（後者は Facade に存在しない） |
| RPY-4 | 中 | D+E | `reveal_candles` が `CausalCandlePort` と宣言しながら `WindowedCandlePort` のメソッドを呼ぶ。unit fake は誤った契約を「正しい」として固定 |
| RPY-5 | 中 | D | アーキ純度テストが `simulator.` 接頭辞の `ImportFrom` しか見ない。`import marketdata` 等は素通り |
| RPY-6 | 中 | C | H 経路の形成足差し替えが位置ベース（`in_period[:-1] + [snapshot]`）で、`apply_forming` の時刻分岐防御を持たない |
| RPY-7 | 中 | A | MTF テストの fake は `memo` 無しで呼ぶが本番は常に memo 付き。記憶層の指紋一致/put 分岐がリプレイ側で 1 度も踏まれない |
| RPY-8 | 中 | C+D | `CausalComputePort`（6 メソッド）に構造適合テストが 0 本。fake は 2 メソッドのみ |
| RPY-9 | 中 | D | docstring が「窓構築の唯一源」と書く `_causal_h_window` をリビール経路は使わない。`fold_bars` が 2 実装並存（現在は逐語同一） |
| RPY-10 | 低 | E | MP forming の主 fake が Protocol の `frm` を持たず、本番到達不能な後方互換分岐を恒久固定 |
| RPY-11 | 低 | C | unit に `available_days` / `WindowedCandlePort` / `CatalogPort` のテストが 0 件（integration が実経路を押さえているため実害は無い） |
| RPY-12 | 低 | B | ISP 分割テストの 4 本が `hasattr` の有無のみ。束ねた実体が正しいかは規定していない |

---

### 5.8 replay_ui フロント（JS）— 15 件

#### 高

**RJS-1（A）本番が呼ばないメソッドだけをテストしている（`computeSeq`）**

- テスト: `forming_seq_client.test.js:39-85` — 全 6 ケースが `computeSeq()`
- 実装: `forming_plan_cache.js:119` — 本番は `computeSeqMulti()` のみ。`computeSeq` の呼び出し元は JS に 0 件

**RJS-2（B）「参照実装との一致」が同一ファイルの自己比較（symlink）**

- テスト: `market_profile_dwell_accumulator.test.js:16-27` — 移植版と「参照実装」を別 import して `deepEqual`
- 実測: `readlink -f` で両者とも同一実体に解決＝恒真

**RJS-3（A）保管庫の記録ゲートを一度も通していない**

- テスト: `replay_full_window_store.test.js:72-83` — リプレイ側は第 5 引数に `null` を**テストが手渡している**
- 実装: `replay_indicator_controller.js:181-211` — 判断点は `isFullLive = _untilTime === undefined && opts.mode !== 'latest'`。`_recordFullSeries` 自体は `_untilTime` を見ない（docstring の「ライブ以外は記録しない」は未実装）

**RJS-4（E）統合ハーネスの fakeController が実 controller の面を持たない**

- テスト: `replay_mp_wiring.test.js:54-61` ほか 3 ファイル — fake は 4 メソッドのみ
- 実装: `replay.js:163-205` — `windowTokenOf/seedRevealFromStore/storedInstanceIds/revealNeedsBuild/buildRevealBase/revealTo/renderStored` を `typeof` ガード越しに呼ぶ。fake には全て不在＝毎回 false 側

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| RJS-5 | 中 | C | 足内更新の唯一経路 `updateForming` に単体テストが無く、実装は `try { updateLastCandle } catch { /* noop */ }`。renderer から改名されると完全な no-op になる |
| RJS-6 | 中 | G | `assert.equal(calls.at(-1).asof, undefined)` は恒真（`asof` という識別子は JS 実装に 0 件、fake は URL 生成を通らない） |
| RJS-7 | 中 | D | 撤去済みゲート（`ticklive`）をテスト名が宣言。61 ケース中 55 行が UI から到達不能なモードで機構を駆動している |
| RJS-8 | 中 | F | 同一 symlink 実体を両側で手写しテストし片側が退化（純横ドラッグ検証はライブ側のみ、`stopPropagation` 検証は replay 側のみ） |
| RJS-9 | 中 | F | catalog / facade でも同一実体に対する期待値ドリフト（replay 4 ID vs ライブ 6 ID など） |
| RJS-10 | 中 | B | `assert.ok(replay.includes('this._scopedParams('))` 等のソース文字列 assert。死んだ分岐に移動しても緑（ISSUE-278 #8 の再発を止められない） |
| RJS-11 | 中 | G | `if (targets.length > 0) { assert }` / `if (client.calls.length >= 1) { assert }` — 前提が崩れると 0 回実行で緑 |
| RJS-12 | 中 | C | `INTRABAR_FORMING_IDS` の登録漏れを検出できない（2 ID ハードコードのみ。カタログ横断の導出テストが無い） |
| RJS-13 | 中 | C | リプレイ固有 5 モジュール（`mp_growth_driver` / `replay_range_menu` / `replay_bar_view` / `replay_speed_menu` / `replay_popup`）に直接テストが無い |
| RJS-14 | 低 | D | ファイル名 `_mtf_exclusion` と冒頭 docstring が「除外」を謳い、同ファイルの assertion は「包含」を固定（ISSUE-290 で規約が反転） |
| RJS-15 | 低 | B | 期待値を被検査モジュールの export から計算する恒等式＋`assert.ok(true)` 1 件 |

---

### 5.9 common / common_view / unified_ui / dataset — 15 件

common 95 + common_view 14 + unified_ui 28 passed。

#### 高

**CORE-1（A）並行追記の耐性検定が本番の読み経路を通らない**

- テスト: `test_concurrent_append_read.py:123,161`
- 実装: `serving_cache.py:114-129`（`jp225_tick` の 1m 供給は rollup 経路）/ `tail_reader.py:56-83`。耐性（不完全行検出＋時間予算リトライ）は `ohlc_csv_loader.py:74-111` にしかなく、`tail_reader` には検出も再試行も torn-read フォールバックも無い
- 隠すバグ: テスト docstring が名指しする当の 276 MB ファイルの 1m 配信で、末尾 torn 行が例外か NaN 混入 candles としてフロントへ抜ける

**CORE-2（A）同じ検定の書き手も代役。本番の追記関数は競合下で一度も実行されていない**

- テスト: `test_concurrent_append_read.py:147-156` — `_single_writer` という手書きの書き手
- 実装: `tick_m1.py:402-406` — 本番は `_format_m1_for_csv` → `to_csv(header=False)` → 1 write。テストから 0 回

**CORE-3（A）`common.forming_window` の「共有中立核」宣言が成立しておらず、規則が 3 実装に分裂** — V-2 参照

**CORE-4（D）「参照実装に bit 一致」という移設の根拠が 2 点で成立していない**

- テスト: `common/tests/test_forming_window.py:39-41` — forming キーの大小無視を assert
- 実装: 参照実装 `prototype_260626-01/proto_server.py:140-144` は df 列のみ大小無視で `key in forming` は**大小区別**。本番ライブ `forming_bar.py:278-281` は 5 列を無条件上書きで「未指定キー保存」規則を持たない

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| CORE-5 | 中 | C | モジュール依存宣言の AST 検定が 23 モジュール中 3 件にしか適用されず、最重要の「marketdata は上位を逆 import しない」はどこでも強制されていない。docstring 本文も未照合 |
| CORE-6 | 中 | C | unified_ui の 28 テストは全て `create_router_server` 直呼び。`main()`・CLI・env 既定・`--read-timeout 0 → None` 変換が未検証 |
| CORE-7 | 中 | C | prefix 境界（`startswith(prefix + "/")`）に回帰テストが無い。退行すると `/livefoo` が live core へ無言で転送される |
| CORE-8 | 中 | D | 半開窓 `[start,end)` の保証は CSV 側だけ。Dukascopy は期間フィルタを一切しない |
| CORE-9 | 中 | D | `isinstance(src, CandleSource)` はメソッド名の有無しか見ない。シグネチャ変更は素通り |
| CORE-10 | 中 | C | `with_volumes=True` の 5 要素タプル経路（tick parquet の書込スキーマを決める）が無検定 |
| CORE-11 | 中 | C | `SOURCE_TO_APPLIED` の「唯一源」に対し `call_binding.py:277-283` が 4 番目の複製を持つ |
| CORE-12 | 中 | C | CSV 列順の「規則源は `header_for`」とコメントしながら `tick_m1.py:259-261` はインライン再実装（未知列の扱いが逆） |
| CORE-13 | 中 | C | `_append_m1_csv` が既存ヘッダを読まない。6 列 CSV と 8 列 CSV が実在し、旧ヘッダへ up/dn 付き行を追記すると自己修復にも入れない |
| CORE-14 | 中 | C | `common/gpd.py` / `extremal_index.py` の公開 6 関数がテスト 0・利用者 0（収束失敗時の挙動が未観測） |
| CORE-15 | 低 | G | 恒真・弱い assertion 5 件（`is not None` / `body is None or …` / `== 3.0 or abs(...) < 1e-9` / 代入元との比較） |

---

### 5.10 個別インジケータ 27 モジュール — 15 件

1,230 passed / 8 skipped（モジュール個別実行）。

#### 高

**IND-1（D）profit_volatility の描画列とσ水準線は repaint する（no-repaint を描画されない配列で検証）** — V-8 参照

**IND-2（D）freeze_last の「`out[-1]` 以外は 1 ビットも変えない」が raw だけの保証**

- テスト: `test_standardize_causal_freeze_last.py:179-191` — `raw_level_count[:-1]` の一致のみ
- 実装: `src/core.py:481-487` — freeze_last が `z[-1]` を変える → `levels` が変わる → `level_count_clamped` が全バーに波及
- 実測（W=30）: `up_329` 3.754 → 16.568、確定バーの clamped が 1.589 変化

**IND-3（A）テストとプロダクトで `src` のモジュール同一性が違う。横断実行が不可能**

- テスト側: 各モジュールが `sys.path.insert(0, parents[1])` → `from src.core import …`（グローバル名 `src`）
- 本番側: `call_binding.py:225-235` の `_load_src_package` が `_<indicator>_src` の一意名でロード
- 実測: `pytest indigators` = **83 collection error**（1,510 件しか収集されない）。`__pycache__` 削除後も再現

**IND-4（D）profit_arctan / profit_oscillator / profit_adx_needle も IND-1 と同型**

- テスト: `profit_arctan/tests/test_core.py:250-263` ほか 2 件 — いずれも `raw_level_count` のみ no-repaint 検証
- 実測: arctan `up_329` 24.152 → 23.170
- 因果性テストが見張っているのは z 生成ループと freeze_last 分岐だけで、`compute_sigma_levels` はどのモジュールの因果性テストからも見られていない

#### 中 / 低

| ID | 重大度 | 分類 | 内容 |
|---|---|---|---|
| IND-5 | 中 | C | `moving_averages/__init__.py` の再エクスポート層が壊れている（`from moving_averages import *` が AttributeError）。この層は profit_osi_ma / profit_arctan の本番 import 経路で、触るテストは 0 件 |
| IND-6 | 中 | C | `prev_calculated > 0` 分岐（sma/ema/smma）が全テストで 0 回。本番の増分器はこれを使い「full と bit 一致」を前提にしている（現状は実測一致＝潜在） |
| IND-7 | 中 | C/F | `add_moving_averages` が丸ごと無検証（例外分岐 5 本）。他 20 モジュールが持つ「add_* を FakeChart で叩く」層がこのモジュールだけ欠落 |
| IND-8 | 中 | F/C | `compute_rsi_stateful` が無検証。同型の LWMA stateful には 169 行の専用 bit 一致テストがあるのに非対称 |
| IND-9 | 中 | C | cvfe は 135 テストあるが本番エントリ `add_cvfe`（kwargs 20 個・2 分岐）の出現 0 件 |
| IND-10 | 中 | F/C | profit_band の本番 2 variant のうち `add_robust_profit_band` が無検証（部品は個別に検証済み、合成だけが穴） |
| IND-11 | 中 | D | profit_rmm_macd の複製同一性テストが `window=None`（非既定）でのみ比較。既定＝本番パスのズレを検出できない |
| IND-12 | 中 | F | `freeze_last` は 7 モジュールに口があり、テストは 4 本、本番消費者は **0 件**（既知 ISSUE-028）。oscillator / adx_needle / arctan は素通し自体が無検証 |
| IND-13 | 低 | D | 「`_causal_z` と整合」の主張が丸めで食い違う（profit_system は 5 桁丸め、profit_volatility は丸めなし） |
| IND-14 | 低 | G | moving_averages の 8 skip は `test_lwma_stateful.py:86` の 1 箇所 ×parametrize 格子（**条件 skip ではなく恒常的なアーティファクト**・環境非依存・失敗の隠蔽なし） |
| IND-15 | 低 | D | ma_marod の Protocol 検証が `object()` → TypeError のみ。名前だけ揃った偽物は受理される |

#### 付随して確定した事実 — `profit_hl_band` と `profit_hlband` は別物

- 元 MQL が別ファイル（`PRO!fit_HLBand.mq4` / `PRO!fitHLBand.mq4`）
- 計算が別（`|H−C| / |L−C|` vs `high−low`）、出力が別（overlay 8 本のみ vs separate ヒストグラム + overlay 8 本）
- 両方が本番生存（`call_binding.py:626,633,640`・catalog にも両方あり）
- 非対称は 1 点のみ: hl_band だけが因果窓 + 比率正規化に是正済み（`test_causal_ratio.py` 305 行）。hlband は SPEC が「全系列」を明示する忠実移植なので仕様どおり

---

## 6. 起票との対応表

142 件を根本原因の単位で 22 件に束ね、`ISSUE.md` へ登録した。

| Issue | 種別 | 概要 | 主な findings |
|---|---|---|---|
| ISSUE-319 | 挙動 | ライブ足内更新の対象集合が front 19 件・back 6 件で非対称 | V-1 / XL-1 |
| ISSUE-320 | 設計 | 形成中バー差し込み規則が 3 実装に分裂 | V-2 / CORE-3 / CORE-4 |
| ISSUE-321 | 設計 | `MtfProjectionPort` の宣言が実注入具象と全引数不一致 | V-3 / V-4 / IAPI-1 / IAPI-3 / IAPI-6 |
| ISSUE-322 | 挙動 | `/tf_period_profile` の as-of 時計が本番で壁時計 | V-5 / MP-2 |
| ISSUE-323 | 挙動 | σクランプ帯を全期間集計で作るため描画列が repaint | V-8 / IND-1 / IND-2 / IND-4 |
| ISSUE-324 | 挙動 | `src=dwell/m1` の集計窓が `to` を最大 1 バー超過 | MP-1 |
| ISSUE-325 | 挙動 | `stop_out_at_open` が every-tick 経路に未実装 | SIM-1 / SIM-13 |
| ISSUE-326 | 構造 | UC-003 `compare_stats` が結線されておらず死んだ API | SIM-2 / SIM-15 |
| ISSUE-327 | 挙動 | Tick の bid/ask 規約が 2 系統 | SIM-4 / SIM-12 |
| ISSUE-328 | 挙動 | リプレイの MP 取得が `period` / `clock` を送らない | MP-5 |
| ISSUE-329 | 挙動 | `mode='latest'` + `computeTimeframe` で forming が捨てられる | RPY-1 |
| ISSUE-330 | 設計 | H 経路の形成足 snapshot が volume を持たない | RPY-2 / RPY-9 |
| ISSUE-331 | 構造 | `moving_averages` パッケージの再エクスポート層が壊れている | IND-5 |
| ISSUE-332 | 挙動 | 初期ロードで自動ビュー介入（`focusTimeRange`） | IJS-5 |
| ISSUE-333 | 構造 | テストの `src` 名衝突・basename 衝突で横断 pytest が不成立 | IND-3 |
| ISSUE-334 | 検定 | 構造テストがソース文字列一致で成立している（パターン 1） | V-6 / IJS-1 / IJS-2 / RPY-3 / XL-2 / IAPI-10 / MP-12 ほか |
| ISSUE-335 | 検定 | 期待値を被検査コードの式から生成するトートロジー（パターン 2） | MD-8 / SIM-9 / SIM-10 / MP-6 / RJS-2 / IAPI-13 ほか |
| ISSUE-336 | 検定 | 死んだ API・存在しない分岐をテストが検証している（パターン 3） | V-7 / IJS-3 / IJS-6 / IJS-8 / RJS-1 / IAPI-5 / SIM-6 / SIM-7 ほか |
| ISSUE-337 | 検定 | 生成物の鮮度ガードが片方向（パターン 4） | MP-3 / MP-8 / XL-3 / XL-4 |
| ISSUE-338 | 構造 | 同じ規則の第 2 実装が検定の外に置かれている（パターン 5） | MD-4 / XL-2 / XL-7 / XL-9 / CORE-11 / CORE-12 |
| ISSUE-339 | 検定 | WF 決定論ほか 5 モジュールが git 未追跡 fixture に gate | SIM-3 |
| ISSUE-340 | 検定 | ミューテーション検証が無く「赤くならないテスト」を機械検出できない | 334 / 335 / 336 の親 |

着手順の推奨: **ISSUE-333（横断 1 コマンド実行）→ ISSUE-340（ミューテーション導入）** を先に置く。334 / 335 / 336 を個別に潰しても、再発を止める仕組みが無ければ同じ形が戻る。

---

## 7. 未検証・本調査の限界

- **実 UI での再現は未実施。** V-1（13 指標が足内で動かない）・V-8（σ水準線が動いて見える）・MP-1（未来の価格帯が描かれる）はいずれもコード経路と compute 層の直接実測までで、実 HTTP 経路・実チャートでの目視確認はしていない。
- **ミューテーション実測は未実施。** 「反転しても緑」という記述はコード読解による帰結。コード変更禁止の制約下で調査したため、実際に壊して赤を確認していない。
- **「潜在」と明記した項目**（XL-2 / XL-3 / XL-4 / MD-4 / IND-6 / IND-8 / IND-11 / RPY-9 ほか）は、現時点で値が一致していることを実測済み。指摘は「検定が無い＝将来の乖離を落とせない」という構造についてであり、現在バグが出ているという主張ではない。
- **統計解析モジュール**（`indigators/market_profile/analysis/mp_stats`）は差異なしと判定した。参照実装との独立実装間等価を取っており、写経・トートロジーに該当しない。
- **skip 20 件はすべて条件付き**（実データ / fixture 未配置）。ただし SIM-3 の 5 モジュールは fixture が git 未追跡のため、環境が変われば無言 skip になる。
- **件数について。** 初報で「137 件」と述べたのは概算だった。領域別の実数を合計した正しい値は **142 件**（本文の各節の合計と一致する）。重大度 高 42 件は変わらない。

---

## 関連

- `docs/testing-notes.md` — 「テストとコードが違う」の 2 パターンと、パターン 2（テスト自体が実態とズレている）の検出手段。本監査はそのパターン 2 の全数調査にあたる。
- `ISSUE.md` — ISSUE-319 〜 ISSUE-340
