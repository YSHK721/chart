// tf_period_tooltip.js — tf-period プロファイルバーのオンマウス読取ツールチップ（DOM のみ・lwc 非依存）。
//
// 設計入力: 依頼者指示 2026-07-13「プロファイルバーにオンマウスしたときにその価格帯のデータを表示」
//   （表示場所はカーソル追従ツールチップ＝a案を依頼者選択）。
// 責務（SRP）: hit（primitive.tfPeriodLevelAt の戻り値）を受けてカーソル近傍に小さな読取欄を
//   描く/消すだけ。レベル探索は primitive、クロスヘア座標は ChartRenderer、配線は composition root。
// 隔離: document / container は注入（テストは fake DOM）。チャート API には一切触れない。
//
// 色（段階 5-E）: インラインスタイルの色は配線点参照（var(--ct-*)）へ移した。ここに値を書くと
//   テーマで地や文字色を変えてもツールチップだけ旧色に残る（app.css で解いたのと同じ二重定義）。

import { chromeVar } from './chrome_css_var.js';

// 値の表示整形: count 列（整数）は「滞在 N tick（シェア%）」、zp 列（実数 z）は「z=+x.xx」。
export function formatTooltipLines(hit) {
  const lines = [];
  if (hit.timeLabel) {
    lines.push(hit.timeLabel);
  }
  lines.push(`価格 ${hit.price.toFixed(4).replace(/\.?0+$/, '')}`);
  if (Number.isInteger(hit.value)) {
    const share = (hit.tpoUnits > 0) ? `（${((hit.value / hit.tpoUnits) * 100).toFixed(1)}%）` : '';
    lines.push(`滞在 ${hit.value} tick${share}`);
  } else {
    lines.push(`z ${hit.value >= 0 ? '+' : ''}${hit.value}`);
  }
  if (hit.poc != null) {
    lines.push(`POC ${hit.poc}`);
  }
  if (hit.vaLow != null && hit.vaHigh != null) {
    lines.push(`VA ${hit.vaLow}〜${hit.vaHigh}`);
  }
  if (Number.isInteger(hit.value) && hit.tpoUnits > 0) {
    lines.push(`周期計 ${hit.tpoUnits} tick`);
  }
  return lines;
}

// 周期始端 UNIX 秒 → 表示ラベル。日内周期は 'HH:MM'（UTC）、1D（86400 の倍数）は 'MM-DD'。
export function formatPeriodLabel(timeSec) {
  if (timeSec == null) {
    return ''; // null/undefined は明示ガード（Number(null)=0 の 1970 化を防ぐ）。
  }
  const t = Number(timeSec);
  if (!Number.isFinite(t)) {
    return '';
  }
  const d = new Date(t * 1000);
  const p2 = (n) => String(n).padStart(2, '0');
  if (t % 86400 === 0) {
    return `${p2(d.getUTCMonth() + 1)}-${p2(d.getUTCDate())}`;
  }
  return `${p2(d.getUTCHours())}:${p2(d.getUTCMinutes())}`;
}

export class TfPeriodTooltip {
  // document/container 注入（DOM 不在＝null なら全メソッド no-op：node 単体テスト互換）。
  constructor({ document: doc, container } = {}) {
    this._doc = doc ?? null;
    this._container = container ?? null;
    this._el = null;
  }

  _ensureEl() {
    if (this._el || !this._doc || !this._container) {
      return this._el;
    }
    const el = this._doc.createElement('div');
    el.className = 'tfp-tooltip';
    // 最小限のインラインスタイル（CSS ファイル非依存・チャート上の小さな読取欄）。
    // 段階 5-E: 色は配線点（chrome_tokens.js）が単一情報源。DOM 要素なので :root の
    //   カスタムプロパティが継承され、CSS 機構がそのまま使える（canvas と違い注入不要）。
    //   fallback は chromeVar() が CHROME_CURRENT から組むため、ここに値を書き写さない。
    el.style.cssText = 'position:absolute;z-index:30;pointer-events:none;display:none;'
      + `background:${chromeVar('tfpTooltipSurface')};`
      + `border:1px solid ${chromeVar('tfpTooltipBorder')};border-radius:4px;`
      + `padding:6px 8px;font:11px/1.5 system-ui;color:${chromeVar('uiText')};white-space:pre;`;
    this._container.appendChild(el);
    this._el = el;
    return el;
  }

  // hit をカーソル (x,y) 近傍に表示する。コンテナ右端/下端では反対側へフリップ（はみ出し防止）。
  show(x, y, hit) {
    const el = this._ensureEl();
    if (!el) {
      return;
    }
    el.textContent = formatTooltipLines(hit).join('\n');
    el.style.display = 'block';
    const cw = this._container.clientWidth || 0;
    const ch = this._container.clientHeight || 0;
    const ew = el.offsetWidth || 120;
    const eh = el.offsetHeight || 60;
    const left = (x + 14 + ew > cw) ? Math.max(0, x - 14 - ew) : x + 14;
    const top = (y + 14 + eh > ch) ? Math.max(0, y - 14 - eh) : y + 14;
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }

  hide() {
    if (this._el) {
      this._el.style.display = 'none';
    }
  }
}
