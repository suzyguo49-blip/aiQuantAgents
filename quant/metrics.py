"""回测绩效指标。"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 242   # A股年均交易日


def performance(equity: pd.Series, benchmark: pd.Series | None = None) -> dict:
    """根据净值曲线计算核心绩效指标。"""
    equity = equity.dropna()
    if len(equity) < 2:
        return {}
    ret = equity.pct_change().dropna()
    total_return = equity.iloc[-1] / equity.iloc[0] - 1.0
    years = len(equity) / TRADING_DAYS
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1 / years) - 1.0 if years > 0 else 0.0
    ann_vol = ret.std() * np.sqrt(TRADING_DAYS)
    sharpe = (ret.mean() * TRADING_DAYS) / ann_vol if ann_vol > 0 else 0.0

    # 最大回撤及恢复时间
    cummax = equity.cummax()
    drawdown = equity / cummax - 1.0
    max_dd = drawdown.min()
    max_dd_idx = drawdown.idxmin()
    recovery_idx = cummax[cummax.index > max_dd_idx].idxmax() if len(cummax[cummax.index > max_dd_idx]) > 0 else None
    max_dd_recovery_days = (recovery_idx - max_dd_idx).days if recovery_idx else None

    # Sortino(下方波动)
    downside_ret = ret[ret < 0]
    downside_vol = downside_ret.std() * np.sqrt(TRADING_DAYS) if len(downside_ret) > 0 else ann_vol
    sortino = (ret.mean() * TRADING_DAYS) / downside_vol if downside_vol > 0 else 0.0

    # Calmar
    calmar = cagr / abs(max_dd) if max_dd < 0 else 0.0

    # 月胜率
    monthly_ret = equity.resample("M").last().pct_change().dropna()
    win_rate_monthly = (monthly_ret > 0).mean() if len(monthly_ret) > 0 else 0.0

    out = {
        "total_return": total_return,
        "cagr": cagr,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "max_dd_recovery_days": max_dd_recovery_days,
        "calmar": calmar,
        "win_rate_daily": (ret > 0).mean(),
        "win_rate_monthly": win_rate_monthly,
        "days": len(equity),
    }
    if benchmark is not None:
        bench = benchmark.reindex(equity.index).dropna()
        if len(bench) >= 2:
            out["benchmark_return"] = bench.iloc[-1] / bench.iloc[0] - 1.0
            out["excess_return"] = total_return - out["benchmark_return"]
    # 极短窗口(如只有1个收益点)会让 std 等为 NaN —— 非法 JSON,统一清成 0.0
    out = {k: (0.0 if isinstance(v, float) and not math.isfinite(v) else v)
           for k, v in out.items()}
    return out


def format_report(metrics: dict) -> str:
    """把指标字典格式化成可读文本。"""
    if not metrics:
        return "（无足够数据生成绩效报告）"
    pct = lambda x: f"{x * 100:.2f}%"
    lines = [
        f"  累计收益      {pct(metrics['total_return'])}",
        f"  年化收益(CAGR) {pct(metrics['cagr'])}",
        f"  年化波动      {pct(metrics['ann_vol'])}",
        f"  夏普比率      {metrics['sharpe']:.2f}",
        f"  Sortino比率   {metrics.get('sortino', 0):.2f}",
        f"  最大回撤      {pct(metrics['max_drawdown'])}",
        f"  最大回撤恢复  {metrics.get('max_dd_recovery_days', '—')} 天",
        f"  Calmar       {metrics['calmar']:.2f}",
        f"  日胜率        {pct(metrics['win_rate_daily'])}",
        f"  月胜率        {pct(metrics.get('win_rate_monthly', 0))}",
        f"  交易日数      {metrics['days']}",
    ]
    if "benchmark_return" in metrics:
        lines.append(f"  基准收益      {pct(metrics['benchmark_return'])}")
        lines.append(f"  超额收益      {pct(metrics['excess_return'])}")
    return "\n".join(lines)
