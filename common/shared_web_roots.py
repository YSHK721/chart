"""共有フロント供給パッケージの唯一源（アクター非依存の中立核・ISSUE-502 C-4 3b）。

## 何の台帳か

``indigators/<pkg>/web/js`` に**フロントモジュールの実体**を置き、consumer 側（配信 core）の
``web/js`` 配下 symlink 経由でのみ配られるパッケージの一覧である。実体がそこにある以上、
どの配信 core も静的配信の**許可根**に当該サブツリーを含めなければならない——含めないと
URL は同じでも ``realpath`` が許可根の外へ出て 404 になる（2026-09-06 実測。C-4 3b の
第 1 回はこれで中断した）。

## なぜ台帳が要るか（重複列挙の除去）

この一覧は元々 2 箇所へ手書きで列挙されていた:

  - ``indigators/indicator_ui/api/framework/server.py`` の ``_MP_WEB_JS_ROOT``
  - ``simulator/replay_ui/framework/static_file_server.py`` の _allowed_roots

「どのパッケージが共有フロントを供給するか」という**1 つの規則**に所有者が 2 人いる状態で、
共有パッケージを 1 つ増やすには閉じているはずの HTTP 殻 2 枚を開いて直す必要があった
（OCP 違反）。しかも 2 箇所の一方だけ直しても片方の core だけが 404 になり、症状は
「特定のページでだけモジュールが読めない」という遠い形で出る。規則はここだけが持ち、
殻は導出する。

## なぜ中立核（common）が所有するか

消費者は**別アクター**に属する 2 者である。

  - indigators のライブ配信殻（indicator_ui/api/framework/server.py）
  - simulator の共有静的配信クラス（replay / sim / dashboard の 3 core が使う）

実体をどちらかへ置くと ``simulator → indigators`` あるいは ``indigators → simulator`` の
辺が生まれる。規則の実体は stdlib だけで書かれた純粋な経路導出でありどちらにも属さない。
``common.dev_paths``（ISSUE-502 C-1）・``common.watch_loop``（ISSUE-479 F-3）と同一の解である。

## 依存方向

本モジュールは **stdlib のみ**に依存する（common の中立核規約）。
"""
from __future__ import annotations

from pathlib import Path

#: 共有フロントを供給するパッケージ名（``indigators/`` 直下）。
#:
#: ここへ 1 行足せば、ライブ殻・replay/sim/dashboard の共有静的配信クラスの**両方**の
#: 許可根へ同時に伝播する（値を殻へ書き写さない）。第 2 の列挙が復活していないことは
#: ``common/tests/test_shared_web_roots.py`` の単一源検定が強制する。
#:
#: - ``chart_kernel``  : どの機能にも属さない中立核。2 種を持つ。
#:   (1) チャート共通部品（色役割・クロム表色・Series Primitive の生存管理・足内畳み込み）。
#:   (2) 市場データ語彙（時間足台帳 tf_ledger_generated/tf_meta・セッション日境界 session_day・
#:       セッション OHLC 集計 session_ohlc）。ISSUE-502 C-4 3c で market_profile から移した。
#:       移す前は「時間足とは何か」を**機能パッケージ**（Market Profile）が所有しており、
#:       live/replay/dashboard の 3 core がその語彙を使うためだけに market_profile へ依存して
#:       いた（語彙の所有者と機能の所有者が同一という SRP 違反）。
#:   indicator_ui と market_profile の双方がここへ**一方向**に依存する（C-4 の循環はこの実体が
#:   indicator_ui 側に置かれていたことだけが原因だった）。
#: - market_profile: Market Profile のフロント一式（actor/client/primitive/domain）。
#: - ``indicator_kit`` : live core（indicator_ui）と replay core（simulator/replay_ui）が**共に**
#:   使うチャートフロント部品一式（描画・操作・凡例・テンプレート・配色・建玉サイジングの
#:   adapter/front と、それが載る domain/usecase）。ISSUE-502 C-4 3d で indicator_ui から移した。
#:   移す前は「live の配信 core」と「live/replay 共有部品の供給元」が同一パッケージで、
#:   両者は別アクター（live 面の変更 / 共有部品の変更）だったため SRP 違反だった。実測では
#:   indicator_ui の実体 112 本のうち 105 本が replay の実行グラフに乗っており、**共有部品が
#:   主で live 専任分が従**という比率だった（残る 7 本＝index.html の合成根・live 公開面・
#:   tick 再生など live 専用）。chart_kernel / market_profile へ**一方向**に依存する。
SHARED_WEB_PACKAGES: "tuple[str, ...]" = (
    "chart_kernel",
    "market_profile",
    "indicator_kit",
)


def shared_web_js_roots(indigators_root: Path) -> "tuple[Path, ...]":
    """``indigators_root`` 起点で共有フロント供給サブツリー（``<pkg>/web/js``）を返す。

    許可するのは ``web/js`` サブツリー**だけ**である（最小権限）。web 根全体を許可すると
    package.json / tests/ / node_modules/ まで配信面へ露出する。

    解決（``Path.resolve``）は行わない。呼び出し側が「今起動しているツリー」から渡した根に
    対して純粋に join するだけであり、正規化の要否は各殻の既存方針に委ねる（ISSUE-279 の
    「パスは常に起動中のツリーから解決する」規約に沿う）。
    """
    return tuple(indigators_root / name / "web" / "js" for name in SHARED_WEB_PACKAGES)
