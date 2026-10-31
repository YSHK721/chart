"""§4（事前登録の外）: 1h の外れの改善が β ならではのものかを、長い平均と比べて確かめる。

実行: lightweight-charts-python-main/.venv/bin/python -m tools.measure.tr_beta.run_attrib
出力: tools/measure/tr_beta/results/attrib.csv
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tools.measure.tr_beta import beta as B
from tools.measure.tr_beta import run as RN

MODELS = {"w": ("w",), "beta": ("w", "b"), "long": ("w", "l"), "both": ("w", "l", "b")}
NS = (50, 100)
OTHERS = {"b": ("w", "l"), "l": ("w", "b")}


def usable(s: dict, n: int) -> np.ndarray:
    """Y・W・長い平均が有限で、β の符号が 0 でない足。"""
    return np.isfinite(s["y"]) & np.isfinite(s["w"]) & np.isfinite(s[f"l{n}"]) & (s[n] != 0)


def compare_models(is_: dict, oos: dict, n: int, models=MODELS, solve=np.linalg.lstsq) -> dict:
    """係数を IS で決め、OOS の外れを見積もりごとに返す。"w" からの改善も返す。"""
    ok_is, ok_oos = usable(is_, n), usable(oos, n)
    coefs = {name: B.fit_log_model(is_, n, terms, ok_is, solve) for name, terms in models.items()}
    preds = {name: B.predict_log_model(oos, n, models[name], coefs[name]) for name in models}
    errs = {name: B.relative_error(oos["y"], preds[name], ok_oos) for name in models}
    return dict(errs=errs, gain={k: errs["w"] - v for k, v in errs.items()}, coefs=coefs, preds=preds, ok=ok_oos,
                corr=float(np.corrcoef(oos[n][ok_oos], np.log(oos["w"][ok_oos] / oos[f"l{n}"][ok_oos]))[0, 1]))


def partial_residuals(s: dict, n: int, term: str, y_coef, v_coef) -> tuple[np.ndarray, np.ndarray]:
    """他の項を除いた term と log Y を返す。使えない足は term 側 0・log Y 側 NaN（circular_test が除く）。"""
    ok = usable(s, n)
    others = OTHERS[term]
    r = np.where(ok, B.TERMS[term](s, n) - B.linear_value(s, n, others, v_coef), 0.0)
    e = np.where(ok, np.log(s["y"]) - B.linear_value(s, n, others, y_coef), np.nan)
    return r, e


def partial_test(is_: dict, oos: dict, n: int, term: str, margin: int = RN.MARGIN, solve=np.linalg.lstsq) -> dict:
    """term の偏回帰の傾きを IS・OOS で全ずらし検定する。除く項の係数は IS で決める。"""
    ok = usable(is_, n)
    others = OTHERS[term]
    y_coef = B.fit_log_model(is_, n, others, ok, solve)
    v_coef = B.fit_linear(B.TERMS[term](is_, n), is_, n, others, ok, solve)
    out = {}
    for label, s in (("is", is_), ("oos", oos)):
        r, e = partial_residuals(s, n, term, y_coef, v_coef)
        slope, _, p = B.exact_partial_test(r, e, margin)
        out.update({f"{term}_slope_{label}": slope, f"{term}_p_{label}": p})
    return out


def attrib_row(is_: dict, oos: dict, n: int, rng, count: int = RN.COUNT, margin: int = RN.MARGIN, label: str = "oos_hours") -> dict:
    """1 行分の値。n の見積もりの比較・係数の縮み・偏回帰の p・週単位の幅。"""
    r = compare_models(is_, oos, n)
    ok_oos = r["ok"]
    row = dict(n=n, hours=label, corr=r["corr"],
               coef_is_alone=r["coefs"]["beta"][2], coef_is_with_long=r["coefs"]["both"][3],
               coef_oos_alone=B.fit_log_model(oos, n, MODELS["beta"], ok_oos)[2],
               coef_oos_with_long=B.fit_log_model(oos, n, MODELS["both"], ok_oos)[3],
               **{f"gain_{k}": v for k, v in r["gain"].items()})
    row["gain_incr"] = r["gain"]["both"] - r["gain"]["long"]
    row.update(partial_test(is_, oos, n, "b", margin))
    row.update(partial_test(is_, oos, n, "l", margin))
    y, week = oos["y"][ok_oos], oos["week"][ok_oos]
    pred = {k: v[ok_oos] for k, v in r["preds"].items()}
    pairs = {"gain_beta": ("w", "beta"), "gain_long": ("w", "long"), "long_minus_beta": ("beta", "long"), "gain_incr": ("long", "both")}
    for name, (base, new) in pairs.items():
        lo, hi = B.gain_interval(y, pred[base], pred[new], week, count, rng)
        row.update({f"{name}_lo": lo, f"{name}_hi": hi})
    return row


def main():
    rng = np.random.default_rng(RN.SEED)
    is_ = RN.series(RN.PERIODS["IS"][0], "1h", *RN.PERIODS["IS"][1:], ns=NS, with_long=True)
    oos = RN.series(RN.PERIODS["OOS"][0], "1h", *RN.PERIODS["OOS"][1:], ns=NS, with_long=True)
    oos_is_hours = RN.series(RN.PERIODS["OOS"][0], "1h", *RN.PERIODS["OOS"][1:], ns=NS, with_long=True, hours=is_["hours"])
    rows = [attrib_row(is_, oos, n, rng) for n in NS] + [attrib_row(is_, oos_is_hours, n, rng, label="is_hours") for n in NS]
    df = pd.DataFrame(rows)
    df.to_csv(RN.OUT / "attrib.csv", index=False)
    print(df.round(4).T.to_string())


if __name__ == "__main__":
    main()
