"""回测命令行入口。

用法：
    python run_backtest.py                      # 默认参数跑近一年
    python run_backtest.py 2024-06-01 2026-06-18  # 指定区间

回测会自动往前多加载 lookback 天，保证起始日就能算出因子。
"""
from __future__ import annotations

import sys
import time

import pandas as pd

from quant.market_data import load_market_data
from quant.strategy import Strategy
from quant.backtest import Backtester

LOOKBACK_BUFFER_DAYS = 90   # 因子最长 lookback(60) + 余量


def main():
    start = sys.argv[1] if len(sys.argv) > 1 else "2025-06-01"
    end = sys.argv[2] if len(sys.argv) > 2 else None

    # 数据起点往前推，保证起始日因子可算
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=LOOKBACK_BUFFER_DAYS)).strftime("%Y-%m-%d")

    print(f"加载市场数据 [{load_start} ~ {end or '最新'}] …")
    t = time.time()
    md = load_market_data(load_start, end)
    print(f"  加载完成：{len(md.symbols)} 只标的 × {len(md.dates)} 个交易日，耗时 {time.time()-t:.1f}s\n")

    strat = Strategy(top_k=10, rebalance_days=5, weighting="equal", stop_loss=0.10)
    print(f"策略：{strat.name} | 持仓 {strat.top_k} 只 | 每 {strat.rebalance_days} 日换仓 | 止损 {strat.stop_loss:.0%}")
    print(f"因子权重：{ {k: v[1] for k, v in strat.factor_config.items()} }\n")

    bt = Backtester(md, strat, initial_capital=1_000_000)
    print(f"回测区间 [{start} ~ {end or '最新'}] …")
    t = time.time()
    result = bt.run(start, end)
    print(f"  回测完成，耗时 {time.time()-t:.1f}s\n")

    print("=" * 40)
    print("【绩效报告】")
    print(result.report())
    print("=" * 40)
    print(f"\n总交易笔数：{len(result.trades)}")
    print(f"期末持仓：{len(result.final_holdings)} 只")
    print(f"期末净值：{result.equity.iloc[-1]:,.0f}（初始 1,000,000）")


if __name__ == "__main__":
    main()
