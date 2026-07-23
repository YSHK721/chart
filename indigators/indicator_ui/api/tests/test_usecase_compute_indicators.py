"""usecase.compute_indicators（ISSUE-092 ①）— 業務手順の純関数を隔離検証する。

handle_compute（controller・薄殻）が委譲する usecase 純関数
``compute_indicators(request, *, dataset_port, compute_adapter, forming_bar,
full_compute, latest_compute, compute_error)`` を、すべて fake 協調子で検証する
（実 marketdata / 実 adapter / 実データに非依存＝F.I.R.S.T の Fast/Independent）。

検証観点（内部設計書 §4.5 / §6.3 / §7.3 の業務手順を usecase へ移設した後も同値）:
    - 入口検証（indicatorId/variant 必須）
    - datasetRef ホワイトリスト（DatasetPort.is_known）
    - timeframe ホワイトリスト（DatasetPort.is_known_timeframe）
    - データロード（DatasetPort.load_dataframe）
    - mode="latest" 時の forming_bar 注入（resolve_now_unix → apply_forming_bar）
    - mode="full"（省略）時は forming_bar 非注入
    - limit による直近 N 本 tail
    - ComputeError → error_type 伝播 / KeyError → validation 翻訳
"""

from __future__ import annotations

from usecase.compute_indicators import (
    ComputeRequest,
    ComputeResult,
    compute_indicators,
)


# --------------------------------------------------------------------------- #
# fake 協調子
# --------------------------------------------------------------------------- #
class _FakeDF:
    """df は usecase にとって不透明（compute へ素通し）。tail のみ観測する。"""

    def __init__(self, tag: str = "base"):
        self.tag = tag

    def tail(self, n: int) -> "_FakeDF":
        return _FakeDF(tag=f"{self.tag}.tail({n})")


class _FakeDatasetPort:
    def __init__(self, *, known=True, tf_known=True, df=None):
        self._known = known
        self._tf_known = tf_known
        self._df = df if df is not None else _FakeDF()
        self.loaded: tuple | None = None

    def is_known(self, ref):  # noqa: ANN001
        return self._known

    def is_known_timeframe(self, tf):  # noqa: ANN001
        return self._tf_known

    def load_dataframe(self, ref, tf):  # noqa: ANN001
        self.loaded = (ref, tf)
        return self._df


class _FakeFormingBar:
    def __init__(self, now_value=777):
        self._now_value = now_value
        self.resolved_override = "UNSET"
        self.applied: tuple | None = None

    def resolve_now_unix(self, override):  # noqa: ANN001
        self.resolved_override = override
        return self._now_value if override is None else override

    def apply_forming_bar(self, df, ref, tf, now):  # noqa: ANN001
        self.applied = (ref, tf, now)
        return _FakeDF(tag="formed")


class _RecordingCompute:
    """full_compute / latest_compute の呼出を記録し、固定 series を返す。"""

    def __init__(self, series):
        self.series = series
        self.calls: list = []

    def __call__(self, adapter, compute_id, variant, df, params, **kwargs):  # noqa: ANN001
        # min_tail（ISSUE-162・additive kwarg）は契約上受理する（既定 None＝挙動不変）。
        self.calls.append((adapter, compute_id, variant, df, params))
        return self.series


class _FakeComputeError(Exception):
    def __init__(self, error_type, message):
        super().__init__(message)
        self.error_type = error_type
        self.message = message


def _kw(**over):
    """compute_indicators の既定協調子キーワードを組む（テストで一部だけ差し替え）。"""
    kw = dict(
        dataset_port=_FakeDatasetPort(),
        compute_adapter=object(),
        forming_bar=_FakeFormingBar(),
        full_compute=_RecordingCompute([{"name": "full", "kind": "line", "data": []}]),
        latest_compute=_RecordingCompute([{"name": "latest", "kind": "line", "data": []}]),
        compute_error=_FakeComputeError,
    )
    kw.update(over)
    return kw


def _req(**over):
    base = dict(
        generation=0,
        indicator_id="tgp_btlm",
        variant="default",
        params={"a": 1},
        dataset_ref="sample",
        timeframe=None,
        mode="full",
        forming_now=None,
        limit=None,
    )
    base.update(over)
    return ComputeRequest(**base)


# --------------------------------------------------------------------------- #
# 入口検証
# --------------------------------------------------------------------------- #
def test_missing_indicator_id_returns_validation_error():
    result = compute_indicators(_req(indicator_id=None), **_kw())
    assert isinstance(result, ComputeResult)
    assert result.ok is False
    assert result.error_type == "validation"
    assert result.error_message == "indicatorId と variant は必須です。"


def test_missing_variant_returns_validation_error():
    result = compute_indicators(_req(variant=None), **_kw())
    assert result.error_type == "validation"


# --------------------------------------------------------------------------- #
# ホワイトリスト（DatasetPort 経由）
# --------------------------------------------------------------------------- #
def test_unknown_dataset_ref_returns_validation_error():
    port = _FakeDatasetPort(known=False)
    result = compute_indicators(_req(dataset_ref="nope"), **_kw(dataset_port=port))
    assert result.error_type == "validation"
    assert result.error_message == "未知の datasetRef です: 'nope'"
    assert port.loaded is None  # ロードに進まない


