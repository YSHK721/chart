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


def _ok(s: dict, n: int) -> np.ndarray:
    return np.isfinite(s["y"]) & np.isfinite(s["w"]) & np.isfinite(s[f"l{n}"]) & (s[n] != 0)


def compare_models(is_: dict, oos: dict, n: int, models=MODELS, solve=np.linalg.lstsq) -> dict:
    """係数を IS で決め、OOS の外れを見積もりごとに返す。c なしの "w" からの改善も返す。"""
    ok_is, ok_oos = _ok(is_, n), _ok(oos, n)
    errs, coefs = {}, {}
    for name, terms in models.items():
        coefs[name] = B.fit_log_model(is_, n, terms, ok_is, solve)
        errs[name] = B.relative_error(oos["y"], B.predict_log_model(oos, n, terms, coefs[name]), ok_oos)
    return dict(errs=errs, gain={k: errs["w"] - v for k, v in errs.items()}, coef_long=coefs["long"],
                corr=float(np.corrcoef(oos[n][ok_oos], np.log(oos["w"][ok_oos] / oos[f"l{n}"][ok_oos]))[0, 1]))


def main():
    rng = np.random.default_rng(RN.SEED)
    rows = []
    is_ = RN.series(*RN.PERIODS["IS"][:1], "1h", *RN.PERIODS["IS"][1:])
    oos = RN.series(*RN.PERIODS["OOS"][:1], "1h", *RN.PERIODS["OOS"][1:])
    for n in NS:
        r = compare_models(is_, oos, n)
        row = dict(n=n, corr=r["corr"], **{f"gain_{k}": v for k, v in r["gain"].items()})
        for label, s in (("is", is_), ("oos", oos)):
            e = np.log(s["y"]) - np.log(B.predict_log_model(s, n, MODELS["long"], r["coef_long"]))
            e[~_ok(s, n)] = np.nan
            d, _, p = B.circular_test(s[n], e, B.mean_gap, B.draw_shifts(s["bars"], RN.COUNT, RN.MARGIN, rng))
            row.update({f"d_{label}": d, f"p_{label}": p})
        rows.append(row)
    df = pd.DataFrame(rows)
    df.to_csv(RN.OUT / "attrib.csv", index=False)
    print(df.round(4).to_string())


if __name__ == "__main__":
    main()
