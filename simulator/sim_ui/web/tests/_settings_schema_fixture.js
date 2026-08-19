// Tester Settings schema（GET /sim/settings-schema の payload）のダブル（Phase 8 スライス 5）。
//
// **語彙をわざと架空にする**: 実際の時間足ラベル・Model の生値・対象接尾辞をここに書くと、
// 「front が内蔵候補を持たない」ことを検定できない（front 側に同じ語彙が残っていても緑に
// なってしまう）。トークンは schema からしか来ないことを示すため、実在しない語（P1 / m0 /
// AAA.zzz …）を使う。実物の語彙が配信まで届くことは python 側の実 HTTP 検定
// （`sim_ui/tests/integration/test_serve_sim_settings_schema.py`）が固定する。
//
// 形（キー名・入れ子）は adapter/settings_schema_api_controller.py の応答と 1:1。

/** `GET /sim/settings-schema` の payload（呼ぶたびに新しい実体を返す＝テスト間で共有しない）。 */
export function settingsSchema() {
  return {
    ok: true,
    key_order: [
      "Expert", "Indicator", "Symbol", "Period", "Optimization", "Model",
      "Dates", "FromDate", "ToDate", "ForwardMode", "ForwardDate",
      "Deposit", "Currency", "ProfitInPips", "Leverage", "ExecutionMode",
      "OptimizationCriterion", "Visual",
    ],
    required_keys: ["Symbol", "Period", "Model"],
    enum_options: {
      Period: [
        { token: "P1", label: "PL1" },
        { token: "P2", label: "PL2" },
        { token: "P3", label: "PL3" },
      ],
      Model: [{ token: "m0", label: "ML0" }, { token: "m1", label: "ML1" }],
      Optimization: [{ token: "o0", label: "OFF" }, { token: "o1", label: "ON" }],
      Dates: [{ token: "d0", label: "ALL" }, { token: "d2", label: "LAST" }],
      ForwardMode: [{ token: "f0", label: "NONE" }, { token: "f4", label: "CUSTOM" }],
      OptimizationCriterion: [{ token: "c0", label: "C0" }],
    },
    scalar_specs: {
      Expert: { expert_only: false },
      Indicator: { expert_only: false },
      Symbol: { expert_only: false },
      FromDate: { expert_only: false },
      ToDate: { expert_only: false },
      ForwardDate: { expert_only: false },
      Deposit: { expert_only: true },
      Currency: { expert_only: true },
      ProfitInPips: { expert_only: true },
      Leverage: { expert_only: true },
      ExecutionMode: { expert_only: true, proven: [7], provisional: { 9: "TBD-99" } },
      Visual: { expert_only: false },
    },
    // label は EA 名の語幹（実物の schema も同じ）。合成根の既存検定が使う ea_name と
    // そろえてあるのは、Expert 選択が指標候補の取得起点になるためである。
    expert_options: [
      { token: "PRO_fit_Band_EA.zzz", label: "PRO_fit_Band_EA" },
      { token: "TC24051901.zzz", label: "TC24051901" },
    ],
    unsupported: [
      // 束縛（keys）と発火条件（trigger/tokens）は**サーバの宣言**（`UnsupportedRule.ui`）が
      // 配る。front はこれを照合するだけで、キー名からの再導出も既定値との差分判定もしない。
      // 6 形すべてを 1 件ずつ置き、実物の rule と同型にしてある。
      { unsupported_id: "X-01", field: "optimization", reason: "最適化は対象外です",
        keys: ["Optimization"], trigger: "except_tokens", tokens: ["o0"] },
      { unsupported_id: "X-02", field: "forward_mode", reason: "フォワードは未確定です", tbd: "TBD-99",
        keys: ["ForwardMode"], trigger: "except_tokens", tokens: ["f0"] },
      // 特定トークンで発火（実物の N-16 `Dates=2` と同型）
      { unsupported_id: "X-03", field: "date_range.preset", reason: "窓を決定できません",
        keys: ["Dates"], trigger: "on_tokens", tokens: ["d2"] },
      // 特定トークンで発火（実物の N-05 `Model=実ティック` と同型）
      { unsupported_id: "X-04", field: "tick_model", reason: "実ティックの供給元がありません",
        keys: ["Model"], trigger: "on_tokens", tokens: ["m1"] },
      // キーが投入本文に載るなら発火（実物の N-15 と同型）
      { unsupported_id: "X-05", field: "date_range", reason: "窓の適用は実行後にしか分かりません",
        keys: ["FromDate", "ToDate"], trigger: "on_presence" },
      // 配った候補に無い値なら発火（実物の N-01 と同型）
      { unsupported_id: "X-06", field: "subject_path", reason: "実行可能な EA に限られます",
        keys: ["Expert"], trigger: "off_candidates" },
      // 実行対象データセットの権威値と異なれば発火（実物の N-11 と同型）
      { unsupported_id: "X-07", field: "currency", reason: "口座通貨が決済通貨と異なります",
        keys: ["Currency"], trigger: "off_profile" },
      // 生トークンでは判定できない（実物の N-10＝構造不変条件の防壁と同型）
      { unsupported_id: "X-08", field: "symbol", reason: "単一銘柄のみ受けます",
        keys: ["Symbol"], trigger: "none" },
    ],
  };
}

/** `GET /sim/run-options` の datasets 1 件（Tester パネルの既定値の供給元）。 */
export function runProfile() {
  return {
    dataset: "ds1", data_path: "/d/ds1.csv", symbol: "SYM", period: "P2",
    contract_size: 10.0, digits: 1, point_size: 0.1, leverage: 10.0,
    volume_min: 0.01, volume_max: 100.0, volume_step: 0.01, stops_level: 0,
    settlement_currency: "XYZ",
  };
}
