// host_view.js — ロール契約を「宣言」から「実体」へ変える射影（ISP・ISSUE-255）。
//
// 問題（実測 2026-08-04〜08-08）:
//   IndicatorController は 5 つの協働子へ `this` を**丸ごと**渡していた。協働子が実際に触る
//   host メンバーは 5〜21 個なのに、渡していたのは 40 超のメソッドと 20 超のフィールドを持つ
//   host 全体である。契約（TimeframeHost / MarketProfileHost）はコメントと記述オブジェクトで
//   宣言されていたが、**実行時には何も狭めていない**（宣言と実体の乖離＝ISSUE-262 と同じ形）。
//
// 本モジュールの役割:
//   契約（role / methods / fields / optionalFields）から、その面**だけ**を通す射影を作る。
//   協働子には host ではなく射影を渡す。契約に無いメンバーへ触れると **例外**（フェイルクローズ）。
//
// 設計（SOLID）:
//   - ISP: 協働子はロールに必要な面だけを受け取る。host の他の関心事は見えない。
//   - DIP: 協働子は具象 host ではなく「契約」に依存する。契約は協働子（クライアント）側が所有し、
//     host はそれを満たす側になる。
//   - OCP: 面を増やすときに触るのは契約 1 箇所。射影の実装は不変。
//   - LSP: メソッドは host に bind して返すため、subclass（ReplayIndicatorController）の
//     override がそのまま効く。フィールドは**参照のたびに** host から読むので可変状態も追随する。
//
// 非目標: 不変性の強制ではない（返す値そのものは host の参照）。狭めるのは**面**であって値ではない。
export function createHostView(host, contract) {
  if (host == null) throw new TypeError('createHostView: host が null です');
  if (contract == null || !contract.role) {
    throw new TypeError('createHostView: role を持つ契約が必要です');
  }
  const allowed = new Set([
    ...(contract.methods || []),
    ...(contract.fields || []),
    ...(contract.optionalFields || []),
  ]);
  if (allowed.size === 0) {
    throw new TypeError(`createHostView: ${contract.role} の契約が空です`);
  }

  const deny = (prop) => new TypeError(
    `${contract.role} 契約外の host メンバーへアクセスしました: ${String(prop)}。`
    + ' 必要なら契約へ追加してください（協働子に host 全体を渡さないための射影です）。'
  );

  return new Proxy(Object.create(null), {
    get(_target, prop) {
      // Symbol（Symbol.toPrimitive / Symbol.toStringTag 等）と Promise 判定の 'then' は
      //   言語・ランタイム側の探索であり、契約違反ではない。undefined を返して素通しする。
      if (typeof prop === 'symbol' || prop === 'then') return undefined;
      if (!allowed.has(prop)) throw deny(prop);
      const value = host[prop];
      return typeof value === 'function' ? value.bind(host) : value;
    },
    set(_target, prop) {
      // 協働子は host のフィールドを書き換えない（次 state は host のコミット用メソッドへ依頼する）。
      throw new TypeError(
        `${contract.role} は host のメンバーを書き換えられません: ${String(prop)}。`
        + ' 状態の確定は host が公開するコミット用メソッド経由で行ってください。'
      );
    },
    has(_target, prop) {
      return typeof prop === 'symbol' ? false : allowed.has(prop);
    },
    ownKeys() {
      return [...allowed];
    },
    getOwnPropertyDescriptor(_target, prop) {
      if (typeof prop === 'symbol' || !allowed.has(prop)) return undefined;
      return { configurable: true, enumerable: true, writable: false, value: host[prop] };
    },
  });
}
