# 実行トレース（シミュレーション結果のティック粒度保存・読込・分析）基本設計書

- 起票: ISSUE-508（`ISSUE.md:14554`）
- 承認済みスコープ: **段階 1〜3（記録まで）**／記録の既定は**明示 ON ＋期間指定**
- 段階 4（分析面）・段階 5（リプレイ再生）は本設計の対象外（別ターンで裁定）
- 依頼者裁定 2026-09-10: リプレイ再生は**既存 replay_ui へ重ねる**（sim_ui へ再生装置を新設しない）

---

## 1. Objective（目的）

集計統計（`BacktestStats`）と確定トレード（`TradeRecord`）だけでは「なぜその時点で建てたのか」
「その瞬間に口座がどう見えていたか」が追えない。**ティック粒度の推移を全項目保存し、後から
何度でも読み返せる**ようにする。

## 2. 実測（すべて file:line を確認済み・憶測を含まない）

| # | 事実 | 出典 |
|---|---|---|
| 1 | エンジンに観測口は無い。外へ出る辺は run 終了時の `BacktestResult` 1 回のみ。`usecase/` に Observer/callback/event は 0 件 | `usecase/run_backtest.py:474` |
| 2 | `equity_curve` は**時刻を持たない裸の float 列**（append のみ） | `usecase/margin_guard.py:81,152` |
| 3 | 評価点は**ティック時刻を捨てている**（`_tick_time` を破棄） | `usecase/tick_schedule.py:71` |
| 4 | 指標は**足単位**（`update(bar_index)`）。ティック粒度の指標値はエンジンに存在しない | `usecase/ports.py:206-214`・`run_backtest.py:368` |
| 5 | `BacktestResult.indicator_values` は宣言のみで**誰も書き込まない**（死んだフィールド） | `usecase/models.py:202`（生産側 0 件） |
| 6 | 1 ヶ月・実ティックの評価点数 = **952,832 点**（JP225 2025-01 実測） | `data/marketdata/ticks/2025/01/*/JP225_ticks.parquet` |
| 7 | 同規模 12 列の保存: **parquet 61.6MB / JSON 271.1MB**（乱数＝圧縮最悪ケース実測。write 0.33s / read 0.08s） | 実測 2026-09-10 |
| 8 | `pyarrow>=15` は既存の宣言済み依存（**ライブラリ追加は不要**） | `simulator/requirements.txt` |
| 9 | 未登録指標名の公開エラー契約は既に `available`（登録名一覧）を運んでいる。**同じ一覧を作る規則が 3 箇所に在る** | `adapter/indicator/registry.py:31`・`null_registry.py:40`・`sim_ui/adapter/ea_registry_series_catalog.py:59-67`（例外プローブ） |
| 11 | 層規約の**実効的な権威は宣言でなく機械的検査**。`usecase` の許容は `domain` ＋同層＋stdlib ＋共有最下層（`marketdata`/`datawindow`）で、`pandas`/`pyarrow` は別ゲートが 0 件固定 | `tests/unit/test_layer_dependency_direction.py:332-337,366-375`・`tests/unit/test_tick_parquet.py:591-604` |
| 12 | `IndicatorPort` を**継承**する実装は 2 件のみ（test double は duck typing） | `adapter/indicator/registry.py:22`・`null_registry.py:29` |
| 13 | `_INJECTED_ONLY_KEYS` に `position_manager` が**入っていない**（既存の穴。本設計はこの穴を増やさない） | `sim_ui/main/composition_root_jobs.py:201` |
| 10 | M1 全履歴は 4,608,034 行。全 run 常時記録は GB 級になる（＝明示 ON が必須） | `data/marketdata/jp225_m1.csv` |

**却下した案**: MT5 同型の「実行中リアルタイム描画」。`unified_ui/router.py:302` が全読み＋
`:319` 自前 `Content-Length`＋`:153` `_HOP_BY_HOP` に `transfer-encoding` を含み落とすため、
SSE も chunked も**構造的に中継不能**。§12.7（実行中ジョブの部分結果は非公開）とも正面衝突する。

## 3. SRP: アクター単位のモジュール分割

「なぜ分けるか」は**改訂の動機（アクター）が別**であることに基づく。同居させると、どの改訂も
同じファイルを開くことになり、変更の影響範囲が構造から読めない。

