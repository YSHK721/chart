"""cache_settings — MP ディスク永続キャッシュの設定（gateway 層・単一情報源・ISSUE-183）。

永続化の基点（cache root）と保存形式の版数（cache version）は**偶有的性質**（どこに・どの形式で
置くかという技術選択）であり、本質層（compute＝集計数学とキャッシュ協調の方針）に居住させない。

経緯（ISSUE-183 item5・実測）: これらは ``market_profile_dwell._CACHE_ROOT`` /
``market_profile_dwell._CACHE_VERSION`` / ``market_profile_zp._ZP_CACHE_ROOT`` /
``market_profile_zp._ZP_CACHE_VERSION`` として compute の **module private** に置かれ、Composition Root
（:mod:`market_profile_api.gateway.composition`）がそれを外から読んでいた。private を層外から読む＝
カプセル化の破れであると同時に、永続化設定という偶有的性質が本質層に住み続ける構造でもあった。
本モジュールへ**移送**（複製ではない）し、compute 側の定義は撤去した（二重情報源を作らない）。

ISSUE-172 との整合: 「配置記述子（``CacheLayout``）の生成は書込パスの所有者（Store / controller）が
所有する」という単一情報源化は不変である。本モジュールが持つのは記述子ではなく、Store が
``root_provider`` / ``cache_version_provider`` を通じて **call-time** に読む素の設定値のみ。
世代 subdir 名は従来どおり Store の ``_relative_parts`` / ``_znull_relative_parts`` が唯一導出する。

差し替え（テストの tmp 隔離・版数 bump シミュレーション）は本モジュールの module 属性を
monkeypatch する。Store は provider クロージャ経由で call-time に読むため差し替えは即時反映される。
"""

from __future__ import annotations

from pathlib import Path

# --------------------------------------------------------------------------- #
# dwell 日別ロールアップ
# --------------------------------------------------------------------------- #
#: 保存形式バージョン。読込時に不一致なら無視して再計算（fail-safe）。パスにも ``v<version>`` として現れる。
#:   v4: ISSUE-089 active table 窓キー化に伴い、先勝ち表が焼き込まれた v3 日次 npz を全再計算。
#:   v3: セッション日切り（ISSUE-078・NY17:00 ET 基準）。日キーが UTC 深夜→セッション始端へ変わるため
#:       旧 UTC 日ロールアップ（v2）を全無効化する。
#:   v2: 日次ロールアップに「ソースティック署名(sig)」を併記。完了日を空でキャッシュした後にティックが
#:       届いても署名変化で自動再計算する（無効化ロジック・stale-empty 修正）。v1 は不一致で全再計算。
DWELL_CACHE_VERSION: int = 4

#: 基点の上書き。``None``＝既定（``DATA_DIR/cache/market_profile_dwell``）。テストは tmp を注入する。
DWELL_CACHE_ROOT: "Path | None" = None

# --------------------------------------------------------------------------- #
# zp（超過占有スコア z(p)）日別成果物
# --------------------------------------------------------------------------- #
#: 保存形式バージョン。
#:   v3: bp 相対 log 格子（ISSUE-079）＝znull を全無効化（mgrid は格子非依存で温存）。
ZP_CACHE_VERSION: int = 3

#: 基点の上書き。``None``＝既定（``DATA_DIR/cache/market_profile_zp``）。テストは tmp を注入する。
ZP_CACHE_ROOT: "Path | None" = None
