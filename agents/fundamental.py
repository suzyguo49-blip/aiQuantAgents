"""基本面分析师：估值水平、估值历史分位、业绩改善信号。"""
from __future__ import annotations

from agents.base import BaseAnalyst
from data_source import get_fundamentals


class FundamentalAnalyst(BaseAnalyst):
    name = "基本面分析师"
    system = """你是一位专业的 A股基本面分析师。
基于提供的估值数据，分析：
- 估值水平是否合理（结合 PE/PB/PS、股息率）
- 当前估值在该股自身历史中的分位（分位越低越便宜）
- 业绩改善信号（动态PE < 静态PE 通常意味着近端盈利在改善）
- 结合行业属性给出估值判断
给出明确的基本面结论（低估/合理/高估），不下买卖指令。中文输出，控制在 300 字内。"""

    def fetch_data(self, symbol: str) -> dict:
        return get_fundamentals(symbol)

    def build_user_prompt(self, symbol: str, f: dict) -> str:
        improving = "是（近端盈利改善）" if f["earnings_improving"] else "否"
        return (
            f"请分析以下标的的基本面估值：\n"
            f"标的：{f['name']}（{symbol}） 行业：{f['industry']}\n"
            f"数据日期：{f['trade_date']}\n"
            f"静态PE：{f['pe']}  PE(TTM)：{f['pe_ttm']}  PB：{f['pb']}  PS(TTM)：{f['ps_ttm']}\n"
            f"股息率(TTM)：{f['dv_ttm']}%\n"
            f"总市值：{f['total_mv_yi']}亿  流通市值：{f['circ_mv_yi']}亿\n"
            f"PE(TTM)历史分位：{f['pe_ttm_percentile']}%  PB历史分位：{f['pb_percentile']}%\n"
            f"业绩改善信号（动态PE<静态PE）：{improving}"
        )


analyst = FundamentalAnalyst()


def analyze(symbol: str) -> str:
    return analyst.analyze(symbol)
