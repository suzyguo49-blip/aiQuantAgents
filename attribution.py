"""归因 Layer A —— 用 journal 历史决策 + A股数据库前向价格,算"系统画像"。

不需要用户记成交:决策日 D 的标的,用数据库里 D 之后 horizon 个交易日的真实涨跌
作为"结果",评价 建仓/加仓/清仓/减仓 的方向命中率、行业与因子组合的前向收益。

诚实口径:
- 前向收益 = 决策方向对不对,**不是**你账户的实际盈亏(那要 Layer B 真实成交)。
- 数据少 / 窗口未到期时大量决策计入 pending,统计需积累后才可信。
"""
from __future__ import annotations

import pandas as pd

HORIZON = 20   # 默认前向窗口(交易日)

# 各动作的"命中"定义:买类盼涨,卖类盼跌
_HIT_UP = {"建仓", "加仓"}
_HIT_DOWN = {"清仓", "减仓"}


def forward_return(md, symbol: str, as_of, horizon: int = HORIZON):
    """symbol 在 as_of 之后 horizon 个交易日的收益;数据不足/未到期返回 None。"""
    idx = md.close.index
    pos = idx.searchsorted(pd.Timestamp(as_of), side="right") - 1
    if pos < 0:
        return None
    fwd = pos + horizon
    if fwd >= len(idx):
        return None    # 未到期:还没满 horizon 天
    if symbol not in md.close.columns:
        return None
    p0 = md.close.iloc[pos][symbol]
    pn = md.close.iloc[fwd][symbol]
    if pd.isna(p0) or pd.isna(pn) or p0 <= 0:
        return None
    return float(pn / p0 - 1.0)


def _summ(frs: list[float], hit_up: bool) -> dict:
    if not frs:
        return {"n": 0, "hit_rate": None, "avg_fwd": None}
    hits = sum(1 for r in frs if (r > 0) == hit_up)
    return {
        "n": len(frs),
        "hit_rate": round(hits / len(frs), 3),
        "avg_fwd": round(sum(frs) / len(frs), 4),
    }


def attribute(journal: dict, md, horizon: int = HORIZON) -> dict:
    """遍历 journal 快照里的 AI 决策,产出系统画像。"""
    per_action: dict[str, list[float]] = {}
    by_industry: dict[str, list[float]] = {}
    by_combo: dict[str, list[float]] = {}     # 因子组合签名 -> 前向收益(买类)
    evaluated = pending = 0

    for snap in journal.get("snapshots", []):
        as_of = snap.get("data_as_of") or snap.get("date")
        plan = snap.get("plan") or {}
        weights = snap.get("weights") or {}
        combo = " · ".join(f"{k}{v}" for k, v in sorted(weights.items())) or "默认"
        for a in plan.get("allocations", []):
            sym, action = a.get("symbol"), a.get("action")
            if not sym or action == "持有":
                continue
            fr = forward_return(md, sym, as_of, horizon)
            if fr is None:
                pending += 1
                continue
            evaluated += 1
            per_action.setdefault(action, []).append(fr)
            ind = md.industries.get(sym, "未知")
            by_industry.setdefault(ind, []).append(fr)
            if action in _HIT_UP:    # 因子组合只看买类(它决定了你买什么)
                by_combo.setdefault(combo, []).append(fr)

    def block(actions, hit_up):
        frs = [r for act in actions for r in per_action.get(act, [])]
        return _summ(frs, hit_up)

    industry = sorted(
        [{"industry": k, "n": len(v), "avg_fwd": round(sum(v) / len(v), 4)}
         for k, v in by_industry.items()],
        key=lambda d: d["avg_fwd"])
    combo = sorted(
        [{"combo": k, "n": len(v), "avg_fwd": round(sum(v) / len(v), 4)}
         for k, v in by_combo.items()],
        key=lambda d: -d["avg_fwd"])

    return {
        "horizon": horizon,
        "evaluated": evaluated,
        "pending": pending,
        "build": block(["建仓", "加仓"], True),     # 买类:命中=涨
        "clear": block(["清仓", "减仓"], False),    # 卖类:命中=跌(躲对了)
        "by_industry": industry,
        "by_combo": combo,
    }


def summary_line(attr: dict) -> str:
    """浓缩成一句话,可注入 AI prompt。"""
    if attr.get("evaluated", 0) == 0:
        return "（历史样本不足,暂无可信归因）"
    b, c = attr["build"], attr["clear"]
    parts = []
    if b["n"]:
        parts.append(f"建仓命中率{b['hit_rate']:.0%}(均{b['avg_fwd']:+.1%})")
    if c["n"]:
        parts.append(f"清仓命中率{c['hit_rate']:.0%}")
    if attr["by_industry"]:
        worst = attr["by_industry"][0]
        if worst["avg_fwd"] < 0:
            parts.append(f"{worst['industry']}行业前向收益为负({worst['avg_fwd']:+.1%})")
    return "用户历史归因(前向%d日,%d个决策):" % (attr["horizon"], attr["evaluated"]) + " · ".join(parts)
