"""datawindow — 取得窓（半開区間 ``[start, end)``）の規則を持つ中立共有パッケージ。

なぜ独立パッケージなのか（ISSUE-401 🟡-2・`api_shared`（ISSUE-094 🔵-11）と同じ論法）:
    取得窓の境界を epoch 秒へ正規化する規則と半開判定は、`simulator` のアクターにも
    `marketdata` のアクターにも属さない。にもかかわらず実体が 2 箇所（Candle 段
    ``marketdata/csv_source.py`` と Bar 段 ``simulator/adapter/repository/windowed_market_data.py``）
    に分かれ、**naive datetime の解釈が食い違っていた**（実測: ``TZ=Asia/Tokyo`` ・
    ``datetime(2025, 1, 10)`` naive で 32400 秒＝9 時間差）。所有者が両者の外側にある以上、
    実体も両者の外側の中立パッケージへ置く。`marketdata` は独立パッケージであり `simulator`
    を import できない（依存方向）ため、共有点は両者より下に置くしかない。

なぜ `common/` ではないのか（実測に基づく棄却）:
    1. 責務不一致: `common/README.md` は当層を「特定の指標に属さず、複数の**指標**から
       横断的に再利用する純粋ロジック（numpy のみ）／MQL の Include 系ユーティリティの
       移植先」と宣言している。取得窓の境界正規化は指標プリミティブではない。
    2. 依存の汚染（実測）: ``common/__init__.py`` は ``applied_price`` を eager import する
       ため ``import common.<任意>`` で numpy が読み込まれる。
       ``import simulator.domain.bar_time`` は現状 numpy を読み込まない（``sys.modules``
       実測）。`common/` へ置くと domain 層の宣言（``bar_time.py`` 「numpy / pandas を
       import しない」・``sltp.py`` 「domain 層は外部依存ゼロ」）が transitively 破れる。
    本パッケージは ``__init__`` で何も import しない（**標準ライブラリのみ**）ため、domain
    層から読んでもこの汚染が起きない。

解決経路: ``tools/dev_paths.txt`` の台帳にリポジトリ根 ``.`` が載っているため、pytest・
serve.sh・venv の .pth の 3 消費者すべてから解決可能（台帳の変更は不要）。

公開面:
    ``datawindow.half_open`` — ``epoch_seconds_of_datetime`` / ``HalfOpenEpochWindow``。
    本 ``__init__`` は再エクスポートを持たない（eager import を持たないことが要件のため）。
    利用側は ``from datawindow.half_open import ...`` と書く（``api_shared`` と同じ流儀）。
"""
