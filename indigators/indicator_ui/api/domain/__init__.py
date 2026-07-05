"""indicator_ui API domain 層（純 Python・標準ライブラリのみ）。

外部型（pandas/numpy/HTTP/JS/lightweight-charts）を一切参照しない最深層。
内部設計書 §2.2 import ルール（domain は標準ライブラリのみ）に準拠する。

## 配線状況（アーキ実態の明示・2026-07-05 監査反映）
本 domain 層（validation/indicator_def/param_def/series_def/applied_instance/favorite）は
**現時点で配信経路（server→controller→adapter.compute→外部 src）から参照されていない**（test 以外の
production 参照ゼロ・実測確認済）。実際のパラメータ検証は次の 2 経路が担う:
  - backend: ``adapter/compute/call_binding._accepted_kwargs`` によるシグネチャ濾過（thin）。
  - frontend: ``web/js/usecase/form_model`` → ``web/js/domain/constraint_eval`` による rich 検証。
したがって「domain 層＝配信コア」ではなく、**実配信のビジネスルールは ``adapter/compute``**
（market_profile の TPO/POC/VA 等）にある。本層は「将来の backend catalog / サーバ側検証
エンドポイントを想定した内側ドメイン層」として保持する（YAGNI 承知の上・削除せず・additive 方針）。
配線する場合は server に catalog ルートを追加し、controller から本層を参照する。
"""
