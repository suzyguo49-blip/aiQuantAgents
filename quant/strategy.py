"""选股策略：综合分 + 可交易池 -> 目标持仓权重。

把"选哪些票、各配多少仓"的逻辑独立出来，便于回测和实盘共用同一套规则。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.factors import DEFAULT_FACTORS


@dataclass
class Strategy:
    name: str = "multi_factor"
    top_k: int = 10                  # 持仓只数
    rebalance_days: int = 5          # 每 N 个交易日换仓
    weighting: str = "equal"         # equal | score（按分数加权）
    stop_loss: float = 0.10          # 单票止损（相对买入价回撤 10% 离场）
    max_per_industry: int = 3        # 单行业最多持仓数（分散风险）
    factor_config: dict = field(default_factory=lambda: dict(DEFAULT_FACTORS))

    def select(
        self,
        scores: pd.Series,           # 当日各标的综合分
        tradable: pd.Series,         # 当日可交易布尔
        industries: dict,
    ) -> dict[str, float]:
        """返回 {symbol: 目标权重}，权重和为 1（满仓）。"""
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
