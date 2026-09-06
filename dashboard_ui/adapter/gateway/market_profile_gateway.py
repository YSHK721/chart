"""P-MP の実装: 価格ラダーの行価格 → 直近 1m プロファイルの TPO 密度（依頼者承認 2026-09-06）。

何を出すか:
    dataset_ref の **1m 確定足 直近 2000 本**から作った 1 本のプロファイル（ビン幅 5pt・
    VA は MP core の既定規約）を引き、各行の水準価格が入る bin の `norm`（0..1）を返す。
    表示は色の濃度＋横バー（フロント）で、数値は出さない。

なぜ 1D×60 本ではなく 1m×2000 本か（実測 2026-09-06・依頼者承認「a) 1m 足の短窓へ変更」）:
    日足 TPO は各日の高安**全域**を数えるため、近接した水準がことごとく同じ bin の同じ
    密度になる。実UIの可視ラダー域（±130pt）が 1〜2 ビンへ潰れ、行ごとの差が出なかった
    （norm 0.83〜0.96・ビンを 5pt に細分しても distinct 2/7）。原因は解像度ではなく
    **素材の性質**なので、ビンを細かくしても直らない。1m×2000 本なら span ≈ 2,251pt で
    可視 7 行が 7/7 分離した（norm 0.03〜0.44）。窓の外の遠隔行は None ＝空欄（設計どおり）。

なぜビン数を固定しないか:
    固定ビン数だと版面の分解能が素材の広さに左右される（広い窓ほど 1 ビンが太る）。
    幅を :data:`MP_BIN_WIDTH_POINTS` に固定し、ビン数は窓の span から導出する。
    上限 :data:`MP_MAX_BINS` は計算量の保護である（実測: 450 ビンで 3.2ms・1125 ビンで 4.2ms）。

なぜ計算は 1 本か:
    プロファイルは確定足だけから決まるので epoch の中で不変である。行ごと・要求ごとに
    畳み直しても**出力は正しいまま**であり、状態検証では原理的に落ちない
    （ISSUE-450 / ISSUE-457 / ISSUE-464 と同型の「作ってから捨てる」欠陥）。したがって
    持ち越しの口を通す。専用のストアは新設せず、確定素材の共有に既に使っている
    :class:`MaterialStore` へ相乗りする（同じ性質の量に第 2 の置き場所を作らない）。

版（epoch）の取り方:
    `(確定足の本数, 窓先頭 time, 窓末尾 time, 窓末尾 close)`。本数と端だけでは、遡り訂正で
    確定足の中身が入れ替わったときに古いプロファイルを配り続ける（ISSUE-457 の同型）。
    1m なので epoch は**毎分**進む。1 回の畳み込みは実測 ~3ms（450 ビン 3.2ms・
    1125 ビン 4.2ms・2026-09-06）であり、毎分 1 回の作り直しは許容できる。逆に言えば
    ティックごとに作り直せば同じ 3ms が毎ティック載るので、下の「形成中足の扱い」が
    崩れると計算量が桁で変わる。

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

形成中足の扱い（**供給の末尾 1 本を無条件に落とす**・実測 2026-09-06）:
    窓に入れない。入れると epoch がティックごとに動き、更新粒度が「1m バー確定」から
    無言でずれる（§7）。落とし方を `forming_bar()` との突合に頼ってはならない:
    `now_unix` は**表示足**の末尾 time であり、表示足が 1m より粗いと 1m の周期に載らない
    ので `forming_bar()` は None を返す。その結果、形成中の 1m 足が「確定足」として窓に
    残り、毎ティック再計算 ＋ norm のちらつきが起きる（実測: 粗い表示足で 20 ティック中
    **20 回**の再計算。ダッシュボードの 8 時間足のうち 7 つが 1m より粗い）。

    そこで `bars()[:-1]` ——**末尾 1 本を条件なしで落とす**。これは P-2 の明文契約
    （`dashboard_ui/usecase/sheet_ports.py`: 確定足だけを見たい呼び出し側は `bars()[-2]`）
    そのものであり、決定的で時計を要らなくする。休場中は確定足を 1 本（2000 本中 1 本）
    余計に失うが、窓の性質は変わらない。`now_unix` は P-MP の署名に残る（面の契約）が、
    **窓の決定には使わない**。

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

#: プロファイルの素材にする時間足（依頼者承認 2026-09-06「a) 1m 足の短窓へ変更」）。
MP_TIMEFRAME = "1m"
#: 窓の本数（同承認: 直近 2000 本。実測で可視 7 行が 7/7 分離した長さ）。
MP_WINDOW_BARS = 2000
#: 価格ビンの幅（pt）。版面の分解能を素材の広さから切り離すため、ビン**数**ではなく
#: 幅を固定する。
MP_BIN_WIDTH_POINTS = 5.0
#: ビン数の上限（計算量の保護）。span がどれだけ広くてもここで頭打ちにする。
MP_MAX_BINS = 2000


class MarketProfileGateway:
    """P-MP。1m 確定足の窓から作った 1 本のプロファイルで全行に答える。

    Args:
        bar_port: P-2（足の供給）。1m の足をここから読む（末尾 1 本は確定扱いしない）。
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
        """各価格の norm（要求と同じ順序・同じ件数）。範囲外・素材なしは None。

        `now_unix` は P-MP の面が運ぶ引数だが、**窓の決定には使わない**（モジュール
        docstring「形成中足の扱い」）。窓は供給の並びだけで決まり、時計に依存しない。
        """
        asked = tuple(float(price) for price in prices)
        if not asked:
            return ()
        profile = self._profile_of(dataset_ref=dataset_ref)
        if profile is None:
            return tuple(None for _ in asked)
        return tuple(_norm_at(profile, price) for price in asked)

    # ---------------------------------------------------------------- 素材
    def _profile_of(self, *, dataset_ref: str) -> "dict | None":
        confirmed_count, window = self._window_of(dataset_ref=dataset_ref)
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
            # 名前は素材の作り方を表す。ビン数は動的なので、導出元の定数で名乗る。
            name=("market_profile", MP_WINDOW_BARS, MP_BIN_WIDTH_POINTS),
            factory=lambda: self._compute_profile(window),
        )

    def _window_of(self, *, dataset_ref: str) -> "tuple[int, tuple[Bar, ...]]":
        """`(確定足の総数, 窓＝確定足の末尾 MP_WINDOW_BARS 本)`。

        `bars()` の末尾は形成中でありうる（P-2 の契約）ので**無条件に**落とす。時計と
        突き合わせない理由はモジュール docstring「形成中足の扱い」に書いた。
        """
        supplied = tuple(
            self._bar_port.bars(dataset_ref=dataset_ref, timeframe=MP_TIMEFRAME)
        )
        confirmed = supplied[:-1]
        # 総数も返す: epoch の第 1 要素にして、窓の外の増減も版へ反映させる。
        return len(confirmed), confirmed[-MP_WINDOW_BARS:]

    def _compute_profile(self, window: "Sequence[Bar]") -> dict:
        compute = self._compute if self._compute is not None else _reference_compute
        candles = [
            {
                "time": int(bar.time), "open": float(bar.open),
                "high": float(bar.high), "low": float(bar.low),
                "close": float(bar.close),
            }
            for bar in window
        ]
        return compute(candles, n_bins=_bins_for(candles))


