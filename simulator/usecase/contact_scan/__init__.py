"""contact_scan — 価格×指標の接点（クロス）を全ティック走査で抽出する純エンジン。

参照実装 prototype_260626-01/contact_scan/（engine/crossings/bar_window/spec）を simulator の
usecase 層へ bit 一致で移植したもの。試作の pandas 依存（ScanContext.df）を plain 配列
（highs/lows/closes/bar_times/ma_by_time）へ置換し、usecase 層から numpy/pandas を排除する
（CLEAN_ARCH: 偶有的技術は adapter/tools 層へ隔離）。挙動（符号規約・タッチ・境界・窓ラベル・
preview/full_scan・event/summary schema）は参照実装と不変。

増分1: 移動平均（EMA）× 価格の接点。レベル = ma[i-1]（直前確定足の MA 値）。
増分2（ContactSpec 拡張点）は Protocol として確保（本パッケージでは未実装）。
"""
