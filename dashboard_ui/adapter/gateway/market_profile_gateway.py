"""P-MP の実装: 価格ラダーの行価格 → 直近 1D プロファイルの TPO 密度（依頼者承認 2026-09-06）。

何を出すか:
    dataset_ref の **1D 確定足 直近 60 本**から作った 1 本のプロファイル（bins=60・VA は
    MP core の既定規約）を引き、各行の水準価格が入る bin の `norm`（0..1）を返す。
    表示は色の濃度＋横バー（フロント）で、数値は出さない。

なぜ計算は 1 本か:
    プロファイルは確定足だけから決まるので epoch の中で不変である。行ごと・要求ごとに
    畳み直しても**出力は正しいまま**であり、状態検証では原理的に落ちない
    （ISSUE-450 / ISSUE-457 / ISSUE-464 と同型の「作ってから捨てる」欠陥）。したがって
    持ち越しの口を通す。専用のストアは新設せず、確定素材の共有に既に使っている
    :class:`MaterialStore` へ相乗りする（同じ性質の量に第 2 の置き場所を作らない）。

版（epoch）の取り方:
    `(確定足の本数, 窓先頭 time, 窓末尾 time, 窓末尾 close)`。本数と端だけでは、遡り訂正で
    確定足の中身が入れ替わったときに古いプロファイルを配り続ける（ISSUE-457 の同型）。

ストアの鍵に接頭辞を付ける理由（実測 2026-09-06）:
    :class:`MaterialStore` は**鍵ごとに版を 1 つだけ**持ち、版が変わればその鍵の素材を丸ごと
    捨てる。鍵 `(dataset_ref, timeframe)` は P-1
    （`dashboard_ui/adapter/gateway/indicator_ui_compute_gateway.py`）が自分の版の
    定義で所有しており、そこへ別の版の定義で相乗りすると互いの素材を毎要求押し出し合う。
    実測: 素材が不変な 5 要求で P-1 の full 発行が 1 回のはずが **10 回**、MP の畳み込みが
    1 回のはずが **5 回**になった（出力は正しいままなので状態検証では原理的に落ちない
    ——ISSUE-450 / ISSUE-457 と同型）。ストア自体は共有したまま、鍵の名前空間だけを分ける
    （既存の非所有者も `("role", …)` / `("elapsed", …)` と同じ形を採っている）。
    この不変量は `tests/complexity/test_market_profile_computed_once.py` が機械的に固定する。

形成中足の扱い:
    窓に入れない。入れると epoch がティックごとに動き、更新粒度が「1D バー確定」から
    無言でずれる（§7）。P-2 の `bars()` の末尾は形成中足でありうるので、`forming_bar()` と
    time が一致する末尾は落とす（`bars()[-1]` を確定値として扱わない・P-2 の契約）。

技術隔離:
    MP core（`market_profile_api`）を知ってよいのは adapter だけである
    （`dashboard_ui/tests/unit/test_dashboard_import_direction.py` の R3 が機械強制する）。
    探索パスの用意は `indigators.indicator_ui.api_loader` が唯一源であり、遅延 import の
    直前に必ず呼ぶ——他の口（series_port）の実行順に暗黙に依存させない
    （既存前例: `adapter/series_role_table.py` の遅延 import の口）。
"""
from __future__ import annotations

import math
from typing import Sequence

from dashboard_ui.adapter.gateway.material_store import MaterialStore
from dashboard_ui.domain.bar import Bar

#: プロファイルの素材にする時間足（依頼者承認 2026-09-06: シート共通の 1D）。
MP_TIMEFRAME = "1D"
#: 窓の本数（同承認: 直近 60 本）。
MP_WINDOW_BARS = 60
#: 価格ビン分割数（参照実装 `compute_candle_profile` の既定と同値）。
MP_BINS = 60


