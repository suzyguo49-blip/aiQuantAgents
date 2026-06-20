"""把 AI 的百分比仓位方案翻译成「今日可执行下单清单」。

纯算术，不调 AI：用真实现价 + 100 股整手 + 你的实际现金，算出每只该买/卖几手。
执行顺序贴近实盘：先卖(回笼现金) → 再买(受可用现金约束，买不下就如实标注)。

注意：金额按现价估算，未计佣金/印花税/滑点；目标股数按"就近整手"取整，
故实际权重会与方案略有出入——这是 A股 100 股整手的固有约束。
"""
from __future__ import annotations

LOT = 100   # A股一手 = 100 股


def _round_lot(shares: float) -> int:
    """就近取整到 100 股整手。"""
    return int(round(shares / LOT)) * LOT


def build_orders(plan: dict, holdings: list[dict], cash: float, prices: dict) -> dict:
    """
    plan:     AI 方案 dict，含 allocations[{symbol,name,target_weight_pct,...}], cash_reserve_pct
    holdings: 当前持仓 [{symbol,name,shares,close}]
    cash:     可用现金
    prices:   {symbol: 现价}（候选 + 持仓合并而来）
    返回 {orders[...], total_assets, cash_after, unpriced[...]}
    """
    hold_shares = {h["symbol"]: h.get("shares", 0) for h in holdings}
    names = {h["symbol"]: h.get("name", h["symbol"]) for h in holdings}

    holdings_val = sum(sh * (prices.get(s) or 0) for s, sh in hold_shares.items())
    total_assets = cash + holdings_val

    # 1) 各标的目标股数（按目标权重 × 总资产 ÷ 现价，就近整手）
    targets: dict[str, int] = {}
    unpriced: list[str] = []
    for a in plan.get("allocations", []):
        s = a.get("symbol")
        if not s:
            continue
        names.setdefault(s, a.get("name", s))
        p = prices.get(s)
        if not p:
            unpriced.append(names.get(s, s))
            continue
        tw = float(a.get("target_weight_pct") or 0)
        targets[s] = _round_lot(tw / 100.0 * total_assets / p)
    # 方案未提到的持仓 → 维持不动（不擅自清仓）
    for s, sh in hold_shares.items():
        targets.setdefault(s, sh)

    def mk(symbol, side, shares, price, note=""):
        return {
            "symbol": symbol, "name": names.get(symbol, symbol),
            "side": side, "shares": shares, "lots": shares // LOT,
            "price": round(price, 2), "amount": round(shares * price, 2),
            "note": note,
        }

    avail = cash
    sells, buys = [], []

    # 2) 先卖出（回笼现金）
    for s, ts in targets.items():
        delta = ts - hold_shares.get(s, 0)
        p = prices.get(s)
        if delta < 0 and p:
            shares = -delta
            avail += shares * p
            note = "清仓" if ts == 0 else "减仓"
            sells.append(mk(s, "卖出", shares, p, note))

    # 3) 再买入（受可用现金约束）
    for s, ts in targets.items():
        delta = ts - hold_shares.get(s, 0)
        p = prices.get(s)
        if delta > 0 and p:
            want = delta
            affordable = int(avail // (p * LOT)) * LOT     # 现金最多买几手
            shares = min(want, affordable)
            is_new = hold_shares.get(s, 0) == 0
            if shares <= 0:
                buys.append(mk(s, "买入", 0, p, "现金不足，无法买入"))
                continue
            avail -= shares * p
            note = ("建仓" if is_new else "加仓")
            if shares < want:
                note += "（现金受限，少于目标）"
            buys.append(mk(s, "买入", shares, p, note))

    return {
        "orders": sells + buys,
        "total_assets": round(total_assets, 2),
        "cash_after": round(avail, 2),
        "unpriced": unpriced,
    }
