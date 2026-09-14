"""PARAM_BINDING — param 既定値の導出と add_* への束縛（call_binding から分離・ISSUE-502 段階 4B）。

責務は 1 つだけである: **指標記述子表が宣言した param を、実 callable の受理引数へ突き合わせる**。
表そのもの（どの指標がどの param を宣言するか）は ``call_binding._TABLE`` が所有し、本モジュールは
表を引数として受け取る（指標名を 1 つも知らない）。

分離の理由（SRP）: 「param 既定値の導出規則が変わる」動機（catalog 配信仕様・variant 粒度）と
「指標が 1 件増える」動機は独立である。従来は両者が 1 ファイルに同居していた。
"""

from __future__ import annotations

import copy
import inspect
from typing import Any, Callable, Mapping

#: 計算.時間足の既定（ISSUE-274）。"chart"＝チャートの時間足に追従する（＝投影しない）。
CALC_TIMEFRAME_DEFAULT = "chart"

#: 指標 src ではなく **計算層が消費する** param（ISSUE-274 の計算.時間足）。
#: 全 variant の受理集合に含まれ、CallBinding の invoke が add_* 呼出前に取り除く。
LAYER_CONSUMED_PARAMS: frozenset[str] = frozenset({"timeframe"})


def accepted_param_names(callable_: Callable) -> "set[str] | None":
    """``callable_`` が受理するキーワード引数名の集合（``**kwargs`` を持つなら ``None``＝無制限）。

    「この variant が受理する param」の実体はここ（add_* のシグネチャ）にしかない。
    ``_TABLE`` の ``params_defaults`` 宣言が実シグネチャと一致することは
    api/tests/test_call_binding_param_scopes.py が本関数を使って固定する。
    """
    sig = inspect.signature(callable_)
    if any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values()):
        return None
    return {
        name for name, p in sig.parameters.items()
        if p.kind in (p.KEYWORD_ONLY, p.POSITIONAL_OR_KEYWORD)
    }


def bind_kwargs(callable_: Callable, params: dict[str, Any]) -> dict[str, Any]:
    """``params`` を ``callable_`` の受理引数へ束縛する。未受理キーは **例外**（ISSUE-278 #8）。

    従来は「当該 variant の add_* が取らない引数は黙って捨てる」縮退だった。これは
    ``params_defaults`` の宣言粒度が compute_id、実契約が variant であることの差を吸収する
    ためのもので、結果として **UI が効かないコントロールを表示し続けた**（実測: profit_band
    variant=global で normalize/window/atr_period/min_obs を動かしても応答は byte 同一）。
    宣言粒度を variant へ揃えた（各 variant が受理引数だけを宣言する）ため、差を埋める
    無言破棄は不要になった。以後、未受理キーの到来は front/back の契約違反であり
    ValueError＝error.type="validation" として可視化する
    （翻訳は indicator_compute_adapter の ValueError 翻訳境界が行う）。

    ``**kwargs`` を持つ callable は素通しする（受理集合が定義できないため）。

    計算量: signature 解析の発行は束縛 1 回につき 1 回であり param 数に依らない
    （``api/tests/test_call_binding_complexity.py`` C2 が固定する）。
    """
    allowed = accepted_param_names(callable_)
    if allowed is None:
        return dict(params)
    unknown = sorted(set(params) - allowed)
    if unknown:
        raise ValueError(
            f"{getattr(callable_, '__name__', callable_)} が受理しない param が渡されました: "
            f"{unknown}。variant ごとの受理引数は GET /catalog の paramScopes を参照してください。"
        )
    return dict(params)


def derive_param_defaults(table: Mapping[tuple[str, str], Any]) -> dict[str, dict[str, Any]]:
    """記述子表の ``params_defaults`` 宣言から compute_id → param 既定値を導出する（ISSUE-180）。

    ``catalog_schema.PARAM_DEFAULTS``（``GET /catalog`` の配信値）の唯一の生成元。返り値は deep copy
    のため、呼び出し側の変更は表へ波及しない。既定値は指標（compute_id）の単位で 1 セット
    ＝全 variant の宣言の和であり、共有 param は全 variant で同値でなければならない。

    整合検査（宣言漏れ・食い違いの構造的検出）:
      - 表の **全エントリ**（compute_id, variant）が ``params_defaults`` を持つ（漏れは ValueError）。
      - 同一 compute_id の複数 variant が同じ param を **異なる既定値** で宣言することを禁ずる。
    dict の挿入順は表のエントリ順（＝従来の配信順）を保つ。
    """
    out: dict[str, dict[str, Any]] = {}
    for (compute_id, variant), spec in table.items():
        defaults = spec.get("params_defaults")
        if defaults is None:
            raise ValueError(
                f"params_defaults が未宣言のエントリがあります: {(compute_id, variant)}。"
                "各 variant が受理する param の既定値を宣言してください（ISSUE-278 #8）。"
            )
        merged = out.setdefault(compute_id, {})
        for name, value in defaults.items():
            if name in merged and merged[name] != value:
                raise ValueError(
                    f"variant 間で param 既定値が食い違っています: {compute_id}.{name} "
                    f"({merged[name]!r} != {value!r} / variant={variant})。"
                )
            merged[name] = copy.deepcopy(value)
    for defaults in out.values():
        # 計算.時間足（ISSUE-274）: 「この指標を何の足で計算するか」は指標固有の性質ではなく
        #   全指標共通の設定であるため、各エントリへ同じリテラルを配らず本導出で 1 度だけ注入する
        #   （front の catalog.js も同じく REGISTRY 構築時に 1 箇所から注入する＝両側とも単一定義）。
        #   指標 src へは渡らない（invoke が計算層の param として pop する）。
        defaults.setdefault("timeframe", CALC_TIMEFRAME_DEFAULT)
    return out


def derive_param_scopes(
    table: Mapping[tuple[str, str], Any],
) -> dict[str, dict[str, list[str]]]:
    """compute_id → variant → その variant が受理する param 名（ISSUE-278 #8）。

    ``GET /catalog`` が ``paramScopes`` として配信し、front はこれで (a) ダイアログに出す
    コントロール (b) ``/compute`` へ送る params を variant ごとに決める。従来は front が
    variant 横断の全 params を送り、受理しない引数を back が無言で捨てていたため、
    **効かないコントロールが UI に出続けていた**（実測: profit_band global の
    normalize/window/atr_period/min_obs は応答 byte 同一）。

    宣言（``params_defaults`` のキー集合）が実シグネチャと一致することは
    api/tests/test_call_binding_param_scopes.py が ``accepted_param_names`` と突き合わせて固定する。
    """
    out: dict[str, dict[str, list[str]]] = {}
    for (compute_id, variant), spec in table.items():
        names = list(spec.get("params_defaults") or {})
        names += [n for n in sorted(LAYER_CONSUMED_PARAMS) if n not in names]
        out.setdefault(compute_id, {})[variant] = names
    return out
