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

from typing import Any, Callable

from adapter.compute.latest_meta import latest_meta

#: 系列 JSON から末尾 1 点の値だけを取り出すときのキー。
_DATA = "data"


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

    def tail_at(spec, state) -> "dict[str, float] | None":
        if empty or not is_incremental(spec.indicator_id, spec.variant, spec.params):
            return None
        # 差し込み規則は共有核 apply_forming と同値（末尾 time 一致 → 置換）。窓の末尾 time は
        #   形成中バーの周期と一致する前提（ライブは同一 tf の窓を渡す）。
        set_last_bar(window, {
            "open": state.open, "high": state.high, "low": state.low,
            "close": state.close, "volume": float(state.volume),
        })
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
