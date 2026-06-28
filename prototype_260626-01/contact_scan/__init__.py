"""contact_scan — オフライン全ティック接点（クロス）抽出エンジン（ISSUE-028 Phase2・増分1）。

確定足だけでは見落とす「価格と指標の接点（取引が成立するクロス点）」を、候補足のみ全ティック
走査して網羅・定量化する。指標コア / dataset / marketdata は read-only 消費（改変しない）。
replay の viz とは分離（engine は proto_server を import しない）。

増分1: 移動平均（EMA）× 価格の接点。レベル = ma[i-1]（直前確定足の MA 値）。
増分2（W本体×σ・freeze_last）は ContactSpec の拡張点として確保（本パッケージでは未実装）。
"""
