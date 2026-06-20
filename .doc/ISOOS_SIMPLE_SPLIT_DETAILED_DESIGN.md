# IS/OOS 単純分割（Simple Split）詳細設計書

## 1. 文書情報

- 作成日：2026-06-20
- バージョン：v1.0.0
- 作成者：system-internal-design エージェント
- 上位設計（唯一の正）：`/workspaces/app/.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md` v0.2.0
- 対象システム種別：バッチ／オフライン分析ツール（committed バックテストエンジン上のオーケストレーション層）
- 本書の責務：基本設計 v0.2.0 を「誰が実装しても同じ結果になる決定論的水準」（クラス名・メソッドシグネチャ・主要処理フロー・テスト契約）へ落とし込む。
- 変更履歴：
  - v1.0.0 (2026-06-20) 初版。基本設計 v0.2.0 の確定済前提を実装可能水準へ詳細化。

### 1.1 基本設計からの逸脱の有無

逸脱なし。本書は基本設計 v0.2.0 の確定事項（呼出経路 B-1・IS truncation option b・`slice_is_bars` 純関数・区間定義 H-1・出力先検証 H-2・劣化指標 C-2・保証境界 C-1）を**実装シグネチャへ具体化するのみ**であり、新規の設計判断を導入しない。設計判断を要した箇所（公開 API のフィールド型・例外クラス・関数分割）はすべて §10 の根拠表に代替案比較・定量評価とともに併記する。

### 1.2 実コードによる前提実証（本詳細設計の土台）

本書の全設計判断は、以下の実コード／実データの直接確認に基づく（証拠先行）。

| 前提 | 実証箇所 | 確認内容 |
|---|---|---|
| `execute(request)` が `BacktestResult` を返す | `simulator/usecase/run_backtest.py:153` `def execute(self, request: RunBacktestRequest) -> BacktestResult` | UC が直接呼べる戻り値型 |
| `execute` が `request.bars` を尊重 | `run_backtest.py:203`・`488` `for bar_index, bar in enumerate(bars)` | 差し替えた bars が走る |
| `controller.run` は bars を再ロード | `simulator/adapter/controller.py:50` `bars = self._market_data.load(...)` | run 経由では truncation 無効 |
| `controller._interactor` 読み取り可 | `controller.py:35` `self._interactor = interactor` | 属性改変なしで `.execute` 呼出可 |
| `trading_start` warmup 機構 | `run_backtest.py:208-209`・`492-494` `if trading_start is not None and bar.time < trading_start: continue` | OOS の split 前 warmup を実現 |
| registry は位置インデックス保持・`update` no-op | `simulator/adapter/indicator/registry.py:25-35`・`53` | head 切り後も位置 0..k-1 整合 |
| strategy は `.iloc[bar_index]` 参照 | `pro_fit_band.py:69`・`tc24051901.py:42` | 位置インデックス整合の根拠 |
| `BacktestStats` の劣化対象 6 フィールド実在 | `simulator/usecase/models.py:97-105` | profit/profit_factor/recovery_factor/expected_payoff/sharpe_ratio/trades |
| **IS が full の head-prefix**（option b の決め手） | `bars_m1.csv` と `bars_m1_is.csv` を直接比較 | 両者 line 2..22772 byte-identical（03-23 01:00:00〜04-14 23:59:00）。full は line 22773 で 04-15 01:01:00 へ継続、IS は 04-14 23:59:00 で終端 |
| 先例 trading_start 二値 | `reconcile_is.py:36`=`2026-04-01`／`reconcile.py:36`=`2026-04-15` | IS/OOS で別 trading_start |
| tools 先例パターン | `simulator/tools/export_trade_markers.py:9-14` | build_interactor→`controller._interactor.execute`→新規 OUT のみ |

---

## 2. モジュール／クラス詳細設計

### 2.0 ファイル構成（新規追加のみ・committed 無改変）

| ファイル | 層 | 新規/既存 | 責務 |
|---|---|---|---|
| `simulator/usecase/run_is_oos.py` | usecase（新規） | 新規 | オーケストレーション UC。`slice_is_bars` 純関数・`RunIsOosRequest`/`RunIsOosResult`/`DegradationReport`/`MetricDegradation` DTO・`run_is_oos(...)` 関数・`extract_metrics`／劣化算出。domain のみ依存。 |
| `simulator/tools/run_is_oos_cli.py` | tools（新規） | 新規 | 実行入口。CLI 引数解釈・読み取り専用ロード・`build_interactor` で `(controller, request)` 構築・`run_segment` コールバック構成・`run_is_oos` 呼出・出力先検証・JSON/Markdown 整形・新規 OUT 書込。 |
| `simulator/tests/unit/test_slice_is_bars.py` | test（新規） | 新規 | `slice_is_bars` 境界・空区間。 |
| `simulator/tests/unit/test_degradation_report.py` | test（新規） | 新規 | ratio/delta・ゼロ除算。 |
| `simulator/tests/unit/test_output_guard.py` | test（新規） | 新規 | 出力先検証関数の拒否プレフィクス。 |
| `simulator/tests/unit/test_is_oos_barmode_index.py` | test（新規） | 新規 | bar-mode の registry 位置整合（causal EMA bit-identical）。 |
| `simulator/tests/integration/test_is_oos_stop_probe.py` | test（新規） | 新規 | 先例 2026-04 再現（IS net+11370/5224・OOS net-4020/2438）＋mtime 不変。 |

committed（`simulator/domain`・既存 `simulator/usecase`・既存 `simulator/adapter`・`simulator/main`）への差分は 0 行（NFR-S2／C2）。

### 2.1 `simulator/usecase/run_is_oos.py`（オーケストレーション UC）

#### 2.1.1 モジュール先頭ポリシー（committed 規約の継承）

