"""UC-001 の責務分割を構文木で施行する（ISSUE-502 段階 4A・SOLID 精査台帳 2026-09-06）。

固定する仕様:
    `RunBacktestInteractor` は **run のライフサイクルだけ**を持ち、その run のあいだに起きる
    個別の仕事（決済の記帳・約定執行・SL/TP 判定・証拠金・建玉変更）は、それぞれ別の
    協働クラスが**唯一の所有者**として持つこと。

なぜ行数ではなく責務を測るか:
    「940 行を 5 分割した」は分割の**結果**であって仕様ではない。行数の上限を検定に
    したら、規則を 1 行足すたびに赤くなる装置（改訂の妨害）になり、しかも
    「短いファイルを 6 つ作って全部が同じことを知っている」形は素通りする。
    固定すべきは **どの知識がどこにしか無いか** であり、それは構文木にしか現れない。

なぜ「無い」ことを測るか（立証責任）:
    分割は放っておくと戻る——run ライフサイクルの中に「ここだけ直接書いたほうが早い」
    1 行が足され、次の改訂でそれが 2 行になる。是正前の 940 行はその累積である。
    本ファイルは「run ライフサイクルが低位 API を**呼ばない**」を機械的に赤にすることで、
    その第 1 行目を入れさせない。

計算量（プロジェクト絶対命令 2026-08-28）:
    分割で発行が増えていないことを Test Spy で表明する（末尾のクラス）。測るのは時間では
    なく回数であり、回数そのものは期待値に焼き込まない（固定するのは無駄の不在）。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from simulator.usecase import margin_guard as margin_guard_module
from simulator.usecase import order_execution as order_execution_module
from simulator.usecase import position_directives as position_directives_module
from simulator.usecase import run_backtest as run_backtest_module
from simulator.usecase import sltp_monitor as sltp_monitor_module
from simulator.usecase import trade_ledger as trade_ledger_module

#: 責務の所有者（モジュール名 → 何を所有するか）。ここに無いモジュールは実行経路ではない。
_OWNERS = {
    "run_backtest": "run ライフサイクル（準備段・バーの進み方・評価点の成立順・終了段）",
    "trade_ledger": "決済の記帳（確定トレード・証拠金解放・Deal・balance）",
    "order_execution": "約定執行（成行・reverse・ペンディング設置/トリガ/清算）",
    "sltp_monitor": "SL/TP 到達判定",
    "margin_guard": "含み損益の値洗い・equity 記録・証拠金割れの処理",
    "position_directives": "建玉変更（トレーリング・部分決済）の適用",
}

_MODULES = {
    "run_backtest": run_backtest_module,
    "trade_ledger": trade_ledger_module,
    "order_execution": order_execution_module,
    "sltp_monitor": sltp_monitor_module,
    "margin_guard": margin_guard_module,
    "position_directives": position_directives_module,
}


def _tree(key):
    source = Path(_MODULES[key].__file__).read_text(encoding="utf-8")
    return ast.parse(source, filename=_MODULES[key].__file__)


def _called_names(tree):
    """呼ばれている名前（呼び出し式の関数名・属性名）を行番号つきで返す。"""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            out.append((func.id, node.lineno))
        elif isinstance(func, ast.Attribute):
            out.append((func.attr, node.lineno))
    return out


def _attribute_targets(tree, attr):
    """`X.attr` の形で参照されている箇所を行番号つきで返す（読み書きを問わない）。"""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == attr
    ]


def _equity_appends(tree):
    """equity 系列への追記（`*.equity_curve.append(...)`）の行番号を返す。

    受け側の名前は私有（`self._equity_curve`）でも公開（`state.equity_curve`）でも拾う
    ——私有名だけ／公開名だけを見る検定は、片方の書き方へ移った瞬間に何も測らなくなる。
    """
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "append"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr.lstrip("_") == "equity_curve"
    ]


def _imported_names(tree, module_name):
    """`from <module_name> import a, b` で持ち込まれた名前の集合。"""
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == module_name:
            names |= {alias.name for alias in node.names}
    return names


def _imported_modules(tree):
    """import している module 名の集合（`import x` と `from x import ...` の両方）。"""
    names: "set[str]" = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names |= {alias.name for alias in node.names}
    return names


class TestEveryResponsibilityHasExactlyOneOwner:
    """各規則の定義点が 1 つであること（写しが無いこと）。"""

    def test_the_run_lifecycle_never_touches_the_execution_primitives(self):
        """run ライフサイクルが約定価格・到達判定の低位関数を呼ばないこと。

        呼べるのは受理の門（admit_orders）だけである——「その注文を受け付けてよいか」は
        run の入口の判断であり、約定価格の決め方（どのクォートで建てるか・買い決済は Bid か）
        とは別の関心である。
        """
        allowed = {"admit_orders"}
        imported = _imported_names(_tree("run_backtest"), "simulator.usecase._execution")
        assert imported == allowed, (
            f"run ライフサイクルが約定の低位関数を持ち込んでいる: {sorted(imported - allowed)}。"
            f" 所有者は {_OWNERS['order_execution']} / {_OWNERS['sltp_monitor']} である。"
        )

    def test_the_trade_record_is_created_in_the_ledger_only(self):
        """確定トレードと決済 Deal の生成点が帳簿ただ 1 つであること。

        なぜ 1 つでなければならないか: 確定トレード・証拠金解放・Deal・balance は
        1 回の決済で必ず同時に動く。生成点が散れば「trades にだけ載って balance_curve に
        載らない」形の食い違いが起こり得るのに、出力の総和を見るだけの検証では落ちない。
        """
        offenders = {
            key: [
                lineno
                for name, lineno in _called_names(_tree(key))
                if name in ("TradeRecord", "from_close")
            ]
            for key in _MODULES
            if key != "trade_ledger"
        }
        assert not any(offenders.values()), (
            f"帳簿の外で確定トレード／決済 Deal を組んでいる: "
            f"{ {k: v for k, v in offenders.items() if v} }"
        )
        owner = [
            name for name, _ in _called_names(_tree("trade_ledger"))
            if name in ("TradeRecord", "from_close")
        ]
        assert owner, "帳簿が確定トレードを組んでいない（所有者が空になっている）"

    def test_the_account_holdings_are_mutated_by_the_ledger_and_the_executor_only(self):
        """口座の保有列・証拠金を触るのが帳簿と約定執行だけであること。

        触ってよい 2 者はそれぞれ向きが逆である（約定執行は**積み**、帳簿は**外す**）。
        SL/TP 監視・建玉変更・run ライフサイクルは、どちらも自分では触らず帳簿へ渡す。
        触れる者が増えるほど、保有列の並び（走査順＝反映順・byte 依存）を保証する主体が
        曖昧になる。
        """
        allowed = {"trade_ledger", "order_execution"}
        touched = {
            key: sorted(
                set(_attribute_targets(_tree(key), "open_positions"))
                | set(_attribute_targets(_tree(key), "margin"))
            )
            for key in _MODULES
        }
        # 正の対照: 許された 2 者は実際に触っている。触っていなければ検出規則そのものが
        #   効いていない（属性名を書き間違えた検出器は「違反 0」を無条件に主張する）。
        for owner in sorted(allowed):
            assert touched[owner], f"{owner} が口座を触っていない（検出規則が空振り）"
        offenders = {k: v for k, v in touched.items() if k not in allowed and v}
        assert not offenders, f"口座の保有列／証拠金を第三者が触っている: {offenders}"

    def test_the_equity_series_is_appended_by_the_margin_guard_only(self):
        """equity 系列へ追記するのが証拠金監視だけであること。

        「評価点 1 つにつき equity ちょうど 1 点」は equity 系 stats（系列長・最大
        ドローダウン）の土台である。追記者が散れば、この 1:1 を構造からは保てなくなる。
        """
        # run ライフサイクルは終了段で**読む**（結果 DTO へ載せる）ため参照は残る。
        # 禁じるのは追記（`.append`）だけである。
        appended = {key: _equity_appends(_tree(key)) for key in _MODULES}
        assert appended["margin_guard"], (
            "証拠金監視が equity 系列へ追記していない（所有者が空になっている）。"
            " 追記の検出規則そのものが効いていない可能性がある。"
        )
        others = {k: v for k, v in appended.items() if k != "margin_guard" and v}
        assert not others, f"証拠金監視の外で equity 系列へ追記している: {others}"

    def test_the_margin_call_is_raised_by_the_margin_guard_only(self):
        """証拠金割れ例外の送出点が証拠金監視だけであること。"""
        raised = {
            key: [
                lineno
                for name, lineno in _called_names(_tree(key))
                if name == "MarginCallError"
            ]
            for key in _MODULES
        }
        # 正の対照: 所有者は実際に送出している（送出が消えれば run を捨てる経路が死ぬ）。
        assert raised["margin_guard"], "証拠金監視が割れを送出していない（所有者が空）"
        offenders = {k: v for k, v in raised.items() if k != "margin_guard" and v}
        assert not offenders, f"証拠金監視の外で証拠金割れを送出している: {offenders}"

    def test_the_pending_orders_are_owned_by_the_executor_only(self):
        """残存ペンディングの列を持つのが約定執行だけであること。

        設置・トリガ・持ち越し・貼り替え・再アームはすべて約定執行の手続きである。
        run ライフサイクル側に列を持たせると 5 箇所が同じ列を直接書き換える形になり、
        「貼り替えたつもりで消し忘れる」類の食い違いを型で防げない。
        """
        held = {
            key: _attribute_targets(_tree(key), "_resting")
            + _attribute_targets(_tree(key), "resting_pending")
            for key in _MODULES
        }
        # 正の対照: 所有者は実際に列を持っている（属性名が変われば検出規則が空振りする）。
        assert held["order_execution"], "約定執行が残存ペンディングを持っていない"
        offenders = {k: v for k, v in held.items() if k != "order_execution" and v}
        assert not offenders, (
            f"約定執行の外で残存ペンディングを持っている: {offenders}"
        )


class TestTheCollaboratorsDoNotDependOnTheLifecycle:
    """依存の向きが一方向であること（協働クラス → run ライフサイクル の辺が無い）。"""

    @pytest.mark.parametrize(
        "key", [k for k in _MODULES if k != "run_backtest"]
    )
    def test_a_collaborator_never_imports_the_interactor(self, key):
        """協働クラスは Interactor を import しないこと。

        逆流すると、規則の所有者が run の進み方を知ることになり、分割の意味が消える
        （どちらの改訂も両方のファイルを開くことになる）。
        """
        imported = _imported_modules(_tree(key))
        assert "simulator.usecase.run_backtest" not in imported, (
            f"{key} が run ライフサイクルを import している（依存が逆流している）"
        )

    def test_the_interactor_class_defines_only_lifecycle_methods(self):
        """`RunBacktestInteractor` が持つメソッドが run ライフサイクルの 7 つだけであること。

        名前を焼き込むのは、ここが「増えやすい場所」だからである。是正前は 18 メソッドで
        あり、増えるときは必ず「1 つだけなら」で増えた。増やすこと自体を禁じるのではなく、
        **増やすなら宣言を直す**という手続きを踏ませる（宣言と実装が食い違えば赤になる）。
        """
        tree = _tree("run_backtest")
        klass = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "RunBacktestInteractor"
        )
        methods = {
            node.name
            for node in klass.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert methods == {
            "__init__",
            "_session_gate",
            "_begin_run",
            "execute",
            "_make_schedule",
            "_run",
            "_evaluate_point",
            "_finish_run",
        }, sorted(methods)


# ---- 計算量: 分割で発行が増えていないこと（発行 − 使用 = 0） ----

class TestTheSplitDoesNotIssueExtraWork:
    """協働クラスの導入で「作ってから捨てる」形が増えていないこと。

    測るのは 2 つ:
      * 協働クラスの組み立ては run につき 1 組（バー数に非比例）。
      * 帳簿の決済発行 − 確定トレード件数 = 0（記帳したものは全部出力に載る）。
    """

    @staticmethod
    def _interactor(strategy=None):
        from simulator.usecase.run_backtest import RunBacktestInteractor
        from simulator.tests.unit.test_run_backtest_single_engine import (
            _NullIndicators,
            _NullStrategy,
            _OneTickPerBar,
        )

        return RunBacktestInteractor(
            strategy=strategy if strategy is not None else _NullStrategy(),
            indicators=_NullIndicators(),
            tick_model=_OneTickPerBar(),
        )

    @pytest.mark.parametrize("overrides", [{}, {"tick_model": "real_ticks"}])
    def test_the_collaborators_are_built_once_per_run_not_once_per_bar(
        self, overrides, monkeypatch
    ):
        from simulator.tests.unit.test_run_backtest_single_engine import (
            _bars,
            _config,
            _request,
        )

        built: "dict[str, int]" = {}
        for module, class_name in (
            (trade_ledger_module, "TradeLedger"),
            (order_execution_module, "OrderExecutor"),
            (sltp_monitor_module, "SltpMonitor"),
            (margin_guard_module, "MarginGuard"),
            (position_directives_module, "PositionDirectiveApplier"),
        ):
            built[class_name] = 0
            original = getattr(run_backtest_module, class_name)

            def _spy(*args, _name=class_name, _original=original, **kwargs):
                built[_name] += 1
                return _original(*args, **kwargs)

            monkeypatch.setattr(run_backtest_module, class_name, _spy)
            assert getattr(module, class_name) is original  # 同一実体（写しが無い）

        self._interactor().execute(_request(_bars(24), config=_config(**overrides)))
        # 発行（組み立て）− 使用（run が使う 1 組）= 0。
        assert {name: count - 1 for name, count in built.items()} == {
            name: 0 for name in built
        }, built

    @pytest.mark.parametrize("overrides", [{}, {"tick_model": "real_ticks"}])
    def test_the_build_count_does_not_grow_with_the_number_of_bars(
        self, overrides, monkeypatch
    ):
        """バー数 40 / 160 の 2 点で組み立て発行が変わらないこと（オーダーの表明）。"""
        from simulator.tests.unit.test_run_backtest_single_engine import (
            _bars,
            _config,
            _request,
        )

        original = run_backtest_module.OrderExecutor
        measured = {}
        for bar_count in (40, 160):
            built: "list[int]" = []
            monkeypatch.setattr(
                run_backtest_module,
                "OrderExecutor",
                lambda **kw: (built.append(1), original(**kw))[1],
            )
            self._interactor().execute(
                _request(_bars(bar_count), config=_config(**overrides))
            )
            measured[bar_count] = len(built)
        assert measured[160] - measured[40] == 0, measured

    @pytest.mark.parametrize("overrides", [{}, {"tick_model": "real_ticks"}])
    def test_every_ledger_close_lands_in_the_output(self, overrides, monkeypatch):
        """帳簿の決済発行 − 出力に載った確定トレード = 0（記帳して捨てる形が無い）。"""
        from simulator.tests.unit.test_run_backtest_single_engine import (
            _OrdersPerBar,
            _bars,
            _config,
            _market,
            _request,
        )

        issued: "list[int]" = []
        original = trade_ledger_module.TradeLedger.close
        monkeypatch.setattr(
            trade_ledger_module.TradeLedger,
            "close",
            lambda self, ot, **kw: (issued.append(1), original(self, ot, **kw))[1],
        )
        interactor = self._interactor(
            strategy=_OrdersPerBar(
                {0: [_market("buy")], 2: [_market("sell")], 4: [_market("buy")]}
            )
        )
        result = interactor.execute(_request(_bars(8), config=_config(**overrides)))
        assert len(issued) - len(result.trades) == 0, (len(issued), len(result.trades))
        assert len(result.trades) > 0, "決済が 1 件も起きない run では何も測れていない"
