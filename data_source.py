"""A股数据源：复用 quant 项目的 stock_data.db（真实历史数据）。

库内 symbol 为 baostock 格式（sh.600519 / sz.000001），本模块对外
接受用户习惯的纯代码（600519），内部自动转换。

各分析师按需取用：
    get_kline       -> 技术面（量价、均线）
    get_fundamentals-> 基本面（估值、估值历史分位）
    get_capital_flow-> 资金面（换手、量比、成交额趋势）
    get_risk_data   -> 风控（ST、估值极值、停牌缺口）
"""
from __future__ import annotations

import sqlite3
from functools import lru_cache

import config


class DataError(Exception):
    """标的不存在或数据缺失。"""


def normalize_symbol(symbol: str) -> str:
    """600519 -> sh.600519；000001 -> sz.000001；已带前缀则原样返回。"""
    s = symbol.strip().lower()
    if s.startswith("sh.") or s.startswith("sz."):
        return s
    s = s.split(".")[0]  # 容忍 600519.SH 这类写法
    if s.startswith("6"):
        return f"sh.{s}"
    if s.startswith(("0", "3")):
        return f"sz.{s}"
    if s.startswith(("8", "4")):
        return f"bj.{s}"  # 北交所，库里可能没有
    raise DataError(f"无法识别的股票代码：{symbol}")


def _connect() -> sqlite3.Connection:
    # 只读打开，避免误写 4GB 主库。
    uri = f"file:{config.QUANT_DB_PATH}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


@lru_cache(maxsize=1)
def latest_trade_date() -> str:
    with _connect() as conn:
        row = conn.execute("SELECT MAX(trade_date) AS d FROM stock_daily").fetchone()
    return row["d"]


def _recent_rows(conn: sqlite3.Connection, bs: str, days: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT * FROM stock_daily
        WHERE symbol = ?
        ORDER BY trade_date DESC
        LIMIT ?
        """,
        (bs, days),
    ).fetchall()
    if not rows:
        raise DataError(f"库中无 {bs} 的行情数据（可能非 A股或未收录）")
    return list(reversed(rows))  # 转为时间正序


def get_basic_info(symbol: str) -> dict:
    bs = normalize_symbol(symbol)
    with _connect() as conn:
        row = conn.execute(
            "SELECT code_name, industry, area, market, ipo_date "
            "FROM stock_basic WHERE code = ?",
            (bs,),
        ).fetchone()
    if not row:
        return {"symbol": bs, "name": "未知标的", "industry": "", "area": "", "market": ""}
    return {
        "symbol": bs,
        "name": row["code_name"],
        "industry": row["industry"] or "",
        "area": row["area"] or "",
        "market": row["market"] or "",
        "ipo_date": row["ipo_date"] or "",
    }


def get_kline(symbol: str, days: int = 20) -> dict:
    """技术面：近 N 日量价 + 均线。"""
    bs = normalize_symbol(symbol)
    with _connect() as conn:
        rows = _recent_rows(conn, bs, days)
    closes = [round(r["close"], 2) for r in rows if r["close"] is not None]
    highs = [r["high"] for r in rows if r["high"] is not None]
    lows = [r["low"] for r in rows if r["low"] is not None]
    vols = [r["volume"] for r in rows if r["volume"] is not None]

    def ma(n: int) -> float | None:
        return round(sum(closes[-n:]) / n, 2) if len(closes) >= n else None

    # 量能趋势：近 5 日均量 vs 前 5 日均量
    vol_trend = "数据不足"
    if len(vols) >= 10:
        recent = sum(vols[-5:]) / 5
        prev = sum(vols[-10:-5]) / 5
        if prev > 0:
            chg = (recent - prev) / prev
            vol_trend = "放量" if chg > 0.2 else "缩量" if chg < -0.2 else "平量"

    return {
        "symbol": bs,
        "name": rows[-1]["name"],
        "days": len(closes),
        "date_range": f"{rows[0]['trade_date']} ~ {rows[-1]['trade_date']}",
        "closes": closes,
        "latest_close": closes[-1],
        "period_high": round(max(highs), 2) if highs else None,
        "period_low": round(min(lows), 2) if lows else None,
        "ma5": ma(5),
        "ma10": ma(10),
        "ma20": ma(20),
        "pct_chg_latest": round(rows[-1]["pct_chg"], 2) if rows[-1]["pct_chg"] is not None else None,
        "volume_trend": vol_trend,
    }


def _percentile(conn: sqlite3.Connection, bs: str, column: str, value: float) -> int | None:
    """value 在该标的全部历史 column 中的分位（0-100，越低越便宜）。"""
    if value is None:
        return None
    row = conn.execute(
        f"""
        SELECT
          SUM(CASE WHEN {column} <= ? THEN 1 ELSE 0 END) AS below,
          COUNT(*) AS total
        FROM stock_daily
        WHERE symbol = ? AND {column} IS NOT NULL AND {column} > 0
        """,
        (value, bs),
    ).fetchone()
    if not row or not row["total"]:
        return None
    return round(100 * row["below"] / row["total"])


def get_fundamentals(symbol: str) -> dict:
    """基本面：最新估值 + 估值历史分位 + 业绩改善代理信号。"""
    bs = normalize_symbol(symbol)
    info = get_basic_info(symbol)
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT trade_date, close, pe, pe_ttm, pb, ps_ttm, dv_ttm,
                   total_mv, circ_mv
            FROM stock_daily WHERE symbol = ?
            ORDER BY trade_date DESC LIMIT 1
            """,
            (bs,),
        ).fetchone()
        if not row:
            raise DataError(f"库中无 {bs} 的估值数据")
        pe_pct = _percentile(conn, bs, "pe_ttm", row["pe_ttm"])
        pb_pct = _percentile(conn, bs, "pb", row["pb"])

    pe = row["pe"]
    pe_ttm = row["pe_ttm"]
    # 业绩改善代理（复用 quant 的 C-104 逻辑）：动态 PE < 静态 PE
    earnings_improving = (
        pe_ttm is not None and pe is not None
        and pe_ttm > 0 and pe > 0 and pe_ttm < pe
    )
    return {
        "symbol": bs,
        "name": info["name"],
        "industry": info["industry"],
        "trade_date": row["trade_date"],
        "pe": round(pe, 2) if pe else None,
        "pe_ttm": round(pe_ttm, 2) if pe_ttm else None,
        "pb": round(row["pb"], 2) if row["pb"] else None,
        "ps_ttm": round(row["ps_ttm"], 2) if row["ps_ttm"] else None,
        "dv_ttm": round(row["dv_ttm"], 2) if row["dv_ttm"] else None,
        "total_mv_yi": round(row["total_mv"] / 1e4, 1) if row["total_mv"] else None,  # 万元->亿元
        "circ_mv_yi": round(row["circ_mv"] / 1e4, 1) if row["circ_mv"] else None,
        "pe_ttm_percentile": pe_pct,   # 当前 PE 处于自身历史的百分位
        "pb_percentile": pb_pct,
        "earnings_improving": earnings_improving,
    }


