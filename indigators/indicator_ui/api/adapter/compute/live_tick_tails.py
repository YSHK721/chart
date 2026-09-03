"""ライブ毎ティック末尾値の計算アダプタ（ISSUE-250 Phase 1）。

責務:
    :mod:`usecase.serve_live_tick_tails`（純ロジック）へ注入する ``tail_at`` を組み立てる。
    窓のロードは **1 回**だけ行い、以降は形成中バーの差し替えのみを繰り返す（リプレイの
    ``causal_compute_seq`` と同じ畳み方＝ISSUE-233。1 ステップの限界費用が指標計算そのもの
    だけになる）。

値の同値性:
    形成中バーの差し込みは中立共有核 :func:`common.forming_window.apply_forming` の唯一の
    定義を通す（リプレイと同一実体）。したがって各時点の窓は ``/compute`` の
    ``mode="latest"`` に ``forming`` を渡したときと同値であり、そこから先は同じ
    ``latest_compute``（増分ディスパッチ）を通る。

増分器を持たない指標:
    ``latest_compute`` は従来経路（窓全体の再計算）へ落ちる。毎ティック呼ぶと納期に間に
    合わないため、本アダプタは **増分宣言のある指標だけ**を対象にし、それ以外は ``None`` を
    返して純ロジック側で明示的に落とす（黙って粒度を落とさない＝ISSUE-233 の教訓）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from adapter.compute.latest_meta import latest_meta
from common.forming_window import forming_patch

logger = logging.getLogger(__name__)

#: 系列 JSON から末尾 1 点の値だけを取り出すときのキー。
_DATA = "data"


def _last_bar_time(window: Any) -> "int | None":
    """窓の末尾バーの ``time``（UNIX 秒）。判定材料が無ければ ``None``。

    date-index（pandas ``Timestamp``）の ``.value``（ns）から導く
    （``forming_bar.py`` と同じ変換式。``.timestamp()`` は使わない）。本モジュールは
    pandas を import しない（依存は注入側へ寄せる＝``set_last_bar`` と同じ規律）ため、
    ``.value`` の有無だけを見る。持たない index（時刻でない窓）は ``None`` を返し、
    呼び出し側が「一致を確認できなかった」として記録する。
    """
    value = getattr(window.index[-1], "value", None)
    return None if value is None else int(value // 1_000_000_000)


def forming_bar_of_state(state: Any) -> "dict[str, Any]":
    """形成中バーの状態 → 差し替え規則が受け取る形（``time`` ＋ OHLCV）。唯一の写像。

    窓の末尾を揃える側（controller の窓供給）と末尾行へ値を書く側（:func:`make_tail_at`）は
    **同じ材料**から作らなければならない。2 箇所で別々に組むと、片方だけ変えた瞬間に窓と値が
    別のバーを指す（ISSUE-232 の失敗モード）。
    """
    return {
        "time": int(state.time),
        "open": float(state.open), "high": float(state.high),
        "low": float(state.low), "close": float(state.close),
        "volume": float(state.volume),
    }


def window_with_forming(window: Any, bar: "dict[str, Any]", *, inject: Callable) -> Any:
    """窓の末尾を形成中バー ``bar`` の周期へ揃えた窓を返す（供給側の唯一の適用点）。

    なぜ供給側で揃えるか: 末尾行への代入が正しいのは「窓の末尾＝形成中バー」＝述語
    :func:`common.forming_window.forming_patch` が ``"replace"`` を返すときだけである。
    ところが確定窓は直前の確定分までしか持たない（1m は ``tools/live_tick_watch.py`` の
    排他 floor で M1 CSV が M-1 までしか無い）ため、分 M の途中では末尾が構造的に M-1 になり、
    **確定済みの行**へ形成中の OHLCV を書いてしまう。窓の側を形成中バーへ揃えることで、
    前提を仮定ではなく構造で満たす。

    Args:
        window: 確定バーの窓（末尾 1 本が比較対象）。
        bar: 形成中バー（:func:`forming_bar_of_state` の写像）。
        inject: ``(window, [bar]) -> 新しい窓``（pandas 依存を注入側へ寄せる＝
            ``set_last_bar`` と同じ規律。実体は adapter/compute/forming_bar.py の
            注入関数で、/compute の形成中バー注入と同じものを共有する）。

    Returns:
        ``"append"`` のときだけ ``inject`` を通した新しい窓。``"replace"``（既に末尾＝形成中
        バー＝上位足の rollup partial 等）・``"skip"``（末尾より過去）・末尾 time を読めない窓は
        ``window`` をそのまま返す（複製もしない＝既に前提を満たす経路の挙動を変えない）。
    """
    last_time = _last_bar_time(window)
    if last_time is None:
        return window  # 時刻 index でない窓＝比較材料が無い（呼び出し側が記録する）。
    if forming_patch(last_time, bar).mode != "append":
        return window
    return inject(window, [bar])


def is_incremental(indicator_id: str, variant: str, params: "dict[str, Any]") -> bool:
    """その指標が真の増分計算を宣言しているか（＝毎ティック納期に載るか）。

    ISSUE-278 #3: ここに ``except Exception: return False`` を置かない。未登録・未宣言の指標は
    ``latest_meta`` 自身が安全既定（recurrence・K=1）へ落とすため、例外を握る必要が無い。
    握ると宣言テーブルの実装バグ（壊れた lambda 等）まで「対象外」に潰れ、その指標だけが
    痕跡なくティック更新から消える。
    """
    meta = latest_meta(indicator_id, variant, params)
    return meta.archetype == "incremental" and meta.incremental is not None


def make_tail_at(
    *,
    df: Any,
    adapter: Any,
    latest_compute: Callable[..., list],
    set_last_bar: Callable[[Any, dict], None],
) -> Callable:
    """``tail_at(spec, state) -> {系列名: 値} | None`` を組み立てる。

    Args:
        df: 確定バーの DataFrame（窓ロード済み・1 回だけ渡す）。
        adapter: IndicatorComputePort。
        latest_compute: 増分ディスパッチ（``min_tail`` 省略＝K=1）。
        set_last_bar: 窓の**末尾行だけ**を形成中バーで上書きする注入関数（pandas 依存を
            注入側へ寄せる）。窓は 1 回だけ複製し、以降は末尾行の代入のみ＝1 ステップの
            費用が窓長に比例しない（DataFrame を毎ティック作り直すと 12.3ms/tick になる）。
    """
    window = df.copy()
    empty = len(window) == 0
    reported = False

    def tail_at(spec, state) -> "dict[str, float] | None":
        nonlocal reported
        if empty or not is_incremental(spec.indicator_id, spec.variant, spec.params):
            return None
        bar = forming_bar_of_state(state)
        values = {k: v for k, v in bar.items() if k != "time"}
        # 差し込み規則は共有核 forming_patch ただ 1 つ（F-9）。末尾行への代入が正しいのは
        #   「窓の末尾＝形成中バーと同じバー」＝ mode == "replace" のときだけである。以前は
        #   比較せず代入し、その前提をコメントで仮定していただけだった（前提が破れると別の
        #   バーの値を黙って描く＝ISSUE-232 の失敗モード）。窓は供給側
        #   （:func:`window_with_forming`）が同じ材料で揃えてあるので、ここが否定されるのは
        #   バッチが周期をまたいだ場合など「本当に食い違った」ときだけになる。
        last_time = _last_bar_time(window)
        if forming_patch(last_time, bar).mode != "replace":
            if not reported:
                reported = True   # ISSUE-278 #3 と同じ規律: 無音にせず、かつ毎ティック吐かない。
                logger.warning(
                    "live tails: 窓の末尾が形成中バーと別のバー（窓末尾 time=%s・形成中 time=%s）"
                    "＝末尾行の代入は続けるが、窓と形成中バーの対応は保証されない",
                    last_time, state.time,
                )
        set_last_bar(window, values)
        # ISSUE-278 #3: ここで例外を握らない。「増分器が扱えない」は prepare→None の明示契約が
        #   既に表現しており、追加の except は増分器の実装バグ（形状不一致・dtype 不整合）まで
        #   同じ「対象外」へ潰す。潰すと応答にもログにも痕跡が残らず、その指標だけティック更新が
        #   止まる。境界（controller）が 1 度だけ記録して tails 全体を落とす方が復旧できる。
        series = latest_compute(
            adapter, spec.indicator_id, spec.variant, window, dict(spec.params)
        )
        out: "dict[str, float]" = {}
        for s in series or []:
            data = s.get(_DATA) or []
            if data:
                out[s.get("name")] = float(data[-1].get("value"))
        return out or None

    return tail_at
