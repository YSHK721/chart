// tickvol_bands_catalog_entry.js — 「取引密度帯」の IndicatorDef factory（アクター駆動型）。
//
// 一日の取引時間のうちティックが集中する時刻帯のチャートパネル背景色を変える（依頼者確定 2026-08-01）。
// 系列（線）を描かないアクター駆動型のため /compute を持たず、専用アクターが
// /tickvol_profile から帯を取得してプリミティブへ渡す（market_profile と同じ扱い）。
//
// 依存注入で受けるのは catalog.js のローカル helper（param / OHLC）とドメイン型。catalog.js から
// factory を呼んで REGISTRY へ登録する（catalog.js が MP 定義に対して行っているのと同じ規律）。

import { tickvolBandsSupportsTf } from '../domain/tickvol_bands.js';

export function makeTickvolBandsDef({
  IndicatorDef, SeriesDef, SeriesKind, ParamType, ConstraintKind, param, OHLC,
}) {
  // 時間足ゲート（1 時間足以下）。ctx 不明（単体テスト・A 方式）では制限しない＝安全側
  //   （mp_source_capability の optionEnable と同じ安全則）。
  const tfOk = (_values, ctx) => !ctx || ctx.timeframe == null || tickvolBandsSupportsTf(ctx.timeframe);

  return new IndicatorDef({
    id: 'tickvol_bands',
    displayNameKey: 'ind.tickvol_bands',
    category: { group: 'builtin', nameKey: 'cat.volume' },
    // プロファイル系（価格でなく「時間帯の性質」を描く）＝profile タブ。
    tab: 'profile',
    placement: 'overlay',
    params: [
      // sessions: 集計に使う過去セッション日数（当日は含まない＝因果窓）。
      //   実測（2026-08-01）: 直近 3 か月内の互いに素な 20 セッション窓どうしで HIGH ビン集合の
      //   Jaccard 0.84 に対し、9 か月離れると 0.33。長い窓（60 セッション）は 0.44 へ劣化する。
      //   ＝「癖」は数か月スケールで入れ替わるのでローリング直近窓が必須。上限 25 は 1 分足原子の
      //   供給 tail（50,000 行 ≒ 39 セッション）に対する安全域。
      param('sessions', ParamType.INT, 20,
        // 整数のため閉区間 [5,25] は開区間 (4,26) と同値（既存の制約語彙だけで表す）。
        [{ kind: ConstraintKind.RANGE_OPEN, operands: [4, 'sessions', 26], messageKey: 'err.sessions.range' }],
        null, {
          group: 'group.calc', order: 1, label: '参照セッション数', unit: 'unit.days',
          step: 1, min: 5, max: 25,
          tooltip: '当日を除く直近このセッション日数から時刻帯の密度を推定する（実測: 20 前後で安定・数か月以上遡ると別の癖になる）',
          conditionalVisible: tfOk,
        }),
      // pct: HIGH 判定のパーセンタイル（分布内の相対閾値）。実測で絶対 tickvol 閾値は窓により
      //   2 倍ドリフトする（市場全体の活況度が動く）ため、閾値は必ず相対値で持つ。
      param('pct', ParamType.INT, 75,
        // 整数のため閉区間 [50,95] は開区間 (49,96) と同値。
        [{ kind: ConstraintKind.RANGE_OPEN, operands: [49, 'pct', 96], messageKey: 'err.pct.range' }],
        null, {
          group: 'group.calc', order: 2, label: '強調する上位割合(%)', unit: 'unit.percent',
          step: 5, min: 50, max: 95,
          tooltip: '時刻帯を密度順に並べ、上位 (100 - この値)% を「濃い」として背景色を変える（既定 75＝上位 25%）',
          conditionalVisible: tfOk,
        }),
    ],
    // 系列は描かない（背景プリミティブが描く）。IndicatorDef が series 非空を要求するためのダミー
    //   1 本（market_profile と同じ扱い・レンダラへは渡らない）。
    series: [
      new SeriesDef({ kind: SeriesKind.HORIZONTAL_LINE, sourceColumn: null, seriesName: 'tickvol_bands', dynamic: false }),
    ],
    compute: {
      computeId: 'tickvol_bands', requiredColumns: OHLC, timeRequired: false,
      backendParam: null, variants: ['default'],
    },
  });
}
