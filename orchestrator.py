"""主管 Agent（Orchestrator）—— 多 Agent 研究流水线。

阶段：
  1. 并行调用各专职分析师（技术/基本面/资金/风控）收集多维结论
  2. 多空辩论：多头与空头分别基于上述结论对抗论证
  3. 主管汇总：综合分析师结论 + 多空辩论，给出最终研究观点

新增分析师：在 ANALYSTS 列表里加一个实例即可，会自动并行参与并进入辩论。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from dashscope import Generation

import config
from data_source import DataError, get_basic_info
from agents import debate
from agents.base import BaseAnalyst
from agents.technical import TechnicalAnalyst
from agents.fundamental import FundamentalAnalyst
from agents.capital import CapitalAnalyst
from agents.risk import RiskAnalyst

# 研究团队成员。要扩团队，在这里加分析师实例即可。
ANALYSTS: list[BaseAnalyst] = [
    TechnicalAnalyst(),
    FundamentalAnalyst(),
    CapitalAnalyst(),
    RiskAnalyst(),
]

CHIEF_SYSTEM = f"""你是一位资深 A股投资研究主管，统筹多位专职分析师与多空辩论。
基于下方"各维度分析师结论"和"多空辩论"，输出一份结构化研究观点：

## 一、各维度要点
（技术面 / 基本面 / 资金面 / 风控，每条一句话概括）

## 二、多空交锋
（多头核心理由 vs 空头核心理由，各一两句）

## 三、综合判断
（明确给出：偏多 / 偏空 / 中性，并说明权衡逻辑）

## 四、主要风险提示
（列出最关键的 2-3 条风险）

中文输出，条理清晰。最后必须原样附上以下声明：
{config.DISCLAIMER}"""

# 交易计划模式：输出次日可执行的买卖点
CHIEF_TRADING_SYSTEM = f"""你是一位资深 A股交易主管，统筹多位分析师与多空辩论，
负责把研究结论转化为"次日可执行的交易计划"。基于下方各维度结论和多空辩论，
**结合技术面给出的最新收盘价、均线、区间高低/支撑压力位**，输出：

## 一、结论速览
（一句话：明日操作方向——建议买入 / 观望 / 回避，并给出信心强弱）

## 二、次日交易计划
- **操作建议**：买入 / 观望 / 回避
- **建议买入区间**：给出具体价格区间（参考支撑位、均线，不要凭空捏造）
- **止损位**：具体价格（跌破即离场）
- **目标价/压力位**：具体价格
- **建议仓位**：轻仓 / 半仓 / 重仓（结合风控结论）

## 三、决策依据
（2-3 句话说明为何这样定买卖点，引用各分析师与多空的关键论据）

## 四、风险提示
（列出最关键的 1-2 条风险，及计划失效的信号）

