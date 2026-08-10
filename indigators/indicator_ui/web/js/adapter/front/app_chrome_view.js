// AppChromeView（adapter/front/app_chrome_view.js）— アプリ外枠 DOM の**所有規約**（ISSUE-278 #16）。
//
// 解決する問題（実測）: ツールバーと指標ダイアログを index.html へ直書きしていたため、配信される
//   3 ページ（indicator_ui / replay_ui / unified_ui）へ同じマークアップを手書き複製する義務が
//   生まれていた。指標ダイアログは 3 ページで **1440 文字が byte 一致** ＝純粋な三重複製で、
//   リプレイバーでは実際にドリフトしていた（`rp-speed` の title だけ :8000 が「リアルタイム＝実時間」の
//   説明を欠く）。取り残しは過去に 3 回（`#rp-mode` の option 欠落 4079461 ／ カテゴリボタン
//   ISSUE-221 ／ ペイン別凡例の器 ISSUE-277）。
//
// 規約（ISSUE-277 overlay_host と同一・SOLID）:
//   - SRP: 外枠 DOM は「それを配線する側」が所有する。ページが持つのはアンカー（#app）だけ。
//   - OCP: 領域の追加は install 関数を 1 本足すだけ。HTML も既存 install も改変不要。
//   - DIP: View はページが宣言した id ではなく、注入されたアンカー要素に依存する。
//   - LSP: `#app` を持つページはどれも等価な宿主。要素の取り残しという部分実装が起こり得ない。
//   - フェイルクローズ: DOM がある環境でアンカーが無ければ**例外**（無言 no-op にしない）。
//     ISSUE-276/277 の全滅は「要素不在なら no-op」が契約違反を無症状にしたため気付けなかった。
//
// 冪等性: 既に同じ id/クラスの要素があれば再利用し、二重生成しない（再 mount・テスト再実行で増えない）。

// アプリ外枠のアンカー。配信 3 ページすべてが持つ唯一の共通土台。
export const APP_ANCHOR_SELECTOR = '#app';

// 銘柄名の単一情報源。ツールバーの表示と、足情報のコピー（どのチャートの値か）で同じ文字列を使う。
//   ここを複製すると「画面は NI225・コピーは別名」という食い違いが静かに生まれる。
export const CHART_SYMBOL = 'NI225';

function resolveAnchor(doc, { anchor = null, anchorSelector = APP_ANCHOR_SELECTOR } = {}) {
  // DOM 非対応（SSR・要素生成しか持たないスタブ document）は描画対象が存在しない＝縮退する。
  //   版面を「解決できない」環境と、版面が「無い」ページ（＝契約違反）とは厳密に区別する。
  if (!doc || typeof doc.createElement !== 'function') {
    return null;
  }
  if (!anchor && typeof doc.querySelector !== 'function') {
    return null;
  }
  const root = anchor ?? doc.querySelector(anchorSelector);
  if (!root) {
    throw new Error(`app_chrome_view: アンカー ${anchorSelector} がページに無い`);
  }
  return root;
}

