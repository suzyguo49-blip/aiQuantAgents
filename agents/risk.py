"""风控分析师：ST、估值极值、加速下行等硬性风险排查。"""
from __future__ import annotations

from agents.base import BaseAnalyst
from data_source import get_risk_data


class RiskAnalyst(BaseAnalyst):
    name = "风控分析师"
    system = """你是一位严谨的 A股风控分析师，职责是给投资决策"踩刹车"。
基于提供的硬性风险信号（ST状态、估值极值、连续下跌等），分析：
- 是否存在不可忽视的硬性风险
- 风险的严重程度（高/中/低）
- 对持有/参与该标的的警示建议
立场偏保守，宁可多提示风险。中文输出，控制在 250 字内。"""

    def fetch_data(self, symbol: str) -> dict:
        return get_risk_data(symbol)

    def build_user_prompt(self, symbol: str, r: dict) -> str:
        flags = "\n".join(f"  - {f}" for f in r["risk_flags"])
        return (
            f"请对以下标的做风控排查：\n"
            f"标的：{r['name']}（{symbol}）\n"
            f"数据日期：{r['trade_date']}\n"
            f"是否ST：{'是' if r['is_st'] else '否'}\n"
            f"PE(TTM)：{r['pe_ttm']}（历史分位 {r['pe_ttm_percentile']}%）\n"
            f"系统检测到的风险信号：\n{flags}"
        )


analyst = RiskAnalyst()


def analyze(symbol: str) -> str:
    return analyst.analyze(symbol)