所有价格必须基于技术面给出的真实数据推导，标注"参考价"。中文输出。
最后必须原样附上以下声明：
{config.DISCLAIMER}"""

_CHIEF_PROMPTS = {"research": CHIEF_SYSTEM, "trading": CHIEF_TRADING_SYSTEM}


def _run_analyst(analyst: BaseAnalyst, symbol: str) -> tuple[str, str]:
    try:
        return analyst.name, analyst.analyze(symbol)
    except Exception as e:  # 单个分析师失败不应拖垮整体
        return analyst.name, f"（{analyst.name}分析失败：{e}）"


def run(user_request: str, symbol: str, progress=None, mode: str = "research") -> str:
    """运行完整研究流水线。

    user_request: 原始自然语言请求（用于主管措辞参考）
    symbol: 已解析出的股票代码
    progress: 可选回调 progress(stage_label) 用于前端展示进度
    mode: "research"（结构化研究观点）| "trading"（次日买卖点交易计划）
    """
    def emit(msg: str) -> None:
        if progress:
            progress(msg)

    chief_system = _CHIEF_PROMPTS.get(mode, CHIEF_SYSTEM)

    info = get_basic_info(symbol)
    name = info["name"]

    # —— 阶段 1：并行跑所有分析师 ——
    emit(f"调度 {len(ANALYSTS)} 位分析师并行分析 {name}…")
    with ThreadPoolExecutor(max_workers=len(ANALYSTS)) as pool:
        results = list(pool.map(lambda a: _run_analyst(a, symbol), ANALYSTS))

    reports_text = "\n\n".join(f"【{n}】\n{text}" for n, text in results)

    # —— 阶段 2：多空辩论 ——
    emit("多空辩论：多头与空头基于结论对抗论证…")
    with ThreadPoolExecutor(max_workers=2) as pool:
        bull_fut = pool.submit(debate.bull_case, symbol, name, reports_text)
        bear_fut = pool.submit(debate.bear_case, symbol, name, reports_text)
        bull = bull_fut.result()
        bear = bear_fut.result()

    debate_text = f"【多头观点】\n{bull}\n\n【空头观点】\n{bear}"

    # —— 阶段 3：主管汇总 ——
    final_word = "次日交易计划" if mode == "trading" else "最终研究观点"
    emit(f"研究主管汇总各方观点，形成{final_word}…")
    resp = Generation.call(
        model=config.ORCHESTRATOR_MODEL,
        messages=[
            {"role": "system", "content": chief_system},
            {
                "role": "user",
                "content": (
                    f"用户请求：{user_request}\n"
                    f"标的：{name}（{symbol}） 行业：{info['industry']}\n\n"
                    f"=== 各维度分析师结论 ===\n{reports_text}\n\n"
                    f"=== 多空辩论 ===\n{debate_text}\n\n"
                    f"请汇总成{final_word}。"
                ),
            },
        ],
        api_key=config.API_KEY,
        max_tokens=4000,
    )
    if resp.status_code == 200:
        return resp.output.text
    raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")


# ============ 投资组合统筹（实盘"今日" · AIStrategy 内核）============
# 关键约束：只跑一次 AI；候选股的量化因子已算好，直接喂给主管做配置决策。
# AI 不进回测热循环（见 quant/strategy.py 的 PositionPolicy 契约）。

_PORTFOLIO_INTRO = """你是一位严谨的 A股投资组合经理，负责统筹「今日」的仓位配置。
量化数据(因子 z 分、价格、行业、当前持仓、现金)是你判断的**基底**，始终优先依据它。"""

# 三档信息纪律（量化基底常开；研报/舆情独立叠加，见 [[ai-portfolio-may-read-news]]）
_DISC_QUANT = """
【纪律 · 纯量化】只能依据所给量化数据。**严禁编造或引用任何新闻、研报、传闻、市场情绪**，编造即违规。"""
_DISC_RESEARCH = """
【已开启 · 研报】可联网检索券商研报/机构评级/分析师目标价作参考，引用须注明"(研报)"；
研报仅作量化基底之上的微调，不能颠覆量化结论。"""
_DISC_SENTIMENT = """
【已开启 · 舆情】可联网检索近期新闻/市场情绪作参考，引用须注明"(新闻/舆情)"；
舆情噪声大、时效强，仅作边际参考，不能主导决策。"""

_PORTFOLIO_TASK = """
你的任务：给出今日的目标仓位方案，统筹考虑：
- 因子强弱(综合分/各因子 z 分越高越强)
- 行业分散(避免单一行业过度集中)
- 当前持仓的处置(继续持有/加仓/减仓/清仓)
- 保留合理现金缓冲(不必满仓)

