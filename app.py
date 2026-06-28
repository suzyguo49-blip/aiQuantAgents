"""Flask web 服务入口。"""
import json
import math
import queue
import threading
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
from flask import Flask, request, jsonify, Response, stream_with_context

import config
import orchestrator
import orders as orders_mod
import portfolio_store
import journal_store
from data_source import DataError, normalize_symbol, get_basic_info
from quant.market_data import load_market_data
from quant.strategy import Strategy, AIStrategy
from quant.backtest import Backtester
from quant import factors

app = Flask(__name__, static_folder="static", static_url_path="")
# 前端 JS/HTML 频繁迭代（且常经 ngrok 分享），关掉静态文件缓存，避免浏览器用旧版
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0


@app.after_request
def _no_cache_assets(resp):
    p = request.path
    if p == "/" or p.endswith((".js", ".html", ".css")):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


# 市场数据加载较慢(~15s)，按加载窗口缓存复用
_md_cache: dict = {}

# AI 接口访问日志：{(ip, date_str): [{'time': ..., 'symbol': ..., 'ok': bool}, ...]}
_ai_log: dict = defaultdict(list)


def _client_ip() -> str:
    """取真实客户端 IP（内网穿透/反代后真实 IP 在 X-Forwarded-For）。"""
    fwd = request.headers.get("X-Forwarded-For", "")
    return fwd.split(",")[0].strip() if fwd else (request.remote_addr or "unknown")


@app.before_request
def _gate():
    """对所有 /api/ 接口做访问密码校验（未设密码则放行，本地自用不受影响）。"""
    if not config.ACCESS_PASSWORD:
        return
    if not request.path.startswith("/api/"):
        return
    if request.path == "/api/auth_status":  # 门禁状态查询本身放行
        return
    supplied = request.headers.get("X-Access-Password") or request.args.get("pw")
    if supplied != config.ACCESS_PASSWORD:
        return jsonify({"error": "访问口令错误或未提供", "auth_required": True}), 401


def _check_ai_quota(symbol: str = "") -> bool:
    """返回 True 表示该 IP 今日 AI 配额已用尽，同时记录日志。"""
    key = (_client_ip(), date.today().isoformat())
    log_entry = {"time": datetime.now().isoformat(), "symbol": symbol}

    if len([e for e in _ai_log[key] if e.get("ok")]) >= config.DAILY_AI_LIMIT_PER_IP:
        log_entry["ok"] = False
        _ai_log[key].append(log_entry)
        return True

    log_entry["ok"] = True
    _ai_log[key].append(log_entry)
    return False


def _admin_gate() -> dict | None:
    """检验管理员密钥，返回 None 表示通过；否则返回错误响应。"""
    if not config.ADMIN_KEY:
        return {"error": "管理员功能未启用", "admin_required": True}, 403

    supplied = request.headers.get("X-Admin-Key") or request.args.get("admin_key")
    if supplied != config.ADMIN_KEY:
        return {"error": "管理员密钥错误或未提供", "admin_required": True}, 403

    return None


def _get_market_data(load_start: str, end: str | None):
    key = (load_start, end)
    if key not in _md_cache:
        _md_cache[key] = load_market_data(load_start, end)
    return _md_cache[key]


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/backtest")
def backtest_page():
    return app.send_static_file("backtest.html")


@app.route("/select")
def select_page():
    return app.send_static_file("select.html")


@app.route("/api/auth_status")
def auth_status():
    """前端用来判断是否需要口令、已存口令是否有效（不泄露口令本身）。"""
    required = bool(config.ACCESS_PASSWORD)
    supplied = request.headers.get("X-Access-Password") or request.args.get("pw")
    ok = (not required) or (supplied == config.ACCESS_PASSWORD)
    return jsonify({"required": required, "ok": ok})


@app.route("/api/factors")
def list_factors():
    """返回因子库元信息，供选股界面渲染勾选框/权重滑块。"""
    return jsonify([
        {"key": k, "label": v["label"], "desc": v["desc"],
         "default_weight": v["default_weight"]}
        for k, v in factors.FACTOR_LIBRARY.items()
    ])