| モジュール | 層 | アクター（改訂の動機） |
|---|---|---|
| `usecase/run_trace_ports.py` | usecase | **エンジンの観測契約**。「いつ観測させるか」が変わるときだけ動く |
| `usecase/indicator_catalog_ports.py` | usecase | **指標供給の列挙契約**。登録系列を列挙できることを要求する側 |
| `adapter/trace/trace_window.py` | adapter | **どこを残すか**（運用の要求＝期間指定・記録量の抑制） |
| `adapter/trace/columnar_run_trace.py` | adapter | **何を残すか（点粒度）**。評価点 1 つ 1 行の列集合 |
| `adapter/trace/indicator_trace.py` | adapter | **何を残すか（足粒度）**。指標系列。行粒度も協働相手も点粒度と交わらない（是正 D-3） |
| `adapter/trace/parquet_trace_store.py` | adapter | **どう永続化するか**（保存形式・圧縮・ファイル分割） |
| `sim_ui/adapter/trace_writer.py` | adapter | **ジョブ成果物の形**（`job_dir` の中の置き場と公開規則） |
| `sim_ui/main/run_job.py`（既存・加法） | main | **結線**（Composition Root。仕様→実体の束縛） |

依存方向は外側→内側の一方向のみ:
`main → adapter → usecase → domain`。usecase は adapter/framework/main を import しない。
同層 import は許可される（層ゲートの実測 `test_layer_dependency_direction.py:366-375`）ため、
`sim_ui/adapter → adapter/trace` は合法。`adapter/trace/__init__.py` を置くこと
（既存 adapter 配下 10 サブパッケージすべてが持つ＝循環ゲートのパッケージ粒度の前提）。

## 4. 段階 1: エンジンの観測口

### 4.1 Port（`usecase/run_trace_ports.py`・新規）

```python
class RunTracePort(abc.ABC):
    @abc.abstractmethod
    def observe(
        self, point: Any, account: Any, open_trades: Any, halted: bool
    ) -> None: ...
```

引数は既存 Port と同じく `Any` 注釈で統一する（是正 D-6。先例 `marker_ports.py:21-23`・
`ports.py:148-150` が `Account` をまさに `Any` で受けている）。

- **値オブジェクトを組んで渡さない**。観測は 1 run で評価点の本数（実測 952,832 回）起きる。
  DTO を組めば、記録しない実装（窓の外・OFF）では必ず捨てられるオブジェクトが同数生まれる。
  「作ってから捨てる」形は出力が正しいままなので状態検証では原理的に落ちない（ISSUE-450 と同型）。
- `ports.py` は無改変。新 Port は別ファイル（先例 `usecase/marker_ports.py`）。
  観測は実行に必要な境界ではない（既定は注入されない）。

### 4.2 接ぎ木点（`usecase/run_backtest.py`・既存 3 行の加法）

1. `__init__` に `run_tracer: Any = None`（既定 None＝byte-identical。先例は
   `session_calendar` / `position_manager` / `schedule` の 3 つ）
2. `_RunState` に `tracer: Any`（**必ず `state.tracer` で読む**）
3. `_run` の評価点ループ（`:461-464`）直後に 1 箇所だけ:

```python
            for point in points:
                open_trades, halted = self._evaluate_point(state, point, open_trades, halted)
                if state.tracer is not None:
                    state.tracer.observe(point, state.account, open_trades, halted)
```

ここが唯一の呼出点である理由: 「その評価点の全副作用が確定した直後」であり、
`account` が当該点のクォートで値洗いされた後の唯一の瞬間である。
`_evaluate_point` の内側へ入れると早期 return 3 経路ぶんの写しが必要になる（複製）。

**`None` 判定にする理由（Null Object を採らない理由）**: 既定経路に 1 回の no-op 呼出も
足さない。周囲の先例（`run_backtest.py:512` の `self._position_manager is not None`）と同型。

### 4.3 侵してはならない構造ゲート（実測済み・すべて既に緑）

| ゲート | 要求 |
|---|---|
| `tests/unit/test_run_backtest_responsibility_split.py`（メソッド集合 8 固定） | **新メソッドを足さない**（呼ぶだけ） |
| 同（`_execution` の import 許可は `admit_orders` のみ） | 低位関数を持ち込まない |
| `tests/unit/test_run_backtest_single_engine.py`（`_RunState` 残滓ゲート） | `state.tracer` で読む（読み手 0 なら赤） |
| 同（config 直読みゲート） | フック内で `getattr(config, ...)` を書かない。設定で on/off するなら `usecase/run_features.py` |
| 同（協働クラスは run につき 1 回） | tracer を `_run` の中で組まない |
| `tests/integration/test_run_backtest_fingerprint.py`（指紋 A/B/C） | 1 bit も動かない |