```python
"""UC: IS/OOS 単純分割オーケストレーション（基本設計 v0.2.0 / FR-01..06）。

committed エンジンを無改変で IS 区間 [start, split) と OOS 区間 [split, end) で別々に
実行し、両 BacktestStats と劣化指標を返す。エンジン実行手段は run_segment コールバック
として呼出側（tools 層）から注入する（DIP・usecase→domain のみ依存）。

usecase は domain のみ依存（adapter/framework/main・pandas を import しない）。
時刻は domain と同じく numpy.datetime64 | int（pd.Timestamp を前提にしない）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable
```

`pandas`・`simulator.main`・`simulator.adapter` を import しないこと（クリーンアーキ依存方向。`run_backtest.py:8` の既存規約を継承）。`BacktestStats` 型は `simulator.usecase.models` から import 可（usecase 層内・同階層）。

#### 2.1.2 純関数 `slice_is_bars`

```python
def slice_is_bars(bars: Any, split: Any) -> list:
    """IS 区間用に bars の head 区間（bar.time < split）を返す純関数（B-2/H-3）。

    入力:
      bars : domain Bar の列（イテラブル）。各要素は .time（split と比較可能な型）を持つ。
      split: 分割境界。bar.time と比較可能な型（numpy.datetime64 / epoch int）。
             split 自身は OOS 側（半開区間 [split, end)）。

    戻り値:
      bar.time < split を満たす「先頭連続区間」を新規 list で返す。

    境界規約（決定論）:
      - bar.time < split  -> 保持（IS 取引区間 + IS warmup）
      - bar.time == split -> 除外（split は OOS 側）
      - bar.time > split  -> 除外
      入力は時刻昇順（committed の load 保証）を前提とし、最初に bar.time >= split を
      満たした位置で打ち切る（head-prefix。中抜き・tail 切りは行わない）。
      これにより保持バーの位置インデックス 0..k-1 が full df 由来の指標 registry と整合する。

    副作用: なし（入力 bars を破壊しない・新規 list を返す）。
    """
```

実装（決定論・参照実装）：

```python
    result: list = []
    for bar in bars:
        if bar.time < split:
            result.append(bar)
        else:
            break  # 時刻昇順前提：最初の split 到達で head 区間は確定
    return result
```

- **境界の正当性（option b byte-exact の根拠）**：`bars_m1.csv`（full）と `bars_m1_is.csv`（IS）は 03-23 01:00:00〜04-14 23:59:00 が byte-identical で、full は 04-15 01:01:00 へ継続する（§1.2 実証）。`slice_is_bars(full, split=2026-04-15)` は `bar.time < 2026-04-15` を保持するため、結果バー集合は `bars_m1_is.csv` と一致する。位置 0..k-1 の `price` 列が不変であり、causal EMA（`main/__init__.py:211-225` seed=price[0]・α=2/(period+1) 前方再帰）は full/IS 長で位置 0..k-1 が bit-identical。よって registry 非再構築でも IS の指標値は一致する。
- **`break` 採用の根拠**：時刻昇順前提（committed `market_data.load` の保証）で最初の `>= split` 到達後は全て OOS 側のため、フィルタ全走査（`[b for b in bars if b.time < split]`）より早期打ち切りが正しく、かつ「head-prefix のみ保持・中抜き禁止」の契約を構造的に表現する（§10 判断 1）。

#### 2.1.3 DTO 定義

```python
@dataclass
class RunIsOosRequest:
    """単純分割の入力（基本設計 §6.1・§5.2）。

    エンジン構築パラメータ（lot_size 等）は run_segment コールバック側（tools 層）に
    閉じるため本 Request は持たない。本 Request は「区間スライスと劣化算出に UC が
    必要とする最小集合」に限定する。
    """
    split: Any              # 分割境界（bar.time と比較可能な型・必須）
    is_trading_start: Any   # IS 取引開始境界（必須・H-1。data 先頭〜is_trading_start=IS warmup）
    # メタ情報（出力レポートへの埋め込み用・任意）
    metric_names: "tuple[str, ...]" = (
        "profit", "profit_factor", "recovery_factor",
        "expected_payoff", "sharpe_ratio", "trades",
    )
```

```python
@dataclass
class MetricDegradation:
    """1 指標の IS/OOS 劣化（C-2: ratio・delta 両格納）。"""
    name: str
    is_value: float
    oos_value: float
    ratio: "float | None"   # OOS/IS。IS_value == 0 のとき None（未定義）
    delta: float            # OOS - IS（常に格納）
```

```python
@dataclass
class DegradationReport:
    """全主要指標の劣化集合（C-2）。"""
    metrics: "list[MetricDegradation]" = field(default_factory=list)

    def by_name(self, name: str) -> "MetricDegradation | None":
        for m in self.metrics:
            if m.name == name:
                return m
        return None
```

```python
@dataclass
class RunIsOosResult:
    """単純分割の出力一式（基本設計 §5.2 IsOosResult）。"""
    is_stats: Any          # BacktestStats（IS 区間）
    oos_stats: Any         # BacktestStats（OOS 区間）
    degradation: DegradationReport
```

- **型注釈に `Any` を用いる箇所**：`split`/`is_trading_start`（時刻型は呼出側で正規化済・usecase は `numpy.datetime64|int` 前提だが pandas 非依存にするため具体型束縛しない）、`is_stats`/`oos_stats`（`BacktestStats` だが import 循環回避と疎結合のため `Any`）。これは `models.py:13` の既存規約（`from typing import Any`）と整合。

#### 2.1.4 run_segment コールバック契約

```python
# 型エイリアス（ドキュメント用）。run_segment は UC が IS/OOS 各区間を実行する手段。
#   引数: bars（full or IS-truncated）, trading_start（IS=is_trading_start / OOS=split）
#   戻り値: BacktestStats（当該区間の成績）
RunSegment = Callable[[Any, Any], Any]
```

