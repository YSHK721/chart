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

**時刻表現: `trace.start` / `trace.end` は epoch 秒（JSON 整数）で受ける**（是正 F-1/F-2・
工程 2 レビュー 2026-09-10）。

初版はここで 2 つの誤りを犯していた。いずれも実測で崩れた:

| 初版の記述 | 実測した事実 |
|---|---|
| 「日付トークンの解釈は `main/tester_settings/window.py` の窓境界解決が既に持つ」 | **持っていない**。`_resolve_custom`（`window.py:143-162`）の入力は既に `datetime.date` オブジェクト（`usecase/tester_settings/models.py:153`）。やっているのは `_midnight_utc` と `+timedelta(days=1)` だけ。文字列解釈の実体は `framework/tester_settings/validation.py:216-229` の `_strict_date` **ただ 1 つ**で、private・書式は `YYYY.MM.DD` 固定・**時分を表現できない**。委ねることは原理的に不可能 |
| 「JSON は文字列しか運べない」 | **誤り**。JSON は数値を運び、`spec.json` は既に数値を運んでいる（`file_job_ledger.py:61` が `dict(submission.backtest)` を直列化し `point_size` 等が数値で入る）。`EPOCH_CONVERTERS` の第 1 エントリ `is_epoch_integer`（`bar_time.py:72`）は **epoch 整数をそのまま受理する** |

epoch 秒で受けると、迂回ではなく**構造から**次の 4 つが同時に満たされる:
(a) 新しい parser を書かない (b) `EPOCH_CONVERTERS` を広げない (c) 第 2 の日付規則を作らない
(d) **受付境界での変換そのものが要らなくなる**。

- `TraceWindow.of(start, end)` は `epoch_seconds()` を通してから
  `datawindow.half_open.HalfOpenEpochWindow` を持つ。両方 `None` は窓なし（全区間）
- `start > end` は **`TraceWindow` が `ConfigError` を送出する**。`HalfOpenEpochWindow` は
  空窓として黙って `contains=False` を返す仕様（`half_open.py:76-78` に明記。妥当性検査は
  呼出側の責務）であり、そこに頼ると「窓を間違えたのに 0 行で成功する」run ができる
- 整数なので **`submit_job` が受付時に検査できる**（framework loader への依存なし）。
  「投入は通ったが実行だけ落ちる」を作らない（`submit_job.py:89-91` の既存規律）
- 人が日付で入力する変換は **front の責務**（front は既に profile から 8 キーを導出している）

**禁止**: `EPOCH_CONVERTERS` に文字列エントリを足すこと（`bar.time` に文字列が通るようになる
別裁定）。受付側で `strptime` / `fromisoformat` を新しく書くこと（同じ文字列が経路で違う時刻に
化ける。ISSUE-401 で 32,400 秒差を実測済みの同型）。

### 6.5 出力（`job_dir/trace/`）

**出力は `job_dir` 直下の平坦名にする**（是正 F-3）。初版は `job_dir/trace/` というサブ
ディレクトリを指定していたが、**現行ルートから取得できない**（実測）:
`FileJobLedger._FILENAME_RE = \A[A-Za-z0-9_-]+\.[A-Za-z0-9]+\Z`（`file_job_ledger.py:41`）は
区切りを含む名前を受理せず、配信口 `serve_sim_jobs.py:180-190` は `_data, job_id, filename` の
**3 セグメント固定**である。サブディレクトリに置けば「書けたが誰も読めない」成果物になる。

| ファイル（`job_dir` 直下） | 所有者 | 内容 |
|---|---|---|
| `trace_points.parquet` | `columnar_run_trace` | 6.1 の列（1 評価点 1 行） |
| `trace_indicators.parquet` | `indicator_trace`（D-3） | `bar_index` ＋ **エンジンが実際に読んだ**指標値（足単位） |
| `trace_meta.json` | `sim_ui/adapter/trace_writer` | 窓・記録行数・列の意味・`marketdata_window` の有無・生成元 job_id |

**`trace_writer` は「公開規則」を所有しない**（是正 F-3）。公開可否の関門は
`FileJobLedger` と配信口が既に持っており、writer が持つのは**置き場（ファイル名）だけ**である。
`job_id` も writer が導出しない——`run_job.py`（Composition Root）が `job_dir.name` を渡す
（`FileJobLedger.job_dir` の規約の 2 つ目の実装を作らない）。

### 6.5.0 `time` 列の出所（是正 F-5・導出は 1 箇所）