def _bins_for(candles) -> int:
    """窓の span から導いたビン数（幅 :data:`MP_BIN_WIDTH_POINTS`・上限 :data:`MP_MAX_BINS`）。

    span は MP core の価格レンジ定義（`price_range`）**だけ**から取る。`max(high) - min(low)`
    をここへ書き写すと、同じ量に第 2 の定義ができる。

    書き写しても**出力は壊れない**（実測 2026-09-06）: 縮退（高安が 1 点に潰れた窓）で
    core は `price_max = price_min + 1` へ安全化するが、その差 1pt は `int(1 / 5.0) = 0`
    へ落ち、下の `max(1, …)` が写しの span = 0 ともども 1 ビンへ吸収する。ゼロ割れもしない
    ——span は割る数ではなく割られる数だからである。つまり両定義の差は出力に現れず、
    **状態検証では原理的に落ちない**。写しが害をなすのは core がレンジ定義を変えたとき
    （安全化の幅・空リストの扱い・外れ値の除外など）で、そのとき版面の分解能だけが
    黙ってずれる。だから「唯一源であること」は出力ではなく構造で守るしかない:
    `tests/unit/test_market_profile_gateway.py` が価格レンジ面を差し替えた Test Spy で
    「n_bins がこの面の返り値に追従する」ことを機械的に固定している。
    """
    price_min, price_max = _core_price_range(candles)
    return max(1, min(MP_MAX_BINS, int((price_max - price_min) / MP_BIN_WIDTH_POINTS)))


def _core():
    """MP core のモジュール（遅延 import は**この 1 か所**だけ）。

    探索パスの用意は `api_loader` が唯一源であり、遅延 import の直前に必ず呼ぶ——他の口
    （series_port）の実行順に暗黙に依存させない。口ごとに import を書き写すと、探索パスの
    用意が片方だけ抜けても「もう一方を先に通した要求」では動いてしまい、経路によって
    生き死にが変わる（既存前例: `adapter/series_role_table.py` の遅延 import の口）。
    """
    from indigators.indicator_ui import api_loader  # 遅延: 技術隔離を本層に閉じる

    api_loader.load_compute()   # 探索パスの用意は唯一源が持つ（実行順に依存させない）。
    from market_profile_api.compute import market_profile  # noqa: E402

    return market_profile


def _core_price_range(candles) -> "tuple[float, float]":
    """MP core の価格レンジ定義（`price_range`）をそのまま読む。"""
    return _core().price_range(candles)


def _reference_compute(candles, *, n_bins: int) -> dict:
    """MP core の参照実装（`compute_candle_profile`）をそのまま呼ぶ（無改変で読むだけ）。

    VA 比率の解決規則も core の唯一源（`resolve_va_pct`）に従う——ここへ数字を書くと
    経路ごとに別の既定へ黙って落ちる（ISSUE-260 が断った事故と同型）。
    """
    core = _core()
    return core.compute_candle_profile(
        candles, n_bins, va_pct=core.resolve_va_pct(None),
    )


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
