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

// 既定の時間足メニュー（present＝バックエンド対応 9 足）。サーバ TIMEFRAME_RULES と一致させる。
const DEFAULT_GROUPS = [
  { cat: '分', items: [['1m', '1分'], ['5m', '5分'], ['15m', '15分'], ['30m', '30分']] },
  { cat: '時間', items: [['1h', '1時間'], ['4h', '4時間']] },
  { cat: '日', items: [['1D', '日'], ['1W', '週'], ['1M', '月']] },
];

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
    if (typeof doc.addEventListener === 'function') {
      doc.addEventListener('click', () => this._setOpen(false));
    }
    pop.addEventListener('pointerdown', (e) => {
      if (e && typeof e.stopPropagation === 'function') {
        e.stopPropagation();
      }
    });
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