### 4.4 計算量テスト（絶対命令・段階 1 の必須要件）

**測るのは時間ではなく回数。回数そのものを期待値に焼き込まない**（固定するのは無駄の不在）。

| 検定 | 表明 |
|---|---|
| 未注入 | `run_tracer=None` のとき**観測の発行 0**（Spy で計測。呼出そのものが起きない） |
| 発行＝使用 | `記録行数 − 窓内の評価点数 = 0`。窓内の評価点数は**スケジュールを包む Spy が独立に数える**（数値をテストへ焼き込まない） |
| 1 点 1 回 | どの評価点も 2 回以上記録されない（同上の差が 0 であることで表明） |
| オーダー | バー数の異なる 2 点以上で「**窓の外の評価点は 1 行も記録されない**」を固定（記録量は窓が決め、run 長が決めない） |
| 非侵襲 | 観測あり／なしで `BacktestResult` の指紋が一致（観測が run の結果を変えない） |

**恒真にしない（工程 5 レビュー 🔴-1・🟡-2・🟡-3。変異試験で実測）**: 上表のどの表明も、
**記録 0 件で緑になってはならない**。`observe` の呼出を全削除する変異で表明 4（オーダー）・
表明 3・表明 1 の主 assert がいずれも緑のまま残った＝ISSUE-450 と同型（既存 1,233 件が緑の
まま 20 日間浪費を保護した）。各検定に**正の対照**（測っている対象が 0 件でないこと）を必ず
同梱する。

**引数の中身を縛る（工程 5 レビュー 🔴-2）**: `observe` が受ける `account` / `open_trades` /
`halted` は §6.1 の記録列（`balance` / `equity` / `open_count` / `open_volume_*` / `halted`）の
**供給元そのもの**である。点の識別子（`bar_index` / `tick_ordinal`）しか見ない検定では、
`open_trades` に `[]` を、`halted` に `False` を渡す変異が緑を保つ（実測）。段階 3 で
`open_count` が常に 0・`halted` が常に False で出力されても検出できない。

**呼出位置を縛る（工程 5 レビュー 🔴-3）**: 「評価点の全副作用が確定した**直後**」は §4.2 の
唯一の設計根拠であり、Port の事前条件（`run_trace_ports.py`）でもある。`observe` を
`_evaluate_point` の**前**へ移す変異が緑を保つ（実測）＝記録される `equity` / `balance` /
`open_count` が「評価前の値」へ静かにずれても検出されない。観測時点で当該点ぶんの equity が
既に積まれていることを表明して固定する。

**評価点の 3 クラスを網羅する（工程 5 レビュー 🟡-1）**: 合成バー点（ティック 0 件バーの
持ち越し点）・halt 後の点・保有ありの点は、既存 fixture（`_OneTickPerBar`）では**原理的に
生成されない**（実測 `synthetic_bar_points=0` / `halted_points=0`）。これらの観測を落とす変異が
緑を保つ。fixture を足して各クラスで表明 2 を回す。

**主語の確定（architecture-executor の TBD への裁定）**: 窓が絞るのは**記録行数**であって
`observe` の**呼出回数**ではない。tracer を注入した run では呼出は評価点数ぶん起きる
（窓判定はその中で行う）。「窓の外は発行 0」を呼出回数の主張として書くと、恒真か実装不能の
どちらかになる。よって上表の主語はすべて**記録行数**とする。

## 5. 段階 2: 評価点にティック時刻を載せる

実測 3 のとおり `tick_schedule.py:71` が `_tick_time` を捨てている。合成ティック経路では
4 疑似ティックの `bar.time` が全同一であり、**ティック粒度の時間軸が作れない**。

- `EvaluationPoint` に `tick_time: Any = None` を末尾へ追加（frozen・既定付き＝加法）
- `TickSchedule` は**ティックモデルが供給した時刻をそのまま載せる**（加工しない）
- `BarSchedule` は載せない（`None` のまま＝そのスケジュールはティックを持たない）
- 数値に影響しない（指紋不変）。時刻の正規化規則は `domain.bar_time.epoch_seconds` が単一ソース

