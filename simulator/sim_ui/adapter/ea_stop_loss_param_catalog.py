"""A-EaStopLossParamCatalog: ea_name → SL を決める設定パラメータ名（§12.8 受付時検証）。

裁定（2026-08-11）: ケリー基準は損切り幅が無ければリスク額を定義できず f→ロット変換が
成立しない。よって「戦略設定が SL を保証すること」は sizing ON の**投入時必須項目**であり、
E-3 と同じ受付検証で扱う。判定は**ハードコードの戦略リストではなく** EA 別に導出する
（§12.1「戦略リストのハードコード禁止」・`ea_registry_series_catalog` と同じ流儀）。

**単一ソース**: 注入された戦略構築関数（束縛は `simulator.main.build_ea_strategy`）が返す
**実際の戦略オブジェクト**の実装ソースを読み、SL 系設定名の参照有無を調べる。
ea_name → パラメータ名の対応表を本モジュールに書き写さない。

依存の向きと探索方法（ISSUE-405 の是正・実測）:
    以前は ``getattr(sim_main, "_EA_FACTORIES", {}).get(ea_name)`` で私有な登録表を覗き、
    ``_factory_tc24051901`` へのフォールバック規則を書き写した上で、**factory 関数の
    ソース文字列**から戦略クラス名を推測していた（``f"{name}(" in source`` の総当たり）。
    この推測は `_factory_weekly_vol_band` で失敗する——戦略をビルダ関数
    ``make_weekly_vol_band(...)`` 経由で組むため、factory のソースに戦略クラス名が
    現れない。結果 WeeklyVolBand は「探索失敗（``None``）」に落ちていた。
    公開アクセサで**実際に組み立てて** ``type(strategy)`` を採れば推測が不要になり、
    WeeklyVolBand も戦略クラス（`WeeklyVolBand`）まで到達する。

なぜ「実行」ではなく「ソース参照の有無」か:
    SL を組む分岐（例: `TC24051901._build_order` の `cfg["stop_loss_points"]`）は、戦略が実際に
    発注する条件（指標のクロス等）が揃ったバーでしか通らない。受付時に発注条件を再現するのは
    ミニ・バックテストの実行に等しく、NFR-01（計算を request path に載せない）と投入応答
    1 秒以内（§8.1 Phase 2 通過条件 2）に反する。参照の有無なら**発注を再現せずに**判定できる。
    構築（12 行の使い捨て CSV の読み込み）は伴うが、これは E-3 の系列カタログが同じ受付経路で
    既に行っている段であり、結果は ea_name ごとにキャッシュされる。

SL 系設定名の語彙（`_STOP_LOSS_PARAM_NAMES`）は**戦略の一覧ではなく、プロジェクト全体で
1 つしかない設定名**である。実測（`grep -n 'stop_loss' simulator/adapter/strategy/*.py`）で、
SL 幅を決める設定キーは `stop_loss_points` のみであることを確認済み。

返り値の 3 値（実装と一致させること・🟡-3）:
    ``frozenset({...})`` 構築でき、SL を決める設定名が判明した。
    ``frozenset()``      構築できたが、戦略実装が SL 設定名を参照しないと判明した。
    ``None``             **構築できなかった**（どの形式の探索用データでも組めない、
                         または戦略クラスのソースを読めない）。

    実測（ISSUE-405 の是正で変わった点）: `WeeklyVolBand_EA` は以前 ``None``（探索失敗）
    だったが、構築ベースでは戦略クラス `WeeklyVolBand` に到達し、その実装ソースが
    ``stop_loss_points`` を参照しないため ``frozenset()`` を返す。すなわち「調べられなかった」
    から「調べた上で、SL は設定パラメータでは決まらない」へ**意味が変わる**。
    後者は実測に一致する（WeeklyVolBand の SL は `VolatilityBand` から導かれ、
    `stop_loss_points` 設定では決まらない）。受付側の挙動は変わらない
    （`submit_job._reject_if_stop_loss_not_guaranteed` は ``if not params: return``）。

fail-safe の向き: 探索に失敗しても（``None``）**拒否しない**。受付では判定せず、
    実行中の内部不変条件違反（fail-stop・`SizingRequiresStopLossError`）へ委ねる。
    ここで「拒否」に倒すと、SL を正しく持つ戦略まで投入できなくなる（E-3 とは逆向き。
    E-3 は「系列が無い＝サイジング不能」が確定するため拒否が正しい）。
"""
from __future__ import annotations

import inspect

from simulator.sim_ui.adapter.ea_build_probe import EaBuildProbe
from simulator.sim_ui.usecase.job_ports import StopLossParamCatalogPort

# SL 幅を決める設定名の語彙。実測（simulator/adapter/strategy/*.py の grep）で
# プロジェクト全体にこの 1 つしかない。戦略の一覧ではないことに注意。
_STOP_LOSS_PARAM_NAMES = ("stop_loss_points",)


class EaStopLossParamCatalog(StopLossParamCatalogPort):
    """戦略実装のソースから SL 系設定名の参照有無を導く。"""

    def __init__(self, probe: EaBuildProbe) -> None:
        """``probe``: 戦略実体を組む :class:`EaBuildProbe`（**必須**）。

        既定値を置かないのは R-4 と同型（既定束縛があると adapter → main の外向き依存が
        復活する）。束縛は Composition Root が持つ。
        """
        self._probe = probe
        self._cache: "dict[str, frozenset[str] | None]" = {}

    def stop_loss_params(self, ea_name: str) -> "frozenset[str] | None":
        if ea_name in self._cache:
            return self._cache[ea_name]
        found = self._probe_params(ea_name)
        self._cache[ea_name] = found
        return found

    # ---- 内部 ----

    def _probe_params(self, ea_name: str) -> "frozenset[str] | None":
        try:
            strategy = self._probe.for_ea(ea_name)
        except Exception:
            return None  # どの形式でも構築できない＝探索失敗
        try:
            source = inspect.getsource(type(strategy))
        except (OSError, TypeError):
            return None  # ソースを読めない＝探索失敗
        return frozenset(
            name
            for name in _STOP_LOSS_PARAM_NAMES
            if f'"{name}"' in source or f"'{name}'" in source
        )
