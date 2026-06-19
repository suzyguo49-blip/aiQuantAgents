"""Flask web 服务入口。"""
import json
import queue
import threading
from collections import defaultdict
from datetime import date

import pandas as pd
from flask import Flask, request, jsonify, Response, stream_with_context

import config
import orchestrator
from data_source import DataError, normalize_symbol, get_basic_info
from quant.market_data import load_market_data
from quant.strategy import Strategy
from quant.backtest import Backtester
from quant import factors

app = Flask(__name__, static_folder="static", static_url_path="")

# 市场数据加载较慢(~15s)，按加载窗口缓存复用
_md_cache: dict = {}

# AI 接口按 IP+日期 计数，超额拒绝，保护通义千问额度
_ai_calls: dict = defaultdict(int)


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


def _check_ai_quota() -> bool:
    """返回 True 表示该 IP 今日 AI 配额已用尽。"""
    key = (_client_ip(), date.today().isoformat())
    if _ai_calls[key] >= config.DAILY_AI_LIMIT_PER_IP:
        return True
    _ai_calls[key] += 1
    return False


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


@app.route("/api/select", methods=["POST"])
def select_stocks():
    """按用户自定义的因子权重选股，返回 top-N + 各因子拆解。"""
    data = request.get_json() or {}
    weights = data.get("weights") or {}
    top_k = int(data.get("top_k", 10))
    as_of = data.get("as_of") or None

    cfg = factors.build_factor_config(weights)
    if not cfg:
        return jsonify({"error": "请至少启用一个因子并设置正权重"}), 400

    # 选股只需因子回看窗口，往前留 180 天
    base = pd.Timestamp(as_of) if as_of else pd.Timestamp.today()
    load_start = (base - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    try:
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
        return jsonify({
            "as_of": day.strftime("%Y-%m-%d"),
            "factor_keys": list(cfg.keys()),
            "picks": result,
        })
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

    # 每 IP 每日 AI 调用上限，保护额度
    if _check_ai_quota():
        return jsonify({
            "error": f"今日 AI 分析次数已达上限（每日 {config.DAILY_AI_LIMIT_PER_IP} 次），请明天再试。"
        }), 429

    try:
        symbol = normalize_symbol(raw)
        info = get_basic_info(symbol)
    except DataError as e:
        return jsonify({"error": str(e)}), 400

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
