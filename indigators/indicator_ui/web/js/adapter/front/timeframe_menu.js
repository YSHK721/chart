// timeframe_menu.js — 時間足ドロップダウン（ISSUE-117/122/123・DOM アダプター・両アプリ共有）。
//
// 設計入力: ユーザー指示（2026-07-18）「時間足を選択できる UI を追加」＝ TradingView の
//   時間足カテゴリメニュー相当（ss2026071894749.jpg）。
//
// ISSUE-123（値渡し是正）: メニュー DOM（トリガー＋カテゴリ＋項目）は本コンポーネントが生成する。
//   旧実装は index.html 側に項目 DOM を直書きしており、present/replay の 2 つの index.html へ
//   複製（値渡し）されて時間足集合の二重管理が生じていた。現方式は index.html に空マウント
//   `<div class="tf-menu" id="tf-menu"></div>` のみを置き、項目集合は groups 注入（既定＝present の
//   9 足）で単一ソース化する。replay は対応 8 足（30m なし）を composition root から注入する。
//
// 責務（SRP）: メニュー DOM 生成と開閉（トリガークリック・項目選択後クローズ・外側クリッククローズ）。
//   時間足の選択実行・active/トリガーラベル同期は既存機構に委譲する＝項目は data-timeframe を持ち、
//   IndicatorController.bind() の一括配線と TimeframeController.syncButtons() に乗る
//   （本コンポーネントは controller を知らない・DIP）。install は controller.bind() より前
//   （bootstrap 内）に呼ばれる前提（bind が [data-timeframe] を収集するため）。
//   DOM 不在（SSR/テスト）は no-op。

import { installDocumentCloseHandler, removeDocumentCloseHandler } from './menu_document_close.js';
import { TF_CODES } from '../../domain/tf_meta.js';

// 時間足コード → 日本語ラベル。**表示名だけ**を持つ（どの足が存在するかは持たない）。
//   ISSUE-254: かつては既定メニューが時間足の集合そのものを直書きしており、台帳へ足を足しても
//   メニューに出ない（＝どこを直せばよいか分からない）状態だった。集合と順序は台帳
//   （domain/tf_meta.js の TF_CODES＝Python 台帳の生成物）を唯一源にし、ここはラベルだけにする。
//   台帳に足が増えてラベル未定義になった場合は、コード自体をラベルとして必ず表示する
//   （黙って落とさない）。網羅は timeframe_menu.test.js が固定する。
const TF_LABELS = {
  '1m': '1分', '5m': '5分', '15m': '15分', '30m': '30分',
  '1h': '1時間', '4h': '4時間',
  '1D': '日', '1W': '週', '1M': '月',
};

// 時間足コード → カテゴリ見出し（表示のグルーピングのみ）。
const TF_CATEGORY = {
  '1m': '分', '5m': '分', '15m': '分', '30m': '分',
  '1h': '時間', '4h': '時間',
  '1D': '日', '1W': '日', '1M': '日',
};

// 既定の時間足メニュー＝**台帳の全時間足**をカテゴリ順に並べたもの（値の直書きなし）。
function groupsFromLedger(codes = TF_CODES) {
  const groups = [];
  for (const code of codes) {
    const cat = TF_CATEGORY[code] ?? 'その他';
    let g = groups.find((x) => x.cat === cat);
    if (!g) {
      g = { cat, items: [] };
      groups.push(g);
    }
    g.items.push([code, TF_LABELS[code] ?? code]);
  }
  return groups;
}

const DEFAULT_GROUPS = groupsFromLedger();

// 時間足キー → 表示ラベル（'1m'→'1分'・'1D'→'日'）の写像を groups から導出する。
//   ラベルの単一情報源は本モジュールの groups 定義（既定＝present 9 足）であり、利用側は
//   キーとラベルを二重定義しない（チャートテンプレートの保存ダイアログ文言が本写像を使う。
//   基本設計_チャートテンプレート §6.2「この時間足（例：日）に紐付ける」）。
//   replay の 8 足は既定 groups の部分集合（30m 非対応）でラベル語彙は同一のため既定で足りる。
export function timeframeLabels(groups = DEFAULT_GROUPS) {
  const map = {};
  for (const g of groups ?? []) {
    for (const [tf, text] of g.items ?? []) {
      map[tf] = text;
    }
  }
  return map;
}