class MarketProfileGateway:
    """P-MP。1D 確定足の窓から作った 1 本のプロファイルで全行に答える。

    Args:
        bar_port: P-2（足の供給）。1D の確定足と形成中足をここから読む。
        store: 確定素材の共有ストア（epoch 単位の持ち越し。**専用ストアは作らない**）。
        compute: プロファイル計算面。省略時は MP core の参照実装
            （`compute_candle_profile`）を遅延 import して使う。注入点があるのは
            計算量テスト（Test Spy）が**発行回数を数える面**を必要とするためである。
    """

    def __init__(self, *, bar_port, store: "MaterialStore", compute=None) -> None:
        self._bar_port = bar_port
        self._store = store
        self._compute = compute

    def norms_at(
        self, *, dataset_ref: str, prices: "Sequence[float]", now_unix: int
    ) -> "tuple[float | None, ...]":
        """各価格の norm（要求と同じ順序・同じ件数）。範囲外・素材なしは None。"""
        asked = tuple(float(price) for price in prices)
        if not asked:
            return ()
        profile = self._profile_of(dataset_ref=dataset_ref, now_unix=int(now_unix))
        if profile is None:
            return tuple(None for _ in asked)
        return tuple(_norm_at(profile, price) for price in asked)

    # ---------------------------------------------------------------- 素材
    def _profile_of(self, *, dataset_ref: str, now_unix: int) -> "dict | None":
        confirmed_count, window = self._window_of(
            dataset_ref=dataset_ref, now_unix=now_unix
        )
        if not window:
            return None
        epoch = (
            confirmed_count,
            int(window[0].time),
            int(window[-1].time),
            float(window[-1].close),
        )
        return self._store.material(
            key=("market_profile", dataset_ref, MP_TIMEFRAME),
            epoch=epoch,
            name=("market_profile", MP_WINDOW_BARS, MP_BINS),
            factory=lambda: self._compute_profile(window),
        )

    def _window_of(
        self, *, dataset_ref: str, now_unix: int
    ) -> "tuple[int, tuple[Bar, ...]]":
        """`(確定足の総数, 窓＝確定足の末尾 MP_WINDOW_BARS 本)`（形成中足は除く）。"""
        supplied = tuple(
            self._bar_port.bars(dataset_ref=dataset_ref, timeframe=MP_TIMEFRAME)
        )
        forming = self._bar_port.forming_bar(
            dataset_ref=dataset_ref, timeframe=MP_TIMEFRAME, now_unix=now_unix,
        )
        confirmed = (
            supplied[:-1]
            if supplied and forming is not None and int(forming.time) == int(supplied[-1].time)
            else supplied
        )
        # 総数も返す: epoch の第 1 要素にして、窓の外の増減も版へ反映させる。
        return len(confirmed), confirmed[-MP_WINDOW_BARS:]

    def _compute_profile(self, window: "Sequence[Bar]") -> dict:
        compute = self._compute if self._compute is not None else _reference_compute
        return compute(
            [
                {
                    "time": int(bar.time), "open": float(bar.open),
                    "high": float(bar.high), "low": float(bar.low),
                    "close": float(bar.close),
                }
                for bar in window
            ],
            n_bins=MP_BINS,
        )


def _reference_compute(candles, *, n_bins: int) -> dict:
    """MP core の参照実装をそのまま呼ぶ（写しを持たない・無改変で読むだけ）。

    VA 比率の解決規則も core の唯一源（`resolve_va_pct`）に従う——ここへ数字を書くと
    経路ごとに別の既定へ黙って落ちる（ISSUE-260 が断った事故と同型）。
    """
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離を本層に閉じる

    api_loader.load_compute()   # 探索パスの用意は唯一源が持つ（実行順に依存させない）。
    from market_profile_api.compute.market_profile import (  # noqa: E402
        compute_candle_profile,
        resolve_va_pct,
    )

    return compute_candle_profile(candles, n_bins, va_pct=resolve_va_pct(None))


def _norm_at(profile: "dict", price: float) -> "float | None":
    """価格が入る bin の norm。プロファイルの価格域の外・素材なしは None。

    bin の引き方は MP core の bin 帰属
    （`market_profile_api/compute/market_profile.py`・参照実装）と**字面まで同じ**式にする——
    `(price - price_min) / span * n_bins` であって `(price - price_min) / 幅` ではない。
    代数的には同値だが、幅を先に割ると丸めが 1 回増え、**bin 境界ちょうど**の価格で
    core と別の bin へ落ちる（実測 2026-09-06: price_min=38000 / price_max=42000 /
    n_bins=60 で境界 61 点のうち 6 点が食い違った）。ずれても「隣の bin の濃さ」が出る
    だけで出力は正しく見えるため、状態検証では原理的に落ちない種類のずれである。
    一致は `tests/unit/test_market_profile_gateway.py` の差分検定が機械的に固定する
    （`bins[].price` は bin **中心**なので、中心の一致で引く方法は採れない）。
    """
    bins = profile.get("bins") or ()
    n_bins = int(profile.get("n_bins") or 0)
    if not bins or n_bins <= 0 or not math.isfinite(price):
        return None
    price_min = float(profile.get("price_min", 0.0))
    price_max = float(profile.get("price_max", 0.0))
    span = price_max - price_min
    if span <= 0.0 or price < price_min or price > price_max:
        return None
    # 上端（price == price_max）は最後の bin に属する（区間の閉じ側を落とさない）。
    index = min(n_bins - 1, int((price - price_min) / span * n_bins))
    if index < 0 or index >= len(bins):
        return None
    norm = float(bins[index].get("norm"))
    return norm if math.isfinite(norm) else None