初版は `time` 列の出所を書いていなかった。`point.tick_time` は **`None` になり得る**
（`BarSchedule` の点・ティック 0 件バーの持ち越し点。`evaluation_point.py` の契約・
`test_run_trace_observation.py` が実測固定）。`epoch_seconds(None)` は `ConfigError` になる。

**規則**: 点の epoch 時刻は `point.tick_time`、それが `None` のときは `point.bar.time`。
これは値からの**推定ではなく**、契約上宣言された不在（ティックを持たない点）への充当である
（§5.1 で撤回した判別子とは別物）。

**導出点は `ColumnarRunTrace` ただ 1 つ**とし、得た epoch 値を `time` 列にも
`window.contains(epoch)` にも渡す。窓判定側と列出力側の 2 箇所で導出すると、
「窓が通した点の `time` 列が窓の外」という食い違いが**例外を出さずに**起こる。
したがって `TraceWindow.contains` の引数は `EvaluationPoint` ではなく **epoch 秒（int）** である
（`TraceWindow` は `EvaluationPoint` を知らない＝`HalfOpenEpochWindow` の薄いラッパに純化する）。

### 6.5.2 指標トレースの鍵と、窓付き run での既知の不整合（F-4）

`indicator_trace` が記録するのは「**エンジンが実際に読んだ値**」である。戦略は
`series.iloc[bar_index]` で系列を位置参照する（`adapter/strategy/tc24051901.py:42`）。
トレースは run の事実の記録なので、同じ引き方で得た値をそのまま残す。

**既知の不整合（段階 3 が作る欠陥ではない・実測）**: 指標 registry は `data_path` の
**全 CSV** から作られる（`ea_bindings/sources.py:30` に窓の適用が無い）一方、`bars` は
`marketdata_window` で絞られる（`main/__init__.py:441-468`）。したがって
`marketdata_window` を伴う run では `bar_index` の指す先が両者で一致しない。
これは**エンジンに既存の性質**であり、別 ISSUE として起票する（本設計の範囲外）。

段階 3 が負う義務は**隠さないこと**だけである: `trace_meta.json` に
`marketdata_window` の有無を記録し、段階 4 の分析面が誤読しないようにする。
窓付き run の指標トレースを「正しい対応づけ」として提示してはならない。

### 6.5.2.1 窓の申告は**実際に走った run の入力**から採る（工程 4 レビュー 3-A・必須是正）

実行経路は 2 本ある（`run_job.py`）。`marketdata_window` の**供給元が経路で異なる**:

| 経路 | 窓の供給元 | 実測 |
|---|---|---|
| 現行（settings 不在） | `backtest` ブロック | front が送る `PROFILE_KEYS` 11 本に `marketdata_window` は**含まれない** |
| settings 有り | **`.ini` の `FromDate`/`ToDate`** → `resolve_data_window` | `kwargs_mapper._derived_bindings` が `marketdata_window ← window.marketdata_window` を導く |

したがって `trace_meta.json` を `spec["backtest"]` から組むと、**`.ini` で期間を絞った run が
`marketdata_window: false` / `indicator_bar_index_is_comparable: true` と偽って申告する**。
§6.5.2 が段階 3 に課した唯一の義務が、settings 経路でだけ果たされない。

**規則**: 窓の申告は **その run が実際に使った実効 kwargs**（settings 経路なら
`effective_to_interactor_kwargs(effective, binding)` の結果）から採る。`spec["backtest"]` を
無条件に読んではならない。

同じファイルの先例がこの誤りを既に名指している（`run_job.py` の `_write_report_payload`
まわり）: 表示用の足を `backtest` ブロックから取り直すと「`.ini` の期間窓が効いていない
全期間の足が『今の結果の足』として表示される。**窓を絞った run ほど食い違いが大きくなる**」。
トレースだけがその形に従っていなかった。

### 6.5.2.2 常駐メモリの実測と、窓なし ON の扱い（工程 5 レビュー 🟡-B）

記録は run のあいだ列を RAM に持ち、`ParquetTraceStore` が書出し時に DataFrame 化する。
初版はディスク量（§2 実測 7）だけを論じ、**常駐量を裁定していなかった**。工程 5 の実測:

| 入力 | 1 行あたり | 1 ヶ月・実ティック（952,832 点）換算 |
|---|---|---|
| 値が重複する | 146 B | 139 MB |
| 毎点異なる値 | 406 B | 387 MB |

