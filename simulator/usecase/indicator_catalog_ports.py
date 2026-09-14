"""IndicatorSeriesNamesPort（指標系列の列挙契約・RUN_TRACE_BASIC_DESIGN §6.3）。

何を解くか:
    IndicatorPort（`simulator/usecase/ports.py`） は `get(name)` / `update(bar_index)` しか持たない。「登録系列を
    列挙できる」は**実行に必要な契約ではない**——エンジンは名前で引くだけであり、
    一覧を要るのは分析・トレースという**別の要求者**である。実行の契約へ足すと、
    列挙を必要としない実装まで列挙を強いられる（ISP 違反）。

なぜ別ファイルか:
    先例は `usecase/ports.py:249-284` の形式別 1 メソッド Port（ISP・ISSUE-099）と、
    別ファイルで宣言された Port 6 本（`simulator/usecase/marker_ports.py` / `simulator/usecase/optimize_ports.py` /
    `simulator/usecase/scan_contacts_ports.py` / `simulator/usecase/sizing_ports.py` / `simulator/usecase/validation_ports.py` / `simulator/usecase/vol_band_ports.py`）。
    `ports.py` は無改変である。

なぜ IndicatorSeriesCatalogPort（`simulator/sim_ui/usecase/job_ports.py`） という名にしないか（是正 D-1）:
    `sim_ui/usecase/job_ports.py` に**同名の別契約が既に実在する**
    （`series_for(ea_name)`＝ea_name → 系列名という別の問い）。同名を作れば読み手が
    2 つの契約を取り違える。

同じ一覧を作る規則を増やさない（設計書 実測 9）:
    未登録参照の公開エラー契約が運ぶ available は本 Port の `names()` から導く。
    是正前は同じ一覧を作る規則が 3 箇所（`registry.py` / `null_registry.py` /
    `ea_registry_series_catalog.py` の例外プローブ）に在った。

依存規律: 標準ライブラリのみ。usecase 層は adapter / framework / main を import しない。
"""
from __future__ import annotations

import abc


class IndicatorSeriesNamesPort(abc.ABC):
    """登録済みの指標系列名を列挙する境界。"""

    @abc.abstractmethod
    def names(self) -> "tuple[str, ...]":
        """登録系列名を**登録順**で返す。

        事後条件:
          1. 並びは**登録された順**である（辞書順に並べ替えない）。トレースの列順は
             registry の宣言順であり、名前の綴りが列の位置を決めるのではない。
          2. 系列を 1 本も持たない供給は空タプルを返す（`None` を返さない）。
             「無い」を値で表さないと、参照側が「列挙できなかった」と区別できない。
          3. 呼出はエンジンの状態を変えない（列挙は観測であって実行ではない）。
        """
        raise NotImplementedError
