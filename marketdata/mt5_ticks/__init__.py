"""marketdata.mt5_ticks — MT5 リアルタイムティックの増分供給（ISSUE-447 段階 1）。

バッチとリアルタイムを分けず「最後に保存したティック以降の増分を引き続ける」単一供給ループを
組み立てるための層。既存 Dukascopy 木は一切触らず、別トークン（例 ``JP225@OANDA-Japan-MT5-Live``）
で同居する。

確定原則（変更不可）:
    **VM 側は定義を持たない**。tick 木レイアウト・列定義・時刻変換（DST）の知識は
    :mod:`marketdata.tick_m1` を唯一の権威として本パッケージが参照する。VM 側
    （``tools/mt5_tick_feed.py``）は MT5 の生応答をそのまま返すだけで、レイアウトも列名も
    DST 規則も持たない。

層と依存宣言（``marketdata/tests/test_mt5_module_dependency_declarations.py`` が AST で強制）:

=======================================  =========  ===========================================
モジュール                               層          許可する外部依存
=======================================  =========  ===========================================
``marketdata.mt5_ticks.server_clock``    domain     stdlib のみ
``marketdata.mt5_ticks.cursor``          domain     stdlib のみ
``marketdata.mt5_ticks.wire``            domain     stdlib のみ
``marketdata.mt5_ticks.port``            usecase    stdlib のみ
``marketdata.mt5_ticks.journal``         adapter    pandas / :mod:`marketdata.tick_m1` / 同下位
``marketdata.mt5_ticks.ingest``          adapter    :mod:`marketdata.tick_m1` / sanitize / 同下位
``marketdata.mt5_ticks.archive_ingest``  adapter    :mod:`marketdata.tick_m1` / 同下位（pandas 無し）
``marketdata.mt5_ticks.m1_chain``        adapter    pandas / tick_m1 / rollup / 同下位
``marketdata.mt5_ticks.rebuild``         adapter    pandas / tick_m1 / rollup / 外れ値規約 / 同下位
``marketdata.mt5_ticks.usecases``        usecase    同パッケージのみ
``marketdata.mt5_ticks.http_source``     framework  stdlib＋wire/port
``marketdata.mt5_ticks.fakes``           test 支援  同パッケージのみ（本番から import されない）
=======================================  =========  ===========================================

モジュールを完全修飾で書く理由: 名前だけを書くと「どこのモジュールなのか」が本ファイルから
辿れず、宣言整合性検定（静的品質ゲートの C1 DECL）が到達不能と判定する。
宣言は辿れて初めて宣言である。

sanitize（銘柄・サーバ名→パス成分）の唯一の実装は `tools/capture_mt5_symbol_spec.py` にあり、
ingest はそれを import する（複製を作らないため層の向きを曲げる・設計 §4）。

依存規則の施行を `tools/tests/test_tools_composition_declaration.py` 側に置かないのは、
そちらが tools 直下を**非再帰**で走査するためである（パッケージ配下へ潜れば検査を免れる穴を
突かない）。本パッケージの規則は本パッケージ側の検定で施行する。
"""
