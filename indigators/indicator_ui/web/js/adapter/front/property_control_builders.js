// property_control_builders.js — control_type → DOM コントロール生成器のテーブル（OCP・ISSUE-181）。
//
// 設計入力（ISSUE-181）: properties_dialog.js の `_buildControl` は control_type ごとの
//   switch（8 分岐 + default）で、新しい control_type を足すたびにダイアログ本体の改変を要した。
//   usecase 側 `usecase/form_model.js:16-25`（CONTROL_BY_TYPE）は同種の写像を既に
//   `Object.freeze` の宣言テーブルで持つ。本モジュールは adapter 側にだけ残っていた switch を
//   同じ形（凍結テーブル + 既定値）へ揃え、PropertiesDialog から生成手続きを外へ出す。
//
// 責務: 「1 フィールドの値編集コントロール（DOM 要素）を作り、値変更をホストへ通知する」ことのみ。
//   ダイアログ枠・タブ・検証・スタイル/可視性ペインは持たない（それらは PropertiesDialog の責務）。
//
// ホストとの結合面（ControlContext）は 4 メンバのみ:
//   { doc, getValue(name), setValue(name, value), onChange() }
//   値の所有者はホスト（PropertiesDialog._values）のままにする。getValue/setValue は
//   呼び出し時に解決する遅延アクセサであり、ホスト側が _values を差し替えても（デフォルト復元）
//   従来どおり最新の入れ物を参照する（挙動不変）。
//
// ★ upstream JS API（addLineSeries / applyOptions 等）は一切参照しない（properties_dialog.js §8.4 と同一規律）。

// 期間プリセットの換算・提示は usecase の純関数が唯一の判定源（基本設計_期間プリセット.md §8.2）。
//   本モジュールは DOM 生成とイベント配線のみを担い、換算規則・提示規則を再実装しない。
import { parsePeriodInput, presetsFor } from '../../usecase/period_presets.js';

// i18n 解決器を持たないプロトタイプ向けの簡易ラベル化（キー末尾を表示）。
export function humanizeKey(key) {
  if (key === null || key === undefined) {
    return '';
  }
  const s = String(key);
  return s.includes('.') ? s.split('.').pop() : s;
}