### 5.1 「合成経路は `None`」という規則を置かない理由（初版の誤りの是正）

本設計書の初版は「合成ティック経路は `None`」と書いた。これは**判別材料が無いのに判別を
要求する規則**であり、実装は値比較のヒューリスティック（`tick_time != bar.time` なら実ティック）
でしか満たせない。実際 TDD 段でそう埋められた。これは症状の出る条件を避ける形＝対症療法である:

- バー時刻ちょうどに来た**実ティックが `None` に落ちる**（原理的に区別できない）
- そもそも `None` にして得られるものが無い（「合成か実か」は `tick_model` 設定＝**run 単位の
  性質**であり、点単位の列に属さない。§6.1 は別に `is_synthetic` を持つ）

**性能は撤回の理由にしない**（工程 5 レビュー 🟡-5・実測 2026-09-10）。当初この節は
「評価点ごと（952,832 回）に比較を hot path へ足す」ことを理由の 1 つに挙げていたが、実測すると
判別子の比較は **4.6 ns/点＝run 全体の 0.19%** にすぎない。一方、本設計が採用した段階 2 の
`EvaluationPoint` フィールド 1 本追加は **78.7 ns/点**（frozen dataclass の `object.__setattr__`
が 1 回増える）であり、段階 2 全体の増分は **+107 ns/点＝+4.5%**（952,832 点換算で +0.10 s/run）である。
4.6 ns を性能理由で退けながら 79 ns を無条件に足す論は成り立たない。撤回は**正当性のみ**で
十分に成立する（区別できないものを区別したことにするから誤りなのであって、遅いからではない）。

**根本**: 合成疑似ティックの時刻が `bar.time` であることは欠落ではなく**事実**である。実 MT5 の
OHLC 疑似ティックは分未満の時刻を持たない（`adapter/execution/tick_model.py:42` が
`bar.time` を返すのは、それが真値だからである）。バー内での順序は `tick_ordinal` が既に表す。

したがって規則は「**供給された時刻をそのまま運ぶ**」の 1 つだけとする。判別子は要らず、
比較も要らず、端の場合も生まれない。運ぶ情報は初版より**増える**（実ティックの時刻が
1 つも落ちない）。

**この是正は検定のアサーション変更を伴う**（合成経路で `None` を期待する検定は誤った仕様を
固定している）。よって工程 4（リファクタリング＝アサーション変更禁止）ではなく、
**工程 3（TDD）へ差し戻して直す**。

## 6. 段階 3: トレースの永続化

### 6.1 記録する列（1 評価点 1 行）

| 群 | 列 |
|---|---|
| 時刻・位置 | `time`（epoch 秒）・`bar_index`・`tick_ordinal`・`granularity`・`is_synthetic` |
| クォート | `eval_bid`・`eval_ask` |
| 口座 | `balance`・`equity`・`floating_pnl`・`margin`・`margin_level`・`swap`・`commission` |
| 保有 | `open_count`・`open_volume_buy`・`open_volume_sell` |
| 状態 | `halted` |

### 6.2 記録しないもの（複製禁止・実測に基づく）

- **指標値を毎点コピーしない**。指標は足単位（実測 4）であり、毎点複写は同じ値を
  ティック本数ぶん重複させるだけである。指標は `bar_index` を鍵に**別ファイル 1 本**へ足単位で残す。
- **trades / deals を写さない**。既に `report.json`（`report_payload_writer`）と
  `stats.json` が持つ。写せば同じ事実の 2 経路が静かに食い違う。
- **EA 内部の派生値は出せない**（正直な限界）。`adapter/strategy/ma_slope.py` の
  `slope` / `threshold` は `on_new_bar` のローカル変数に閉じ、戻り値は `list[Order]` のみ。
  見るには `StrategyPort` の契約変更という**別の裁定**が要る。

### 6.3 指標系列の列挙（新 Port・ISP）

`IndicatorPort` は `get(name)` / `update(bar_index)` しか持たない。「登録系列を列挙できる」は
実行に必要な契約ではなく、**要求する側が別**（分析・トレース）である。よって**別 Port** とする
（先例: `ports.py:249-284` の形式別 1 メソッド Port＝ISP・ISSUE-099、および別ファイル Port
6 本 `marker_ports` / `optimize_ports` / `scan_contacts_ports` / `sizing_ports` /
`validation_ports` / `vol_band_ports`）。