- **契約**：`run_segment(bars, trading_start) -> BacktestStats`。tools 層が `build_interactor(...)` で構築した `(controller, request)` を閉包し、`request.bars = bars` / `request.trading_start = trading_start` を設定して `controller._interactor.execute(request)` を呼び、`result.stats` を返す（B-1）。
- **UC が main 非依存である根拠**：UC は `run_segment` を `Callable` として受けるのみで `build_interactor` を import しない。依存方向は tools→usecase（内向き・DIP）。

#### 2.1.5 公開関数 `run_is_oos`

```python
def run_is_oos(
    *,
    request: RunIsOosRequest,
    full_bars: Any,
    run_segment: RunSegment,
) -> RunIsOosResult:
    """IS/OOS を 2 回実行し劣化指標つき結果を返す（FR-01..06）。

    引数:
      request    : RunIsOosRequest（split・is_trading_start・metric_names）
      full_bars  : 全期間バー列（warmup 含む・読み取り専用・OOS 入力）
      run_segment: 1 区間実行コールバック（B-1: controller._interactor.execute 経由）

    処理:
      (1) 入力検証（範囲・空区間判定式 M-1）。NG は IsOosValidationError。
      (2) is_bars = slice_is_bars(full_bars, request.split)
      (3) IS  : is_stats  = run_segment(is_bars,  request.is_trading_start)
      (4) OOS : oos_stats = run_segment(full_bars, request.split)
      (5) degradation = build_degradation_report(is_stats, oos_stats, request.metric_names)
      (6) RunIsOosResult(is_stats, oos_stats, degradation)

    戻り値: RunIsOosResult
    例外  : IsOosValidationError（区間空・範囲不正）
    """
```

実装フロー（決定論）：

```python
    full_list = list(full_bars)
    is_bars = slice_is_bars(full_list, request.split)
    # 入力検証（M-1: IS バー数 >= 1 かつ OOS で bar.time >= split のバー数 >= 1）
    oos_count = sum(1 for b in full_list if b.time >= request.split)
    if len(is_bars) < 1:
        raise IsOosValidationError("IS 区間が空（bar.time < split を満たすバーが 0 件）")
    if oos_count < 1:
        raise IsOosValidationError("OOS 区間が空（bar.time >= split を満たすバーが 0 件）")
    # 範囲整合（start <= is_trading_start <= split）。end 側は OOS 非空で担保済。
    if not (request.is_trading_start <= request.split):
        raise IsOosValidationError("is_trading_start は split 以下である必要がある")
    is_stats = run_segment(is_bars, request.is_trading_start)
    oos_stats = run_segment(full_list, request.split)
    degradation = build_degradation_report(
        is_stats, oos_stats, request.metric_names
    )
    return RunIsOosResult(is_stats=is_stats, oos_stats=oos_stats, degradation=degradation)
```

- **`full_bars` を `list()` で 1 度だけ実体化する理由**：イテレータの 2 度走査（IS slice と OOS）を避けるため。`slice_is_bars` 内で再度 `for` を回すが、`full_list` 渡しで再走査可能。
- **例外クラス**：`IsOosValidationError`（`run_is_oos.py` 内に定義）。基本設計 §7.2「IS/OOS いずれかの失敗を明示して中断（部分結果の黙殺をしない）」に従い、検証 NG はエンジン呼出前に送出する。domain 例外（`BacktestError` 系）はエンジンから送出され UC を素通りして tools 層が捕捉する（基本設計 §6.3）。

#### 2.1.6 指標抽出・劣化算出（後段②再利用点・基本設計 §7.4）

```python
def extract_metrics(stats: Any, names: "tuple[str, ...]") -> "dict[str, float]":
    """BacktestStats から劣化対象指標を name->値 で抽出する（後段② ObjectivePort 前身）。

    names の各要素は BacktestStats の実在フィールド名（getattr で取得）。
    """
    return {n: float(getattr(stats, n)) for n in names}


def build_degradation_report(
    is_stats: Any, oos_stats: Any, names: "tuple[str, ...]"
) -> DegradationReport:
    """IS/OOS の BacktestStats から ratio・delta を両格納した劣化レポートを構築（C-2）。"""
    is_m = extract_metrics(is_stats, names)
    oos_m = extract_metrics(oos_stats, names)
    metrics = []
    for n in names:
        iv = is_m[n]
        ov = oos_m[n]
        ratio = (ov / iv) if iv != 0.0 else None  # ゼロ除算は ratio=None（delta は常に格納）
        metrics.append(
            MetricDegradation(name=n, is_value=iv, oos_value=ov, ratio=ratio, delta=ov - iv)
        )
    return DegradationReport(metrics=metrics)
```

- **`extract_metrics` を独立関数に切り出す根拠**：基本設計 §7.4「指標抽出を独立関数に切り出し後段② `ObjectivePort` が再利用」を実装で満たす（§6 拡張点）。

### 2.2 `simulator/tools/run_is_oos_cli.py`（実行入口）

#### 2.2.1 責務とモジュールポリシー

責務（`export_trade_markers.py:6-14` の Composition Root 利用側パターンを継承）：

1. CLI 引数解釈（`argparse`）。
2. `build_interactor(...)` で `(controller, request)` を構築（committed 公開 IF のみ）。
3. `run_segment` コールバックを構成（`controller._interactor.execute` 経由・B-1）。
4. `split`・`is_trading_start` を `bar.time` 型へ正規化（pandas 依存は tools 層に閉じる・TBD-5）。
5. `run_is_oos(request=..., full_bars=request.bars, run_segment=...)` を呼ぶ。
6. 出力先検証（`assert_safe_output_dir`）→ JSON/Markdown 整形 → 新規 OUT 書込。

`pandas`・`simulator.main` の import は tools 層では許容（Composition Root 利用側）。

#### 2.2.2 CLI 引数

