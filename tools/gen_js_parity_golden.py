"""Python→JS パリティ golden fixture 生成（ISSUE-087 🔴-3: 二重実装の生成同期）。

規則の権威は Python（marketdata.session_day / market_profile _value_area / marketdata.tf_meta）。
本スクリプトが境界網羅の (input, expected) を JSON へ書き出し、JS テスト
（market_profile/web/tests/py_parity_golden.test.js）が JS 実装（session_day.js /
dwell_accumulator.valueArea / tf_meta.js）との一致を検定する。規則変更時は本スクリプトを
再実行して fixture を更新する（手写しスポット値による弱同期を置換）。

実行: PYTHONPATH=. python3 tools/gen_js_parity_golden.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "indigators" / "market_profile" / "api"))
sys.path.insert(0, str(ROOT / "indigators" / "indicator_ui" / "api"))

import numpy as np  # noqa: E402

from marketdata import session_day as sd  # noqa: E402
from marketdata import resample  # noqa: E402
from marketdata import tf_meta  # noqa: E402
# ISSUE-091 A7: private 名でなく公開 API（value_area）を参照する。
# ISSUE-260: VA 比率の既定は Python 唯一源。JS は生成物として読む（第 2 定義を作らない）。
from market_profile_api.compute.market_profile import VA_PCT_DEFAULT, value_area  # noqa: E402

OUT = ROOT / "indigators" / "market_profile" / "web" / "tests" / "fixtures" / "py_parity_golden.json"
#: 時間足台帳の JS 生成物（実体は market_profile 側・indicator_ui からは symlink で共有）。
JS_OUT = ROOT / "indigators" / "market_profile" / "web" / "js" / "domain" / "tf_ledger_generated.js"
#: MP ソース能力の JS 生成物（zp 対応 tf。Python の配信 controller が唯一源）。
MP_CAP_OUT = (ROOT / "indigators" / "market_profile" / "web" / "js" / "domain"
              / "mp_capability_generated.js")
#: MP パラメータ既定値の JS 生成物（VA 比率。Python の compute が唯一源・ISSUE-260）。
MP_PARAM_OUT = (ROOT / "indigators" / "market_profile" / "web" / "js" / "domain"
                / "mp_param_defaults_generated.js")


def _utc(y, m, d, hh=0, mm=0, ss=0):
    return int(datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc).timestamp())


def session_cases() -> list[int]:
    """境界網羅の検定時刻: 米 DST 切替（3月第2日・11月第1日）前後・週跨ぎ・月末・日常点。"""
    pts: list[int] = []
    # 米 DST 切替日（2024-2026）: 春（3月）と秋（11月）の切替日 ±（前日・当日・翌日、各 3 時刻）。
    dst_days = [
        (2024, 3, 10), (2024, 11, 3),
        (2025, 3, 9), (2025, 11, 2),
        (2026, 3, 8), (2026, 11, 1),
    ]
    for (y, m, d) in dst_days:
        base = _utc(y, m, d)
        for off_day in (-1, 0, 1):
            for hh in (0, 12, 21, 22, 23):
                pts.append(base + off_day * 86400 + hh * 3600)
    # 週跨ぎ（金→土→日→月）・月末・年跨ぎ・日常点。
    for (y, m, d) in [(2026, 7, 10), (2026, 7, 11), (2026, 7, 12), (2026, 7, 13),
                      (2026, 1, 31), (2026, 2, 28), (2025, 12, 31), (2026, 1, 1),
                      (2026, 4, 30), (2026, 7, 15)]:
        for hh in (0, 3, 12, 20, 21, 22, 23):
            pts.append(_utc(y, m, d, hh, 30))
    return sorted(set(pts))


def value_area_cases() -> list[dict]:
    # ISSUE-260: 既定比率（0.70）以外でも Python↔JS の VA が一致することを固定する。
    #   既定でしか検定していないと「比率が届いていない／写しがずれている」を検出できない。
    cases = [
        # 整数 TPO（count 系）。
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [1, 3, 8, 2, 1], "pct": 0.70},
        {"centers": [10.0, 11.0, 12.0], "tpo": [5, 5, 5], "pct": 0.70},
        {"centers": [10.0], "tpo": [7], "pct": 0.70},
        # float z（zp 系・ISSUE-085 の切り捨てバグ回帰域）。
        {"centers": [10.0, 11.0, 12.0, 13.0], "tpo": [0.9, 0.9, 0.9, 0.9], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [0.1, 0.2, 5.0, 0.3, 0.1], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0], "tpo": [0.0, 0.0, 2.5, 0.5], "pct": 0.70},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "tpo": [2.2, 0.4, 3.1, 3.1, 0.2, 1.9], "pct": 0.70},
        # 非既定比率（ISSUE-260）: 同一分布・異なる pct で拡張幅が変わることまで含めて固定する。
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [1, 3, 8, 2, 1], "pct": 0.30},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [1, 3, 8, 2, 1], "pct": 0.55},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0], "tpo": [1, 3, 8, 2, 1], "pct": 0.95},
        {"centers": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], "tpo": [2.2, 0.4, 3.1, 3.1, 0.2, 1.9], "pct": 0.45},
        {"centers": [10.0, 11.0, 12.0, 13.0], "tpo": [0.9, 0.9, 0.9, 0.9], "pct": 0.99},
    ]
    out = []
    for c in cases:
        lo, hi = value_area(np.asarray(c["tpo"], dtype=float), np.asarray(c["centers"]), c["pct"])
        out.append({**c, "expected": [float(lo), float(hi)]})
    return out


def tf_ledger() -> "list[dict]":
    """時間足台帳（唯一の定義＝marketdata.resample.TF_DESCRIPTORS）。

    JS へ配るのは**派生属性まで含めた全体**にする。値（barSec）だけを同期して派生属性
    （floorable / calendar）を JS 側で書き直していたため、``floorable`` の写しがずれて
    ライブの更新粒度が時間足で割れた（ISSUE-253）。判断に使う属性を残らず配る。

    barSec も台帳 ``TfDescriptor.bar_sec`` から採る（ISSUE-261）。かつては別 dict
    （``tf_meta.TF_BAR_SEC`` の手書き）を引いており、生成器が 2 つの表を突き合わせていた。
    """
    return [
        {
            "code": code,
            "barSec": int(d.bar_sec),
            "floorable": bool(d.floorable),
            "calendar": bool(d.calendar),
        }
        for code, d in resample.TF_DESCRIPTORS.items()
    ]


def _zp_supported_tfs() -> "tuple[str, ...]":
    """src=zp が対応する時間足（Python 側の唯一源から採る）。

    定義は配信 controller（``_ZP_TF_ALLOWED``）が持つ。ここで値を書き写さない。
    """
    from market_profile_api.controller.tf_period_profile_controller import _ZP_TF_ALLOWED

    return tuple(_ZP_TF_ALLOWED)


def render_tf_ledger_js(rows: "list[dict]") -> str:
    """時間足台帳の JS モジュール（データのみ・自動生成）を組み立てる。"""
    entries = ",\n".join(
        "  {{ code: '{code}', barSec: {barSec}, floorable: {floorable}, calendar: {calendar} }}".format(
            code=r["code"], barSec=r["barSec"],
            floorable=str(r["floorable"]).lower(), calendar=str(r["calendar"]).lower(),
        )
        for r in rows
    )
    return (
        "// tf_ledger_generated.js — 時間足台帳（**自動生成・手で編集しない**）。\n"
        "//\n"
        "// 生成元: marketdata/resample.py の TF_DESCRIPTORS ＋ marketdata/tf_meta.py の TF_BAR_SEC。\n"
        "// 生成器: tools/gen_js_parity_golden.py（規則変更時に再実行する）。\n"
        "//\n"
        "// なぜ生成物なのか（ISSUE-254）: 台帳を JS 側にも書くと第 2 定義になり、派生属性\n"
        "//   （floorable / calendar）が静かにずれる。実際 floorable の写しがずれて、ライブの\n"
        "//   更新粒度が時間足で割れた（ISSUE-253: 1W/1M だけ tick 再生から脱落）。定義は Python\n"
        "//   ただ 1 つとし、JS は生成された値を読むだけにする。陳腐化は parity 検定が落とす。\n"
        "//\n"
        "//   code      : 時間足コード（挿入順＝台帳の順序）\n"
        "//   barSec    : 名目バー秒長（1W=7日・1M=30日。厳密境界はラベル規約が担う）\n"
        "//   floorable : 単純 floor で期間始端を表せるか（1W/1M は false）\n"
        "//   calendar  : セッション日（ブローカー暦日）で集計する上位足か\n"
        "export const TF_LEDGER = Object.freeze([\n" + entries + ",\n].map(Object.freeze));\n"
    )


def forming_fold_cases() -> "list[dict]":
    """形成中バー畳み込みの境界ケース（Python↔JS パリティ・ISSUE-272）。

    規則は「open 固定・high/low 走行極値・close は当該 tick」。JS 側は domain/forming_fold が
    唯一実装で、Python 側は usecase.serve_live_tick_tails.forming_states が同規則を持つ
    （言語が違うため共有できない）。値の一致を本 fixture が拘束する。
    """
    from usecase.serve_live_tick_tails import forming_states

    seqs = [
        [100.0],                                   # 1 tick
        [100.0, 101.0, 99.0, 100.5],               # 上げ→下げ→戻し
        [100.0, 100.0, 100.0],                     # 同値のみ（極値が動かない）
        [100.0, 99.0, 98.0],                       # 単調下降（open が high のまま）
        [100.0, 101.0, 102.0],                     # 単調上昇（open が low のまま）
        [0.0, -1.5, 2.5],                          # 負値・0 を含む
    ]
    out = []
    for prices in seqs:
        ticks = [[1_700_000_000_000 + i * 1000, p] for i, p in enumerate(prices)]
        states = forming_states(ticks, lambda ms: 1_700_000_000)   # 全 tick が同一バー
        out.append({
            "prices": prices,
            "expected": [
                {"open": s.open, "high": s.high, "low": s.low, "close": s.close}
                for s in states
            ],
        })
    return out


def render_mp_capability_js(zp_tfs: "tuple[str, ...]") -> str:
    """MP ソース能力の JS モジュール（データのみ・自動生成）を組み立てる。"""
    items = ", ".join(f"'{tf}'" for tf in zp_tfs)
    return (
        "// mp_capability_generated.js — MP ソース能力（**自動生成・手で編集しない**）。\n"
        "//\n"
        "// 生成元: market_profile_api/controller/tf_period_profile_controller.py の _ZP_TF_ALLOWED。\n"
        "// 生成器: tools/gen_js_parity_golden.py（規則変更時に再実行する）。\n"
        "//\n"
        "// なぜ生成物なのか（ISSUE-264）: zp 対応 tf は台帳から導出できない『能力宣言』であり、\n"
        "//   Python と JS の両方に手書きで存在していた。同期手段が無いため、ずれるとサーバは 400 を\n"
        "//   返すのにフロントは選択可能なまま＝**無言の機能不全**になる（ISSUE-253 と同型）。\n"
        "//   定義は Python ただ 1 つとし、JS は生成された値を読むだけにする。\n"
        "export const ZP_SUPPORTED_TFS = Object.freeze([" + items + "]);\n"
    )


def render_mp_param_defaults_js(va_pct_default: float) -> str:
    """MP パラメータ既定値の JS モジュール（データのみ・自動生成）を組み立てる。"""
    return (
        "// mp_param_defaults_generated.js — MP パラメータ既定値（**自動生成・手で編集しない**）。\n"
        "//\n"
        "// 生成元: market_profile_api/compute/market_profile.py の VA_PCT_DEFAULT。\n"
        "// 生成器: tools/gen_js_parity_golden.py（既定変更時に再実行する）。\n"
        "//\n"
        "// なぜ生成物なのか（ISSUE-260）: バリューエリア比率という 1 つの業務パラメータの\n"
        "//   決定権が UI・controller・compute・front domain の 4 面に分散し、`/market_profile` の\n"
        "//   非増分 refresh 以外は UI をどう操作しても 0.70 のままだった（＝効かないツマミ）。\n"
        "//   定義は Python ただ 1 つとし、JS は生成された値を読むだけにする。実効値（要求ごとの\n"
        "//   解決結果）はサーバ応答（/market_profile_forming の vaPct）に従う。\n"
        f"export const VA_PCT_DEFAULT = {va_pct_default!r};\n"
    )


def main() -> None:
    sessions = [
        {
            "t": t,
            "dayStart": sd.session_day_start(t),
            "nextDayStart": sd.next_session_day_start(sd.session_day_start(t)),
            "label": sd.session_date_label(t),
            "barTime": sd.session_bar_time(t),
        }
        for t in session_cases()
    ]
    golden = {
        "generator": "tools/gen_js_parity_golden.py",
        "session_day": sessions,
        "tf_bar_sec": dict(tf_meta.TF_BAR_SEC),
        "tf_ledger": tf_ledger(),
        # src=zp の対応 tf（ISSUE-261）。台帳から導出できない「能力宣言」（周期内分数が少なすぎる
        #   1m/5m は z が退化するため除外）であり、Python と JS の両方に手書きで存在していた。
        #   同期手段が無く、ずれるとサーバは 400 を返すのにフロントは選択可能なまま＝無言の機能不全に
        #   なる。値は Python 側を唯一源とし、JS 側の写しが乖離したら parity 検定で落とす。
        "zp_supported_tfs": list(_zp_supported_tfs()),
        # 形成中バー畳み込みの Python↔JS 一致（ISSUE-272）。
        "forming_fold": forming_fold_cases(),
        # VA 比率の既定（ISSUE-260）。JS 側の生成物（mp_param_defaults_generated.js）と catalog の
        #   param 既定がこの値から乖離したら parity 検定で落とす。
        "va_pct_default": VA_PCT_DEFAULT,
        "value_area": value_area_cases(),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(golden, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {OUT} (sessions={len(sessions)}, va={len(golden['value_area'])})")
    JS_OUT.write_text(render_tf_ledger_js(tf_ledger()), encoding="utf-8")
    print(f"wrote {JS_OUT}")
    MP_CAP_OUT.write_text(render_mp_capability_js(_zp_supported_tfs()), encoding="utf-8")
    print(f"wrote {MP_CAP_OUT}")
    MP_PARAM_OUT.write_text(render_mp_param_defaults_js(VA_PCT_DEFAULT), encoding="utf-8")
    print(f"wrote {MP_PARAM_OUT}")


if __name__ == "__main__":
    main()
