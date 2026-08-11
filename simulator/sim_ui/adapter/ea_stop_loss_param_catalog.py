"""A-EaStopLossParamCatalog: ea_name → SL を決める設定パラメータ名（§12.8 受付時検証）。

裁定（2026-08-11）: ケリー基準は損切り幅が無ければリスク額を定義できず f→ロット変換が
成立しない。よって「戦略設定が SL を保証すること」は sizing ON の**投入時必須項目**であり、
E-3 と同じ受付検証で扱う。判定は**ハードコードの戦略リストではなく** EA 別に導出する
（§12.1「戦略リストのハードコード禁止」・`ea_registry_series_catalog` と同じ流儀）。

**単一ソース**: `simulator.main._EA_FACTORIES`（未登録時のフォールバック先 `_factory_tc24051901`
を含む）が返す**実際の戦略オブジェクト**の実装ソースを読み、SL 系設定名の参照有無を調べる。
ea_name → パラメータ名の対応表を本モジュールに書き写さない（書き写した表は登録表が
増えた時に必ず取り残される）。

なぜ「実行」ではなく「ソース参照の有無」か:
    SL を組む分岐（例: `TC24051901._build_order` の `cfg["stop_loss_points"]`）は、戦略が実際に
    発注する条件（指標のクロス等）が揃ったバーでしか通らない。受付時に発注条件を再現するのは
    ミニ・バックテストの実行に等しく、NFR-01（計算を request path に載せない）と投入応答
    1 秒以内（§8.1 Phase 2 通過条件 2）に反する。参照の有無なら実行なしで判定できる。

SL 系設定名の語彙（`_STOP_LOSS_PARAM_NAMES`）は**戦略の一覧ではなく、プロジェクト全体で
1 つしかない設定名**である。実測（`grep -n 'stop_loss' simulator/adapter/strategy/*.py`）で、
SL 幅を決める設定キーは `stop_loss_points` のみであることを確認済み。

返り値の 3 値（実装と一致させること・🟡-3）:
    ``frozenset({...})`` 探索でき、SL を決める設定名が判明した。
    ``frozenset()``      探索できたが、SL 設定パラメータを持たないと判明した。
                         **現状これに該当する EA は無い**（該当すれば新設 EA）。
    ``None``             **探索できなかった**（factory のソースから戦略クラスを特定できない）。

    実測: `_factory_weekly_vol_band` は `make_weekly_vol_band(...)` という**ビルダ関数**で
    戦略を組み立てるため、factory のソースからは戦略クラスを特定できない。したがって
    WeeklyVolBand は **None（探索失敗）**を返す。「SL 設定パラメータを持たない」と
    判明したわけではない。
    （`weekly_vol_band` の SL が `VolatilityBand` 由来であることは別途 Read で確認した
     事実だが、**本アダプタの探索経路がそれを根拠にしているわけではない**。）

fail-safe の向き: 探索に失敗しても（``None``）**拒否しない**。受付では判定せず、
    実行中の内部不変条件違反（fail-stop・`SizingRequiresStopLossError`）へ委ねる。
    ここで「拒否」に倒すと、SL を正しく持つ戦略まで投入できなくなる（E-3 とは逆向き。
    E-3 は「系列が無い＝サイジング不能」が確定するため拒否が正しい）。
"""
from __future__ import annotations

import inspect
from typing import Any

from simulator.sim_ui.usecase.job_ports import StopLossParamCatalogPort

# SL 幅を決める設定名の語彙。実測（simulator/adapter/strategy/*.py の grep）で
# プロジェクト全体にこの 1 つしかない。戦略の一覧ではないことに注意。
_STOP_LOSS_PARAM_NAMES = ("stop_loss_points",)


class EaStopLossParamCatalog(StopLossParamCatalogPort):
    """戦略実装のソースから SL 系設定名の参照有無を導く。"""

    def __init__(self) -> None:
        self._cache: "dict[str, frozenset[str] | None]" = {}

    def stop_loss_params(self, ea_name: str) -> "frozenset[str] | None":
        if ea_name in self._cache:
            return self._cache[ea_name]
        found = self._probe(ea_name)
        self._cache[ea_name] = found
        return found

    # ---- 内部 ----

    @staticmethod
    def _strategy_class(ea_name: str) -> "type | None":
        """`_EA_FACTORIES` を単一ソースとして戦略クラスを引く（表を写さない）。

        factory の実行にはデータファイルが要るため呼ばない。factory 関数の本体から
        戻り値の戦略クラスを取り出すのではなく、**戦略クラスの決定だけ**を
        `simulator.main` の登録表から辿る。
        """
        try:
            from simulator import main as sim_main
        except Exception:
            return None
        factory = getattr(sim_main, "_EA_FACTORIES", {}).get(ea_name)
        if factory is None:
            factory = getattr(sim_main, "_factory_tc24051901", None)
        if factory is None:
            return None
        # factory 本体のソースから、生成している戦略クラス名を取り出す。
        try:
            source = inspect.getsource(factory)
        except (OSError, TypeError):
            return None
        for name, obj in vars(sim_main).items():
            if not isinstance(obj, type):
                continue
            # `return MaSlope(), registry, ...` のような生成箇所を探す
            if f"{name}()" in source or f"{name}(" in source.split("return", 1)[-1]:
                if _looks_like_strategy(obj):
                    return obj
        return None

    def _probe(self, ea_name: str) -> "frozenset[str] | None":
        cls = self._strategy_class(ea_name)
        if cls is None:
            return None      # 戦略クラスを特定できない＝探索失敗
        try:
            source = inspect.getsource(cls)
        except (OSError, TypeError):
            return None      # ソースを読めない＝探索失敗
        return frozenset(
            name for name in _STOP_LOSS_PARAM_NAMES if f'"{name}"' in source or f"'{name}'" in source
        )


def _looks_like_strategy(obj: Any) -> bool:
    """`StrategyPort` の実装らしさ（engine が呼ぶ 3 点を持つか）で判定する。"""
    return all(
        callable(getattr(obj, hook, None))
        for hook in ("on_init", "on_new_bar", "on_position_check")
    )
