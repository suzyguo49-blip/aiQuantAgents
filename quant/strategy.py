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
    trim_to_target: bool = False     # 对称再平衡：超配持仓修剪到目标（默认关，保持原行为）
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


@dataclass
class AIStrategy(PositionPolicy):
    """实盘"今日"用的 AI 统筹策略。一次 AI 调用产出目标仓位方案。

    信息开放度开关(量化基底常开)：
        use_research  额外读券商研报/评级(慢变量)
        use_sentiment 额外读新闻/舆情(快变量)
    两者都 False = 纯量化档(回测可信度最高，可与 RuleStrategy 对齐校准)。

    刻意不实现 backtest 用的 select()——AI 不进回测热循环(核心架构原则)。
    实盘统筹走 plan()，候选股的量化因子已算好，直接喂给主管。
    """
    use_research: bool = False
    use_sentiment: bool = False

    def select(self, scores, tradable, industries) -> dict[str, float]:
        raise NotImplementedError(
            "AIStrategy 不进回测热循环。回测请用 RuleStrategy；"
            "实盘今日统筹请调用 plan()。")

    def plan(self, candidates, holdings, cash, progress=None) -> dict:
        # 延迟导入，避免把 dashscope 拉进轻量的回测路径
        import orchestrator
        return orchestrator.run_portfolio(
            candidates, holdings, cash,
            use_research=self.use_research,
            use_sentiment=self.use_sentiment,
            progress=progress,
        )


# 向后兼容别名：旧代码(app.py / backtest.py / run_backtest.py)仍可用 Strategy。
# RuleStrategy 是规范名，Strategy 指向同一个类。
Strategy = RuleStrategy
