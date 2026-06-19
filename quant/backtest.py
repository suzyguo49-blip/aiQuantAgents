"""回测引擎。

时间逐日回放，严格避免前视偏差：
  - 决策只用到当日收盘(close[T])及之前的数据
  - 成交统一在次日开盘(open[T+1])撮合
  - 计入手续费 + 印花税(卖出) + 滑点
  - 支持单票止损、A股 100 股整手

每个交易日的处理顺序：
  1. 用今日开盘价撮合昨日生成的订单
  2. 用今日收盘价对组合估值，记录净值
  3. 用截至今日收盘的信息生成"明日开盘要执行的订单"（换仓/止损）
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from quant.market_data import MarketData
from quant.factors import composite_score
from quant.strategy import Strategy
from quant import metrics as metrics_mod


@dataclass
class Costs:
    commission: float = 0.0003   # 双边佣金 万3
    stamp_tax: float = 0.0005    # 印花税，仅卖出
    slippage: float = 0.001      # 滑点（开盘价上下浮动）


@dataclass
class Trade:
    date: pd.Timestamp
    symbol: str
    side: str        # buy | sell
    price: float
    shares: int
    amount: float
    reason: str      # rebalance | stop_loss


@dataclass
class BacktestResult:
    equity: pd.Series                 # 策略净值曲线
    benchmark: pd.Series              # 等权全市场基准
    trades: list[Trade]
    metrics: dict
    holdings_history: pd.Series       # 每日持仓只数
    final_holdings: dict
    names: dict = field(default_factory=dict)          # symbol -> 名称
    industries: dict = field(default_factory=dict)     # symbol -> 行业

    def report(self) -> str:
        return metrics_mod.format_report(self.metrics)

    def to_dict(self) -> dict:
        """序列化为前端可用的 JSON（净值/基准/回撤/持仓/交易）。"""
        eq = self.equity
        bench = self.benchmark.reindex(eq.index)
        # 归一化到初始 1.0，便于同图对比
        eq_norm = eq / eq.iloc[0]
        bench_norm = bench / bench.iloc[0]
        drawdown = eq / eq.cummax() - 1.0
        labels = [d.strftime("%Y-%m-%d") for d in eq.index]

        # 最终持仓（带名称/行业）
        final = [
            {"symbol": s, "name": self.names.get(s, s),
             "industry": self.industries.get(s, ""), "shares": sh}
            for s, sh in sorted(self.final_holdings.items())
        ]
        # 最近交易（取末尾若干条）
        recent = [
            {"date": t.date.strftime("%Y-%m-%d"), "symbol": t.symbol,
             "name": self.names.get(t.symbol, t.symbol),
             "side": t.side, "price": round(t.price, 2),
             "shares": t.shares, "reason": t.reason}
            for t in self.trades[-30:]
        ]
        return {
            "labels": labels,
            "equity": [round(x, 4) for x in eq_norm.tolist()],
            "benchmark": [round(x, 4) if x == x else None for x in bench_norm.tolist()],
            "drawdown": [round(x * 100, 2) for x in drawdown.tolist()],
            "holdings_count": [int(x) for x in self.holdings_history.reindex(eq.index).fillna(0).tolist()],
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in self.metrics.items()},
            "final_holdings": final,
            "recent_trades": recent,
            "trade_count": len(self.trades),
        }


class Backtester:
    def __init__(
        self,
        md: MarketData,
        strategy: Strategy,
        initial_capital: float = 1_000_000.0,
        costs: Costs | None = None,
    ):
        self.md = md
        self.strat = strategy
        self.capital0 = initial_capital
        self.costs = costs or Costs()

    def run(self, start: str, end: str | None = None) -> BacktestResult:
        md, strat, c = self.md, self.strat, self.costs
        dates = md.dates
        start_ts = pd.Timestamp(start)
        end_ts = pd.Timestamp(end) if end else dates[-1]

        # 预计算：综合分 + 可交易掩码（全程向量化，仅算一次）
        scores = composite_score(md, strat.factor_config)
        tradable = md.tradable_mask()

        # 回测交易日（含次日撮合，需多留一天）
        bt_dates = dates[(dates >= start_ts) & (dates <= end_ts)]

        cash = self.capital0
        holdings: dict[str, dict] = {}   # symbol -> {shares, entry}
        pending: dict[str, float] = {}   # 次日目标权重；空 dict 表示无换仓
        pending_stops: set[str] = set()  # 次日要止损卖出的标的

        equity_curve: dict[pd.Timestamp, float] = {}
        holding_counts: dict[pd.Timestamp, int] = {}
        trades: list[Trade] = []
        last_rebalance = -10**9

        date_list = list(bt_dates)
        for i, today in enumerate(date_list):
            opens = md.open.loc[today]
            closes = md.close.loc[today]

            # —— 1. 撮合昨日订单（以今日开盘价）——
            if pending_stops or pending:
                # 先处理止损卖出
                for sym in list(pending_stops):
                    if sym in holdings:
                        px = opens.get(sym)
                        if px and not np.isnan(px):
                            cash += self._sell(today, sym, holdings, px, "stop_loss", trades)
                pending_stops.clear()

                # 再处理换仓：先卖出不在目标里的，再按目标买入
                if pending:
                    total_equity = cash + self._holdings_value(holdings, closes)
                    target = pending
                    # 卖出：不在目标中的持仓
                    for sym in list(holdings.keys()):
                        if sym not in target:
                            px = opens.get(sym)
                            if px and not np.isnan(px):
                                cash += self._sell(today, sym, holdings, px, "rebalance", trades)
                    # 买入/调仓到目标权重
                    for sym, w in target.items():
                        px = opens.get(sym)
                        if not px or np.isnan(px):
                            continue
                        target_val = w * total_equity
                        cur_val = holdings.get(sym, {}).get("shares", 0) * px
                        if target_val > cur_val:
                            budget = min(target_val - cur_val, cash)
                            cash -= self._buy(today, sym, holdings, px, budget, trades)
                    pending = {}

            # —— 2. 收盘估值，记录净值 ——
            equity = cash + self._holdings_value(holdings, closes)
            equity_curve[today] = equity
            holding_counts[today] = len(holdings)

            # 最后一天不再生成订单
            if i + 1 >= len(date_list):
                break

            # —— 3. 用今日收盘信息生成"明日订单" ——
            # 3a. 止损检查（持仓相对买入价回撤超阈值）
            for sym, pos in holdings.items():
                cl = closes.get(sym)
                if cl and not np.isnan(cl):
                    if cl <= pos["entry"] * (1 - strat.stop_loss):
                        pending_stops.add(sym)

            # 3b. 换仓（到达调仓周期）
            if i - last_rebalance >= strat.rebalance_days:
                if today in scores.index:
                    target = strat.select(
                        scores.loc[today], tradable.loc[today], md.industries
                    )
                    if target:
                        pending = target
                        last_rebalance = i

        equity = pd.Series(equity_curve).sort_index()
        benchmark = self._benchmark(bt_dates)
        m = metrics_mod.performance(equity, benchmark)
        return BacktestResult(
            equity=equity,
            benchmark=benchmark,
            trades=trades,
            metrics=m,
            holdings_history=pd.Series(holding_counts).sort_index(),
            final_holdings={s: p["shares"] for s, p in holdings.items()},
            names=md.names,
            industries=md.industries,
        )

    # —— 撮合辅助 ——

    def _buy(self, date, sym, holdings, px, budget, trades) -> float:
        """以 px(含滑点) 买入，金额不超过 budget；返回实际花费现金。"""
        fill = px * (1 + self.costs.slippage)
        # 100 股整手
        shares = int(budget / (fill * (1 + self.costs.commission)) // 100 * 100)
        if shares <= 0:
            return 0.0
        gross = shares * fill
        fee = gross * self.costs.commission
        cost = gross + fee
        pos = holdings.get(sym)
        if pos:   # 加仓：更新加权成本
            tot = pos["shares"] + shares
            pos["entry"] = (pos["entry"] * pos["shares"] + fill * shares) / tot
            pos["shares"] = tot
        else:
            holdings[sym] = {"shares": shares, "entry": fill}
        trades.append(Trade(date, sym, "buy", fill, shares, cost, "rebalance"))
        return cost

    def _sell(self, date, sym, holdings, px, reason, trades) -> float:
        """全部卖出 sym；返回回收现金。"""
        pos = holdings.pop(sym)
        shares = pos["shares"]
        fill = px * (1 - self.costs.slippage)
        gross = shares * fill
        fee = gross * (self.costs.commission + self.costs.stamp_tax)
        proceeds = gross - fee
        trades.append(Trade(date, sym, "sell", fill, shares, proceeds, reason))
        return proceeds

    @staticmethod
    def _holdings_value(holdings: dict, closes: pd.Series) -> float:
        val = 0.0
        for sym, pos in holdings.items():
            cl = closes.get(sym)
            if cl and not np.isnan(cl):
                val += pos["shares"] * cl
            else:  # 停牌：按买入价估值
                val += pos["shares"] * pos["entry"]
        return val

    def _benchmark(self, bt_dates) -> pd.Series:
        """等权全市场基准：所有可交易票的等权日收益累乘。"""
        close = self.md.close.loc[bt_dates]
        ret = close.pct_change(fill_method=None)
        eq_ret = ret.mean(axis=1)          # 等权
        nav = (1 + eq_ret.fillna(0)).cumprod() * self.capital0
        return nav
