"""实盘周记存储 —— 因子权重日志 + 每日总资产快照 + 周战绩。

本地 JSON(journal.json，已 gitignore)，含真实财务数据。
- 权重日志：每周定一版因子组合，追加一条，今日策略默认用最新一版
- 总资产快照：每天从券商截图读出的总资产数字
- 周战绩：按自然周聚合快照，算周初→周末收益率，并标注当周所用因子组合

诚实口径：周战绩是整个账户的真实结果(含一切持仓与你的实际操作)，
不是因子组合的干净归因——干净归因由回测负责。入金/出金会扭曲收益率。
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

_PATH = os.environ.get(
    "SUSU_JOURNAL_PATH", os.path.join(os.path.dirname(__file__), "journal.json"))


def _path() -> str:
    return os.path.abspath(_PATH)


def _load() -> dict:
    if not os.path.exists(_path()):
        return {"weight_log": [], "snapshots": []}
    with open(_path(), "r", encoding="utf-8") as f:
        d = json.load(f)
    d.setdefault("weight_log", [])
    d.setdefault("snapshots", [])
    return d


def _save(d: dict) -> None:
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)


def load_journal() -> dict:
    return _load()


def save_weights(weights: dict, note: str = "",
                 top_k: int | None = None,
                 rebalance_days: int | None = None,
                 stop_loss_pct: float | None = None) -> dict:
    """保存本周因子权重 + 配套约束(持仓数/换仓周期/止损%)。同日覆盖。"""
    clean = {k: round(float(v), 3) for k, v in (weights or {}).items() if float(v) > 0}
    if not clean:
        raise ValueError("请至少设置一个正权重的因子")
    d = _load()
    today = date.today().isoformat()
    entry = {"date": today, "weights": clean, "note": (note or "").strip()}
    if top_k is not None:
        entry["top_k"] = int(top_k)
    if rebalance_days is not None:
        entry["rebalance_days"] = int(rebalance_days)
    if stop_loss_pct is not None:
        entry["stop_loss_pct"] = round(float(stop_loss_pct), 2)
    d["weight_log"] = [e for e in d["weight_log"] if e["date"] != today] + [entry]
    d["weight_log"].sort(key=lambda e: e["date"])
    _save(d)
    return entry


def current_weights() -> dict | None:
    """今日策略要用的因子权重 = 最新一版日志；无则返回 None(调用方回退默认)。"""
    d = _load()
    return d["weight_log"][-1]["weights"] if d["weight_log"] else None


def current_constraints() -> dict:
    """返回当前生效的本周约束(top_k/rebalance_days/stop_loss_pct),没存过则空 dict。"""
    d = _load()
    if not d["weight_log"]:
        return {}
    e = d["weight_log"][-1]
    return {k: e[k] for k in ("top_k", "rebalance_days", "stop_loss_pct") if k in e}


def add_snapshot(total_asset, note: str = "", snap_date: str | None = None,
                 weights: dict | None = None,
                 holdings: list | None = None,
                 cash: float | None = None,
                 plan: dict | None = None,
                 orders: list | None = None,
                 trades: list | None = None) -> dict:
    """记录某日快照(同日覆盖)。包含归因复盘所需的完整信息：
    weights  本日今日策略使用的因子组合
    holdings 本日持仓清单 [{symbol,name,shares,close}]
    cash     本日现金
    plan     今日策略输出(allocations/total_risk/cash_reserve_pct)
    orders   AI 给出的下单清单 [{symbol,name,side,action,price,shares,amount,note}]
    trades   今日你实际执行的交易(手动输入或后续补) [{symbol,side,shares,price,reason}]
    """
    try:
        ta = round(float(total_asset), 2)
    except (TypeError, ValueError):
        raise ValueError("总资产必须是数字")
    if ta <= 0:
        raise ValueError("总资产必须为正")
    d = _load()
    day = snap_date or date.today().isoformat()
    entry = {"date": day, "total_asset": ta, "note": (note or "").strip()}
    if cash is not None:
        entry["cash"] = round(float(cash), 2)
    if weights:
        entry["weights"] = weights
    if holdings:
        entry["holdings"] = holdings
    if plan:
        entry["plan"] = plan
    if orders:
        entry["orders"] = orders
    if trades:
        entry["trades"] = trades
    # 同日合并:保留旧字段,新字段覆盖(允许逐步补:先存策略输出,再补实际交易)
    old = next((e for e in d["snapshots"] if e["date"] == day), None)
    if old:
        old.update(entry)
        entry = old
    else:
        d["snapshots"].append(entry)
    d["snapshots"].sort(key=lambda e: e["date"])
    _save(d)
    return entry


def get_lessons() -> str:
    """用户手写的交易铁律(整段文本)。"""
    return _load().get("lessons", "")


def save_lessons(text: str) -> str:
    d = _load()
    d["lessons"] = (text or "").strip()
    _save(d)
    return d["lessons"]


def attach(snap_date: str, **fields) -> dict:
    """给某日快照追加任意字段(同 key 覆盖)，用于补头部元信息(mode/fidelity/data_as_of 等)。"""
    d = _load()
    snap = next((e for e in d["snapshots"] if e["date"] == snap_date), None)
    if not snap:
        raise ValueError(f"该日尚无快照：{snap_date}")
    for k, v in fields.items():
        if v is not None:
            snap[k] = v
    _save(d)
    return snap


def update_trades(snap_date: str, trades: list) -> dict:
    """补记某日实际交易（在策略快照之外手动输入）。日期必须已有快照。"""
    d = _load()
    snap = next((e for e in d["snapshots"] if e["date"] == snap_date), None)
    if not snap:
        raise ValueError(f"该日尚无快照：{snap_date}")
    snap["trades"] = trades or []
    _save(d)
    return snap


def weekly_performance() -> list[dict]:
    """按 ISO 自然周聚合快照，算每周收益率，并附当周所用因子组合。最新周在前。"""
    d = _load()
    snaps = sorted(d["snapshots"], key=lambda e: e["date"])
    weeks: dict = {}
    for s in snaps:
        iso = date.fromisoformat(s["date"]).isocalendar()
        weeks.setdefault((iso[0], iso[1]), []).append(s)

    out = []
    for items in weeks.values():
        items.sort(key=lambda e: e["date"])
        start, end = items[0], items[-1]
        ret = ((end["total_asset"] - start["total_asset"]) / start["total_asset"]
               if start["total_asset"] else 0.0)
        # 当周所用因子：以该周周日为界，取最后一次不晚于周末的权重日志
        # （这样周末复盘时设的权重也算进本周，而非漏掉）
        d0 = date.fromisoformat(start["date"])
        sunday = (d0 + timedelta(days=7 - d0.isoweekday())).isoformat()
        wlog = [w for w in d["weight_log"] if w["date"] <= sunday]
        out.append({
            "week": f"{start['date']} ~ {end['date']}",
            "start_asset": start["total_asset"],
            "end_asset": end["total_asset"],
            "return_pct": round(ret * 100, 2),
            "weights": wlog[-1]["weights"] if wlog else None,
            "n_snapshots": len(items),
        })
    return list(reversed(out))
