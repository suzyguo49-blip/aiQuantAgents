"""全局配置。"""
import os

# 复用 quant 项目已爬取的历史数据库（4GB，覆盖全 A股日线+估值+资金+风控字段）。
QUANT_DB_PATH = os.environ.get(
    "QUANT_DB_PATH", "/Users/guosixu/Documents/quant/stock_data.db"
)

# 使用阿里云通义千问 API（性价比优于海外大模型）
ORCHESTRATOR_MODEL = "qwen-plus"
ANALYST_MODEL = "qwen-plus"

# API key 从环境变量读取
# 运行前： export DASHSCOPE_API_KEY=sk-ws-...
API_KEY = os.environ.get("DASHSCOPE_API_KEY")

# 非投资建议声明
DISCLAIMER = (
    "⚠️ 本分析由 AI 生成，仅供研究参考，不构成任何投资建议。"
    "数据可能存在延迟或误差，投资有风险，决策需谨慎。"
)
