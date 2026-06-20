"""校准命令行：衡量规则版回测有多接近实盘纯量化 AI 统筹。

用法：
    python run_calibration.py                       # 默认抽 3 个近半年的交易日
    python run_calibration.py 2026-01-15 2026-03-20 2026-05-10   # 指定抽样日

每个抽样日 = 一次 AI 调用，注意会消耗通义千问额度。
"""
from __future__ import annotations

import sys

import pandas as pd

import config
from quant.market_data import load_market_data
from quant import calibration


def main():
    if not config.API_KEY:
        sys.exit("请先设置 DASHSCOPE_API_KEY")

    sample = sys.argv[1:]
    if not sample:
        # 默认在近半年里等距抽 3 天
        end = pd.Timestamp.today()
        sample = [(end - pd.Timedelta(days=d)).strftime("%Y-%m-%d") for d in (150, 90, 30)]

    load_start = (pd.Timestamp(min(sample)) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    print(f"加载市场数据 [{load_start} ~ 最新] …")
    md = load_market_data(load_start, None)

    # 把抽样日对齐到真实交易日（往前找最近的）
    dates = pd.DatetimeIndex(md.dates)
    aligned = []
    for d in sample:
        ds = dates[dates <= pd.Timestamp(d)]
        if len(ds):
            aligned.append(ds[-1])
    print(f"抽样交易日：{[d.strftime('%Y-%m-%d') for d in aligned]}\n")

    rep = calibration.calibrate(md, aligned, top_k=10, progress=lambda m: print("  ·", m))
    if "error" in rep:
        sys.exit(rep["error"])

    print("\n" + "=" * 48)
    print("【校准报告：规则版 vs 纯量化 AI】")
    print(f"  抽样日数        {rep['sample_days']}")
    print(f"  平均权重一致度  {rep['avg_weight_agreement']:.1%}   (1=完全一致)")
    print(f"  平均选股重合度  {rep['avg_name_overlap']:.1%}   (AI 保留了规则版多少持仓)")
    print("-" * 48)
    for d in rep["per_day"]:
        print(f"  {d['date']}  权重一致 {d['weight_agreement']:.1%} | "
              f"选股重合 {d['name_overlap']:.1%} | 候选 {d['n_candidates']} 只")
    print("=" * 48)
    print("\n解读：一致度越高，回测越能代表实盘纯量化档；偏低说明 AI 的统筹")
    print("逻辑与机械规则差异较大，可据此调整规则参数(权重/行业上限)向 AI 靠拢。")


if __name__ == "__main__":
    main()