| 引数 | 型 | 必須 | 既定 | 説明 |
|---|---|---|---|---|
| `--data-path` | str | 必須 | — | 価格データ CSV（読み取り専用） |
| `--ea-name` | str | 必須 | — | 戦略名（`StopEntryProbe_EA` 等・`build_interactor` 引数） |
| `--split` | str | 必須 | — | 分割境界（ISO 日時。例 `2026-04-15`） |
| `--is-trading-start` | str | 必須 | — | IS 取引開始（例 `2026-04-01`・H-1） |
| `--out-dir` | str | 必須 | — | 新規出力先ディレクトリ（検証対象） |
| `--symbol` `--period` `--initial-deposit` `--contract-size` `--lot-size` `--stop-loss-points` `--take-profit-points` `--entry-offset-points` `--entry-type` `--ma-period` `--ma-method` ほか | — | — | — | `build_interactor` の戦略・シンボルパラメータをそのまま透過 |
| `--config-override` | str（`k=v` 反復） | 任意 | — | `config_overrides` dict 構成（`pending_lifecycle=true` 等） |

#### 2.2.3 run_segment コールバックの構成（参照実装）

```python
def make_run_segment(controller: Any, request: Any) -> Callable[[Any, Any], Any]:
    """build_interactor が返した (controller, request) を閉包し run_segment を構成（B-1）。"""
    def run_segment(bars: Any, trading_start: Any) -> Any:
        # request は dataclass。区間ごとに bars/trading_start のみ差し替えて execute。
        request.bars = bars
        request.trading_start = trading_start
        result = controller._interactor.execute(request)  # B-1: controller.run は使わない
        return result.stats
    return run_segment
```

- **`request` を共有して属性差し替えする根拠**：IS→OOS は逐次実行（並行でない）であり、各 `execute` は同期完結する。属性差し替えは execute の前にのみ行う。並行化は後段③で別 request 複製として対応（基本設計 §7.1 スケーラビリティ）。

#### 2.2.4 時刻正規化（TBD-5 の tools 層確定）

```python
def normalize_time(value: str, sample_bar_time: Any) -> Any:
    """CLI 文字列 split/is_trading_start を bar.time と比較可能な型へ正規化（tools 層に pandas を閉じる）。

    sample_bar_time が numpy.datetime64 系なら pd.Timestamp(value).to_datetime64()、
    epoch int 系なら int(pd.Timestamp(value).timestamp()) を返す。
    """
```

- **責務を tools 層に置く根拠**：usecase は `numpy.datetime64|int` のみ前提（`models.py:8`）で pandas 非依存。先例 `reconcile.py:125` は `pd.Timestamp` を直接 `trading_start` に渡すが、それは tools/スクリプト層であり同じ層境界。`bar.time` の実型に合わせて正規化する（§10 判断 5）。

#### 2.2.5 出力先検証関数（H-2・データ非波及機構）

```python
_FORBIDDEN_PREFIXES = (
    "marketdata",
    "simulator/tests/fixtures",
    "simulator/tests/confirmation",
)


def assert_safe_output_dir(out_dir: str, repo_root: Any) -> Any:
    """書込先が既存データディレクトリ配下でないことを検証する純関数（C1/NFR-S1・H-2）。

    引数:
      out_dir  : 書込先（相対/絶対）
      repo_root: リポジトリルート（Path）

    戻り値: 解決済み絶対 Path（許可された場合）
    例外  : OutputGuardError（禁止プレフィクス配下・repo_root 外への書込）

    判定:
      resolved = (repo_root / out_dir).resolve()
      rel = resolved.relative_to(repo_root)  # repo_root 外なら ValueError -> OutputGuardError
      rel の先頭が _FORBIDDEN_PREFIXES のいずれかに一致 -> OutputGuardError
    """
```

参照実装（決定論）：

```python
    from pathlib import Path
    root = Path(repo_root).resolve()
    resolved = (root / out_dir).resolve()
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        raise OutputGuardError(f"repo_root 外への書込は禁止: {resolved}")
    rel_posix = rel.as_posix()
    for pref in _FORBIDDEN_PREFIXES:
        if rel_posix == pref or rel_posix.startswith(pref + "/"):
            raise OutputGuardError(f"既存データディレクトリ配下への書込は禁止: {rel_posix}")
    return resolved
```

- **プレフィクス一致を `== pref or startswith(pref + "/")` とする根拠**：`marketdata2/` のような別ディレクトリを誤拒否しないため（部分文字列一致でなくパスセグメント一致）。`relative_to` で repo_root 外（`../` 脱出・絶対パス）も同時拒否（§10 判断 4）。

#### 2.2.6 出力整形（L-1・tools 層内・新規 presenter なし）

```python
def to_json_dict(result: Any) -> dict:
    """RunIsOosResult を JSON シリアライズ可能な dict へ（asdict パターン・main:464 踏襲）。"""
    from dataclasses import asdict
    return {
        "is_stats": asdict(result.is_stats),
        "oos_stats": asdict(result.oos_stats),
        "degradation": [asdict(m) for m in result.degradation.metrics],
    }


def to_markdown(result: Any) -> str:
    """IS 列 | OOS 列 | ratio | delta の並列レポート（人間可読）。"""
```

- 機械可読 JSON は `stats.json` 相当の `is_oos.json`、人間可読は `report.md` を OUT 配下へ書く。`JsonPresenter`/`MarkdownPresenter`（adapter）は改変も流用もしない（C2・新規 presenter 追加なし）。

#### 2.2.7 tempfile 規約（M-3）

列ブリッジ等で一時ファイルが必要な場合は `tempfile`（標準ライブラリ）で生成し `try/finally` で確実に削除する。tools 層の単一ユーティリティ関数に集約し、複数箇所での直接利用を禁止する（`export_trade_markers.py:22` の方針継承）。本サブフェーズの StopEntryProbe 結合では既存 MT5 CSV をそのまま `data_path` に渡すため列ブリッジは不要（tempfile 未使用）。

---

## 3. シーケンス／データフロー

### 3.1 シーケンス図（split 入力 → 2 run → 劣化 → 出力）

