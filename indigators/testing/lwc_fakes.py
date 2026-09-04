"""スライス横断で共有する lightweight-charts のテストダブル（ISSUE-307）。

なぜ 1 箇所に置くか:
    各指標スライスの ``tests/test_lwc_chart.py`` は、``create_line`` / ``create_histogram`` /
    ``horizontal_line`` を持つだけの Fake を手書きで複製していた（codescan 実測: ``FakeChart``
    20 箇所 / 10 種、``FakeHistogram`` 9 箇所、``FakeLine`` 8 箇所）。10 種に分かれていたのは
    「そのスライスが使う口だけを実装した」ためで、契約そのものは 1 つである。

描画側の契約（実コード接地）:
    ``indigators/*/src/lwc_chart.py`` の ``add_*`` は chart に対して次を duck typing で要求する。

        create_line(name, **kwargs)      -> 系列（``set(data)`` を持つ）
        create_histogram(name, **kwargs) -> 系列（``set(data)`` を持つ）
        horizontal_line(price, **kwargs) -> dict

    本モジュールはその全口を備えた**上位集合**を 1 つ提供する。自分が使わない口が生えても、
    スライスの検証（``chart.lines`` / ``chart.histograms`` / ``chart.hlines`` の本数・名前・値）
    は影響を受けない。

``FakeLine`` / ``FakeHistogram`` は :class:`FakeSeries` の別名である（複製されていた各定義は
name / kwargs / data / set の 4 点で同一だった）。
"""
from __future__ import annotations

from typing import Any


class FakeSeries:
    """``create_line`` / ``create_histogram`` が返す系列のテストダブル。

    生成時の ``name`` と描画オプション ``kwargs`` を保持し、``set(data)`` で渡された
    データを ``data`` に記録するだけの受け皿。``kind`` は生成した口（"line" /
    "histogram"）を :class:`FakeChart` が記録する。
    """

    def __init__(self, name: Any, kind: str | None = None, **kwargs: Any) -> None:
        self.name = name
        self.kind = kind
        self.kwargs = kwargs
        self.data = None

    def set(self, data: Any) -> None:
        self.data = data


#: 複製されていた ``FakeLine`` / ``FakeHistogram`` は :class:`FakeSeries` と同一定義だった。
FakeLine = FakeSeries
FakeHistogram = FakeSeries


class FakeChart:
    """``add_*`` が要求する描画口をすべて備えたテストダブル（描画は行わない）。

    Attributes:
        lines: ``create_line`` で作られた系列（生成順）。
        histograms: ``create_histogram`` で作られた系列（生成順）。
        hlines: ``horizontal_line`` で作られた ``{"price": ..., **kwargs}``（生成順）。
    """

    def __init__(self) -> None:
        self.lines: "list[FakeSeries]" = []
        self.histograms: "list[FakeSeries]" = []
        self.hlines: "list[dict[str, Any]]" = []

    def create_line(self, name: Any, **kwargs: Any) -> FakeSeries:
        series = FakeSeries(name, kind="line", **kwargs)
        self.lines.append(series)
        return series

    def create_histogram(self, name: Any, **kwargs: Any) -> FakeSeries:
        series = FakeSeries(name, kind="histogram", **kwargs)
        self.histograms.append(series)
        return series

    def horizontal_line(self, price: Any, **kwargs: Any) -> "dict[str, Any]":
        line = {"price": price, **kwargs}
        self.hlines.append(line)
        return line
