"""用户真实持仓 + 现金的本地持久化。

存为本地 JSON 文件 portfolio.json（已 gitignore）。含真实财务数据，
仅落在运行机器本地磁盘，不入库、不外传（除非用户主动用截图识别功能）。

数据结构：
    {
      "cash": 100000.0,
      "holdings": [{"symbol": "sh.600519", "shares": 100, "cost": 1680.0}, ...],
      "updated_at": "2026-06-20T15:30:00"
    }

这是实盘 AIStrategy 统筹的输入来源（见 [[ai-portfolio-may-read-news]]）。
"""
from __future__ import annotations

import json
import os
from datetime import datetime

from data_source import DataError, normalize_symbol

_STORE_PATH = os.environ.get(
    "SUSU_PORTFOLIO_PATH",
    os.path.join(os.path.dirname(__file__), "portfolio.json"),
)


def _path() -> str:
    return os.path.abspath(_STORE_PATH)


def load_portfolio() -> dict:
    """读取持仓；文件不存在时返回空组合。"""
    p = _path()
    if not os.path.exists(p):
        return {"cash": 0.0, "holdings": [], "updated_at": None}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_portfolio(cash, holdings: list[dict]) -> dict:
    """校验并保存持仓。symbol 规范化失败会抛 ValueError（带行号便于前端定位）。"""
    try:
        cash_val = round(float(cash), 2)
    except (TypeError, ValueError):
        raise ValueError("现金金额必须是数字")
    if cash_val < 0:
        raise ValueError("现金金额不能为负")

    clean: list[dict] = []
    for i, h in enumerate(holdings, 1):
        raw = str(h.get("symbol", "")).strip()
        if not raw:
            continue  # 跳过空行
        try:
            sym = normalize_symbol(raw)
        except DataError:
            # 非标准 A股代码(ETF/港股等)原样保存，后续统筹时按"不在覆盖范围"处理
            sym = raw.lower()
        try:
            shares = int(h.get("shares", 0))
        except (TypeError, ValueError):
            raise ValueError(f"第 {i} 行股数必须是整数：{raw}")
        if shares <= 0:
            raise ValueError(f"第 {i} 行股数必须为正：{raw}")

        entry = {"symbol": sym, "shares": shares}
        cost = h.get("cost")
        if cost not in (None, "", 0, "0"):
            try:
                entry["cost"] = round(float(cost), 3)
            except (TypeError, ValueError):
                raise ValueError(f"第 {i} 行成本价必须是数字：{raw}")
        clean.append(entry)

    data = {
        "cash": cash_val,
        "holdings": clean,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    with open(_path(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data