```
tools(run_is_oos_cli)        main.build_interactor      usecase.run_is_oos        controller._interactor
        │                              │                          │                          │
 (a) parse args                        │                          │                          │
 (b) build_interactor(...)──────────►  │                          │                          │
        │  ◄──────(controller, request)│                          │                          │
 (c) normalize split/is_start          │                          │                          │
 (d) run_segment = make_run_segment(controller, request)          │                          │
 (e) run_is_oos(request=RunIsOosRequest(split,is_start),          │                          │
        full_bars=request.bars, run_segment) ───────────────────► │                          │
        │                              │              (1) validate(範囲・空区間 M-1)         │
        │                              │              (2) is_bars = slice_is_bars(full,split) │
        │                              │              (3) run_segment(is_bars, is_start)──────►│ execute(request.bars=is_bars,
        │                              │                          │       ◄──IS BacktestStats─│   trading_start=is_start)
        │                              │              (4) run_segment(full, split)────────────►│ execute(request.bars=full,
        │                              │                          │       ◄─OOS BacktestStats─│   trading_start=split)
        │                              │              (5) build_degradation_report(ratio/delta)│
        │  ◄────────────────RunIsOosResult(is,oos,degradation)────│                          │
 (f) assert_safe_output_dir(out_dir)   │                          │                          │
 (g) to_json_dict / to_markdown        │                          │                          │
 (h) write OUT/is_oos.json, OUT/report.md                         │                          │
```

### 3.2 データフロー（option b の in-memory 完結）

```
価格 CSV(読取専用)
  │ build_interactor で market_data.load → request.bars(=full_bars・全期間)
  ▼
full_bars ──slice_is_bars(full,split)[head切り bar.time<split]──► is_bars
  │                                                                  │
  │ (OOS) request.bars=full + trading_start=split                    │ (IS) request.bars=is_bars + trading_start=is_start
  ▼                                                                  ▼
controller._interactor.execute ── OOS BacktestStats          controller._interactor.execute ── IS BacktestStats
  │                                                                  │
  └───────────────► build_degradation_report(ratio=OOS/IS, delta=OOS-IS) ◄──────┘
                                  ▼
                          RunIsOosResult{is_stats, oos_stats, degradation}
                                  ▼
                    assert_safe_output_dir → JSON(is_oos.json) + Markdown(report.md) → 新規 OUT
```

- 中間バー列はプロセスメモリのみ（永続化なし・基本設計 §5.4）。`is_bars` は `slice_is_bars` の新規 list。

---

## 4. 物理データモデル（出力スキーマ）

本サブフェーズに RDB テーブルはない（オフライン分析ツール・プロセス内）。物理データモデルは「出力 JSON/Markdown スキーマ」として確定する。

### 4.1 `OUT/is_oos.json` スキーマ

```json
{
  "is_stats":  { "<BacktestStats の全フィールド>": <number> },
  "oos_stats": { "<BacktestStats の全フィールド>": <number> },
  "degradation": [
    {
      "name": "profit",
      "is_value":  11370.0,
      "oos_value": -4020.0,
      "ratio":     -0.3535...,
      "delta":     -15390.0
    },
    { "name": "profit_factor",   "is_value": <f>, "oos_value": <f>, "ratio": <f|null>, "delta": <f> },
    { "name": "recovery_factor", "...": "..." },
    { "name": "expected_payoff", "...": "..." },
    { "name": "sharpe_ratio",    "...": "..." },
    { "name": "trades",          "is_value": 5224.0, "oos_value": 2438.0, "ratio": 0.4667..., "delta": -2786.0 }
  ]
}
```

- `is_stats`/`oos_stats` は `dataclasses.asdict(BacktestStats)`（`models.py:91-142` の全フィールド・型 float/int）。
- `degradation[].ratio` は `null`（IS_value == 0 時）または number。
- `degradation[].delta` は常に number。
- フィールド名・順序は `RunIsOosRequest.metric_names`（既定 6 指標）に一致（決定論）。

### 4.2 `OUT/report.md` スキーマ（人間可読・並列レポート）

```markdown
# IS/OOS Simple Split Report

- split: 2026-04-15
- is_trading_start: 2026-04-01

## Summary
| metric | IS | OOS | ratio (OOS/IS) | delta (OOS-IS) |
|---|---|---|---|---|
| profit | 11370.0 | -4020.0 | -0.354 | -15390.0 |
| profit_factor | ... | ... | ... | ... |
| recovery_factor | ... | ... | ... | ... |
| expected_payoff | ... | ... | ... | ... |
| sharpe_ratio | ... | ... | ... | ... |
| trades | 5224 | 2438 | 0.467 | -2786 |
```

- ratio 未定義（null）時はセルに `N/A` を表示し delta は表示する。

### 4.3 出力先（TBD-3・運用判断）

許可 OUT は CLI `--out-dir` 引数で渡された新規ディレクトリ配下のみ。`assert_safe_output_dir` が `marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/` プレフィクス配下と repo_root 外を拒否する（§2.2.5）。具体パス命名規約は運用判断（例 `outputs/isoos/<timestamp>/`）。

---

## 5. クリーンアーキテクチャ準拠の実装方針

### 5.1 レイヤ分割と依存方向

```
tools(run_is_oos_cli)  ── import ──►  main.build_interactor   （Composition Root 利用側）
        │                            usecase.run_is_oos
        │  ── import ──►  usecase.run_is_oos  ── import ──►  usecase.models(BacktestStats)
        │                                       （domain のみ・pandas/main 非 import）
        ▼
   controller._interactor.execute  （B-1: run/run_backtest は使わない）
```

| 規律 | 遵守手段 | 検証 |
|---|---|---|
| usecase→domain のみ（内向き） | `run_is_oos.py` は `pandas`・`simulator.main`・`simulator.adapter` を import しない。エンジン実行手段は `run_segment: Callable` で注入（DIP） | import 文の静的検査（テストで `import ast` により禁止 import 不在を assert 可） |
| tools→main/usecase | tools のみ `build_interactor` を import | — |
| committed 無改変（C2/NFR-S2） | 新規ファイルのみ追加。`controller._interactor` は読み取り利用（属性改変なし）。`request.bars`/`request.trading_start` は dataclass の公開フィールド代入（committed が想定する利用・`build_interactor:384-393` が同フィールドを設定済） | `git diff` で committed パス差分 0 を CI 確認（R-4） |

