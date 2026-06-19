"""实盘信号命令行入口。

用法：
    python run_signals.py                  # 仅输出因子信号（快、免费）
    python run_signals.py --ai             # 对买入候选追加 AI 多 Agent 深度尽调
    python run_signals.py --hold sh.600519,sz.000001   # 传入当前持仓

设计：因子层选股（廉价）-> AI 层只深挖 top 买入候选（昂贵），二者解耦。
"""
from __future__ import annotations

import sys

from quant.strategy import Strategy
from quant.signals import generate_signals

TAG = {"buy": "🟢买入", "sell": "🔴卖出", "hold": "⚪持有"}


def main():
    args = sys.argv[1:]
    use_ai = "--ai" in args
    holdings: list[str] = []
    if "--hold" in args:
        holdings = args[args.index("--hold") + 1].split(",")

    strat = Strategy(top_k=10, rebalance_days=5, stop_loss=0.10)
    print("生成最新交易日信号…\n")
    signals = generate_signals(strat, current_holdings=holdings)

    print("=" * 56)
    print("【今日交易信号】")
    print("=" * 56)
    for s in signals:
        print(f"{TAG[s.action]} {s.name}({s.symbol}) 分={s.score:+.2f} "
              f"收={s.close} [{s.industry}] {s.note}")

    if not use_ai:
        print("\n（加 --ai 可对买入候选做 AI 深度尽调）")
        return

    buys = [s for s in signals if s.action == "buy"]
    if not buys:
        print("\n无买入候选，跳过 AI 尽调。")
        return

    import config
    if not config.API_KEY:
        print("\n未设置 DASHSCOPE_API_KEY，跳过 AI 尽调。")
        return

    import orchestrator
    print("\n" + "=" * 56)
    print(f"【AI 深度尽调】对 {min(3, len(buys))} 只首选买入标的")
    print("=" * 56)
    for s in buys[:3]:   # 只挖前 3，控制成本
        print(f"\n———— {s.name}({s.symbol}) ————")
        try:
            view = orchestrator.run(f"分析 {s.symbol}", s.symbol)
            print(view)
        except Exception as e:
            print(f"（尽调失败：{e}）")


if __name__ == "__main__":
    main()
