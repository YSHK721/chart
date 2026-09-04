"""因果性台帳の FS 実装（`IndicatorCausalityLedgerPort`・adapter 層・Phase 3 F-5）。

置き場所は ``<data_root>/indicator_causality.json``（ジョブ台帳と同じ根）。**機械生成のみ**
（検定 CLI が書く）で、手書きの編集を前提にしない。

記録の形（裁定 A/C 反映・**1 行 = 1 系列**）:

    {"schema": 1, "measured_at": "...",
     "conditions": {"ref", "timeframe", "supply_bars", "verify_bars", "verify_coverage",
                    "timeout", "supply_budget", "limit", "tolerance", "probe_mode"},
     "series": [{"indicator", "variant", "params", "series", "selectable",
                 "reason", "detail",
                 "measured": {"bars_compared", "warmup_bars", "max_abs_diff",
                              "first_mismatch_time", "supply_seconds"}}]}

``reason`` は 3 値固定（``mismatch`` / ``supply_cost_exceeded`` /
``verification_incomplete``）。自由文は ``detail`` に置く。DTO 側（`CausalityFinding`）が
値域を強制するため、3 値以外が書かれた台帳は読み込み時に
:class:`CausalityLedgerUnavailableError` になる（誤った台帳を黙って通さない）。

読めないとき（不在・壊れた JSON・schema 不一致・必須キー欠落）も
:class:`CausalityLedgerUnavailableError`。**空台帳へ倒さない**（fail-closed）。空へ倒すと
「検定していないから 0 件」と「検定した結果 0 件」が同じ顔になり、未検定の指標を
使ってよいという誤りを黙って通す。

CLEAN_ARCH §6: FS・json は本ファイルに閉じる。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simulator.sim_ui.usecase.indicator_models import (
    CausalityFinding,
    CausalityLedgerUnavailableError,
    IndicatorSpec,
    LedgerConditions,
    LedgerSnapshot,
)
from simulator.sim_ui.usecase.indicator_ports import IndicatorCausalityLedgerPort

#: 台帳ファイル名（`data_root` 直下）。
LEDGER_FILENAME = "indicator_causality.json"
#: 本実装が読み書きする schema 版。異なる版は読まない（無音の解釈違いを作らない）。
LEDGER_SCHEMA = 1


class FileIndicatorCausalityLedger(IndicatorCausalityLedgerPort):
    """``data_root`` 直下に因果性台帳を持つ :class:`IndicatorCausalityLedgerPort` 実装。"""

    def __init__(self, *, data_root: Any) -> None:
        # cwd 非依存の絶対パスに固定する（起動場所で台帳の位置が変わらないように）。
        self._root = Path(data_root).resolve()

    @property
    def path(self) -> Path:
        return self._root / LEDGER_FILENAME

    # --- IndicatorCausalityLedgerPort ------------------------------------

    def read(self) -> LedgerSnapshot:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CausalityLedgerUnavailableError(
                f"因果性台帳を読めません: {self.path}（{exc}）"
            ) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise CausalityLedgerUnavailableError(
                f"因果性台帳が JSON として解釈できません: {self.path}"
            ) from exc
        return _snapshot_of(payload, self.path)

    def write(self, snapshot: LedgerSnapshot) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        body = json.dumps(_payload_of(snapshot), ensure_ascii=False, indent=2)
        # 同一ディレクトリへ書いてから置換する。途中まで書かれた JSON を読ませない。
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(body + "\n", encoding="utf-8")
        tmp.replace(self.path)


# --- JSON ⇄ DTO -------------------------------------------------------------


def _payload_of(snapshot: LedgerSnapshot) -> "dict[str, Any]":
    conditions = snapshot.conditions
    return {
        "schema": snapshot.schema,
        "measured_at": snapshot.measured_at,
        "conditions": {
            "ref": conditions.ref,
            "timeframe": conditions.timeframe,
            "supply_bars": conditions.supply_bars,
            "verify_bars": conditions.verify_bars,
            "verify_coverage": conditions.verify_coverage,
            "timeout": conditions.timeout,
            "supply_budget": conditions.supply_budget,
            "limit": conditions.limit,
            "tolerance": conditions.tolerance,
            "probe_mode": conditions.probe_mode,
        },
        "series": [
            {
                "indicator": f.spec.indicator,
                "variant": f.spec.variant,
                "params": dict(f.spec.params or {}),
                "series": f.series_name,
                "selectable": f.selectable,
                "reason": f.reason,
                "detail": f.detail,
                "measured": {
                    "bars_compared": f.bars_compared,
                    "warmup_bars": f.warmup_bars,
                    "max_abs_diff": f.max_abs_diff,
                    "first_mismatch_time": f.first_mismatch_time,
                    "supply_seconds": f.supply_seconds,
                },
            }
            for f in snapshot.findings
        ],
    }


def _snapshot_of(payload: Any, path: Path) -> LedgerSnapshot:
    if not isinstance(payload, dict):
        raise CausalityLedgerUnavailableError(f"因果性台帳の形が不正です: {path}")
    if payload.get("schema") != LEDGER_SCHEMA:
        raise CausalityLedgerUnavailableError(
            f"因果性台帳の schema が一致しません: {payload.get('schema')!r}"
            f"（対応は {LEDGER_SCHEMA}）"
        )
    conditions = payload.get("conditions")
    series = payload.get("series")
    if not isinstance(conditions, dict) or not isinstance(series, list):
        raise CausalityLedgerUnavailableError(f"因果性台帳の必須項目が欠けています: {path}")
    try:
        parsed = LedgerConditions(
            ref=conditions["ref"],
            timeframe=conditions["timeframe"],
            supply_bars=int(conditions["supply_bars"]),
            verify_bars=int(conditions["verify_bars"]),
            verify_coverage=float(conditions.get("verify_coverage", 1.0)),
            timeout=conditions.get("timeout"),
            supply_budget=float(conditions.get("supply_budget", 1.0)),
            limit=conditions.get("limit"),
            tolerance=float(conditions.get("tolerance", 0.0)),
            probe_mode=str(conditions.get("probe_mode", "full")),
        )
        findings = tuple(_finding_of(item) for item in series)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        # AttributeError: series 要素・measured が dict でない（str / None / 数値）とき
        #   ``item.get`` で発生する。翻訳せずに漏らすと `/indicators` が文書化された
        #   503 ではなく応答なし（接続断）になり、「サーバが落ちた」としか見えなくなる。
        raise CausalityLedgerUnavailableError(
            f"因果性台帳の内容を解釈できません: {path}（{exc}）"
        ) from exc
    return LedgerSnapshot(
        schema=LEDGER_SCHEMA,
        measured_at=str(payload.get("measured_at", "")),
        conditions=parsed,
        findings=findings,
    )


def _finding_of(item: Any) -> CausalityFinding:
    measured = item.get("measured") or {}
    # reason の値域は CausalityFinding が強制する（ValueError → 上で読み込み失敗に翻訳）。
    return CausalityFinding(
        spec=IndicatorSpec(
            indicator=str(item["indicator"]),
            variant=str(item["variant"]),
            params=dict(item.get("params") or {}),
        ),
        series_name=str(item["series"]),
        selectable=bool(item["selectable"]),
        reason=item.get("reason"),
        detail=item.get("detail"),
        bars_compared=int(measured.get("bars_compared", 0)),
        warmup_bars=int(measured.get("warmup_bars", 0)),
        max_abs_diff=measured.get("max_abs_diff"),
        first_mismatch_time=measured.get("first_mismatch_time"),
        supply_seconds=measured.get("supply_seconds"),
    )