def _json_safe(obj):
    """递归把 NaN/Inf 换成 None —— 标准 JSON 不允许 NaN,否则前端 JSON.parse 会炸。"""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _run_selection(weights: dict, top_k: int, as_of: str | None) -> dict:
    """选股核心：因子打分 → top-N + 各因子拆解。供 /api/select 与今日统筹共用。"""
    cfg = factors.build_factor_config(weights)
    if not cfg:
        raise ValueError("请至少启用一个因子并设置正权重")

    base = pd.Timestamp(as_of) if as_of else pd.Timestamp.today()
    load_start = (base - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    md = _get_market_data(load_start, as_of)
    strat = Strategy(top_k=top_k)
    scores = factors.composite_score(md, cfg)
    tradable = md.tradable_mask()
    day = md.dates[-1] if not as_of else pd.Timestamp(as_of)

    picks = strat.select(scores.loc[day], tradable.loc[day], md.industries)
    syms = list(picks.keys())
    breakdown = factors.score_breakdown(md, cfg, day, syms)
    closes = md.close.loc[day]
    score_row = scores.loc[day]

    result = [{
        "symbol": s,
        "name": md.names.get(s, s),
        "industry": md.industries.get(s, ""),
        "score": round(float(score_row.get(s)), 3),
        "close": round(float(closes.get(s)), 2),
        "weight": round(picks[s], 4),
        "factors": breakdown[s],
    } for s in syms]
    return {
        "as_of": day.strftime("%Y-%m-%d"),
        "factor_keys": list(cfg.keys()),
        "picks": result,
    }


@app.route("/api/select", methods=["POST"])
def select_stocks():
    """按用户自定义的因子权重选股，返回 top-N + 各因子拆解。"""
    data = request.get_json() or {}
    try:
        return jsonify(_run_selection(
            data.get("weights") or {},
            int(data.get("top_k", 10)),
            data.get("as_of") or None,
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/backtest", methods=["POST"])
def backtest():
    data = request.get_json() or {}
    start = data.get("start") or "2025-06-01"
    end = data.get("end") or None
    top_k = int(data.get("top_k", 10))
    rebalance_days = int(data.get("rebalance_days", 5))
    stop_loss = float(data.get("stop_loss", 0.10))
    trim_to_target = bool(data.get("trim_to_target"))
    weights = data.get("weights") or {}

    # 用户传了因子权重就用自定义配置，否则用默认
    factor_config = factors.build_factor_config(weights) if weights else None
    if weights and not factor_config:
        return jsonify({"error": "请至少启用一个因子并设置正权重"}), 400

    # 往前留 90 天 lookback，保证起始日因子可算
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        md = _get_market_data(load_start, end)
        strat = Strategy(top_k=top_k, rebalance_days=rebalance_days, stop_loss=stop_loss,
                         trim_to_target=trim_to_target)
        if factor_config:
            strat.factor_config = factor_config
        result = Backtester(md, strat, initial_capital=1_000_000).run(start, end)
        return jsonify(_json_safe(result.to_dict()))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyze", methods=["POST"])
def analyze():
    data = request.get_json()
    raw = (data.get("symbol") or "").strip()
    # mode: research（研究观点）| trading（次日买卖点）
    mode = data.get("mode", "research")
    if not raw:
        return jsonify({"error": "请输入股票代码"}), 400

    try:
        symbol = normalize_symbol(raw)
        info = get_basic_info(symbol)
    except DataError as e:
        return jsonify({"error": str(e)}), 400

    # 每 IP 每日 AI 调用上限，保护额度
    if _check_ai_quota(symbol):
        return jsonify({
            "error": f"今日 AI 分析次数已达上限（每日 {config.DAILY_AI_LIMIT_PER_IP} 次），请明天再试。"
        }), 429

    def generate():
        # 用队列把后台线程里的进度桥接到 SSE 流
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                result = orchestrator.run(
                    f"帮我分析 {raw}", symbol,
                    progress=lambda m: q.put(("progress", m)), mode=mode,
                )
                q.put(("result", result))
            except Exception as e:
                q.put(("error", str(e)))
            finally:
                q.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

        located = f"已定位标的：{info['name']}（{symbol}）"
        yield f"data: {json.dumps({'progress': located})}\n\n"
        while True:
            kind, payload = q.get()
            if kind == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            elif kind == "progress":
                yield f"data: {json.dumps({'progress': payload})}\n\n"
            elif kind == "result":
                yield f"data: {json.dumps({'text': payload})}\n\n"
            elif kind == "error":
                yield f"data: {json.dumps({'error': payload})}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/admin")
def admin_page():
    """管理面板页面。页面本身公开放行；数据接口 /api/admin/* 才校验管理员密钥
    （前端 owner.js 负责带上 X-Admin-Key）。"""
    return app.send_static_file("admin.html")


@app.route("/today")
def today_page():
    """实盘"今日"页面：录入真实持仓 + 现金，后续接 AI 统筹。
    同样页面放行、数据接口 /api/portfolio 校验管理员密钥。"""
    return app.send_static_file("today.html")


@app.route("/api/portfolio", methods=["GET", "POST"])
def portfolio_api():
    """读取/保存用户真实持仓（需管理员密钥，属个人财务数据）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    if request.method == "POST":
        data = request.get_json() or {}
        try:
            saved = portfolio_store.save_portfolio(
                data.get("cash", 0), data.get("holdings", []))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(saved)

    # GET：读取并尽力补上股票名称，便于核对
    pf = portfolio_store.load_portfolio()
    for h in pf.get("holdings", []):
        try:
            h["name"] = get_basic_info(h["symbol"])["name"]
        except Exception:
            h["name"] = ""
    return jsonify(pf)


def _portfolio_mode_label(use_research: bool, use_sentiment: bool) -> tuple[str, str]:
    """信息开放度组合 -> (模式名, 回测可信度)。量化为基底常开。"""
    if not use_research and not use_sentiment:
        return "纯量化", "高"
    if use_research and not use_sentiment:
        return "量化+研报", "中"
    if use_sentiment and not use_research:
        return "量化+舆情", "中低"
    return "全量(量化+研报+舆情)", "低"


@app.route("/api/portfolio/ocr", methods=["POST"])
def portfolio_ocr():
    """截图识别持仓（需管理员密钥）。只返回识别结果供前端确认，不直接保存。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    data = request.get_json() or {}
    image = data.get("image")
    if not image:
        return jsonify({"error": "未收到图片"}), 400
    try:
        import vision_ocr
        return jsonify(vision_ocr.extract_holdings(image))
    except Exception as e:
        return jsonify({"error": f"识别失败：{e}"}), 500


@app.route("/journal")
def journal_page():
    return app.send_static_file("journal.html")


@app.route("/api/journal", methods=["GET"])
def journal_get():
    """周记全量：权重日志 + 快照 + 周战绩 + 当前生效权重（需管理员密钥）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    j = journal_store.load_journal()
    return jsonify({
        "weight_log": j["weight_log"],
        "snapshots": j["snapshots"],
        "weekly": journal_store.weekly_performance(),
        "current_weights": journal_store.current_weights(),
    })


@app.route("/api/journal/weights", methods=["POST"])
def journal_save_weights():
    """保存本周因子权重（需管理员密钥）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    data = request.get_json() or {}
    try:
        return jsonify(journal_store.save_weights(
            data.get("weights") or {}, data.get("note", ""),
            top_k=data.get("top_k"),
            rebalance_days=data.get("rebalance_days"),
            stop_loss_pct=data.get("stop_loss_pct"),
        ))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/journal/snapshot", methods=["POST"])
def journal_save_snapshot():
    """记录某日总资产快照（需管理员密钥）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    data = request.get_json() or {}
    try:
        return jsonify(journal_store.add_snapshot(
            data.get("total_asset"), data.get("note", ""), data.get("date") or None))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/journal/attribution", methods=["GET"])
def journal_attribution():
    """系统画像:用历史决策 + 数据库前向价格做归因(管理员)。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    import attribution
    horizon = int(request.args.get("horizon", attribution.HORIZON))
    j = journal_store.load_journal()
    snaps = j.get("snapshots", [])
    if not snaps:
        return jsonify({"none": True})
    try:
        start = (pd.Timestamp(min(s["date"] for s in snaps)) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        md = _get_market_data(start, None)
        attr = attribution.attribute(j, md, horizon)
        attr["summary_line"] = attribution.summary_line(attr)
        return jsonify(_json_safe(attr))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/journal/snapshot/trades", methods=["POST"])
def journal_save_trades():
    """补记某日实际执行的交易(管理员)。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    data = request.get_json() or {}
    snap_date = (data.get("date") or "").strip()
    trades = data.get("trades") or []
    if not snap_date:
        return jsonify({"error": "缺少 date"}), 400
    try:
        return jsonify(journal_store.update_trades(snap_date, trades))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/journal/snapshot/ocr", methods=["POST"])
def journal_snapshot_ocr():
    """截图识别总资产（需管理员密钥）。只返回识别结果供确认，不直接保存。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    data = request.get_json() or {}
    if not data.get("image"):
        return jsonify({"error": "未收到图片"}), 400
    try:
        import vision_ocr
        return jsonify(vision_ocr.extract_account_summary(data["image"]))
    except Exception as e:
        return jsonify({"error": f"识别失败：{e}"}), 500


@app.route("/api/today/last", methods=["GET"])
def today_last():
    """返回今天最近一次生成的方案(从周记快照里复原),让页面打开就能看到。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    j = journal_store.load_journal()
    today = date.today().isoformat()
    snap = next((s for s in reversed(j.get("snapshots", [])) if s["date"] == today), None)
    if not snap or not snap.get("plan"):
        return jsonify({"none": True})
    return jsonify({
        "cached_at": snap["date"],
        "data_as_of": snap.get("data_as_of") or snap["date"],
        "cash": snap.get("cash"),
        "weights_source": "本周组合" if snap.get("weights") else "默认",
        "mode": snap.get("mode") or "纯量化",
        "fidelity": snap.get("fidelity") or "高",
        "plan": snap.get("plan"),
        "orders": {"orders": snap.get("orders", [])},
        "uncovered": [],
        "snapshot": {"total_asset": snap["total_asset"], "note": snap.get("note") or ""},
    })


@app.route("/api/today/deep-last", methods=["GET"])
def today_deep_last():
    """返回今天最近一次的深度档方案(复原用),不触发 AI。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]
    j = journal_store.load_journal()
    today = date.today().isoformat()
    snap = next((s for s in reversed(j.get("snapshots", [])) if s["date"] == today), None)
    if not snap or not snap.get("deep_plan"):
        return jsonify({"none": True})
    return jsonify({"plan": snap["deep_plan"], "cached_at": snap["date"]})


@app.route("/api/today/plan", methods=["POST"])
def today_plan():
    """实盘今日：用真实持仓 + 现金 + 选股候选，由 AI 统筹出仓位方案（需管理员密钥）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    data = request.get_json() or {}
    use_research = bool(data.get("use_research"))
    use_sentiment = bool(data.get("use_sentiment"))
    mode, fidelity = _portfolio_mode_label(use_research, use_sentiment)

    pf = portfolio_store.load_portfolio()
    cash = float(pf.get("cash") or 0)
    # 权重优先级：请求传入 > 周记里存的本周组合 > 因子库默认
    weekly = journal_store.current_weights()
    constraints = journal_store.current_constraints()   # top_k / rebalance_days / stop_loss_pct
    if data.get("weights"):
        weights, weights_source = data["weights"], "自定义"
    elif weekly:
        weights, weights_source = weekly, "本周组合"
    else:
        weights = {k: v["default_weight"]
                   for k, v in factors.FACTOR_LIBRARY.items() if v["default_weight"] > 0}
        weights_source = "默认因子"
    # 持仓数:请求 > 本周约束 > 默认 10
    top_k = int(data.get("top_k") or constraints.get("top_k") or 10)

    try:
        sel = _run_selection(weights, top_k, None)

        # 给当前持仓补上名称与最新价（供主管换算市值/处置）
        md_day = sel["as_of"]
        load_start = (pd.Timestamp(md_day) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        md = _get_market_data(load_start, None)
        closes = md.close.loc[md.dates[-1]]
        # 拆分：A股可统筹(有现价) vs 不在覆盖范围(ETF/港股等，库里无数据)
        covered, uncovered = [], []
        for h in pf.get("holdings", []):
            s = h["symbol"]
            close = closes.get(s)
            if close is not None and pd.notna(close):
                covered.append({
                    "symbol": s, "name": md.names.get(s, s),
                    "shares": h["shares"], "close": round(float(close), 2),
                })
            else:
                uncovered.append({"symbol": s, "shares": h["shares"]})

        strat = AIStrategy(use_research=use_research, use_sentiment=use_sentiment)
        plan = strat.plan(sel["picks"], covered, cash, constraints=constraints or None)

        # 把百分比方案翻译成可执行下单清单（真实现价 + 100股整手 + 实际现金）
        prices = {p["symbol"]: p["close"] for p in sel["picks"]}
        for h in covered:
            prices[h["symbol"]] = h["close"]
        orders = orders_mod.build_orders(plan, covered, cash, prices)

        # 自动写当日完整快照(归因复盘用):资产+本日因子+持仓清单+策略输出+下单清单
        a_share_mv = sum(h["close"] * h["shares"] for h in covered)
        total_asset = round(cash + a_share_mv, 2)
        snapshot_note = "今日策略自动同步"
        if uncovered:
            snapshot_note += f"（不含 {len(uncovered)} 只 ETF/港股）"
        try:
            snap = journal_store.add_snapshot(
                total_asset, snapshot_note,
                weights=weights, cash=cash,
                holdings=[{"symbol": h["symbol"], "name": h["name"],
                           "shares": h["shares"], "close": h["close"]} for h in covered],
                plan={k: plan.get(k) for k in (
                    "total_risk", "cash_reserve_pct", "allocations",
                    "industry_allocation", "info_sources", "risk_note", "sources")},
                orders=orders.get("orders", []) if isinstance(orders, dict) else [],
            )
            # 复原 GET /api/today/last 需要的头部元信息
            journal_store.attach(snap["date"], mode=mode, fidelity=fidelity, data_as_of=md_day)
        except Exception:
            pass   # 写快照失败不应让方案生成失败

        return jsonify({
            "mode": mode,
            "fidelity": fidelity,
            "data_as_of": md_day,
            "cash": cash,
            "weights_source": weights_source,
            "plan": plan,
            "orders": orders,
            "uncovered": uncovered,
            "snapshot": {"total_asset": total_asset, "note": snapshot_note},
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/today/deep-plan", methods=["POST"])
def today_deep_plan():
    """深度档 = 对今日下单清单做 AI 复审：逐单做多Agent博弈 → 复审主管判定是否合理。流式。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    # 必须先有今日策略快照(下单清单),否则没东西可复审
    today = date.today().isoformat()
    j = journal_store.load_journal()
    snap = next((s for s in reversed(j.get("snapshots", [])) if s["date"] == today), None)
    orders = (snap or {}).get("orders") or []
    if not orders:
        return jsonify({"error": "请先点🚀生成今日统筹方案(产生下单清单),再做深度复审"}), 400

    def generate():
        q: queue.Queue = queue.Queue()

        def worker():
            try:
                review = orchestrator.review_orders(
                    orders, progress=lambda m: q.put(("progress", m)))
                review["based_on_date"] = today
                journal_store.attach(today, deep_plan=review, deep_as_of=today)
                q.put(("result", review))
            except Exception as e:
                q.put(("error", str(e)))
            finally:
                q.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()
        while True:
            kind, payload = q.get()
            if kind == "done":
                yield f"data: {json.dumps({'done': True})}\n\n"
                break
            yield f"data: {json.dumps({kind if kind != 'result' else 'plan': payload}, ensure_ascii=False)}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


@app.route("/api/admin/stats")
def admin_stats():
    """统计数据：今日各 IP 的 AI 调用记录。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    today = date.today().isoformat()
    stats = []
    for (ip, day), logs in _ai_log.items():
        if day != today:
            continue
        success = sum(1 for e in logs if e.get("ok"))
        total = len(logs)
        last_time = logs[-1]["time"] if logs else ""
        stats.append({
            "ip": ip,
            "calls": total,
            "quota_used": success,
            "quota_remaining": max(0, config.DAILY_AI_LIMIT_PER_IP - success),
            "last_call": last_time,
            "symbols": [e.get("symbol") for e in logs[:10]],  # 最后 10 次的股票代码
        })

    return jsonify({
        "date": today,
        "total_ips": len(stats),
        "limit_per_ip": config.DAILY_AI_LIMIT_PER_IP,
        "stats": sorted(stats, key=lambda x: x["quota_used"], reverse=True),
    })


if __name__ == "__main__":
    import os
    import sys

    if not config.API_KEY:
        sys.exit("请先设置环境变量： export DASHSCOPE_API_KEY=sk-ws-...")

    # 对外开放时设 SUSU_PUBLIC=1：关闭 debug（debug 模式可被远程执行代码）
    public = os.environ.get("SUSU_PUBLIC") == "1"
    if public and not config.ACCESS_PASSWORD:
        print("⚠️ 警告：已对外开放但未设访问口令，任何人都能消耗你的 AI 额度！")
        print("   建议先： export SUSU_ACCESS_PASSWORD=你的口令")
    print(f"启动模式：{'对外开放(debug 关闭)' if public else '本地开发(debug 开启)'}  "
          f"门禁：{'已开启' if config.ACCESS_PASSWORD else '未开启'}")
    app.run(host="0.0.0.0", port=5001, debug=not public, threaded=True)