export class TimeframeMenu {
  // { document, groups }（マウントは id で解決: #tf-menu（空 div）。groups 省略＝present 既定 9 足）。
  constructor({ document: doc, groups } = {}) {
    this._doc = doc;
    this._groups = Array.isArray(groups) && groups.length > 0 ? groups : DEFAULT_GROUPS;
    this._pop = null;
  }

  install() {
    const doc = this._doc;
    if (!doc || typeof doc.getElementById !== 'function' || typeof doc.createElement !== 'function') {
      return; // DOM 不在（SSR/テスト最小 fake）は no-op（防御）。
    }
    const mount = doc.getElementById('tf-menu');
    if (!mount || typeof mount.appendChild !== 'function') {
      return;
    }

    // トリガー（現在足ラベルは TimeframeController.syncButtons が #tf-menu-label へ反映）。
    const trigger = doc.createElement('button');
    trigger.type = 'button';
    trigger.id = 'tf-menu-trigger';
    trigger.className = 'tb-interval tf-menu-trigger';
    trigger.title = '時間足を選択';
    const label = doc.createElement('span');
    label.id = 'tf-menu-label';
    const caret = doc.createElement('span');
    caret.className = 'tf-menu-caret';
    caret.textContent = '▾';
    trigger.append(label, caret);

    // ポップ（カテゴリ見出し＋項目）。項目は data-timeframe を持ち bind()/syncButtons に乗る。
    const pop = doc.createElement('div');
    pop.id = 'tf-menu-pop';
    pop.className = 'tf-menu-pop is-hidden';
    for (const g of this._groups) {
      const cat = doc.createElement('div');
      cat.className = 'tf-menu-cat';
      cat.textContent = g.cat;
      pop.append(cat);
      for (const [tf, text] of g.items) {
        const item = doc.createElement('button');
        item.type = 'button';
        item.className = 'tf-menu-item';
        item.dataset.timeframe = tf;
        item.textContent = text;
        pop.append(item);
      }
    }

    mount.appendChild(trigger);
    mount.appendChild(pop);
    this._pop = pop;

    trigger.addEventListener('click', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation(); // document の外側クリッククローズに拾わせない。
      }
      this._toggle();
    });
    // 項目クリック: 選択自体は bind() の data-timeframe 配線が行う。ここでは閉じるだけ。
    pop.addEventListener('click', (e) => {
      const t = e && e.target;
      if (t && t.dataset && t.dataset.timeframe) {
        this._setOpen(false);
      }
    });
    // 外側クリックで閉じる（メニュー内クリックは pop/trigger 側で stopPropagation/処理済み）。
    // ISSUE-169: 前 mount ぶんの document リスナを外してから張る（線形蓄積の停止）。
    this._docCloseHandler = () => this._setOpen(false);
    this._doc = doc;
    installDocumentCloseHandler(doc, 'timeframe', this._docCloseHandler);
    pop.addEventListener('pointerdown', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation();
      }
    });
  }

  // ISSUE-169: 明示的な後片付け。document スコープのリスナを外す（DOM は呼び出し側が破棄する）。
  //   呼ばれなくても install 時の自己修復で蓄積は有界（document あたり 1 個）になる。
  dispose() {
    removeDocumentCloseHandler(this._doc, 'timeframe', this._docCloseHandler);
    this._docCloseHandler = null;
  }

  _toggle() {
    const pop = this._pop;
    const isOpen = !!(pop.classList && pop.classList.contains && !pop.classList.contains('is-hidden'));
    this._setOpen(!isOpen);
  }

  _setOpen(on) {
    const pop = this._pop;
    if (pop && pop.classList && typeof pop.classList.toggle === 'function') {
      pop.classList.toggle('is-hidden', !on);
    }
  }
}