// ツールバー（シンボル / 時間足メニューのマウント / ライブ追従 / テンプレートメニューのマウント /
//   インジケーターボタン / リプレイトグル）を #app の先頭へ生成する。
//   liveFollow  : ライブ追従トグルを置くか（ライブ core・統合 UI＝true / standalone replay＝false）。
//   enterReplay : リプレイのオン・オフトグルを置くか（統合 UI のみ＝リプレイ層が注入されたとき）。
//   メニュー本体（tf-menu / tpl-menu の項目）は各共有コンポーネントが自分で生成する（本 View は器のみ）。
export function installChartToolbar(doc, { anchor = null, liveFollow = false, enterReplay = false } = {}) {
  const root = resolveAnchor(doc, { anchor });
  if (!root) {
    return null;
  }
  const existing = typeof root.querySelector === 'function' ? root.querySelector('.toolbar') : null;
  if (existing) {
    return existing;   // 再入で増やさない。
  }
  const bar = doc.createElement('div');
  bar.className = 'toolbar';
  bar.innerHTML = [
    `<span class="tb-symbol">${CHART_SYMBOL}</span>`,
    '<span class="tb-sep"></span>',
    // 時間足ドロップダウンのマウント（ISSUE-117/123）。項目集合は timeframe_menu.js が台帳から生成する。
    '<div class="tf-menu" id="tf-menu"></div>',
    '<span class="tb-sep"></span>',
    // ライブ追従トグル（FOLLOW=点灯／ANALYSIS=消灯）。初期 disabled で置き、
    //   LiveFollowController.install() が活性化する＝配線されたときだけ押せる（ISSUE-275）。
    liveFollow
      ? '<button id="live-follow-toggle" class="tb-interval" type="button" aria-pressed="false" title="ライブ追従（点灯=ライブ／消灯=分析）" disabled>ライブ</button><span class="tb-sep"></span>'
      : '',
    // チャートテンプレートのドロップダウンのマウント（§6.1）。項目は chart_template_menu.js が生成。
    '<div class="tpl-menu" id="tpl-menu"></div>',
    // 指標カラーテーマのドロップダウンのマウント（基本設計_指標カラーテーマ §6.1）。テンプレート
    //   （どの指標）とテーマ（どの色）は直交する独立概念のため、同一メニューへ入れず右隣に並べる。
    //   項目 DOM は color_theme_menu.js が生成する。**index.html には 1 枚も書かない**
    //   （器は本 View が所有する＝ISSUE-278 #16 の規約）。
    '<div class="color-theme-menu" id="color-theme-menu"></div>',
    '<button id="indicator-open-btn" class="tb-indicator-btn" type="button" title="インジケーター">'
      + '<span class="ic">∿</span><span class="lbl">インジケーター</span></button>',
    // リプレイのオン・オフトグル（統合 UI のみ）。点灯状態は applyModeUi が aria-pressed で反映する。
    enterReplay
      ? '<span class="tb-sep"></span><button id="enter-replay" class="tb-interval" type="button" aria-pressed="false" title="リプレイ表示のオン・オフ">リプレイ</button>'
      : '',
  ].join('');
  root.insertBefore(bar, root.firstChild);
  return bar;
}

// 指標追加ダイアログ（#indicator-dialog）を #app の直後（アンカーの親）へ生成する。
//   3 ページで byte 一致の複製だった領域。中身の一覧・カテゴリボタンは既存の動的生成が担う
//   （カテゴリは ISSUE-221 でカタログ導出済み。ここに列挙を書かない）。
export function installIndicatorDialog(doc, { anchor = null } = {}) {
  const root = resolveAnchor(doc, { anchor });
  if (!root) {
    return null;
  }
  // ダイアログは全画面バックドロップのため #app の**兄弟**として置く（従来の DOM 位置と同じ）。
  const parent = root.parentNode || root;
  const existing = typeof parent.querySelector === 'function'
    ? parent.querySelector('#indicator-dialog') : null;
  if (existing) {
    return existing;
  }
  const dialog = doc.createElement('div');
  dialog.id = 'indicator-dialog';
  dialog.className = 'dialog-backdrop';
  dialog.innerHTML = [
    '<div class="dialog" role="dialog" aria-label="インジケーター">',
    '<div class="dialog-head">',
    '<span class="dialog-title">インジケーター、メトリクス、ストラテジー</span>',
    '<button id="indicator-dialog-close" class="dialog-close" type="button" aria-label="閉じる">×</button>',
    '</div>',
    '<div class="dialog-tabs">',
    '<button class="dialog-tab is-active" data-tab="indicator" type="button">インジケーター</button>',
    '<button class="dialog-tab" data-tab="strategy" type="button">ストラテジー</button>',
    '<button class="dialog-tab" data-tab="profile" type="button">プロファイル</button>',
    '<button class="dialog-tab" data-tab="pattern" type="button">パターン</button>',
    '</div>',
    '<div class="dialog-body">',
    '<div class="dialog-side">',
    // カテゴリのボタンはカタログから動的生成する（ISSUE-221）。ここへ列挙を書くと、新カテゴリの
    //   指標を足したときに本 View の同時改変が必要になる（実際に 24 指標中 12 件が到達不能だった）。
    '<button class="side-item is-active" data-category="" type="button">すべて</button>',
    '<button class="side-item" data-category="__favorites__" type="button">★ お気に入り</button>',
    '</div>',
    '<div class="dialog-main">',
    '<div class="search-wrap">',
    '<input id="indicator-search" type="text" placeholder="検索" autocomplete="off" />',
    '</div>',
    '<div id="indicator-list"></div>',
    '</div>',
    '</div>',
    '</div>',
  ].join('');
  parent.appendChild(dialog);
  return dialog;
}
