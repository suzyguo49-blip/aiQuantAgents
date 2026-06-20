"""截图识别持仓 —— 通义千问视觉模型(qwen-vl-max)。

把券商 App 的持仓截图识别成结构化数据，供「今日策略」页的录入表预填。
识别结果**不直接保存**——前端展示成可编辑表格让用户确认/修正(确认关)，
确认后才走 portfolio_store 的校验与保存。见 [[ai-portfolio-may-read-news]]。
"""
from __future__ import annotations

import base64
import json
import os
import tempfile

from dashscope import MultiModalConversation

import config

_PROMPT = """这是一张股票券商 App 的持仓截图。请仔细识别其中每一只持仓股票，提取：
- 股票代码（6 位数字，如 600519；若截图只有名称没有代码，code 留 null）
- 持仓股数（整数）
- 持仓成本价 / 成本均价（小数；没有就 null）
以及（如果截图里有）可用资金 / 可用余额。

**只输出一个 JSON 对象**，不要任何解释或 markdown 围栏：
{
  "cash": 数字或 null,
  "holdings": [
    {"code": "600519", "shares": 100, "cost": 1680.5}
  ]
}
严格按截图内容识别，看不清的字段填 null，**绝不编造**。"""


def _strip_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.startswith("json"):
            t = t[4:]
    if not t.lstrip().startswith("{"):
        s, e = t.find("{"), t.rfind("}")
        if s != -1 and e != -1:
            t = t[s:e + 1]
    return json.loads(t)


def extract_holdings(image_data_url: str) -> dict:
    """image_data_url: 形如 'data:image/png;base64,xxxx' 的图片。

    返回 {"cash": 数字|None, "holdings": [{"symbol","shares","cost"}...]}。
    symbol 这里仅做 6 位数字提取，规范化(加 sh./sz.)留到保存时由 portfolio_store 做。
    """
    if "," in image_data_url and image_data_url.strip().startswith("data:"):
        header, b64 = image_data_url.split(",", 1)
        ext = "png"
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
    else:
        b64, ext = image_data_url, "png"

    raw = base64.b64decode(b64)
    fd, path = tempfile.mkstemp(suffix=f".{ext}")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
        resp = MultiModalConversation.call(
            model=config.VL_MODEL,
            api_key=config.API_KEY,
            messages=[{
                "role": "user",
                "content": [{"image": f"file://{path}"}, {"text": _PROMPT}],
            }],
        )
    finally:
        os.remove(path)

    if resp.status_code != 200:
        raise Exception(f"视觉模型错误 {resp.status_code}: {resp.message}")

    # 多模态返回 content 是 [{'text': ...}] 列表
    content = resp.output.choices[0].message.content
    text = content[0]["text"] if isinstance(content, list) else str(content)

    try:
        parsed = _strip_json(text)
    except (json.JSONDecodeError, IndexError) as e:
        raise Exception(f"识别结果解析失败（模型未返回合法 JSON）：{e}")

    # 统一字段名 code->symbol，过滤空行
    holdings = []
    for h in parsed.get("holdings", []):
        code = h.get("code") or h.get("symbol")
        if not code:
            continue
        holdings.append({
            "symbol": str(code).strip(),
            "shares": h.get("shares"),
            "cost": h.get("cost"),
        })
    return {"cash": parsed.get("cash"), "holdings": holdings}