（tracemalloc による外挿。実 run の RSS は未実測。書出し時の DataFrame 化でピークはさらに増える）

**裁定: 窓なし ON（`{"enabled": true}` のみ）は許す。** 1 ヶ月規模で数百 MB は開発機で許容範囲であり、
短い run では窓の指定を強いるほうが煩わしい。

**全履歴規模（4.6M バー）で問題になった場合の根本策は「逐次書出し（streaming）」であって、
上限を設けることでも窓を必須にすることでもない**。前者は原因（run 終了まで全量を持つこと）を
除去するが、後者 2 つは症状の出る条件を避けるだけであり、本プロジェクトが禁じる応急処置に当たる。

### 6.5.2.3 指標 registry の再構築コスト（工程 5 レビュー 🟡-C・実測）

§6.5.1 の裁定（私有属性へ到達せず `build_ea_indicators` を Callable で注入する）の帰結として、
run 中に組んだ registry とは**別にもう一度組む**。浪費ではない（結果は使う）が費用は残る。

**実測（工程 5）**: 実データ `data/marketdata/jp225_m1.csv`（4.6M 行）で `build_ea_indicators`
単発 **8.17 秒 / ピーク RSS 0.88 GB**。成功 run では既に `_load_run_inputs`（`build_interactor`）と
`_supply_contacts` が同種の再構築を行っており、trace ON はそこへ 1 回積む。

計算量検定は「供給 1 回」（writer 局所）を固定するが、**ジョブ全体の再構築回数は誰も見ていない**。
1 回化（`build_interactor` の戻りから供給する等）は段階 4 と合わせて裁定する（本設計では変えない）。

### 6.5.2.4 部分成果物が残る場合の規則（工程 5 レビュー 🟡-E・実測で再現済み）

書出しは points → indicators → meta の順に行う。指標側で失敗すると
**`trace_points.parquet` だけが残り `trace_meta.json` が無い**状態で公開される。

**実測（工程 5・指標 registry の供給だけを失敗させた traced run）**:
```
exit: 0
artifacts: report.md, report_payload_error.json, spec.json, stats.json,
           trace_error.json, trace_points.parquet
points rows: 40   meta present: False
```

終了コードは 0 のまま（§6.5 の裁定どおり run 自体は成功している）で、理由は `trace_error.json` に残る。

**段階 4 の読み手が守る規則（必須）**: `trace_points.parquet` を読む前に
**`trace_error.json` の不在**と **`trace_meta.json` の存在**を確認する。meta 不在の parquet は
「壊れた run」ではなく「書き切れなかったトレース」であり、区別できないまま読むと
不完全な記録を完全なものとして提示することになる。

### 6.5.3 作らない抽象（YAGNI・工程 2 レビュー）

- **`TraceStorePort` を作らない**。保存形式の第 2 実装は要求に無く、JSON は §2 実測 7 で
  4.4 倍のサイズと判明済み＝採らない。`ColumnarRunTrace` → `ParquetTraceStore` は具象直結でよい
- **`TraceWriterPort` を作らない**（`report_payload_writer` と同じく Port 無しのモジュール）
- `adapter/trace/__init__.py` は **docstring のみ・再輸出禁止**。`ParquetTraceStore` を
  再輸出すると `trace_window` を import しただけで pyarrow が読まれ、D-5 の隔離が黙って壊れる
  （先例 `adapter/repository/__init__.py` / `adapter/indicator/__init__.py` はいずれも docstring 1 行）
- `columnar_run_trace` / `indicator_trace` / `trace_window` は **pandas / pyarrow を import しない**。
  列は素の `list` で持ち、`parquet_trace_store` が受け取って初めて DataFrame 化する

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
"trace": { "enabled": true, "start": 1736121600, "end": 1736467200 }
```
（`start` / `end` は epoch 秒。理由は §6.4。省略・`null` は「窓なし＝全区間」）

`sizing` / `strategy` / `settings` と同じ「不在・空なら OFF＝既存挙動 byte 等価」の形。

### 6.6.1 受付経路は端から端まで結線する（Blocker・是正 F-結線）

**`file_job_ledger.py:59-73` を触らないと、`trace` は子プロセスへ 1 バイトも届かない。**
HTTP は 202 を返し、run は成功し、**トレースだけが無言で出ない**。ISSUE-291
（「サーバ分岐を作っても front が送らなければ無言で死ぬ」）の再発そのものである。

実測した全ホップ:
```
front  sim_submission_builder.js:73-87 / job_submit_client.js:51-55   ← trace の口が無い
  ↓ POST /sim/jobs