**名前は `IndicatorSeriesNamesPort`**（是正 D-1）。`IndicatorSeriesCatalogPort` は
`sim_ui/usecase/job_ports.py:84` に**同名の別契約が既に実在する**（`series_for(ea_name)`・
実装 `ea_registry_series_catalog.py:35`・消費者 2 件）。同名を作れば読み手が 2 つの契約を取り違える。

```python
class IndicatorSeriesNamesPort(abc.ABC):
    @abc.abstractmethod
    def names(self) -> "tuple[str, ...]": ...
```

生産実装（`PandasIndicatorRegistry` / `NullIndicatorRegistry`。`IndicatorPort` を継承する
実装は実測でこの 2 件のみ＝実測 12）が加法的に実装する。**併せて既存の重複を減らす**（実測 9）:

1. `registry.py:31` の `"available": list(self._series)` → `list(self.names())`
2. `null_registry.py:40` の `"available": []` → `list(self.names())`
3. `ea_registry_series_catalog.py:59-67` の例外プローブ `_series_names` → `names()` へ委譲
   （例外で名前を列挙するのは第 2 実装であり、`names()` の導入で不要になる）。
   既存 `IndicatorSeriesCatalogPort`（`series_for(ea_name)`）は ea_name→registry という
   **別の問い**なので残す。

### 6.4 期間ゲート（`TraceWindow`）

記録の既定は **明示 ON ＋期間指定**（依頼者裁定）。窓の判定は 1 箇所（`TraceWindow.contains`）
が所有し、半開 `[start, end)` は共有実体 `datawindow.half_open.HalfOpenEpochWindow` を用いる
（境界規則の第 2 の実装を作らない。先例 `adapter/execution/tick_model.py:185`）。
窓未指定（`start`/`end` とも `None`）は全区間。

### 6.5 出力（`job_dir/trace/`）

| ファイル | 所有者 | 内容 |
|---|---|---|
| `points.parquet` | `columnar_run_trace` | 6.1 の列（1 評価点 1 行） |
| `indicators.parquet` | `indicator_trace`（D-3） | `bar_index` ＋ 登録指標系列（足単位） |
| `meta.json` | `sim_ui/adapter/trace_writer` | 窓・記録行数・列の意味・生成元 job_id |

- `meta.json` の**列の意味は writer に書き写さない**。各 trace モジュールが公開する宣言
  （`COLUMNS`）を読むだけにする（書き写した時点で 2 箇所化する・D-3b）
- `report.json` / `stats.json` は**バイト不変**（既存出力へ 1 バイトも触らない）
- 書出し失敗は**終了コードを変えない**（run 自体は成功している）。理由は `trace_error.json` へ残す
  （先例 `run_job.py:_record_report_payload_error`）
- §12.7 不変: **run 完了後**に書き出して公開する（実行中の部分結果を出さない）

### 6.5.1 指標 registry への到達経路（是正 D-4・禁止事項を含む）

`indicators.parquet` は「その run が使った指標 registry」を要るが、その公開到達点は
**存在しない**（`RunBacktestInteractor.__init__` が `self._indicators` に私有・
`BacktestController` に registry のアクセサ無し）。

- **禁止**: `interactor._indicators` への到達。ISSUE-395/398（`controller._interactor`）・
  ISSUE-405（`getattr(sim_main, "_EA_FACTORIES")`）で 2 度是正済みのカプセル化破りと同型
- **禁止**: `RunBacktestInteractor` へ `indicators` プロパティを新設すること。
  `test_run_backtest_responsibility_split.py:275-284` はメソッド集合を 8 名に固定し、
  `property` も `ast.FunctionDef` として数えるため即赤になる
- **採る形**: `run_job.py`（main）が `build_ea_indicators(**backtest)` で registry を組み、
  `trace_writer` へ **Callable で注入**する（先例 `run_job.py:219-224` の `_supply_contacts`・
  `report_payload_writer.py:53-54` の `load_run_inputs` / `contacts_supply` と同一様式）

### 6.6 ジョブ仕様（加法）

```json
"trace": { "enabled": true, "start": "2025-01-06 00:00", "end": "2025-01-10 00:00" }
```