### 5.2 committed 無改変の保証

- `request.bars`/`request.trading_start` への代入は committed `RunBacktestRequest`（`run_backtest.py:75,84`）の公開フィールドへの書込であり、`build_interactor` 自身が同フィールドを構築時に設定する（`main/__init__.py:386,392`）。UC/tools がこれを区間ごとに上書きするのは committed が許容する利用形態であり、ファイル差分を生まない。
- `controller._interactor`（private 属性）の読み取りは先例 `reconcile_is.py:127`・`reconcile.py:127`・`export_trade_markers.py:10` で確立済。属性への再代入は行わない。

---

## 6. テスト設計

### 6.1 テスト全体方針とカバレッジ目標

| 区分 | 対象 | 自動化 | カバレッジ目標 |
|---|---|---|---|
| 単体 | `slice_is_bars`・`build_degradation_report`/`extract_metrics`・`assert_safe_output_dir`・bar-mode 位置整合 | pytest（CI 自動） | 新規 `run_is_oos.py` の行・分岐 100%（純ロジックのため到達可能） |
| 結合 | 先例 2026-04 再現（IS/OOS）＋mtime 不変 | pytest（CI 自動・読み取り専用 fixture 使用） | UC 主経路（slice→2 run→劣化→検証）の end-to-end |
| E2E | tools CLI 起動→OUT 生成 | pytest（subprocess or 関数呼出・tmp_path OUT） | CLI 引数→出力ファイル生成の 1 パス |

- 回帰テスト方針（user memory「bugfix-pair-with-regression-test」）：スライス境界バグ（`< split` 境界・位置インデックスずれ）を禁止する回帰テストを単体に 1 本以上含める（6.2.1）。

### 6.2 単体テスト

#### 6.2.1 `test_slice_is_bars.py`（境界・空区間拒否式）

合成小データ（`marketdata` 非依存・`main:89-91` の方針）で domain `Bar` 相当の最小オブジェクト（`.time` を持つ stub）を用いる。

| ケース | 入力 | 期待 |
|---|---|---|
| 境界 `< split` 保持 | times=[1,2,3], split=3 | 結果 times=[1,2]（split==3 は除外） |
| `== split` 除外 | times=[1,2,3], split=2 | 結果 times=[1] |
| 全保持 | times=[1,2], split=99 | 結果 times=[1,2] |
| IS 空（拒否式の片側） | times=[5,6], split=1 → `run_is_oos` 経由 | `IsOosValidationError`（IS バー数 0） |
| OOS 空（拒否式の片側） | times=[1,2], split=99 → `run_is_oos` 経由 | `IsOosValidationError`（OOS バー数 0） |
| head-prefix のみ（中抜き禁止） | times=[1,2,5,3], split=4（昇順前提違反データ） | `break` により [1,2]（昇順前提のため 5 で打ち切り。非昇順は契約外） |

- 空区間拒否式は基本設計 M-1「IS バー数≥1 かつ OOS で `bar.time>=split` のバー数≥1」を直接 assert する。

#### 6.2.2 `test_degradation_report.py`（ratio/delta 算出・ゼロ除算）

`BacktestStats` を最小フィールドで構築するヘルパ（劣化対象 6 フィールドのみ意味を持つ）。

| ケース | IS | OOS | 期待 |
|---|---|---|---|
| 通常 ratio/delta | profit=100 | profit=50 | ratio=0.5, delta=-50 |
| ゼロ除算 | profit=0 | profit=50 | ratio=None, delta=50（delta は常に格納） |
| 全 6 指標格納 | 各値設定 | 各値設定 | `metrics` に 6 件・name 順一致 |
| trades の劣化 | trades=5224 | trades=2438 | ratio≈0.4667, delta=-2786 |

#### 6.2.3 `test_output_guard.py`（拒否プレフィクス）

| ケース | out_dir | 期待 |
|---|---|---|
| `marketdata/` 拒否 | `marketdata/x` | `OutputGuardError` |
| `fixtures/` 拒否 | `simulator/tests/fixtures/y` | `OutputGuardError` |
| `confirmation/` 拒否 | `simulator/tests/confirmation/z` | `OutputGuardError` |
| 完全一致 `marketdata` | `marketdata` | `OutputGuardError` |
| 類似誤判定なし | `marketdata2/x` | 許可（resolved Path 返却） |
| repo_root 外脱出 | `../escape` | `OutputGuardError` |
| 正常 OUT | `outputs/isoos/run1` | 許可（resolved Path 返却） |

#### 6.2.4 `test_is_oos_barmode_index.py`（bar-mode registry 位置整合・C-1）

bar-mode 経路（`pending_lifecycle` 非設定・`run_backtest.py:203`）の EA（TC24051901 / MaSlope）で、`slice_is_bars` 後の `request.bars` 差し替えが位置インデックス整合（causal EMA で registry 非再構築でも bit-identical）を保つことを境界テストする。

- 検証法：合成小データで (i) full bars を head 切りした IS bars で run、(ii) IS と同じバーだけを含む別 build（registry 再構築）で run、両者の `is_stats`（または trades・indicator_values）が bit-identical であることを assert。causal EMA（`main:211-225`）により位置 0..k-1 の指標値が一致することの実証。
- これは C-1 の「bar-mode は先例突合対象外のため単体で担保」を満たす。

### 6.3 結合テスト `test_is_oos_stop_probe.py`（先例 2026-04 再現・読み取り専用 fixture）

`simulator/tests/confirmation/2026-04_stop-probe_oos/bars_m1.csv`（full・読み取り専用）を `data_path` に、本 UC 経由（`run_is_oos` + tools の run_segment）で IS/OOS を再現する。

