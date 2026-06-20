"""Flask web 服务入口。"""
import json
import queue
import threading
from collections import defaultdict
from datetime import date, datetime

import pandas as pd
from flask import Flask, request, jsonify, Response, stream_with_context

import config
import orchestrator
import portfolio_store
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
    if p == "/" or p.endswith((".js", ".html")):
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
    weights = data.get("weights") or {}

    # 用户传了因子权重就用自定义配置，否则用默认
    factor_config = factors.build_factor_config(weights) if weights else None
    if weights and not factor_config:
        return jsonify({"error": "请至少启用一个因子并设置正权重"}), 400

    # 往前留 90 天 lookback，保证起始日因子可算
    load_start = (pd.Timestamp(start) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    try:
        md = _get_market_data(load_start, end)
        strat = Strategy(top_k=top_k, rebalance_days=rebalance_days, stop_loss=stop_loss)
        if factor_config:
            strat.factor_config = factor_config
        result = Backtester(md, strat, initial_capital=1_000_000).run(start, end)
        return jsonify(result.to_dict())
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


@app.route("/api/today/plan", methods=["POST"])
def today_plan():
    """实盘今日：用真实持仓 + 现金 + 选股候选，由 AI 统筹出仓位方案（需管理员密钥）。"""
    gate = _admin_gate()
    if gate:
        return jsonify(gate[0]), gate[1]

    data = request.get_json() or {}
    use_research = bool(data.get("use_research"))
    use_sentiment = bool(data.get("use_sentiment"))
    if use_research or use_sentiment:
        return jsonify({"error": "研报/舆情档尚未接入数据源，当前仅支持纯量化档"}), 400

    pf = portfolio_store.load_portfolio()
    cash = float(pf.get("cash") or 0)
    top_k = int(data.get("top_k", 10))
    # 留空=用因子库默认权重选股
    weights = data.get("weights") or {
        k: v["default_weight"]
        for k, v in factors.FACTOR_LIBRARY.items() if v["default_weight"] > 0
    }

    try:
        sel = _run_selection(weights, top_k, None)

        # 给当前持仓补上名称与最新价（供主管换算市值/处置）
        md_day = sel["as_of"]
        load_start = (pd.Timestamp(md_day) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
        md = _get_market_data(load_start, None)
        closes = md.close.loc[md.dates[-1]]
        holdings = []
        for h in pf.get("holdings", []):
            s = h["symbol"]
            close = closes.get(s)
            holdings.append({
                "symbol": s,
                "name": md.names.get(s, s),
                "shares": h["shares"],
                "close": round(float(close), 2) if close is not None and pd.notna(close) else None,
            })

        strat = AIStrategy(use_research=use_research, use_sentiment=use_sentiment)
        plan = strat.plan(sel["picks"], holdings, cash)
        return jsonify({
            "mode": "纯量化",
            "fidelity": "高",
            "data_as_of": md_day,
            "cash": cash,
            "plan": plan,
        })
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


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