`sizing` / `strategy` / `settings` と同じ「不在・空なら OFF＝既存挙動 byte 等価」の形。
`build_interactor` へは `run_tracer` を**実体で**渡す（`position_manager` と同じ拡張点の扱い。
JSON スカラーでは渡せないものは `extensions` 経由）。

**是正 D-2**: `run_tracer` を `sim_ui/main/composition_root_jobs.py:201` の
`_INJECTED_ONLY_KEYS` へ**必ず追加する**。`allowed_backtest_keys()` は
`inspect.signature(build_interactor)` の反射（`:204-216`）であり、追加しないと
JSON から `backtest.run_tracer` を投入できてしまう。
（`position_manager` が同集合に無いのは既存の穴＝実測 13。本設計はその穴を増やさない。
穴自体の是正要否は別裁定。）

**是正 D-5**: `run_job.py` の `trace_writer` は **module 直下 import に足さない**（`:45-46`）。
関数内 import に限定する。同ファイルは OFF になり得る拡張をすべて関数内 import に置いており
（`:111-112` sizing / `:140-143` strategy / `:174-177` position_manager）、理由は `:108-109` に
「OFF の経路が実装の import に巻き込まれないようにするため」と明記されている。
module 直下へ足すと trace OFF の全ジョブが parquet 実装を読み込み、既存の隔離が黙って壊れる。

## 7. Exception（例外処理）

| 事象 | 挙動 |
|---|---|
| 記録 OFF | 観測口を注入しない（発行 0） |
| 窓の指定が不正（`start > end`・解釈不能） | `ConfigError` 相当で**即座に失敗**（既定値で黙って埋めない） |
| 指標系列を列挙できない供給 | 明示エラー（空で黙って続行しない＝「指標が無い run」に見せない） |
| parquet 書出し失敗 | run の終了コードは不変。理由を `trace_error.json` へ残す |

## 7.1 実装後に必ず実行するゲート（宣言でなく実行で確認する）

| ゲート | 何を守るか |
|---|---|
| `tests/integration/test_run_backtest_fingerprint.py` | 指紋 A/B/C が 1 bit も動かない |
| `tests/unit/test_run_backtest_responsibility_split.py` | メソッド集合 8・低位関数の非持込 |
| `tests/unit/test_run_backtest_single_engine.py` | `_RunState` 残滓・config 直読み・run 1 回構築 |
| `tests/unit/test_layer_dependency_direction.py` | 内側→外側の逆流 0 |
| `tests/unit/test_package_import_acyclicity.py` | 循環 0（パッケージ粒度） |
| `tests/unit/test_tick_parquet.py`（`:545-561`・`:591-604`） | usecase への pandas/pyarrow 漏出 0・遅延 import の持ち上げ禁止 |
| `sim_ui/tests/unit/test_sim_ui_import_direction.py` | `sim_ui/**`（`main/` 以外）→ `simulator.main` の禁止 |
| 新設: 計算量テスト（§4.4 の 5 表明） | 「作ってから捨てる」形の不在 |

**`BacktestResult.indicator_values`（`models.py:202`・生産側 0 件の死んだフィールド）を
トレースの受け皿として復活させてはならない**（§6.2 の複製禁止と衝突する）。撤去可否は別裁定。

## 8. 段階 4・5（本設計の対象外・申し送り）

- 段階 4: 分析面（口座・証拠金維持率・保有・DD・事象一覧を trades と突合）
- 段階 5: リプレイ再生。**既存 replay_ui へ重ねる**（裁定済み）。
  - 根拠: リプレイ固有 JS **4,415 行**（速度 6 段・コマ送り・足内更新・`INTRABAR_FORMING` 23 指標）
    を作り直さない。チャート/指標本体はライブと同一実体（symlink **133 件**）
  - 口座推移の器は既存（`#um-bottom-pane`・`hostKind:'bottomPane'`・チャートを畳まない裁定
    `unified_ui/web/index.html:61-67`）
  - ただし**モードは相互排他**（`mode_ui_view.js:99`）で 1 モード 1 層（`unified_root.js:328`）、
    `chartApi:true` の行は表示層を読まない（`:316`）。chart 層（リプレイ）と器層（口座推移）を
    1 モードへ同居させる**合成ハンドル 1 つ**の加法追加が要る
  - 前提: ISSUE-507（リプレイのマーカー時刻ゲート）