def test_unknown_timeframe_returns_validation_error():
    port = _FakeDatasetPort(tf_known=False)
    result = compute_indicators(_req(timeframe="9z"), **_kw(dataset_port=port))
    assert result.error_type == "validation"
    assert result.error_message == "未知の timeframe です: '9z'"


def test_none_timeframe_skips_timeframe_check():
    port = _FakeDatasetPort(tf_known=False)  # None は検査対象外
    result = compute_indicators(_req(timeframe=None), **_kw(dataset_port=port))
    assert result.ok is True


# --------------------------------------------------------------------------- #
# 正常系（full）
# --------------------------------------------------------------------------- #
def test_full_mode_calls_full_compute_and_returns_series():
    port = _FakeDatasetPort()
    full = _RecordingCompute([{"name": "MA", "kind": "line", "data": [1]}])
    latest = _RecordingCompute([{"name": "X"}])
    adapter = object()
    result = compute_indicators(
        _req(mode="full", params={"p": 9}),
        **_kw(dataset_port=port, full_compute=full, latest_compute=latest,
              compute_adapter=adapter),
    )
    assert result.ok is True
    assert result.series == [{"name": "MA", "kind": "line", "data": [1]}]
    assert len(full.calls) == 1
    assert latest.calls == []  # latest は呼ばない
    called_adapter, cid, variant, _df, params = full.calls[0]
    assert (called_adapter, cid, variant) == (adapter, "tgp_btlm", "default")
    assert params == {"p": 9} and params is not _req().params  # dict コピー


def test_full_mode_does_not_apply_forming_bar():
    fb = _FakeFormingBar()
    compute_indicators(_req(mode="full"), **_kw(forming_bar=fb))
    assert fb.applied is None
    assert fb.resolved_override == "UNSET"  # resolve_now_unix も呼ばない


# --------------------------------------------------------------------------- #
# latest（forming_bar 注入）
# --------------------------------------------------------------------------- #
def test_latest_mode_applies_forming_bar_and_calls_latest_compute():
    fb = _FakeFormingBar()
    full = _RecordingCompute([{"name": "full"}])
    latest = _RecordingCompute([{"name": "latest"}])
    result = compute_indicators(
        _req(mode="latest", dataset_ref="jp225_tick", timeframe="5m", forming_now=123),
        **_kw(forming_bar=fb, full_compute=full, latest_compute=latest),
    )
    assert result.ok is True
    assert result.series == [{"name": "latest"}]
    assert fb.resolved_override == 123
    assert fb.applied == ("jp225_tick", "5m", 123)
    assert len(latest.calls) == 1 and full.calls == []


def test_latest_mode_resolves_now_via_forming_bar_when_no_forming_now():
    fb = _FakeFormingBar(now_value=999)
    compute_indicators(
        _req(mode="latest", dataset_ref="jp225_tick", timeframe="5m", forming_now=None),
        **_kw(forming_bar=fb),
    )
    assert fb.applied[2] == 999  # provider 解決値


# --------------------------------------------------------------------------- #
# limit（直近 N 本 tail）
# --------------------------------------------------------------------------- #
def test_limit_tails_dataframe_before_compute():
    full = _RecordingCompute([{"name": "MA"}])
    compute_indicators(_req(mode="full", limit=30), **_kw(full_compute=full))
    _, _, _, df, _ = full.calls[0]
    assert df.tag == "base.tail(30)"


def test_non_positive_limit_does_not_tail():
    full = _RecordingCompute([{"name": "MA"}])
    compute_indicators(_req(mode="full", limit=0), **_kw(full_compute=full))
    _, _, _, df, _ = full.calls[0]
    assert df.tag == "base"


# --------------------------------------------------------------------------- #
# エラー翻訳
# --------------------------------------------------------------------------- #
def test_compute_error_is_translated_to_error_result():
    def boom(*a, **k):
        raise _FakeComputeError("empty_series", "必須バケットが空です。")

    result = compute_indicators(_req(mode="full"), **_kw(full_compute=boom))
    assert result.ok is False
    assert result.error_type == "empty_series"
    assert result.error_message == "必須バケットが空です。"


def test_key_error_is_translated_to_validation():
    def boom(*a, **k):
        raise KeyError("nope")

    result = compute_indicators(_req(mode="full"), **_kw(full_compute=boom))
    assert result.error_type == "validation"
    assert result.error_message == "未登録の指標または variant です: 'nope'"


def test_generation_is_preserved_in_result():
    result = compute_indicators(_req(generation=7, indicator_id=None), **_kw())
    assert result.generation == 7


# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# ComputeRequest.from_body（入力アダプト：compute_id 別名・既定値）
# --------------------------------------------------------------------------- #
def test_from_body_resolves_compute_id_alias():
    req = ComputeRequest.from_body({"compute_id": "x", "variant": "default"})
    assert req.indicator_id == "x"


def test_from_body_prefers_indicator_id_over_compute_id():
    req = ComputeRequest.from_body(
        {"indicatorId": "primary", "compute_id": "alias", "variant": "default"}
    )
    assert req.indicator_id == "primary"


def test_from_body_defaults_mode_full_and_empty_params():
    req = ComputeRequest.from_body({"indicatorId": "x", "variant": "default"})
    assert req.mode == "full"
    assert req.params == {}
    assert req.generation == 0
