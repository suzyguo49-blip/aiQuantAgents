"""全局配置。"""
import os

# 复用 quant 项目已爬取的历史数据库（4GB，覆盖全 A股日线+估值+资金+风控字段）。
QUANT_DB_PATH = os.environ.get(
    "QUANT_DB_PATH", "/Users/guosixu/Documents/quant/stock_data.db"
)

# 使用阿里云通义千问 API（性价比优于海外大模型）
ORCHESTRATOR_MODEL = "qwen-plus"
ANALYST_MODEL = "qwen-plus"
VL_MODEL = "qwen-vl-max"   # 视觉模型：识别券商持仓截图

# API key 从环境变量读取
# 运行前： export DASHSCOPE_API_KEY=sk-ws-...
API_KEY = os.environ.get("DASHSCOPE_API_KEY")

# ===== 对外开放时的访问控制（保护 AI 额度，防止被陌生人刷爆）=====
# 访问密码：设了它，前端首次访问需输入；留空=不启用门禁（仅本地自用时）
#   开启： export SUSU_ACCESS_PASSWORD=你的口令
ACCESS_PASSWORD = os.environ.get("SUSU_ACCESS_PASSWORD")

# 每个 IP 每天最多触发多少次 AI 分析（最烧钱的接口），超过即拒绝
DAILY_AI_LIMIT_PER_IP = int(os.environ.get("SUSU_DAILY_AI_LIMIT", "20"))

# 管理员密钥：访问 /api/admin/* 接口需用此钥，跟访问口令分开
#   设置： export SUSU_ADMIN_KEY=你的管理密钥
ADMIN_KEY = os.environ.get("SUSU_ADMIN_KEY")

# 非投资建议声明
DISCLAIMER = (
    "⚠️ 本分析由 AI 生成，仅供研究参考，不构成任何投资建议。"
    "数据可能存在延迟或误差，投资有风险，决策需谨慎。"
)
