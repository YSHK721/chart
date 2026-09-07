// series_style_forms.js — スタイルタブ「表示形式」の台帳と、永続差分の組立（純ロジック・ISSUE-502 段階 4D）。
//
// 責務（単一の変更軸）: **系列スタイルの表示形式語彙**。選択肢の集合・並び・ゲート条件と、
//   その選択値 ⇄ 永続化スキーマ（{display, style}）の対応を 1 箇所で宣言する。
//
// なぜ台帳にするか（是正前の実測）:
//   properties_dialog.js は「選択肢の組立」（`opts=['solid','dotted','dashed']` に
//   `pointStyleEditable` なら 'dot' を unshift、`barStyleEditable` なら 'bar' を push）と
//   「選択値の分解」（'dot'→display=dots / 'bar'→display=bar / それ以外→display=line+style）を
//   **100 行離れた 2 箇所**に手書きで持っていた。第 3 の表示形式を足すには両方を同時に直す必要が
//   あり、片方だけ直せば「選べるのに保存されない」「保存値に戻せない」という無言の欠落になる。
//   同ファイルが使う property_control_builders.js は同型の switch を既に凍結テーブルへ是正済みで、
//   規律が片側にだけ適用されていない状態だった。
//
// 台帳 1 エントリの追加＝表示形式 1 種の追加（ダイアログ本体は不変＝OCP）。
//
// エントリの意味:
//   value     : select の option 値（＝画面に出す表示形式の名前）
//   gate      : 行モデル（buildSeriesStyleRows の戻り）のどのフラグで出現が決まるか。
//               null = 常に出す（基本の線種 3 種）。
//   display   : 永続化スキーマの `display` 値（'line' | 'dots' | 'bar'）
//   lineStyle : 併せて永続化する `style` 値。null = 線種の概念を持たない形式
//               （＝`style` を載せない。dot / bar は線種を持たない）。
//
// **順序は表示順**（select の option 並び）。並べ替えは画面の並びを変える（従来順を保存すること）。
export const SERIES_DISPLAY_FORMS = Object.freeze([
  Object.freeze({ value: 'dot', gate: 'pointStyleEditable', display: 'dots', lineStyle: null }),
  Object.freeze({ value: 'solid', gate: null, display: 'line', lineStyle: 'solid' }),
  Object.freeze({ value: 'dotted', gate: null, display: 'line', lineStyle: 'dotted' }),
  Object.freeze({ value: 'dashed', gate: null, display: 'line', lineStyle: 'dashed' }),
  Object.freeze({ value: 'bar', gate: 'barStyleEditable', display: 'bar', lineStyle: null }),
]);

// 線種が未指定の行に当てる既定（従来の `r.style ?? 'solid'`）。台帳の既定線種はここが唯一源。
export const DEFAULT_LINE_STYLE = 'solid';

// 台帳に無い値を分解するときの既定（従来の switch default と同値＝「線として扱い、値を線種にする」）。
//   property_control_builders.js の DEFAULT_CONTROL_BUILDER と同じ位置付け。
const DEFAULT_DISPLAY = 'line';

function _gateOpen(row, entry) {
  return entry.gate === null || Boolean(row && row[entry.gate]);
}

// この行が「統合 select（線種と系列表示を 1 つにまとめた選択）」を出す対象か。
//   ゲート付きエントリが 1 つでも開いていれば対象（従来の `pointStyleEditable || barStyleEditable`）。
export function usesUnifiedDisplayForm(row) {
  return SERIES_DISPLAY_FORMS.some((e) => e.gate !== null && _gateOpen(row, e));
}

// この行で選べる表示形式の値（台帳順）。ゲートが閉じているものは出さない。
//   ゲート未付与行では基本の線種 3 種だけが残る（従来の 3 択と同一）。
export function displayFormOptions(row) {
  return SERIES_DISPLAY_FORMS.filter((e) => _gateOpen(row, e)).map((e) => e.value);
}

// 行の実描画値（display / style）から初期選択値を決める。
//   線種を持たない形式（dot / bar）は display で一意に決まり、それ以外は線種そのものが値になる。
//   ゲートで出せない形式が初期値になり得る点は従来と同じ（実描画値をそのまま映す）。
export function displayFormInitial(row) {
  for (const e of SERIES_DISPLAY_FORMS) {
    if (e.lineStyle === null && e.display === (row ? row.display : undefined)) {
      return e.value;
    }
  }
  return (row && row.style != null) ? row.style : DEFAULT_LINE_STYLE;
}

// 選択値 → 永続化フィールド（{display} または {display, style}）。
//   台帳外の値は既定（線として扱い、値を線種に載せる）へ落ちる。
export function decodeDisplayForm(value) {
  const entry = SERIES_DISPLAY_FORMS.find((e) => e.value === value);
  if (!entry) {
    return { display: DEFAULT_DISPLAY, style: value };
  }
  return entry.lineStyle === null
    ? { display: entry.display }
    : { display: entry.display, style: entry.lineStyle };
}

// ---------------------------------------------------------------------------
// 永続差分の組立（DOM 非依存）
// ---------------------------------------------------------------------------
// 入力は「現在値と初期値の組」だけを持つスナップショット（DOM 要素は渡さない）。
//   styleRows      : [{ names, color, width, style, unified }]  各項目は { value, initial } か null
//                    （null = その行にその入力が無い＝差分対象外）
//   visibilityRows : [{ names, checked, initial }]
// 出力は { seriesName: { color?, width?, style?, display?, visible? } }。
//   変更が無ければ空オブジェクト。行が bucket 粒度のときは names の全系列へ同じ差分を展開する。
export function collectSeriesStyleDiff({ styleRows = [], visibilityRows = [] } = {}) {
  const patch = {};
  const put = (names, fields) => {
    for (const n of names) {
      patch[n] = { ...(patch[n] ?? {}), ...fields };
    }
  };
  for (const s of styleRows) {
    const fields = {};
    // 色入力を持たない行（ヒート配色・ISSUE-112）は色を差分対象にしない。
    if (s.color && s.color.value !== s.color.initial) {
      fields.color = s.color.value;
    }
    // 線幅/線種入力を持たない行（histogram・ISSUE-111）は色のみ差分対象。空欄は確定させない。
    if (s.width && s.width.value !== s.width.initial && s.width.value !== '') {
      fields.width = Number(s.width.value);
    }
    if (s.style && s.style.value !== s.style.initial) {
      fields.style = s.style.value;
    }
    // 統合 select は台帳が分解する（第 3 の表示形式を足しても本関数は不変）。
    if (s.unified && s.unified.value !== s.unified.initial) {
      Object.assign(fields, decodeDisplayForm(s.unified.value));
    }
    if (Object.keys(fields).length > 0) {
      put(s.names, fields);
    }
  }
  for (const v of visibilityRows) {
    if (v.checked !== v.initial) {
      put(v.names, { visible: v.checked });
    }
  }
  return patch;
}
