// symbol_spec_generated.js — 銘柄仕様台帳（**自動生成・手で編集しない**）。
//
// 生成元: marketdata/dataset_registry.py の REGISTRY（ref→銘柄）
//         ＋ marketdata/symbol_spec.py の SYMBOL_SPECS（銘柄→呼び値・表示桁）。
// 生成器: tools/gen_js_parity_golden.py（台帳変更時に再実行する）。
//
// なぜ生成物なのか（ISSUE-368 工程 2・案 E-3）: 呼び値を JS 側にも書くと第 2 定義になり、
//   Python の台帳と静かにずれる（ISSUE-253 / ISSUE-254 と同型）。定義は Python ただ 1 つと
//   し、JS は生成された値を読むだけにする。陳腐化は marketdata/tests の parity 検定が落とす。
//   HTTP route は作らない（供給元が 1 つ・起動時 1 回の定数。route 化は無音フォールバックと
//   file:// 起動不能を新設する）。
//
//   datasetRef → 銘柄 → { tick: 呼び値, digits: 表示桁 } の 2 段を両方ここで配る。
//   未知 ref / 未知銘柄は undefined になる（呼び側は無音で生値に落とさず機能を落とす）。
export const DATASET_SYMBOLS = Object.freeze({
  'sample': 'TSLA',
  'jp225': 'JP225',
  'jp225_m1': 'JP225',
  'jp225_tick': 'JP225',
  'jp225_mt5': 'JP225',
});

export const SYMBOL_SPECS = Object.freeze({
  'JP225': Object.freeze({ tick: 1.0, digits: 0 }),
  'TSLA': Object.freeze({ tick: 0.01, digits: 2 }),
});