```python
# 擬似（決定論的契約）
controller, request = build_interactor(
    data_path=str(bars_m1_csv), symbol="JP225", period="M1", ea_name="StopEntryProbe_EA",
    initial_deposit=10000.0, contract_size=10.0, volume_min=0.01, volume_max=100.0,
    volume_step=0.01, stops_level=0, digits=1, point_size=0.1, leverage=10.0,
    ma_period=60, ma_method="ema", lot_size=0.1, stop_loss_points=200,
    take_profit_points=500, entry_offset_points=100.0, entry_type="stop",
    config_overrides={  # reconcile.py:112-124 と同一
        "tick_model": "ohlc_expand", "entry_price_basis": "current_open",
        "floating_pnl_basis": "bid_ask", "stop_out_action": "close_and_halt",
        "session_calendar": "jp225", "profit_round_digits": 0, "stop_out_at_open": True,
        "pending_lifecycle": True, "pending_oco": True, "pending_persistent": True,
        "hedged_margin": True,
    },
    stop_out_level=100.0,
)
run_segment = make_run_segment(controller, request)
result = run_is_oos(
    request=RunIsOosRequest(
        split=pd.Timestamp("2026-04-15").to_datetime64(),
        is_trading_start=pd.Timestamp("2026-04-01").to_datetime64(),
    ),
    full_bars=request.bars,
    run_segment=run_segment,
)
# IS 期待（reconcile_is.py:32-34）: net +11370 / balance 21370 / trades 5224
# OOS 期待（reconcile.py:32-34）: net -4020 / balance 5980 / trades 2438
assert result.is_stats.trades == 5224
assert result.is_stats.profit == 11370.0      # net（initial 10000 → balance 21370）
assert result.oos_stats.trades == 2438
assert result.oos_stats.profit == -4020.0
```

- **IS が full の head-prefix である根拠**（本結合が単一 CSV で成立する決め手）：§1.2 実証のとおり `slice_is_bars(full, split=2026-04-15)` の結果は `bars_m1_is.csv` と同一バー集合。よって先例が 2 本の CSV（`bars_m1.csv` + `bars_m1_is.csv`）で達成した IS/OOS を、本 UC は `bars_m1.csv` 1 本＋in-memory head 切りで再現する。
- **`trading_start` の型**：先例は `pd.Timestamp(...)` を直接渡す（`reconcile.py:125`）。本結合では tools の `normalize_time` 相当で `bar.time` 型へ正規化（`Mt5CsvOHLCRepository` の `bar.time` 実型に合わせる）。型が一致しない場合 `bar.time < split` 比較が破綻するため、結合テストで実型整合も同時に実証する（TBD-5 の実コード確定点）。
- **保証境界（C-1）**：本結合は `pending_lifecycle` 経路（StopEntryProbe・every-tick `run_backtest.py:488`）の byte-exact を実証する。bar-mode は 6.2.4 で別途担保。

### 6.4 NFR-S1（既存データ mtime 不変 assert）

結合テスト内で、実行前後に既存データディレクトリ（`marketdata/`・`simulator/tests/fixtures/`・`simulator/tests/confirmation/`）配下の全ファイル mtime を採取し、不変であることを assert する。

```python
def snapshot_mtimes(dirs) -> dict:
    return {p: p.stat().st_mtime for d in dirs for p in Path(d).rglob("*") if p.is_file()}

before = snapshot_mtimes([...既存データディレクトリ...])
# ... run_is_oos + tools 出力（OUT は tmp_path）...
after = snapshot_mtimes([...同...])
assert before == after  # NFR-S1: 既存データ非波及
```

- OUT は `tmp_path`（pytest fixture）配下に限定し `assert_safe_output_dir` を通すことで二重に非波及を担保。

### 6.5 自動化範囲

- 単体・結合・NFR-S1・bar-mode 整合：すべて pytest で CI 自動化。
- E2E（tools CLI）：`run_is_oos_cli.main(argv=[...])` を関数呼出で起動し OUT 生成を assert（subprocess 不要・決定論）。

---

## 7. 後段②最適化／③ウォークフォワードへの拡張点

基本設計「後段サブフェーズへの拡張余地」を実装契約で具体化する。

| 拡張先 | 再利用する本設計の契約 | 拡張方法 |
|---|---|---|
| ②最適化 `usecase/optimize.py` | `extract_metrics(stats, names)`（§2.1.6・指標抽出を独立関数化） | `ObjectivePort` が `extract_metrics` を目的関数評価に再利用。`run_segment(is_bars, is_start)` をパラメータ空間走査で繰り返し呼ぶ |
| ②最適化 | `RunSegment = Callable[[bars, trading_start], BacktestStats]` 契約 | param ごとに `build_interactor` を再構築し run_segment を差し替える（UC は契約不変） |
| ③ウォークフォワード `usecase/walk_forward.py` | `slice_is_bars(bars, split)` + OOS warmup（full + trading_start=split） | anchored/rolling 窓ごとに split を移動し `run_is_oos` 相当を反復。IS truncation（R-1 対策）と OOS warmup（R-3 対策）の機構が窓単位でそのまま適用可能 |

- 鍵となる再利用性：本 UC は「区間（bars, trading_start）→ BacktestStats」を `run_segment` として関数化し、「劣化算出」を `build_degradation_report` として分離した。optimize/walk_forward は両プリミティブを committed エンジン無改変のまま再利用できる。

---

## 8. 制約遵守チェックリスト（DoD 対応）

