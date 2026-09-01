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

===================  =========  =====================================================
モジュール           層          許可する外部依存
===================  =========  =====================================================
``server_clock``     domain     stdlib のみ
``cursor``           domain     stdlib のみ
``wire``             domain     stdlib のみ
``port``             usecase    stdlib のみ
``journal``          adapter    pandas / :mod:`marketdata.tick_m1` / 同パッケージ下位
``ingest``           adapter    :mod:`marketdata.tick_m1` / ``tools`` の sanitize / 同下位
``m1_chain``         adapter    pandas / :mod:`marketdata.tick_m1` / 同パッケージ下位
``usecases``         usecase    同パッケージのみ
``fakes``            test 支援  同パッケージのみ（本番経路から import されない）
===================  =========  =====================================================

規則を ``tools`` 側に置かない（``tools/tests/test_tools_composition_declaration.py`` の
非再帰走査の穴を突かないため、依存宣言の施行は本パッケージ側の検定で行う）。
"""
