"""因子库。

每个因子输入 MarketData 面板，输出 [date × symbol] 的原始因子值，
统一约定：**数值越大 = 越值得买**（反向因子已在内部取负）。

合成前用 `cross_sectional_zscore` 做横截面标准化，使不同量纲的因子可加权相加。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.market_data import MarketData


# —— 单因子 ——

def momentum(md: MarketData, lookback: int = 60, skip: int = 5) -> pd.DataFrame:
    """动量：过去 lookback 日收益，剔除最近 skip 日（避开短期反转噪声）。"""
    past = md.close.shift(skip)
    base = md.close.shift(lookback)
    return past / base - 1.0


def trend(md: MarketData, ma: int = 20) -> pd.DataFrame:
    """趋势：现价相对 N 日均线的偏离（站上均线为正）。"""
    ma_line = md.close.rolling(ma).mean()
    return md.close / ma_line - 1.0


def low_volatility(md: MarketData, lookback: int = 20) -> pd.DataFrame:
    """低波动：过去 lookback 日收益率波动的负值（越稳越高分）。"""
    ret = md.close.pct_change(fill_method=None)
    vol = ret.rolling(lookback).std()
    return -vol


def value_pe(md: MarketData) -> pd.DataFrame:
    """价值（PE）：PE(TTM) 倒数，越便宜越高分；亏损股(PE<=0)给极低分。"""
    pe = md.pe_ttm.where(md.pe_ttm > 0)   # 负/零 PE -> NaN
    score = 1.0 / pe
    return score


def value_pb(md: MarketData) -> pd.DataFrame:
    """价值（PB）：PB 倒数，越便宜越高分。"""
    pb = md.pb.where(md.pb > 0)
    return 1.0 / pb


def reversal(md: MarketData, lookback: int = 5) -> pd.DataFrame:
    """短期反转：过去 lookback 日收益的负值（短期跌多了反弹概率）。"""
    return -(md.close / md.close.shift(lookback) - 1.0)


def liquidity(md: MarketData, lookback: int = 20) -> pd.DataFrame:
    """流动性：过去 lookback 日平均成交额（亿元），高流动性更易进出。"""
    return (md.amount.rolling(lookback).mean()) / 1e8


# —— 横截面标准化与合成 ——

def cross_sectional_zscore(factor: pd.DataFrame) -> pd.DataFrame:
    """逐日横截面 z-score；缺失值留 NaN（合成时按可用因子平均）。"""
    mean = factor.mean(axis=1)
    std = factor.std(axis=1)
    z = factor.sub(mean, axis=0).div(std.replace(0, np.nan), axis=0)
    return z.clip(-3, 3)   # 截断极端值，防止单票主导


# —— 因子注册表：前端据此渲染可勾选/调权重的因子清单 ——
# 每项：key -> {fn, label, desc, default_weight}
FACTOR_LIBRARY: dict[str, dict] = {
    "momentum": {
        "fn": lambda md: momentum(md, 60, 5),
        "label": "动量", "desc": "过去60日涨幅(剔除最近5日)，追强势股",
        "default_weight": 0.30,
    },
    "trend": {
        "fn": lambda md: trend(md, 20),
        "label": "趋势", "desc": "现价相对MA20偏离，站上均线为正",
        "default_weight": 0.20,
    },
    "low_vol": {
        "fn": lambda md: low_volatility(md, 20),
        "label": "低波动", "desc": "过去20日波动率(负)，偏好走势平稳",
        "default_weight": 0.15,
    },
    "value_pe": {
        "fn": lambda md: value_pe(md),
        "label": "价值(PE)", "desc": "PE(TTM)倒数，越便宜越高分",
        "default_weight": 0.20,
    },
    "value_pb": {
        "fn": lambda md: value_pb(md),
        "label": "价值(PB)", "desc": "PB倒数，破净/低估更高分",
        "default_weight": 0.0,
    },
    "reversal": {
        "fn": lambda md: reversal(md, 5),
        "label": "短期反转", "desc": "过去5日跌幅(负)，超跌反弹",
        "default_weight": 0.15,
    },
    "liquidity": {
        "fn": lambda md: liquidity(md, 20),
        "label": "流动性", "desc": "过去20日均成交额(亿)，偏好易进出",
        "default_weight": 0.0,
    },
}

# 默认因子配置：名称 -> (函数, 权重)
DEFAULT_FACTORS = {
    k: (v["fn"], v["default_weight"])
    for k, v in FACTOR_LIBRARY.items() if v["default_weight"] > 0
}


def build_factor_config(weights: dict[str, float]) -> dict:
    """前端传入 {因子名: 权重} -> 合成所需的 {名: (函数, 权重)}。忽略权重<=0 与未知因子。"""
    cfg = {}
    for name, w in weights.items():
        if name in FACTOR_LIBRARY and w and w > 0:
            cfg[name] = (FACTOR_LIBRARY[name]["fn"], float(w))
    return cfg


def composite_score(md: MarketData, factor_config: dict | None = None) -> pd.DataFrame:
    """按权重合成多因子综合分（[date × symbol]，越高越好）。"""
    cfg = factor_config or DEFAULT_FACTORS
    total = None
    weight_sum = 0.0
    for name, (fn, w) in cfg.items():
        z = cross_sectional_zscore(fn(md))
        contrib = z * w
        total = contrib if total is None else total.add(contrib, fill_value=0.0)
        weight_sum += w
    return total / weight_sum if weight_sum else total


def score_breakdown(md: MarketData, factor_config: dict, date, symbols: list[str]) -> dict:
    """给定标的在某日的各因子 z-score 拆解，供选股结果展示"为何入选"。

    返回 {symbol: {因子名: z值}}。
    """
    out: dict[str, dict] = {s: {} for s in symbols}
    for name, (fn, _w) in factor_config.items():
        z_row = cross_sectional_zscore(fn(md)).loc[date]
        for s in symbols:
            v = z_row.get(s)
            out[s][name] = round(float(v), 2) if v == v else None  # NaN -> None
    return out