def get_capital_flow(symbol: str, days: int = 20) -> dict:
    """资金面：换手率、量比、成交额趋势。"""
    bs = normalize_symbol(symbol)
    with _connect() as conn:
        rows = _recent_rows(conn, bs, days)
    turns = [r["turn"] for r in rows if r["turn"] is not None]
    amounts = [r["amount"] for r in rows if r["amount"] is not None]
    latest = rows[-1]

    amount_trend = "数据不足"
    if len(amounts) >= 10:
        recent = sum(amounts[-5:]) / 5
        prev = sum(amounts[-10:-5]) / 5
        if prev > 0:
            chg = (recent - prev) / prev
            amount_trend = f"近5日均额较前5日{'增' if chg >= 0 else '减'}{abs(chg) * 100:.0f}%"

    return {
        "symbol": bs,
        "name": latest["name"],
        "date_range": f"{rows[0]['trade_date']} ~ {rows[-1]['trade_date']}",
        "turn_latest": round(latest["turn"], 2) if latest["turn"] is not None else None,
        "turn_avg": round(sum(turns) / len(turns), 2) if turns else None,
        "volume_ratio": round(latest["volume_ratio"], 2) if latest["volume_ratio"] is not None else None,
        "amount_latest_yi": round(latest["amount"] / 1e8, 2) if latest["amount"] else None,  # 元->亿元
        "amount_trend": amount_trend,
    }


def get_risk_data(symbol: str) -> dict:
    """风控：ST、估值极值、连续跌幅、停牌缺口。"""
    bs = normalize_symbol(symbol)
    with _connect() as conn:
        rows = _recent_rows(conn, bs, 20)
        latest = rows[-1]
        pe_pct = _percentile(conn, bs, "pe_ttm", latest["pe_ttm"])

    flags = []
    if latest["isST"]:
        flags.append("ST/退市风险警示股")
    if latest["pe_ttm"] is not None and latest["pe_ttm"] <= 0:
        flags.append(f"PE(TTM)为负（{round(latest['pe_ttm'], 1)}），公司处于亏损状态")
    if pe_pct is not None and pe_pct >= 90:
        flags.append(f"PE(TTM)处于自身历史 {pe_pct}% 高分位，估值偏贵")
    # 近 5 日累计跌幅
    closes = [r["close"] for r in rows if r["close"] is not None]
    if len(closes) >= 6:
        drop = (closes[-1] - closes[-6]) / closes[-6]
        if drop <= -0.15:
            flags.append(f"近5日累计下跌 {abs(drop) * 100:.0f}%，存在加速下行风险")

    return {
        "symbol": bs,
        "name": latest["name"],
        "trade_date": latest["trade_date"],
        "is_st": bool(latest["isST"]),
        "pe_ttm": round(latest["pe_ttm"], 2) if latest["pe_ttm"] is not None else None,
        "pe_ttm_percentile": pe_pct,
        "risk_flags": flags or ["未发现明显的硬性风险信号"],
    }
