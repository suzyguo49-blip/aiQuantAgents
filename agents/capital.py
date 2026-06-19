"""资金面分析师：换手率、量比、成交额趋势。"""
from __future__ import annotations

from agents.base import BaseAnalyst
from data_source import get_capital_flow


class CapitalAnalyst(BaseAnalyst):
    name = "资金面分析师"
    system = """你是一位专业的 A股资金面分析师。
基于提供的换手率、量比、成交额趋势，分析：
- 资金活跃度（换手率高低、量比是否放大）
- 资金是在流入还是流出（成交额趋势、放量缩量）
- 是否存在主力进出迹象
给出明确的资金面结论（资金流入/流出/观望），不下买卖指令。中文输出，控制在 250 字内。"""

    def fetch_data(self, symbol: str) -> dict:
        return get_capital_flow(symbol, days=20)

    def build_user_prompt(self, symbol: str, c: dict) -> str:
        return (
            f"请分析以下标的的资金面：\n"
            f"标的：{c['name']}（{symbol}）\n"
            f"区间：{c['date_range']}\n"
            f"最新换手率：{c['turn_latest']}%  20日平均换手：{c['turn_avg']}%\n"
            f"最新量比：{c['volume_ratio']}\n"
            f"最新成交额：{c['amount_latest_yi']}亿\n"
            f"成交额趋势：{c['amount_trend']}"
        )


analyst = CapitalAnalyst()


def analyze(symbol: str) -> str:
    return analyst.analyze(symbol)
