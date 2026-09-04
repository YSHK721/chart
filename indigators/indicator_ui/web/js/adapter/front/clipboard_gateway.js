// clipboard_gateway.js — クリップボードへの書き込みポート実装（ユーザー指示 2026-08-09）。
//
// 責務（SRP）: 「文字列をクリップボードへ置く」だけ。何をコピーするかは知らない。
//
// 経路が 2 つあるのは環境の能力差そのもの（症状回避ではない）:
//   - `navigator.clipboard.writeText` は **secure context 限定**の API。localhost（:8000 / :8280）は
//     secure context なので通常はこちらが使われる。
//   - 同じ配信を LAN の IP（http://192.168.x.x:8000 等）で開くと secure context ではなく
//     `navigator.clipboard` 自体が存在しない。そのとき使えるのは `document.execCommand('copy')`
//     （選択範囲のコピー）だけで、これは仕様上の代替経路であり同じ結果（クリップボード更新）を返す。
//   どちらも使えない環境では **false を返す**（成功したふりをしない＝呼び出し側が失敗を表示できる）。
//
// 注入: navigator / document は constructor 注入（テストは fake を渡す）。

export class ClipboardGateway {
  constructor({ navigator: nav = null, document: doc = null } = {}) {
    this._nav = nav ?? (typeof navigator !== 'undefined' ? navigator : null);
    this._doc = doc ?? (typeof document !== 'undefined' ? document : null);
  }

  /**
   * 文字列をクリップボードへ書き込む。
   * @param {string} text 書き込む文字列（空文字は書き込まない＝false）。
   * @returns {Promise<boolean>} 書き込めたら true。
   */
  async writeText(text) {
    if (typeof text !== 'string' || text.length === 0) {
      return false;
    }
    const nav = this._nav;
    if (nav && nav.clipboard && typeof nav.clipboard.writeText === 'function') {
      try {
        await nav.clipboard.writeText(text);
        return true;
      } catch {
        // 権限拒否・非フォーカス等。下の代替経路へ落とす（黙って成功にはしない）。
      }
    }
    return this._writeViaSelection(text);
  }

  // secure context ではない配信（LAN IP 直参照など）向けの代替経路。
  //   一時的な textarea を作って選択し execCommand('copy') する（この API に選択以外の入力手段は無い）。
  //   後片付け（要素の除去）まで本メソッドが持つ。
  _writeViaSelection(text) {
    const doc = this._doc;
    if (!doc || typeof doc.createElement !== 'function' || typeof doc.execCommand !== 'function') {
      return false;
    }
    const body = doc.body;
    if (!body || typeof body.appendChild !== 'function') {
      return false;
    }
    const ta = doc.createElement('textarea');
    ta.value = text;
    // 画面をちらつかせない（可視領域外に置く）。readOnly はモバイルのキーボード出現を抑える。
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    body.appendChild(ta);
    try {
      if (typeof ta.select === 'function') {
        ta.select();
      }
      return doc.execCommand('copy') === true;
    } catch {
      return false;
    } finally {
      if (typeof body.removeChild === 'function') {
        body.removeChild(ta);
      }
    }
  }
}
