"""多空辩论 agent —— 使用阿里云通义千问 API。"""
from __future__ import annotations

from dashscope import Generation

import config

_BULL_SYSTEM = """你是一位坚定的多头分析师。请基于下方各路分析师的结论，
尽你所能论证"看多/买入"的理由：找出所有支持上涨的证据，给出最乐观但仍站得住脚的逻辑。
不要捏造数据，只能基于给定结论推演。中文输出，控制在 300 字内。"""

_BEAR_SYSTEM = """你是一位谨慎的空头分析师。请基于下方各路分析师的结论，
尽你所能论证"看空/回避"的理由：挑出所有风险与瑕疵，给出最审慎的逻辑。
不要捏造数据，只能基于给定结论推演。中文输出，控制在 300 字内。"""


def _argue(system: str, reports_text: str, name: str, symbol: str) -> str:
    resp = Generation.call(
        model=config.ANALYST_MODEL,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"标的：{name}（{symbol}）\n\n各分析师结论如下：\n{reports_text}",
            },
        ],
        api_key=config.API_KEY,
        max_tokens=1200,
    )
    if resp.status_code == 200:
        return resp.output.text
    raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")


def bull_case(symbol: str, name: str, reports_text: str) -> str:
    return _argue(_BULL_SYSTEM, reports_text, name, symbol)


def bear_case(symbol: str, name: str, reports_text: str) -> str:
    return _argue(_BEAR_SYSTEM, reports_text, name, symbol)
