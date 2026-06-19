"""技术面分析师：趋势、均线、量价、支撑压力位。"""
from __future__ import annotations

from agents.base import BaseAnalyst
from data_source import get_kline


class TechnicalAnalyst(BaseAnalyst):
    name = "技术面分析师"
    system = """你是一位专业的 A股技术面分析师。
基于提供的行情数据（收盘价序列、均线、量价、区间高低点），分析：
- 趋势方向（多头/空头/震荡）
- 关键支撑位与压力位
- 均线形态（多头排列/死叉等）
- 量价配合情况
给出明确的技术面结论，但不下买卖指令。语言简洁专业，中文输出，控制在 300 字内。"""

    def fetch_data(self, symbol: str) -> dict:
        return get_kline(symbol, days=20)

    def build_user_prompt(self, symbol: str, k: dict) -> str:
        return (
            f"请分析以下标的的技术面：\n"
            f"标的：{k['name']}（{symbol}）\n"
            f"区间：{k['date_range']}（{k['days']}个交易日）\n"
            f"最新收盘：{k['latest_close']}（当日涨跌 {k['pct_chg_latest']}%）\n"
            f"MA5/MA10/MA20：{k['ma5']} / {k['ma10']} / {k['ma20']}\n"
            f"区间高/低：{k['period_high']} / {k['period_low']}\n"
            f"近20日收盘序列：{k['closes']}\n"
            f"量能趋势：{k['volume_trend']}"
        )


# 模块级单例，供 orchestrator 直接调用
analyst = TechnicalAnalyst()


def analyze(symbol: str) -> str:
    return analyst.analyze(symbol)
