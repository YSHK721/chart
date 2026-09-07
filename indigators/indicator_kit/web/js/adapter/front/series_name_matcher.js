// series_name_matcher.js — F3 系列名照合（§3.3.6）の純ロジック（ISSUE-181・SRP）。
//
// 設計入力（ISSUE-181）: IndicatorController は 5 アクター同居の神クラスで、その 1 つが
//   「カタログ SeriesDef が宣言する期待系列名集合と、compute 応答 series[].name の照合」だった
//   （旧 indicator_controller.js:260-315）。この関心事は
//     - 変更要求の出所がカタログ側（series_name / series_name_pattern の宣言仕様）だけであり、
//     - DOM・renderer・facade・永続化のいずれにも触れない純関数である
//   ため、controller から切り出して単独で保守・試験できる形にする。
//
// 状態: 持たない（入力 def/params のみで決まる純関数群）。IndicatorController が保持していた
//   状態も参照していなかった（this への依存は無かった）ため、移送すべき状態は存在しない。
//   controller 側は既存メソッド名（_expectedSeriesNames / _expandPattern / _validateSeriesNames）を
//   薄い委譲として温存する（replay subclass の this._validateSeriesNames 呼出・差し替えテストを温存）。

// series_name_pattern を展開（{bucket} {pct} 形式）。
//   pattern.bucketsFromParam / pctsFromParam が指定され params が与えられた場合は、当該 param
//   値リストからトークンを生成する（bucketsUpper=大文字化 / pctsInt=整数文字列化）。これにより
//   ユーザが入力した任意期間（pcts 静的リスト外の 252 等）も期待集合に含まれ F3 を通過する。
//   未指定・params 無し時は従来どおり静的 buckets/pcts を直積展開する（profit_band 28 系列等）。
export function expandSeriesNamePattern(pattern, params = null) {
  const template = pattern.template ?? '';
  let buckets = pattern.buckets ?? [''];
  let pcts = pattern.pcts ?? [''];
  if (params) {
    if (pattern.bucketsFromParam && Array.isArray(params[pattern.bucketsFromParam])) {
      buckets = params[pattern.bucketsFromParam].map(
        (v) => (pattern.bucketsUpper ? String(v).toUpperCase() : String(v)),
      );
    }
    if (pattern.pctsFromParam && Array.isArray(params[pattern.pctsFromParam])) {
      pcts = params[pattern.pctsFromParam].map(
        (v) => (pattern.pctsInt ? String(Math.round(Number(v))) : String(v)),
      );
    }
  }
  const out = [];
  for (const bucket of buckets) {
    for (const pct of pcts) {
      out.push(template.replace('{bucket}', bucket).replace('{pct}', pct));
    }
  }
  return out;
}

// SeriesDef.series_name（dynamic は series_name_pattern 展開）の期待集合を返す。
//   params を渡すと、pattern が *FromParam を宣言する系列は現在の params から期待名を
//   生成する（moving_averages: 任意期間 252 等を許容・§3.3.6 拡張）。params 省略時は
//   pattern の静的 buckets/pcts へフォールバック（profit_band 等・後方互換）。
export function expectedSeriesNames(def, params = null) {
  const names = new Set();
  for (const s of def.series ?? []) {
    if (s.dynamic && s.seriesNamePattern) {
      for (const name of expandSeriesNamePattern(s.seriesNamePattern, params)) {
        names.add(name);
      }
    } else if (s.seriesName) {
      names.add(s.seriesName);
    }
  }
  return names;
}

// F3: 期待集合に含まれない系列はスキップ（renderLine に渡さない）＋ console.warn 記録。
//   params は dynamic pattern の *FromParam 展開に用いる（省略時は静的フォールバック）。
export function validateSeriesNames(payloads, def, params = null) {
  const expected = expectedSeriesNames(def, params);
  return (payloads ?? []).filter((p) => {
    const ok = expected.has(p.name);
    if (!ok && typeof console !== 'undefined' && console.warn) {
      console.warn(`[F3] 系列名不一致のためスキップ: instance=${def.id} name=${p.name}`);
    }
    return ok;
  });
}
