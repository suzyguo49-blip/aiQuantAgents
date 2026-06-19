# aiQuantAgents

SUSU 的 A股量化系统 —— 多 Agent AI 研究 + 多因子选股 + 策略回测，三合一。

## 能力
- **个股 AI 研究**：4 个分析师(技术/基本面/资金/风险)并行 → 多空辩论 → 首席综合，给出研究观点或次日买卖计划
- **选股工作台**：自定义因子与权重，全市场打分选 top-N，可一键转 AI 交易计划
- **策略回测**：严格无未来函数(收盘决策、次日开盘成交)，含手续费/印花税/滑点、100股整手、止损，输出净值/回撤/持仓可视化

## 架构要点
- AI 层用阿里云**通义千问**(dashscope)，密钥走环境变量 `DASHSCOPE_API_KEY`
- **AI 不进回测热循环**：全市场扫描只用廉价因子，AI 只深挖最终候选
- 行情数据为外部 SQLite 库(约 4GB)，通过 `config.QUANT_DB_PATH` 引用，不入库

## 运行
```bash
pip install -r requirements.txt
export DASHSCOPE_API_KEY=sk-...          # 通义千问密钥
export QUANT_DB_PATH=/path/to/stock_data.db
python app.py                            # 访问 http://localhost:5001
```

## 目录
- `app.py` —— Flask 服务入口
- `agents/` —— 多 Agent 分析师与辩论
- `orchestrator.py` —— 多 Agent 编排(research / trading 两种模式)
- `quant/` —— 行情加载、因子、策略、回测、信号、绩效
- `static/` —— 三个前端页面 + SUSU 形象
- `run_backtest.py` / `run_signals.py` —— 命令行入口

> 本系统仅供研究学习，不构成任何投资建议。
