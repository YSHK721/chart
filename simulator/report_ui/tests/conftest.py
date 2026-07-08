"""report_ui テスト共通設定。"""


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: 実 run を伴う結合テスト（confirmation オラクル照合）"
    )
    config.addinivalue_line(
        "markers", "e2e: Playwright ヘッドレス描画検証（web 骨格）"
    )