**只输出一个 JSON 对象**(不要任何解释文字、不要 markdown 代码围栏)，结构：
{
  "total_risk": "保守/中性/积极 之一，并附半句理由",
  "cash_reserve_pct": 数字(建议保留现金占总资产百分比),
  "allocations": [
    {
      "symbol": "sh.600519",
      "name": "贵州茅台",
      "action": "建仓/加仓/持有/减仓/清仓 之一",
      "target_weight_pct": 数字(目标仓位占总资产百分比),
      "reason": "一句话，注明依据来源(量化/研报/舆情)"
    }
  ],
  "industry_allocation": {"行业名": 百分比},
  "info_sources": "本次实际参考了哪些信息源(量化/研报/舆情)",
  "risk_note": "1-2句最关键的风险提示"
}
所有 target_weight_pct 加上 cash_reserve_pct 应约等于 100。当前持有但你建议清仓的股票，也要列出且 action=清仓、target_weight_pct=0。"""


def _build_portfolio_system(use_research: bool, use_sentiment: bool) -> str:
    disc = ""
    if not use_research and not use_sentiment:
        disc = _DISC_QUANT
    else:
        if use_research:
            disc += _DISC_RESEARCH
        if use_sentiment:
            disc += _DISC_SENTIMENT
    return _PORTFOLIO_INTRO + disc + "\n" + _PORTFOLIO_TASK


def _strip_json(text: str) -> dict:
    """从模型输出里抠出 JSON 对象，容忍 ```json 围栏与前后赘述。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
    # 退而求其次：截取第一个 { 到最后一个 }
    if not t.lstrip().startswith("{"):
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e != -1:
            t = t[s:e + 1]
    return json.loads(t)


def run_portfolio(
    candidates: list[dict],
    holdings: list[dict],
    cash: float,
    use_research: bool = False,
    use_sentiment: bool = False,
    progress=None,
    constraints: dict | None = None,
) -> dict:
    """投资组合经理统筹今日仓位（一次 AI 调用）。

    candidates: 选股候选 [{symbol,name,industry,score,close,factors{...}}, ...]
    holdings:   当前真实持仓 [{symbol,name,shares,close?}, ...]
    cash:       可用现金
    use_research/use_sentiment: 信息开放度开关（量化基底常开）。开任一档即启用联网检索。
    返回：解析后的仓位方案 dict（附 mode / fidelity / data_as_of 由调用方补充）。
    """
    def emit(m):
        if progress:
            progress(m)

    emit(f"汇总 {len(candidates)} 只候选 + {len(holdings)} 只持仓的量化数据…")

    # 估算总资产，便于主管换算权重
    holdings_val = sum((h.get("close") or 0) * h.get("shares", 0) for h in holdings)
    total_asset = cash + holdings_val

    payload = {
        "可用现金": round(cash, 2),
        "当前持仓市值估算": round(holdings_val, 2),
        "总资产估算": round(total_asset, 2),
        "当前持仓": holdings,
        "选股候选(含因子z分)": candidates,
    }
    if constraints:
        # 把策略回测带来的约束告诉 AI：止损硬约束、行业分散提示。换仓周期是日程层面的，不喂给单次决策。
        cs = []
        if constraints.get("stop_loss_pct") is not None:
            cs.append(f"单票止损线 {constraints['stop_loss_pct']}%：建议持仓相对买入价回撤超过此值即清仓/减仓，对应给出的 risk_note 中要点名最接近止损的标的。")
        if cs:
            payload["本周策略约束(来自策略回测)"] = cs

    use_search = use_research or use_sentiment
    if use_search:
        emit("联网检索研报/舆情中…")
    emit("投资组合经理统筹今日仓位方案…")
    call_kwargs = dict(
        model=config.ORCHESTRATOR_MODEL,
        messages=[
            {"role": "system", "content": _build_portfolio_system(use_research, use_sentiment)},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        api_key=config.API_KEY,
        max_tokens=3000,
        enable_search=use_search,   # 研报/舆情档启用通义千问联网检索
    )
    if use_search:
        # message 格式才会回检索来源(search_info)；forced_search 确保开了开关就真的去搜
        call_kwargs["result_format"] = "message"
        call_kwargs["search_options"] = {"enable_source": True, "forced_search": True}
    resp = Generation.call(**call_kwargs)
    if resp.status_code != 200:
        raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")

    try:
        plan = _strip_json(_resp_text(resp))
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        raise Exception(f"统筹方案解析失败（模型未返回合法 JSON）：{e}")

    plan["sources"] = _extract_sources(resp) if use_search else []
    return plan


def _resp_text(resp) -> str:
    """从 text 格式或 message 格式的返回里取出文本。"""
    out = resp.output
    txt = out.get("text") if hasattr(out, "get") else getattr(out, "text", None)
    if txt:
        return txt
    return out["choices"][0]["message"]["content"]


def _extract_sources(resp) -> list[dict]:
    """从联网检索结果里抽取来源 [{title, url, site}]，容错处理不同返回结构。"""
    out = resp.output
    info = out.get("search_info") if hasattr(out, "get") else getattr(out, "search_info", None)
    if not info:
        return []
    results = info.get("search_results") if hasattr(info, "get") else getattr(info, "search_results", None)
    sources = []
    for r in (results or []):
        get = r.get if hasattr(r, "get") else (lambda k, d=None: getattr(r, k, d))
        url = get("url")
        if url:
            sources.append({"title": get("title") or url, "url": url, "site": get("site_name") or ""})
    return sources


# ============ 次日交易策略（深度博弈 · 逐股多Agent + T+1 条件单）============

_SIGNAL_CHIEF_SYSTEM = """你是一位 A股交易主管。基于各分析师结论与多空辩论，对该股给出"次日可执行"的交易信号。
**只输出一个 JSON 对象**（不要任何解释文字、不要 markdown 代码围栏）：
{
  "bias": "看多/中性/看空",
  "conviction": "高/中/低",
  "buy_price": 数字,        // 建议买入触发价(结合技术面支撑位/现价，元)
  "take_profit": 数字,      // 止盈目标价(元)
  "stop_loss": 数字,        // 止损价(元)
  "view": "一句话：多空博弈后的关键结论"
}
所有价格必须基于技术面给出的真实价位推导，保留 2 位小数。看空时 conviction 仍填看多信心(=低)。"""


def run_trading_signal(symbol: str, name: str | None = None) -> dict:
    """对单只股票跑完整多Agent博弈，返回结构化交易信号 dict。"""
    if not name:
        name = get_basic_info(symbol)["name"]

    with ThreadPoolExecutor(max_workers=len(ANALYSTS)) as pool:
        results = list(pool.map(lambda a: _run_analyst(a, symbol), ANALYSTS))
    reports_text = "\n\n".join(f"【{n}】\n{text}" for n, text in results)

    with ThreadPoolExecutor(max_workers=2) as pool:
        bull = pool.submit(debate.bull_case, symbol, name, reports_text).result()
        bear = pool.submit(debate.bear_case, symbol, name, reports_text).result()
    debate_text = f"【多头观点】\n{bull}\n\n【空头观点】\n{bear}"

    resp = Generation.call(
        model=config.ORCHESTRATOR_MODEL,
        messages=[
            {"role": "system", "content": _SIGNAL_CHIEF_SYSTEM},
            {"role": "user", "content": (
                f"标的：{name}（{symbol}）\n\n"
                f"=== 各维度分析师结论 ===\n{reports_text}\n\n"
                f"=== 多空辩论 ===\n{debate_text}\n\n请给出次日交易信号 JSON。"
            )},
        ],
        api_key=config.API_KEY,
        max_tokens=1200,
    )
    if resp.status_code != 200:
        raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")
    sig = _strip_json(resp.output.text)
    sig["symbol"] = symbol
    sig["name"] = name
    return sig


_DEEP_SYNTHESIS_SYSTEM = """你是一位严谨的 A股投资组合经理。下方给出：可用现金、当前持仓、以及每只股票经"多Agent多空博弈"得到的交易信号(买入价/止盈价/止损价/信心)。
请统筹成一份"次日条件单计划"。

【A股 T+1 铁律】
- 当日买入的股票，当日不可卖出。
- 既有持仓(current_shares>0)：次日可挂卖单(止盈/止损)。
- 新建仓(current_shares=0)：次日只能挂买单；其止盈/止损是"买入成交后(T+2 起)"的目标，sell_timing 标注"今日买入·T+2起"。

【交易单位】买入量、卖出量都用"手"(1 手=100 股)。买入总花费不超过可用现金。卖出手数不得超过现有持仓(current_shares/100)。
【取舍】只对信心足够、风险可控的股给买入；对走弱/看空的既有持仓给卖出(减仓或清仓)；其余持有。

**只输出一个 JSON 对象**（不要任何解释、不要围栏）：
{
  "total_risk": "保守/中性/积极 + 半句理由",
  "cash_reserve_pct": 数字,
  "orders": [
    {
      "symbol": "sh.600519", "name": "贵州茅台", "current_shares": 数字,
      "action": "建仓/加仓/持有/减仓/清仓",
      "buy":        {"price": 数字, "lots": 手数} 或 null,
      "take_profit":{"price": 数字, "lots": 手数} 或 null,
      "stop_loss":  {"price": 数字, "lots": 手数} 或 null,
      "sell_timing": "次日可挂" 或 "今日买入·T+2起" 或 "",
      "view": "一句话依据(引用博弈结论)"
    }
  ],
  "risk_note": "1-2句最关键风险提示"
}
价格保留2位小数。

【orders 必须覆盖以下所有股票，不得静默忽略】
1. 所有"当前持仓"(current_shares>0)：即使建议维持不动，也必须列出 action="持有"、buy/take_profit/stop_loss 全为 null、view 给出"持有逻辑"。
2. 所有"各股交易信号"里出现过的候选(博弈过的)：即使建议放弃，也要列出 action="放弃"、三 leg 全为 null、view 说明"为何不操作"。
既有持仓建议卖出时 action=减仓/清仓 并给 take_profit/stop_loss；新建仓 action=建仓 并给 buy。"""


def run_deep_portfolio(candidates: list[dict], holdings: list[dict], cash: float,
                       top_n: int = 5, progress=None) -> dict:
    """次日交易策略(深度档)：Top-N 候选 + 持仓 逐股多Agent博弈 → 统筹成条件单计划。"""
    def emit(m):
        if progress:
            progress(m)

    # Top-N 候选 + 全部持仓，去重保序
    seen, targets = set(), []
    for c in candidates[:top_n] + holdings:
        s = c["symbol"]
        if s not in seen:
            seen.add(s)
            targets.append((s, c.get("name", s)))

    signals = []
    for i, (sym, nm) in enumerate(targets, 1):
        emit(f"[{i}/{len(targets)}] 多Agent博弈：{nm}（技术/基本面/资金/风控 + 多空）…")
        try:
            signals.append(run_trading_signal(sym, nm))
        except Exception as e:
            signals.append({"symbol": sym, "name": nm, "error": str(e), "view": f"分析失败：{e}"})

    emit("统筹 Agent 汇总成次日条件单计划…")
    hold_map = {h["symbol"]: h for h in holdings}
    payload = {
        "可用现金": round(cash, 2),
        "当前持仓": [{"symbol": h["symbol"], "name": h.get("name"),
                   "current_shares": h.get("shares", 0), "现价": h.get("close")} for h in holdings],
        "各股交易信号(多空博弈后)": [{
            **{k: s.get(k) for k in ("symbol", "name", "bias", "conviction",
                                     "buy_price", "take_profit", "stop_loss", "view")},
            "current_shares": hold_map.get(s["symbol"], {}).get("shares", 0),
        } for s in signals],
    }
    resp = Generation.call(
        model=config.ORCHESTRATOR_MODEL,
        messages=[
            {"role": "system", "content": _DEEP_SYNTHESIS_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        api_key=config.API_KEY,
        max_tokens=3500,
    )
    if resp.status_code != 200:
        raise Exception(f"阿里云 API 错误 {resp.status_code}: {resp.message}")
    plan = _strip_json(_resp_text(resp))
    plan["signals"] = signals          # 附原始逐股信号供展示
    _sanitize_deep_plan(plan, hold_map)
    return plan


def _sanitize_deep_plan(plan: dict, hold_map: dict) -> None:
    """安全校验：卖出手数不得超过现有持仓；T+1 时点标注兜底；AI 漏掉的持仓自动补"持有"行。"""
    plan.setdefault("orders", [])
    listed = set()
    for o in plan["orders"]:
        sym = o.get("symbol")
        listed.add(sym)
        held = hold_map.get(sym, {}).get("shares", 0)
        o["current_shares"] = held
        held_lots = held // 100
        for k in ("take_profit", "stop_loss"):
            leg = o.get(k)
            if leg and isinstance(leg, dict) and leg.get("lots") is not None:
                if held_lots > 0:
                    leg["lots"] = min(int(leg["lots"]), held_lots)
        if not o.get("sell_timing"):
            o["sell_timing"] = "今日买入·T+2起" if held == 0 and o.get("buy") else "次日可挂"

    # 防御性补行：AI 漏列的"现有持仓"统一以"持有(AI 未发表意见)"占位，避免静默丢失
    for sym, h in hold_map.items():
        if sym in listed:
            continue
        plan["orders"].append({
            "symbol": sym, "name": h.get("name", sym),
            "current_shares": h.get("shares", 0),
            "action": "持有",
            "buy": None, "take_profit": None, "stop_loss": None,
            "sell_timing": "次日可挂",
            "view": "⚠️ AI 未对该持仓发表意见(可能因博弈未覆盖)，默认建议保持不动；如需主动决策请去『今日策略』快速档查看。",
        })


if __name__ == "__main__":
    import sys
    from data_source import normalize_symbol

    if not config.API_KEY:
        sys.exit("请先设置环境变量： export DASHSCOPE_API_KEY=sk-ws-...")

    raw = sys.argv[1] if len(sys.argv) > 1 else "600519"
    try:
        sym = normalize_symbol(raw)
    except DataError as e:
        sys.exit(str(e))

    print(f"\n【请求】分析 {raw}\n")
    print("【研究观点】\n")
    print(run(f"帮我分析 {raw}", sym, progress=lambda m: print(f"  · {m}")))
