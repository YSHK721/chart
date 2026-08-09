// chrome_theme_applier.js — 解決済みクロム色を 2 つの配信機構へ配る協働子
//   （基本設計_指標カラーテーマ.md §4.3 FR-C12・§5.2 UC-C02 手順 2・A-11）。
//
// 設計入力（E-28）: 色は 2 つの機構に分かれている。(a) lightweight-charts のオプション（JS）と
//   (b) CSS。同一の意味が両機構に分かれて実装されている実例が現在値で、価格線は JS・数値表示は
//   CSS にある。配信を 1 箇所にまとめないと「片側だけ更新される」状態が生まれる。
//
// 責務は**配信の扇形分岐だけ**（SRP）。
//   - 色の決定（テーマ有無・派生・不透明度の合成）は color_resolver.js が済ませている。
//   - 配線点 id → lightweight-charts のオプション経路という upstream 知識は ChartRenderer が持つ
//     （宣言された唯一の upstream 隔離点。本クラスは lwc を知らない・ISSUE-262 の隔離規約）。
//   よって本クラスは import を 1 つも持たず、受け取った値の袋を 2 つの sink へ渡すだけである。
//
// 依存（§7.3 ISP）:
//   chromeSink … applyChromeColors(slots) だけを持てばよい（ChartRenderer の全公開面に依存しない）。
//   rootStyle  … setProperty / removeProperty だけを持てばよい（documentElement.style）。
//
// 縮退（§5.7）:
//   F-C10 chromeSink が applyChromeColors を持たない（SSR・単体テスト）→ JS 配信のみ no-op。
//   F-C11 :root への書き込みが不能（document 不在）→ CSS 配信のみ no-op。
//   いずれも他方の配信は継続する（片方の欠落で全体が止まらない）。

// CSS カスタムプロパティの接頭辞（§1.3）。既存の --live-follow-* と名前空間を分離する。
const CSS_VAR_PREFIX = '--ct-';

export class ChromeThemeApplier {
  constructor({ chromeSink = null, rootStyle = null } = {}) {
    this._sink = chromeSink;
    this._rootStyle = rootStyle;
  }

  // 解決済みの値を 2 機構へ配る。resolved = { slots, tokens }（color_resolver の resolveAllChrome
  //   が返す形）。slots は JS 機構、tokens は CSS 機構が読む。
  apply(resolved) {
    if (!resolved) {
      return;
    }
    this._applyJs(resolved.slots ?? {});
    this._applyCss(resolved.tokens ?? {});
  }

  _applyJs(slots) {
    if (!this._sink || typeof this._sink.applyChromeColors !== 'function') {
      return; // F-C10
    }
    this._sink.applyChromeColors(slots);
  }

  // :root へ --ct-<token> を書く。値が null のトークンは removeProperty で消す
  //   （前回のテーマの値が残ると、適用結果が適用履歴に依存する）。
  _applyCss(tokens) {
    const style = this._rootStyle;
    if (!style || typeof style.setProperty !== 'function' || typeof style.removeProperty !== 'function') {
      return; // F-C11
    }
    for (const [token, color] of Object.entries(tokens)) {
      const name = `${CSS_VAR_PREFIX}${token}`;
      if (color == null) {
        style.removeProperty(name);
      } else {
        style.setProperty(name, color);
      }
    }
  }
}
