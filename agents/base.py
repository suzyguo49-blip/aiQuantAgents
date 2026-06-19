"""分析师子 agent 基类 —— 使用阿里云通义千问 API。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from dashscope import Generation

import config


class BaseAnalyst(ABC):
    name: str = "分析师"
    system: str = ""
    max_tokens: int = 1500

    @abstractmethod
    def fetch_data(self, symbol: str) -> dict:
        """从 data_source 取该维度所需数据。"""

    @abstractmethod
    def build_user_prompt(self, symbol: str, data: dict) -> str:
        """把数据拼成发给模型的用户消息。"""

    def analyze(self, symbol: str) -> str:
        data = self.fetch_data(symbol)
        resp = Generation.call(
            model=config.ANALYST_MODEL,
            messages=[
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.build_user_prompt(symbol, data)},
            ],
            api_key=config.API_KEY,
            max_tokens=self.max_tokens,
        )
        if resp.status_code == 200:
            return resp.output.text
        raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")