// rgba(...)/#rgb/#rrggbb を <input type=color> 用の #rrggbb へ近似変換する。
// 解析不能な値は安全な既定（#2962ff）を返す（プロトタイプ・スタイルタブ最小可）。
export function toHex(value) {
  if (typeof value !== 'string') return '#2962ff';
  const v = value.trim();
  if (/^#[0-9a-fA-F]{6}$/.test(v)) return v.toLowerCase();
  if (/^#[0-9a-fA-F]{3}$/.test(v)) {
    return ('#' + v.slice(1).split('').map((c) => c + c).join('')).toLowerCase();
  }
  const m = v.match(/^rgba?\(\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)/i);
  if (m) {
    const toByte = (n) => Math.max(0, Math.min(255, Math.round(Number(n))));
    const hex = (n) => toByte(n).toString(16).padStart(2, '0');
    return ('#' + hex(m[1]) + hex(m[2]) + hex(m[3])).toLowerCase();
  }
  return '#2962ff';
}

// enum option の表示名: enumLabels（日本語表示マップ）優先。未指定はキー末尾（従来挙動）。
function optionLabel(field, v) {
  return (field.enumLabels && field.enumLabels[v] != null)
    ? field.enumLabels[v]
    : humanizeKey(String(v));
}

export function buildNumber(field, ctx) {
  const doc = ctx.doc;
  const input = doc.createElement('input');
  input.type = 'number';
  input.className = 'prop-input prop-input-number';
  input.dataset.propName = field.name;
  if (field.step !== null) input.step = String(field.step);
  if (field.min !== null) input.min = String(field.min);
  if (field.max !== null) input.max = String(field.max);
  input.value = field.value === null || field.value === undefined ? '' : String(field.value);
  input.addEventListener('input', () => {
    const raw = input.value;
    ctx.setValue(field.name, raw === '' ? null : Number(raw));
    ctx.onChange();
  });
  return input;
}

export function buildSelect(field, ctx) {
  const doc = ctx.doc;
  const sel = doc.createElement('select');
  sel.className = 'prop-input prop-input-select';
  sel.dataset.propName = field.name;
  for (const v of field.enumValues ?? []) {
    const opt = doc.createElement('option');
    opt.value = String(v);
    opt.textContent = optionLabel(field, v);
    if (v === field.value) opt.selected = true;
    sel.append(opt);
  }
  sel.addEventListener('change', () => {
    // enum 値は raw（文字列/数値）。数値 enum は元型へ復元。
    const picked = (field.enumValues ?? []).find((v) => String(v) === sel.value);
    ctx.setValue(field.name, picked !== undefined ? picked : sel.value);
    ctx.onChange();
  });
  return sel;
}

// segmented: ENUM を「横並びセグメントボタン群」で描く（ドロップダウンでなくトグル・§3.1 拡張）。
//   試作 prototype_260630-01 の解像度トグル（ビン ⇄ レンジ）移植。各 option をボタン化し、
//   選択中に is-active を付与。クリックで値を更新後、ホストの onChange を呼ぶ
//   （→ _refreshVisible/_revalidate が走り bins/range 行が即出没する）。
export function buildSegmented(field, ctx) {
  const doc = ctx.doc;
  const wrap = doc.createElement('div');
  wrap.className = 'prop-input prop-segmented';
  wrap.dataset.propName = field.name;
  const options = field.enumValues ?? [];
  const buttons = [];
  const setActive = (val) => {
    for (const b of buttons) {
      b.classList.toggle('is-active', String(b.dataset.segValue) === String(val));
    }
  };
  for (const v of options) {
    const btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'prop-seg-btn';
    btn.dataset.segValue = String(v);
    btn.textContent = optionLabel(field, v);
    btn.addEventListener('click', () => {
      // v は走査中の option 値そのもの（enum の raw な元型＝文字列/数値を保持）。
      //   options.find で自分自身を引き直す必要はないため直接代入する。
      ctx.setValue(field.name, v);
      setActive(ctx.getValue(field.name));
      ctx.onChange();
    });
    buttons.push(btn);
    wrap.append(btn);
  }
  setActive(field.value);
  return wrap;
}

export function buildCheckbox(field, ctx) {
  const doc = ctx.doc;
  const input = doc.createElement('input');
  input.type = 'checkbox';
  input.className = 'prop-input prop-input-checkbox';
  input.dataset.propName = field.name;
  input.checked = Boolean(field.value);
  input.addEventListener('change', () => {
    ctx.setValue(field.name, input.checked);
    ctx.onChange();
  });
  return input;
}

export function buildText(field, ctx) {
  const doc = ctx.doc;
  const input = doc.createElement('input');
  input.type = 'text';
  input.className = 'prop-input prop-input-text';
  input.dataset.propName = field.name;
  input.value = field.value === null || field.value === undefined ? '' : String(field.value);
  input.addEventListener('input', () => {
    ctx.setValue(field.name, input.value === '' ? null : input.value);
    ctx.onChange();
  });
  return input;
}

export function buildColor(field, ctx) {
  const doc = ctx.doc;
  const input = doc.createElement('input');
  input.type = 'color';
  input.className = 'prop-input prop-input-color';
  input.dataset.propName = field.name;
  // <input type=color> は #rrggbb のみ。rgba 既定はそのまま値として保持し、
  // ピッカーには近似 hex を表示する（プロトタイプ・スタイルタブは最小可）。
  input.value = toHex(field.value);
  input.addEventListener('input', () => {
    ctx.setValue(field.name, input.value);
    ctx.onChange();
  });
  return input;
}

// FLOAT_LIST（probabilities）リスト編集（§3.2）。各要素=数値入力＋削除、末尾に追加。
export function buildFloatList(field, ctx) {
  const doc = ctx.doc;
  const wrap = doc.createElement('div');
  wrap.className = 'prop-input prop-list';
  wrap.dataset.propName = field.name;

  const list = Array.isArray(field.value) ? field.value.slice() : [];
  ctx.setValue(field.name, list);

  const rows = doc.createElement('div');
  rows.className = 'prop-list-rows';

  const renderRows = () => {
    rows.innerHTML = '';
    const cur = ctx.getValue(field.name);
    cur.forEach((val, idx) => {
      const r = doc.createElement('div');
      r.className = 'prop-list-row';
      const num = doc.createElement('input');
      num.type = 'number';
      num.step = 'any';
      num.className = 'prop-input prop-input-number';
      num.value = String(val);
      num.addEventListener('input', () => {
        const arr = ctx.getValue(field.name).slice();
        arr[idx] = num.value === '' ? null : Number(num.value);
        ctx.setValue(field.name, arr);
        ctx.onChange();
      });
      const del = doc.createElement('button');
      del.type = 'button';
      del.className = 'prop-list-del';
      del.textContent = '−';
      del.addEventListener('click', () => {
        const arr = ctx.getValue(field.name).slice();
        // 空リスト禁止（最低 1 要素・§3.2）。
        if (arr.length <= 1) return;
        arr.splice(idx, 1);
        ctx.setValue(field.name, arr);
        renderRows();
        ctx.onChange();
      });
      r.append(num, del);
      rows.append(r);
    });
  };
  renderRows();

  const add = doc.createElement('button');
  add.type = 'button';
  add.className = 'prop-list-add';
  add.textContent = '＋追加';
  add.addEventListener('click', () => {
    const arr = ctx.getValue(field.name).slice();
    arr.push(0.95); // 既定追加値（§3.2）。
    ctx.setValue(field.name, arr);
    renderRows();
    ctx.onChange();
  });

  wrap.append(rows, add);
  return wrap;
}

// ENUM_LIST（buckets）マルチセレクト（候補からチェックで複数選択・§3.1）。
export function buildMultiselect(field, ctx) {
  const doc = ctx.doc;
  const wrap = doc.createElement('div');
  wrap.className = 'prop-input prop-multiselect';
  wrap.dataset.propName = field.name;
  const selected = new Set(Array.isArray(field.value) ? field.value : []);
  ctx.setValue(field.name, [...selected]);

  for (const opt of field.enumValues ?? []) {
    const chip = doc.createElement('label');
    chip.className = 'prop-chip';
    const cb = doc.createElement('input');
    cb.type = 'checkbox';
    cb.checked = selected.has(opt);
    cb.addEventListener('change', () => {
      if (cb.checked) selected.add(opt);
      else selected.delete(opt);
      // 元の候補順を保持して配列化。
      ctx.setValue(field.name, (field.enumValues ?? []).filter((v) => selected.has(v)));
      ctx.onChange();
    });
    chip.append(cb, doc.createTextNode(' ' + humanizeKey(String(opt))));
    wrap.append(chip);
  }
  return wrap;
}

// window（Union[str,int]）複合: ラジオ expanding/固定窓 ＋ 数値（§4.3.1）。
export function buildWindowCompound(field, ctx) {
  const doc = ctx.doc;
  const wrap = doc.createElement('div');
  wrap.className = 'prop-input prop-window-compound';
  wrap.dataset.propName = field.name;

  const isExpanding = field.value === 'expanding' || field.value === null || field.value === undefined;

  const radioExp = doc.createElement('label');
  const rExp = doc.createElement('input');
  rExp.type = 'radio';
  rExp.name = `prop-window-${field.name}`;
  rExp.checked = isExpanding;
  radioExp.append(rExp, doc.createTextNode(' 展開(expanding)'));

  const radioFixed = doc.createElement('label');
  const rFixed = doc.createElement('input');
  rFixed.type = 'radio';
  rFixed.name = `prop-window-${field.name}`;
  rFixed.checked = !isExpanding;
  radioFixed.append(rFixed, doc.createTextNode(' 固定窓'));

  const num = doc.createElement('input');
  num.type = 'number';
  num.min = '1';
  num.step = '1';
  num.className = 'prop-input prop-input-number';
  num.disabled = isExpanding;
  num.value = isExpanding ? '' : String(field.value);

  const sync = () => {
    if (rExp.checked) {
      num.disabled = true;
      ctx.setValue(field.name, 'expanding');
    } else {
      num.disabled = false;
      ctx.setValue(field.name, num.value === '' ? null : Number(num.value));
    }
    ctx.onChange();
  };
  rExp.addEventListener('change', sync);
  rFixed.addEventListener('change', sync);
  num.addEventListener('input', sync);

  wrap.append(radioExp, radioFixed, num);
  return wrap;
}

// period（期間入力＋プリセット・基本設計_期間プリセット.md §6.1/§6.3/§7）。
//
// 数値そのもの（`50`）に加えて期間表記（`5d` / `3M` / `1年`）を受理し、暦期間 → 実バー本数の
//   実測換算（usecase/period_presets.js）で本数へ確定する。プリセットは当該パラメータの
//   min/max と実効計算時間足から決まる（提示規則は usecase 側の純関数が唯一の判定源）。
//
// 期間コンテキスト（datasetRef・実効計算時間足）はホストが `ctx.periodContext()` で供給する。
//   未供給（旧ホスト・SSR/単体テスト）のときはプリセットを出さず、数値のみを受け付ける
//   テキスト入力へ退化する（機能の欠落であって障害ではない・F-P2/F-P3 と同じ扱い）。
//
// 値の確定タイミング（§7.3）:
//   - 入力中の文字列が純数値なら即時 setValue（従来の number コントロールと同じ即時検証を保つ）。
//   - 期間表記は blur / Enter で確定し、入力欄の表示を換算後の本数へ置き換える。
export function buildPeriod(field, ctx) {
  const doc = ctx.doc;
  const wrap = doc.createElement('div');
  wrap.className = 'prop-input prop-period';
  wrap.dataset.propName = field.name;

  const input = doc.createElement('input');
  input.type = 'text';
  input.className = 'prop-input prop-input-text prop-period-input';
  input.dataset.propName = field.name;
  input.value = field.value === null || field.value === undefined ? '' : String(field.value);
  input.title = '本数（例 50）または期間（例 5d / 3M / 1年）を入力できます';
  input.placeholder = '50 / 5d / 3M';

  const err = doc.createElement('div');
  err.className = 'prop-period-error';
  err.dataset.propPeriodError = field.name;

  const trigger = doc.createElement('button');
  trigger.type = 'button';
  trigger.className = 'prop-period-trigger';
  trigger.title = '期間から選ぶ';
  trigger.textContent = '⏱';

  const pop = doc.createElement('div');
  pop.className = 'prop-period-pop is-hidden';

  const periodContext = () => (
    typeof ctx.periodContext === 'function' ? (ctx.periodContext() ?? null) : null
  );

  const setError = (message) => {
    err.textContent = message ?? '';
    wrap.classList.toggle('has-error', !!message);
    // 未解決の入力エラーは OK を抑止する（ホストが対応している場合のみ。非対応ホスト＝
    //   単体テストの簡易 ctx や旧ホストでは従来どおり表示のみ）。
    if (typeof ctx.setPendingError === 'function') {
      ctx.setPendingError(field.name, message ?? null);
    }
  };

  const setOpen = (on) => {
    if (typeof pop.classList?.toggle === 'function') {
      pop.classList.toggle('is-hidden', !on);
    }
  };

  const isOpen = () => !!(pop.classList?.contains && !pop.classList.contains('is-hidden'));

  // プリセット一覧を開くたびに組み直す（実効計算時間足は `timeframe` パラメータの変更で動く）。
  const renderPop = () => {
    pop.innerHTML = '';
    const pc = periodContext();
    const presets = pc
      ? presetsFor({
        datasetRef: pc.datasetRef, timeframe: pc.timeframe, min: field.min, max: field.max,
      })
      : [];
    if (presets.length === 0) {
      const empty = doc.createElement('div');
      empty.className = 'prop-period-empty';
      empty.textContent = 'この時間足で使えるプリセットはありません。';
      pop.append(empty);
      return;
    }
    // 見出し: 何を基準にした値かを画面上で判別可能にする（§7.2）。
    const head = doc.createElement('div');
    head.className = 'prop-period-head';
    head.textContent = `${pc.timeframeLabel ?? pc.timeframe} 基準`;
    pop.append(head);
    for (const p of presets) {
      const item = doc.createElement('button');
      item.type = 'button';
      item.className = 'prop-period-item';
      item.dataset.periodUnit = p.unit;
      item.dataset.periodBars = String(p.bars);
      const name = doc.createElement('span');
      name.className = 'prop-period-item-label';
      name.textContent = p.label;
      const bars = doc.createElement('span');
      bars.className = 'prop-period-item-bars';
      bars.textContent = String(p.bars);
      item.append(name, bars);
      if (Number(field.value) === p.bars) {
        item.classList.add('is-active');
      }
      item.addEventListener('click', () => {
        input.value = String(p.bars);
        setError(null);
        ctx.setValue(field.name, p.bars);
        setOpen(false);
        ctx.onChange();
      });
      pop.append(item);
    }
  };

  // 入力確定（blur / Enter）。期間表記は換算し、本数表示へ置き換える。
  const commit = () => {
    const raw = input.value;
    if (raw.trim() === '') {
      setError(null);
      ctx.setValue(field.name, null);
      ctx.onChange();
      return;
    }
    const pc = periodContext();
    // 期間コンテキスト未供給時は数値のみ受理（プリセット非対応ホストでの退化動作）。
    if (!pc) {
      const n = Number(raw);
      if (Number.isFinite(n)) {
        setError(null);
        ctx.setValue(field.name, n);
      } else {
        setError('数値を入力してください。');
      }
      ctx.onChange();
      return;
    }
    const r = parsePeriodInput(raw, {
      datasetRef: pc.datasetRef, timeframe: pc.timeframe, min: field.min, max: field.max,
    });
    if (r.ok) {
      input.value = String(r.bars);
      setError(null);
      ctx.setValue(field.name, r.bars);
      ctx.onChange();
      return;
    }
    // 失敗時は代入せず直前の有効値を保持する（F-P1）。表示は入力文字列のまま残し、
    //   ユーザーが修正できるようにする。
    //   さらに「未解決の入力エラーがある状態で OK を押すと、旧値が黙って確定する」
    //   （＝ユーザーには『設定しても元に戻る』と見える）事故を防ぐため、OK を抑止する
    //   （§5 F-11 の OK 制御と同じ扱い）。エラーが解消されるまで確定できない。
    setError(r.message);
  };

  // フォーカス時に全選択する。期間欄は「本数（例 180）」を表示しているため、選択せずに
  //   `3h` と打つと既存値へ追記されて `1803h` になり、換算が上限超で失敗 → 代入されないまま
  //   OK で旧値が確定する（ユーザーには『設定しても元に戻る』と見える）。入力＝置き換えを
  //   既定にして、この事故を構造的に起こさない。
  const selectAll = () => {
    if (typeof input.select === 'function') {
      input.select();
    }
  };
  input.addEventListener('focus', selectAll);
  // 実 UI 実測（2026-07-29）: focus だけでは不十分。既にフォーカスがある欄を再度クリックしても
  //   focus は発火しないため、2 回目以降の打鍵が既存値へ追記された（`180` → `1801803h`）。
  //   クリック時にも選択が潰れている（キャレットのみ）なら全選択し直し、「クリック→打鍵＝置き換え」
  //   を常に成立させる。部分編集したい場合はクリック後に矢印キー／ドラッグ選択で行える。
  input.addEventListener('click', () => {
    if (input.selectionStart === input.selectionEnd) {
      selectAll();
    }
  });

  input.addEventListener('input', () => {
    // 純数値は即時反映（従来の number コントロールと同じ即時検証を保つ）。
    const raw = input.value.trim();
    if (/^\d+(\.\d+)?$/.test(raw)) {
      setError(null);
      ctx.setValue(field.name, Number(raw));
      ctx.onChange();
      return;
    }
    // 数値以外を打ち始めた時点で直前のエラー表示は消す（確定時に再判定する）。
    //   これが無いと、一度エラーになった欄が修正中もエラー扱いのままで OK が押せない。
    if (err.textContent) {
      setError(null);
    }
  });
  input.addEventListener('blur', commit);
  input.addEventListener('keydown', (ev) => {
    if (ev && ev.key === 'Enter') {
      if (typeof ev.preventDefault === 'function') {
        ev.preventDefault();
      }
      commit();
    }
  });

  trigger.addEventListener('click', () => {
    const next = !isOpen();
    if (next) {
      renderPop();
    }
    setOpen(next);
  });

  // 外側クリック/フォーカス移動で閉じる。document へリスナを残さない（ダイアログは都度生成・破棄
  //   されるため、document 常駐リスナは解除漏れになる）。
  wrap.addEventListener('focusout', (ev) => {
    const next = ev && ev.relatedTarget;
    if (!next || typeof wrap.contains !== 'function' || !wrap.contains(next)) {
      setOpen(false);
    }
  });

  wrap.append(input, trigger, pop, err);
  return wrap;
}

// control_type → 生成器（§3.1）。form_model.js の CONTROL_BY_TYPE と同じ「凍結テーブル」形。
//   新しい control_type はここへ 1 行足すだけで済む（PropertiesDialog は不変＝OCP）。
export const CONTROL_BUILDERS = Object.freeze({
  number: buildNumber,
  select: buildSelect,
  segmented: buildSegmented,
  checkbox: buildCheckbox,
  list: buildFloatList,
  multiselect: buildMultiselect,
  color: buildColor,
  window_compound: buildWindowCompound,
  period: buildPeriod,
});

// 未知の control_type の既定（従来の switch default と同一＝テキスト入力）。
export const DEFAULT_CONTROL_BUILDER = buildText;

// control_type 別レンダリングの単一入口（従来の _buildControl switch と等価）。
export function buildControl(field, ctx) {
  return (CONTROL_BUILDERS[field.controlType] ?? DEFAULT_CONTROL_BUILDER)(field, ctx);
}
