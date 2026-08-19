"""`run_settings_job` の ``extensions`` が写像結果と衝突したときの扱い（🟡-2）。

固定する不変条件:
    拡張点への注入物（`strategy_override` / `position_manager` / `strategy_decorator`）は
    `.ini` からは供給されない引数であり、写像の像とは**交わらない**。交わる名前を渡すのは
    呼出側の誤りであるため、**沈黙で上書きせず `ConfigError` にする**。

なぜ検定にするか（実測された壊れ方）: 実装は `kwargs.update(extensions)` で後勝ちにして
おり、`symbol` のような写像済みの引数を渡すと **`.ini` に書いた条件と違う条件で走った結果**が
「成功」として出力まで進む。当時の docstring は「`build_interactor` の引数検査が受け止める」と
書いていたが、`build_interactor` は受け付ける引数名なら値をそのまま使うため受け止めない
（本検定の Red がその事実の実証である）。

規律の出所: 写像層の `kwargs_mapper._accepted_ea_params`（`.ini` 由来の引数と重なる
`ea_params` を `ConfigError` にする）。同じ理由の検査を、拡張点の注入にも置く。
"""
from __future__ import annotations

from datetime import date

import pytest

from simulator.domain.exceptions import ConfigError
from simulator.main.tester_settings.run_settings_job import run_settings_job
from simulator.tests.tester_settings_engine_fixtures import (
    daily_epochs,
    engine_binding,
    runnable_settings,
    write_comma_csv,
)

FIRST_DAY = date(2024, 1, 1)
BAR_DAYS = 5


@pytest.fixture()
def csv_path(tmp_path):
    return write_comma_csv(tmp_path / "jp225_daily.csv", daily_epochs(FIRST_DAY, BAR_DAYS))


def _effective():
    return runnable_settings(Dates="0").effective()


def test_写像済みの引数名を拡張で渡すと沈黙で上書きせず失敗する(csv_path, tmp_path) -> None:
    # Arrange: `symbol` は写像層が `.ini` から供給する引数（＝拡張点ではない）
    out = tmp_path / "out"
    out.mkdir()
    # Act / Assert
    with pytest.raises(ConfigError) as excinfo:
        run_settings_job(
            _effective(),
            engine_binding(data_path=str(csv_path)),
            output_dir=out,
            extensions={"symbol": "SOMETHING_ELSE"},
        )
    assert "symbol" in str(excinfo.value)
    assert excinfo.value.context["conflicting"] == ["symbol"]
    # 部分的な成果物を残さない（判定は実行より前）
    assert not list(out.iterdir())


def test_衝突しない拡張は従来どおり素通しする(csv_path, tmp_path) -> None:
    """回帰の錨: 交わらない名前の注入は 1 つも塞がない（Phase 6/7 の拡張点）。"""
    # Arrange
    out = tmp_path / "out"
    out.mkdir()
    # Act: `strategy_decorator=None` は「拡張なし」と同じ意味の素通し値
    exit_code, _result, _meta = run_settings_job(
        _effective(),
        engine_binding(data_path=str(csv_path)),
        output_dir=out,
        extensions={"strategy_decorator": None},
    )
    # Assert
    assert exit_code == 0
    assert (out / "stats.json").is_file()
