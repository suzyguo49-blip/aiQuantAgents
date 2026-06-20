"""校准 / 蒸馏 —— 衡量「规则版回测」有多接近「实盘 AI 纯量化统筹」。

做法（见 [[ai-portfolio-may-read-news]]）：
  抽样若干历史交易日 → 每天用同一批候选股(及其因子 z 分)：
    · 规则版 RuleStrategy 给一组目标权重
    · 纯量化 AI(run_portfolio, 两开关都关)给另一组目标权重
  比对两者，得出"吻合度"。吻合度越高，回测越能代表实盘纯量化档。

只用纯量化档校准——研报/舆情是联网的实时信息，非历史时点，天生无法回测对齐。
每个抽样日 = 一次 AI 调用，故默认只抽几天以控成本。
"""
from __future__ import annotations

import pandas as pd

import orchestrator
from quant.factors import build_factor_config, composite_score, score_breakdown, FACTOR_LIBRARY
from quant.strategy import RuleStrategy


def _default_weights() -> dict:
    return {k: v["default_weight"] for k, v in FACTOR_LIBRARY.items() if v["default_weight"] > 0}


def candidates_for_date(md, cfg, day, top_k: int) -> tuple[list[dict], dict]:
    """某交易日的候选股(含因子 z 分) + 规则版目标权重 {sym: w}。"""
    scores = composite_score(md, cfg)
    tradable = md.tradable_mask()
    strat = RuleStrategy(top_k=top_k)
    rule_w = strat.select(scores.loc[day], tradable.loc[day], md.industries)
    syms = list(rule_w.keys())
    breakdown = score_breakdown(md, cfg, day, syms)
    closes = md.close.loc[day]
    score_row = scores.loc[day]
    cands = [{
        "symbol": s,
        "name": md.names.get(s, s),
        "industry": md.industries.get(s, ""),
        "score": round(float(score_row.get(s)), 3),
        "close": round(float(closes.get(s)), 2),
        "factors": breakdown[s],
    } for s in syms]
    return cands, rule_w


def _ai_weights(plan: dict, candidate_syms: list[str]) -> dict:
    """从 AI 方案里取候选股的目标权重，归一到候选集合内(便于与规则版同口径比较)。"""
    raw = {}
    for a in plan.get("allocations", []):
        s = a.get("symbol")
        if s in candidate_syms:
            raw[s] = max(0.0, float(a.get("target_weight_pct") or 0))
    total = sum(raw.values())
    if total <= 0:
        return {s: 0.0 for s in candidate_syms}
    return {s: raw.get(s, 0.0) / total for s in candidate_syms}


def _compare(rule_w: dict, ai_w: dict) -> dict:
    """两组权重(各自归一到候选集)的吻合度指标。"""
    syms = list(rule_w.keys())
    # 权重一致度：1 - 0.5*L1距离，范围 0~1，1=完全一致
    l1 = sum(abs(rule_w.get(s, 0) - ai_w.get(s, 0)) for s in syms)
    weight_agreement = 1 - 0.5 * l1
    # 选股重合度：AI 给了正权重的，占规则版持仓的比例
    rule_set = {s for s in syms if rule_w.get(s, 0) > 0}
    ai_set = {s for s in syms if ai_w.get(s, 0) > 0}
    name_overlap = len(rule_set & ai_set) / len(rule_set) if rule_set else 0.0
    return {
        "weight_agreement": round(weight_agreement, 4),
        "name_overlap": round(name_overlap, 4),
        "n_candidates": len(syms),
    }


def calibrate(md, sample_dates: list, top_k: int = 10, weights: dict | None = None,
              progress=None) -> dict:
    """对若干抽样日做规则 vs 纯量化AI 的权重比对，返回逐日明细 + 平均吻合度。"""
    cfg = build_factor_config(weights or _default_weights())

    def emit(m):
        if progress:
            progress(m)

    per_day = []
    for i, day in enumerate(sample_dates, 1):
        day = pd.Timestamp(day)
        emit(f"[{i}/{len(sample_dates)}] {day.date()} 选股 + AI 统筹…")
        cands, rule_w = candidates_for_date(md, cfg, day, top_k)
        if not cands:
            continue
        # 纯量化档：两开关都关，空持仓、名义满仓
        plan = orchestrator.run_portfolio(cands, [], 1_000_000.0,
                                          use_research=False, use_sentiment=False)
        ai_w = _ai_weights(plan, [c["symbol"] for c in cands])
        cmp = _compare(rule_w, ai_w)
        cmp["date"] = day.strftime("%Y-%m-%d")
        per_day.append(cmp)

    if not per_day:
        return {"error": "无有效抽样日"}
    avg_wa = sum(d["weight_agreement"] for d in per_day) / len(per_day)
    avg_no = sum(d["name_overlap"] for d in per_day) / len(per_day)
    return {
        "sample_days": len(per_day),
        "avg_weight_agreement": round(avg_wa, 4),
        "avg_name_overlap": round(avg_no, 4),
        "per_day": per_day,
    }
