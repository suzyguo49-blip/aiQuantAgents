"""市场数据面板。

一次性从 stock_data.db 加载一个日期窗口的全市场数据，透视成
[日期 × 标的] 的矩阵，供因子层向量化计算、回测层逐日回放。

设计要点：
- 只读打开数据库，绝不写主库
- 矩阵对齐同一套 dates/symbols 索引，方便跨字段运算
- 提供"可交易掩码"：剔除停牌(当日无数据)、ST、上市不足等
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config

_FIELDS = [
    "symbol", "trade_date", "open", "high", "low", "close",
    "volume", "amount", "pe_ttm", "pb", "turn", "total_mv", "isST",
]


@dataclass
class MarketData:
    """全市场面板，字段均为 [date × symbol] 的 DataFrame。"""
    dates: pd.DatetimeIndex
    symbols: pd.Index
    close: pd.DataFrame
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    volume: pd.DataFrame
    amount: pd.DataFrame
    pe_ttm: pd.DataFrame
    pb: pd.DataFrame
    turn: pd.DataFrame
    total_mv: pd.DataFrame
    is_st: pd.DataFrame
    names: dict          # symbol -> 名称
    industries: dict     # symbol -> 行业

    def tradable_mask(
        self,
        min_amount_yi: float = 0.5,
        exclude_st: bool = True,
        min_history_days: int = 60,
    ) -> pd.DataFrame:
        """返回 [date × symbol] 的布尔矩阵：True = 当日可纳入选股池。"""
        mask = self.close.notna()                       # 当日有行情（未停牌）
        if exclude_st:
            mask &= (self.is_st.fillna(0) == 0)
        # 流动性：成交额（元）换算成亿元
        mask &= (self.amount.fillna(0) / 1e8) >= min_amount_yi
        # 上市满 N 日：每只票第 min_history_days 个有效收盘之前置 False
        valid_count = self.close.notna().cumsum()
        mask &= valid_count >= min_history_days
        return mask


def _pivot(df: pd.DataFrame, value: str) -> pd.DataFrame:
    return df.pivot(index="trade_date", columns="symbol", values=value)


def load_market_data(start: str, end: str | None = None) -> MarketData:
    """加载 [start, end] 窗口的全市场面板。

    start/end: "YYYY-MM-DD"。为保证 start 当天就能算因子，调用方应把
    start 往前留出足够的 lookback（见 backtest.py）。
    """
    uri = f"file:{config.QUANT_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cond = "trade_date >= ?"
        params: list = [start]
        if end:
            cond += " AND trade_date <= ?"
            params.append(end)
        df = pd.read_sql(
            f"SELECT {', '.join(_FIELDS)} FROM stock_daily WHERE {cond}",
            conn, params=params,
        )
        meta = pd.read_sql(
            "SELECT code AS symbol, code_name AS name, industry FROM stock_basic", conn
        )
    finally:
        conn.close()

    df["trade_date"] = pd.to_datetime(df["trade_date"])
    df = df.sort_values(["trade_date", "symbol"])

    mats = {f: _pivot(df, f) for f in
            ["open", "high", "low", "close", "volume", "amount",
             "pe_ttm", "pb", "turn", "total_mv", "isST"]}

    names = dict(zip(meta["symbol"], meta["name"]))
    industries = dict(zip(meta["symbol"], meta["industry"].fillna("")))

    close = mats["close"]
    return MarketData(
        dates=close.index,
        symbols=close.columns,
        close=close,
        open=mats["open"],
        high=mats["high"],
        low=mats["low"],
        volume=mats["volume"],
        amount=mats["amount"],
        pe_ttm=mats["pe_ttm"],
        pb=mats["pb"],
        turn=mats["turn"],
        total_mv=mats["total_mv"],
        is_st=mats["isST"],
        names=names,
        industries=industries,
    )
