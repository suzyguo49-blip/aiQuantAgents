"""实盘交易信号。

回测验证过的同一套策略，用最新数据生成"今天该买/卖什么"的可执行清单。
与回测共享 factors/strategy，保证"回测即实盘"。

流程：
  最新数据 -> 综合分 -> 选股 top-K -> 对比当前持仓 -> 买入/卖出/保留信号
可选：把买入候选交给 AI 多 Agent 做深度尽调（orchestrator）。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.market_data import MarketData, load_market_data
from quant.factors import composite_score
from quant.strategy import Strategy


@dataclass
class Signal:
    symbol: str
    name: str
    action: str       # buy | sell | hold
    score: float
    close: float
    industry: str
    note: str = ""


def generate_signals(
    strategy: Strategy,
    current_holdings: list[str] | None = None,
    md: MarketData | None = None,
    as_of: str | None = None,
) -> list[Signal]:
    """生成最新交易日的信号清单。

    current_holdings: 当前持仓的 symbol 列表（baostock 格式 sh.600519）
    as_of: 指定信号日，默认用数据最新日
    """
    current = set(current_holdings or [])
    if md is None:
        # 只需算因子的回看窗口，往前留 120 天足够
        load_start = (pd.Timestamp(as_of or "today") - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
        md = load_market_data(load_start, as_of)

    scores = composite_score(md, strategy.factor_config)
    tradable = md.tradable_mask()
    day = md.dates[-1] if as_of is None else pd.Timestamp(as_of)

    target = strategy.select(scores.loc[day], tradable.loc[day], md.industries)
    closes = md.close.loc[day]
    score_row = scores.loc[day]

    signals: list[Signal] = []

    # 买入：在目标里、当前没持有
    for sym in target:
        if sym not in current:
            signals.append(Signal(
                sym, md.names.get(sym, sym), "buy",
                float(score_row.get(sym, float("nan"))),
                float(closes.get(sym, float("nan"))),
                md.industries.get(sym, ""),
                note=f"目标权重 {target[sym]:.1%}",
            ))

    # 卖出：当前持有、不在目标里
    for sym in current:
        if sym not in target:
            signals.append(Signal(
                sym, md.names.get(sym, sym), "sell",
                float(score_row.get(sym, float("nan"))),
                float(closes.get(sym, float("nan"))),
                md.industries.get(sym, ""),
                note="跌出目标组合",
            ))

    # 保留：既持有又在目标里
    for sym in target:
        if sym in current:
            signals.append(Signal(
                sym, md.names.get(sym, sym), "hold",
                float(score_row.get(sym, float("nan"))),
                float(closes.get(sym, float("nan"))),
                md.industries.get(sym, ""),
            ))

    order = {"buy": 0, "sell": 1, "hold": 2}
    signals.sort(key=lambda s: (order[s.action], -s.score if s.score == s.score else 0))
    return signals
