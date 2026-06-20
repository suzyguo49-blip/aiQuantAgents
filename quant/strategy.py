"""仓位决策契约 + 规则实现。

把"选哪些票、各配多少仓"(决策 policy)与"怎么撮合下单"(执行 mechanics)解耦：

    决策契约  PositionPolicy.select(scores, tradable, industries) -> {sym: 权重}
                 ├── RuleStrategy   纯规则/数学，进回测热循环，便宜、可复现
                 └── AIStrategy     (后续)实盘"今日"用 AI 统筹，循环外跑一次

两种实现共享同一执行引擎(quant/backtest.py 的 _buy/_sell)，
保证整手/滑点/费用/止损算法完全一致，回测才能尽量贴近实盘。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from quant.factors import DEFAULT_FACTORS


class PositionPolicy(ABC):
    """仓位决策契约。任何实现都要能根据当日信息给出目标持仓权重。"""

    @abstractmethod
    def select(
        self,
        scores: pd.Series,           # 当日各标的综合分
        tradable: pd.Series,         # 当日可交易布尔
        industries: dict,
    ) -> dict[str, float]:
        """返回 {symbol: 目标权重}，权重和为 1（满仓）；空 dict 表示空仓。"""
        ...


@dataclass
class RuleStrategy(PositionPolicy):
    """多因子规则策略：综合分排序 + 行业分散 + 等权/分数加权。

    这是回测与校准用的"可复现内核"。行为与 v1.0-基础版完全一致。
    """
    name: str = "multi_factor"
    top_k: int = 10                  # 持仓只数
    rebalance_days: int = 5          # 每 N 个交易日换仓
    weighting: str = "equal"         # equal | score（按分数加权）
    stop_loss: float = 0.10          # 单票止损（相对买入价回撤 10% 离场）
    max_per_industry: int = 3        # 单行业最多持仓数（分散风险）
    factor_config: dict = field(default_factory=lambda: dict(DEFAULT_FACTORS))

    def select(
        self,
        scores: pd.Series,
        tradable: pd.Series,
        industries: dict,
    ) -> dict[str, float]:
        cand = scores[tradable & scores.notna()].sort_values(ascending=False)

        picked: list[str] = []
        ind_count: dict[str, int] = {}
        for sym in cand.index:
            ind = industries.get(sym, "")
            if ind and ind_count.get(ind, 0) >= self.max_per_industry:
                continue
            picked.append(sym)
            ind_count[ind] = ind_count.get(ind, 0) + 1
            if len(picked) >= self.top_k:
                break

        if not picked:
            return {}

        if self.weighting == "score":
            sub = cand[picked]
            shifted = sub - sub.min() + 1e-6     # 平移到正数
            w = shifted / shifted.sum()
            return w.to_dict()
        # 等权
        weight = 1.0 / len(picked)
        return {s: weight for s in picked}


# 向后兼容别名：旧代码(app.py / backtest.py / run_backtest.py)仍可用 Strategy。
# RuleStrategy 是规范名，Strategy 指向同一个类。
Strategy = RuleStrategy