fw     serve_sim_jobs.py（do_POST）→ controller.submit(raw_body)
adp    job_api_controller.py:61-69     body.get("sizing"/"strategy"/"settings") を JobSubmission へ
uc     job_models.py:57-60             dataclass フィールド 4 本
uc     submit_job.py:88-115            受付検証 → ledger.create(submission)
adp    file_job_ledger.py:59-73        ★ spec.json を書く唯一の場所（4 キーを手書き列挙）
  ↓ 子プロセス
main   run_job.py:402-461              spec を読み extensions を組む
```

**根本是正（1 キー足すだけで終わらせない）**: `file_job_ledger.create` の手書き dict 列挙
**そのものが欠陥源**である。`JobSubmission` にブロックが増えるたびに同じ取り残しが起きる
（今回が 5 本目で、実際に取り残された）。`dataclasses.fields(JobSubmission)` からの
**機械導出**へ置換する（`backtest` は必須、他は `dict(v) if v else None`）。
同型の先例は `run_job.py:246-250` の `frozenset(field.name for field in fields(SymbolSpec))`。

**機械的強制（新設ゲート・必須）**: 「ブロックが端から端まで結線されていること」を宣言駆動で
固定する検定を 1 本作る。`fields(JobSubmission)` の各名 `n` について、構文木で次の 3 点を表明する:

1. `job_api_controller.submit` に `body.get("n")` が在る
2. `file_job_ledger.create` の出力 dict に鍵 `n` が在る
3. `run_job.main` に `spec.get("n")` が在る

**期待値の名前一覧をテストへ書かない**（`fields()` から採る）。これが無い限り、6 本目の
ブロックで同じ事故が必ず再発する。正の対照（`fields()` が空でないこと）を同梱する。

### 6.6.2 front の投入口（段階 3 の対象に**含める**）

依頼者裁定は「**明示 ON** ＋期間指定」である。明示 ON を人が表現できなければ機能は存在しない。
よって `sim_submission_builder.js` / `job_submit_client.js` へ trace の口を足すことは
段階 3 の範囲に**含む**。日付 → epoch 秒の変換は front の責務（§6.4）。

### 6.6.3 `run_tracer` 追加が赤にする手書き表 2 件（是正 F-6）

`build_interactor` の公開シグネチャは 3 つの本番モジュールが `inspect.signature` で反射する
「事実上の HTTP スキーマ」であり、加えて**手書きの表が 2 つ**それを厳密等価で固定している。
`run_tracer` を足すと両方が赤になるので、**同じコミットで**追記する:

- `simulator/tools/walk_forward_cli.py:39-59` の `_BUILD_INTERACTOR_KEYWORDS`
  （`test_walk_forward_cli.py:89-93` が `==` で厳密等価を表明）
- `simulator/tests/integration/test_ea_bindings_are_declaration_driven.py:328-337, 376` の
  `_EXPECTED`（**引数の並びまで**固定）と `injected_only`

なお `_BUILD_INTERACTOR_KEYWORDS` が手書きであること自体は既存の欠陥である
（コード内コメントが「実際にそれで壊れた」と記録している）。導出化は別 ISSUE とし、
本設計では追記に留める（本設計が作った欠陥ではないものを、本設計のスコープで直さない）。
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
| **`observe` が例外を送出** | **エンジンは握らない**（下記裁定） |
| parquet 書出し失敗 | run の終了コードは不変。理由を `trace_error.json` へ残す |

### 7.0 `observe` の例外契約（実装着手前の裁定・2026-09-10）

**裁定: `observe` は**運用上の失敗**で例外を送出してはならない。これは実装側の義務であり、
エンジンは `try` / `except` を置かない。**

**射程の限定（工程 4 レビュー 3-B・初版の文面が §6.5.0 と両立しなかった）**: 「送出しない」が
掛かるのは**観測の都合で起きる失敗**（記録先が一杯・列を作れない等）だけである。
**呼出側の契約違反は送出してよい**——むしろ送出しなければならない。

具体例: §6.5.0 は epoch の導出点を `ColumnarRunTrace` ただ 1 つと命じており、その導出は
`epoch_seconds()` を通る。渡された `bar.time` が受理集合（epoch 整数 / `datetime` /
`numpy.datetime64`）の外なら `ConfigError` が出る。これは**エンジンが契約に無い値を渡した**
という事実であり、黙って握れば「時刻の壊れたトレース」が静かに出力される。

初版はこの区別を書かず「例外を送出してはならない」とだけ述べたため、§6.5.0 の要求と
文面上両立しなかった。`RunTracePort` が宣言する事後条件は 2 つ（状態を変えない・戻り値なし）
であり「送出しない」を含まない——実装は正しく、誤っていたのは本節の文面である。

なぜエンジンが握らないか（3 案の比較）:

| 案 | 何が起きるか | 採否 |
|---|---|---|
| エンジンが握って無視する | 記録が欠けても**誰も気づけない**（沈黙する失敗）。段階 4 の分析面は「そういう run だった」として読む | **却下** |
| エンジンが握って run を落とす | 観測の失敗で成功した計算を捨てる。先例 `run_job.py:311-323`（「表示の失敗で成功した計算を捨てない」）と矛盾 | **却下** |
| 実装の義務にする（採用） | 契約違反は隠れず落ちる。エンジンは観測の失敗を**意味づけられない**（何が起きたか知らない）ので、握るのは責務外 | **採用** |

この裁定が現実的である根拠（実測）: `observe` は run 完了後の I/O を持たない（§6.5＝
永続化は run 完了後）。実装がやるのは列への append だけであり、送出する正当な理由が無い。
記録量の上限のような運用要件が生じたら、それは**窓を狭めることで実装内で解く**（`TraceWindow`
の関心）のであって、例外で表明する事柄ではない。

なお `try` を hot path へ置かないのは、CPython 3.11+ のゼロコスト例外により実行コストが
問題になるからではない（**性能は理由にしない**——§5.1 で同じ誤りをしている）。理由は
上表の 2 案がどちらも壊れているからである。

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

## 7.2 段階 3 の通過条件（9 件・工程 5 レビューの統合リスト・すべて現状を実測済み）

| # | 通過条件 | 現状（実測） |
|---|---|---|
| 1 | 生産実装の非侵襲を **fingerprint 級**で測る（A/B/C へ tracer を注入した run の `stats_sha256` / `trades_sha256` が非注入と一致）。加えて `RunTracePort` の全具象を parametrize で回し、`run_trace_ports.py` の「射程」記述を実体に合わせる | `grep -c tracer test_run_backtest_fingerprint.py` = **0**。現在の非侵襲検定はテスト Spy 1 個しか縛っていない |
| 2 | `observe` の例外契約 | **§7.0 で裁定済み**（実装の義務・エンジンは握らない）。§7 の表へ行を追加済み |
| 3 | `build_interactor` へ `run_tracer` を足すコミットと `_INJECTED_ONLY_KEYS` へ足すコミットを**分離しない**（D-2）。`trace_writer` は関数内 import に限定（D-5） | `grep -c run_tracer simulator/main/__init__.py` = **0**（投入路の穴は未開通） |
| 4 | Port 宣言（`RunTracePort.observe`）と呼出点（`run_backtest.py:481`）の**引数並びの一致を構文木で固定** | 引数順入替の変異が緑＝ドリフト自由 |
| 5 | `tick_time` の型正規化（`datetime64` / epoch int → epoch 秒）を `domain.bar_time.epoch_seconds` 単一ソースで行い、型を検定で固定 | 型を固定する検定 **0 件** |
| 6 | `halted` の**両側**拘束（常に `True` も赤にする） | 「常に True」変異が緑。halt しない run の `halted_flags` を検査する検定が 0 件 |
| 7 | `open_count` の**値**拘束（存在検査 `any(n>0)` からの脱却） | `+1` バイアス変異が緑 |
| 8 | **1 バー複数ティック**の fixture を `_FIXTURES` へ追加（`tick_ordinal > 0` の行を作る）。`points.parquet` の主キーが `(bar_index, tick_ordinal)` である以上、鍵の一意性を実形状で測る。材料は同ファイル内の `_NTicksPerBar` に既に在る | 全 fixture が `points_per_bar=1`・`tick_ordinals ⊂ {0, -1}` |
| 9 | 検定名・docstring の主張と**実際の検出範囲を一致させる**（🔴-4 と同型の再発防止） | 「写しでないこと」を主張する検定が写しを検出しない |

**棄却（実測に基づく）**: `run_backtest.py:480-481` の属性引き巻き上げ。ガードのコストは
4.6 ns/点＝run 全体の 0.19% であり、§4.2 の改訂を伴う変更に見合わない。しかも §4.3 の
「`state.tracer` で読む」ゲートの意図（残滓検出）を弱める。

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