| 制約 | 遵守手段 | 検証 |
|---|---|---|
| C1 既存データ非波及 | `assert_safe_output_dir`（§2.2.5）＋ OUT を tmp/新規限定 ＋ tempfile 即時削除 | `test_output_guard.py`＋mtime 不変 assert（§6.4） |
| C2 committed 無改変 | 新規ファイルのみ・`controller._interactor` 読み取り利用・dataclass 公開フィールド代入 | `git diff` committed 差分 0（CI） |
| C3 技術スタック追加禁止 | 純 Python＋`dataclasses`/`typing`/`argparse`/`pathlib`/`tempfile`＋既存 `pandas`（tools 層のみ） | import 検査 |
| 基本設計逸脱なし | §1.1 のとおり確定事項の具体化のみ | 本書 §10 根拠表 |
| 実装着手可能水準 | クラス名・メソッドシグネチャ・参照実装・テスト契約を明記 | §2〜§6 |

---

## 9. リスク・残存課題

| ID | 課題 | 対応 |
|---|---|---|
| 残-1 | TBD-5 時刻正規化の実型確定 | tools `normalize_time` で `bar.time` 実型に合わせ正規化。結合テスト（§6.3）が `bar.time < split` 比較成立を実証（実装フェーズで実型確認） |
| 残-2 | TBD-3 出力パス命名規約 | `--out-dir` 引数化＋`assert_safe_output_dir`。具体命名は運用判断 |
| 残-3 | bar-mode 単体テスト緑化 | §6.2.4 を実装フェーズで緑化（causal EMA bit-identical は静的実証済） |
| 残-4 | StopEntryProbe は registry 未参照（`main:327`）のため結合の byte-exact は bars＋tick 由来 | bar-mode registry 整合は §6.2.4 で分離担保（C-1 の役割分担） |

---

## 10. 設計判断の根拠・代替案比較・定量評価

| # | 判断項目 | 採用 | 代替案 | 根拠 | パフォーマンス影響（定量） |
|---|---|---|---|---|---|
| 1 | `slice_is_bars` の打ち切り | `for`＋`break`（昇順前提・head-prefix） | リスト内包フィルタ全走査 | 「head-prefix のみ・中抜き禁止」契約を構造表現。option b の位置整合前提に一致 | フィルタ O(N) → break で O(k)（k=IS バー数）。IS が full の前半なら平均 ~半減 |
| 2 | run_segment コールバック注入 | tools が `build_interactor`→閉包で注入 | UC が committed `RunBacktestInteractor` を直接構築 | UC を main 非依存（DIP）に保つ。build ロジック複製（DRY 違反）回避 | 実行時オーバヘッド 0（関数呼出 1 段） |
| 3 | DTO の時刻/stats 型を `Any` | `Any`（pandas 非依存・疎結合） | `numpy.datetime64`/`BacktestStats` 具体束縛 | `models.py:8,13` の既存規約継承。usecase が pandas を引き込まない | 0 |
| 4 | 出力先検証のパス一致 | `relative_to`＋セグメント一致 | 部分文字列 `in` 判定 | `marketdata2/` 誤拒否回避・repo_root 外脱出同時拒否 | O(1)（書込前 1 回） |
| 5 | 時刻正規化の責務層 | tools 層（pandas 閉じ込め） | UC 層 / エンジン | usecase の pandas 非依存制約と整合。先例 `reconcile.py:125` も tools/スクリプト層で `pd.Timestamp` | 0 |
| 6 | `full_bars` を `list()` 実体化 1 回 | UC 入口で 1 回 | IS/OOS で都度イテレート | イテレータ 2 度走査回避（slice と OOS count と OOS run） | メモリ O(N)（既に full をロード済のため増分なし）・走査 1 回分削減 |
| 7 | ratio ゼロ除算 | `IS==0` で `None`・delta 常時格納 | 例外送出 / ratio=inf | 基本設計 C-2 準拠（提示のみ・解釈は利用側） | 0 |

### 10.1 パフォーマンス全体評価（NFR-P1/P2）

- エンジン実行回数：IS 1 回＋OOS 1 回＝2 回（NFR-P1）。IS は head 切り（k バー）、OOS は full（N バー）だが OOS の split 前は warmup（約定なし・`run_backtest.py:208-209` continue・指標 update のみ）で低コスト。総処理バー数は単一フル run＋IS 再走分（≈ k バーの warmup 処理）で、単一フル run と同オーダー。
- 劣化算出：`BacktestStats` 6 フィールドの O(1) 算術のみ（バー数 N 非依存・NFR-P2）。
- `slice_is_bars`：O(k)（break 早期打ち切り）。

---

## 11. 参考資料（実証済・実コード）

- `simulator/usecase/run_backtest.py`（`RunBacktestRequest` L68-84・`execute -> BacktestResult` L153・bar-mode `enumerate` L203・every-tick L488・warmup L208-209/L492-494）
- `simulator/adapter/controller.py`（`run` の `market_data.load` 再ロード L50・`_interactor` L35）
- `simulator/main/__init__.py`（`build_interactor` L256-394・causal `_ema_series` L211-225・`asdict` L21/L464）
- `simulator/adapter/indicator/registry.py`（位置インデックス系列保持 L25-35・`update` no-op L53）
- `simulator/usecase/models.py`（`BacktestStats` L91-142・劣化 6 フィールド L97-105・`Any`/`numpy.datetime64|int` 規約 L8,13）
- `simulator/adapter/strategy/pro_fit_band.py`（`.iloc[bar_index]` L69 ほか）
- `simulator/tools/export_trade_markers.py`（tools 層 C1/C3 パターン L6-14・tempfile L22）
- `simulator/tests/confirmation/2026-04_stop-probe_oos/reconcile.py`（OOS 先例・net-4020/2438・trading_start 2026-04-15 L36）
- `simulator/tests/confirmation/2026-04_stop-probe_oos/reconcile_is.py`（IS 先例・net+11370/5224・trading_start 2026-04-01 L36）
- `bars_m1.csv` / `bars_m1_is.csv`（IS=full の head-prefix・03-23 01:00:00〜04-14 23:59:00 byte-identical／full は 04-15 01:01:00 継続）
- `/workspaces/app/.doc/ISOOS_SIMPLE_SPLIT_BASIC_DESIGN.md` v0.2.0（上位設計）
