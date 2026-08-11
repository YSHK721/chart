"""検定対象母集合の導出（`IndicatorCatalogSourcePort` 実装・adapter 層・Phase 3 F-5）。

母集合の唯一の情報源は **ライブの `GET /catalog`**（`adapter.controller.catalog_controller.
handle_catalog` を replay_ui の read-only bridge 経由で再利用する）。指標名の表をここに
手書きすると、指標が増えたときに黙って検定対象から漏れ、「台帳に無い＝選択不可」として
理由なく消える。

規約:
    * variant は ``paramScopes`` の鍵をそのまま使う（variant ごとに受理 param が違う）。
    * params は当該 variant が受理する param の既定値だけを載せる。受理しない param を
      送ると compute が validation エラーを返す（ISSUE-278 #8）。
    * catalog を取得できないときは明示エラー。空の母集合（＝検定対象なし）へ倒すと、
      検定が黙って空振りして「1 件も選択可能でない台帳」を機械生成してしまう。
    * 並びは (indicator, variant) 昇順で決定的にする。

CLEAN_ARCH §6: indicator_ui への依存は本ファイルに閉じる。
"""
from __future__ import annotations

from typing import Any

from simulator.sim_ui.usecase.indicator_models import (
    IndicatorCatalogUnavailableError,
    IndicatorSpec,
)
from simulator.sim_ui.usecase.indicator_ports import IndicatorCatalogSourcePort


class IndicatorCatalogSource(IndicatorCatalogSourcePort):
    """ライブ catalog から検定対象の申告一覧を導く。

    ``catalog_handler``: ``() -> (status, body)`` の呼び出し可能物。既定はライブ実装。
    """

    def __init__(
        self, *, catalog_handler: Any = None, api_path: Any = None, repo_root: Any = None
    ) -> None:
        self._catalog_handler = catalog_handler
        self._api_path = api_path
        self._repo_root = repo_root

    def _handler(self) -> Any:
        if self._catalog_handler is None:
            # 遅延解決: indicator_ui の import を実際に使うときまで起こさない。
            from simulator.replay_ui.adapter import _indicator_ui_bridge

            bridge = _indicator_ui_bridge.load_catalog_handler(
                self._api_path, self._repo_root
            )
            self._catalog_handler = bridge.handle_catalog
        return self._catalog_handler

    def specs(self) -> "list[IndicatorSpec]":
        status, body = self._handler()()
        if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
            raise IndicatorCatalogUnavailableError(
                f"指標カタログを取得できません（status={status}）"
            )
        catalog = body.get("catalog")
        scopes = body.get("paramScopes")
        if not isinstance(catalog, dict) or not isinstance(scopes, dict):
            raise IndicatorCatalogUnavailableError("指標カタログの形が不正です")

        specs: "list[IndicatorSpec]" = []
        for indicator in sorted(scopes):
            defaults = catalog.get(indicator) or {}
            for variant in sorted(scopes[indicator] or {}):
                accepted = scopes[indicator][variant] or []
                specs.append(IndicatorSpec(
                    indicator=indicator,
                    variant=variant,
                    params={
                        name: defaults[name] for name in accepted if name in defaults
                    },
                ))
        return specs
