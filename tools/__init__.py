"""tools — リポジトリ横断の取得・運用 CLI を束ねるパッケージ。

各サブモジュールは既存ライブラリ・ツールの合成点（Composition Root 相当）として
振る舞い、ロジックの重複を持たない。

この宣言は ``tools/tests/test_tools_composition_declaration.py`` が **走査して強制**する
（ISSUE-262）。かつては宣言だけがあり、ロールアップ対象 tf の規則・tick tree レイアウト・
生ティック列が実際には tools 側に第 2 定義として存在していた。規則を tools に置きたく
なったら、それは合成点ではなくライブラリの仕事である（marketdata / simulator へ置き、
tools は呼ぶだけにする）。
"""
