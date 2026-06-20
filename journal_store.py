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


def save_weights(weights: dict, note: str = "") -> dict:
    """保存本周因子权重(同一天重复保存则覆盖当天)。"""
    clean = {k: round(float(v), 3) for k, v in (weights or {}).items() if float(v) > 0}
    if not clean:
        raise ValueError("请至少设置一个正权重的因子")
    d = _load()
    today = date.today().isoformat()
    entry = {"date": today, "weights": clean, "note": (note or "").strip()}
    d["weight_log"] = [e for e in d["weight_log"] if e["date"] != today] + [entry]
    d["weight_log"].sort(key=lambda e: e["date"])
    _save(d)
    return entry


def current_weights() -> dict | None:
    """今日策略要用的因子权重 = 最新一版日志；无则返回 None(调用方回退默认)。"""
    d = _load()
    return d["weight_log"][-1]["weights"] if d["weight_log"] else None


def add_snapshot(total_asset, note: str = "", snap_date: str | None = None) -> dict:
    """记录某日总资产快照(同日覆盖)。"""
    try:
        ta = round(float(total_asset), 2)
    except (TypeError, ValueError):
        raise ValueError("总资产必须是数字")
    if ta <= 0:
        raise ValueError("总资产必须为正")
    d = _load()
    day = snap_date or date.today().isoformat()
    entry = {"date": day, "total_asset": ta, "note": (note or "").strip()}
    d["snapshots"] = [e for e in d["snapshots"] if e["date"] != day] + [entry]
    d["snapshots"].sort(key=lambda e: e["date"])
    _save(d)
    return entry


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
