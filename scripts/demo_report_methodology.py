"""演示: 周报路径 generate-from-report 接入 4 阶段方法论后的真实效果。

跑一次真实 LLM 调用, 看 LLM 是否真的产出了 triage_decision / constraints / upstream_downstream
等方法论字段, 以及 purpose 是否合规。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
GRAY = "\033[90m"
BOLD = "\033[1m"
RESET = "\033[0m"


def banner(title: str) -> None:
    print(f"\n{BOLD}{CYAN}{'=' * 70}{RESET}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{BOLD}{CYAN}{'=' * 70}{RESET}")


# 模拟一份运营周报内容
SAMPLE_REPORT = """
# 电商部 2026-W13 运营周报

## 本周业务情况

本周天猫店铺整体 GMV 80 万 (-12% WoW), 主要原因:
- SKU-12345 (热销单品) 上周差评率从 5% 涨到 18%, 转化率掉到 0.4% (低于 1.5% 红线)
- SKU-67890 库存压在仓库 90 天没动, 占用周转资金
- 直通车计划 "618-头部" CTR 0.8% 但 CPC 已经涨到 5.2 元 (历史均值 2.8 元)

## 本周做的事

1. 客服反馈 SKU-12345 差评后, 我们手动下架, 接下来要观察退货情况
2. SKU-67890 申请了 50% 折扣清仓
3. "618-头部" 计划今晚先暂停, 周一看竞品数据再决定调价

## 期望自动化的事

每天早上 9 点希望系统自动:
- 扫描差评率 > 15% 的 SKU 给运营推钉钉
- 标记滞销 60 天以上的 SKU 给运营决定清仓还是下架
- CTR < 1% 但 CPC > 3 倍均值的计划给投放决定暂停
"""


async def main():
    from app.skills.intelligence.generation_service import generate_from_report

    banner("Demo: 周报路径 4 阶段方法论实测")
    print(f"\n{BOLD}输入: 一份真实风格的电商周报 ({len(SAMPLE_REPORT)} 字){RESET}")
    print(f"{GRAY}{SAMPLE_REPORT[:300]}...{RESET}")

    print(f"\n{BOLD}调用 generate_from_report (真实 LLM, deepseek-chat)...{RESET}")
    try:
        result = await generate_from_report(
            report_content=SAMPLE_REPORT,
            department="EC",
            role="运营",
        )
    except Exception as e:
        print(f"{RED}失败: {e}{RESET}")
        return

    # 1) Phase 0 triage
    banner("Phase 0 Triage 决定")
    triage = result.get("triage_decision", "")
    reason = result.get("triage_reason", "")
    color = GREEN if triage == "CREATE_NEW" else YELLOW
    print(f"\n  {BOLD}triage_decision:{RESET} {color}{triage}{RESET}")
    print(f"  {BOLD}triage_reason:{RESET}   {reason}")

    # 2) constraints (Constraints 视角)
    banner("Constraints 视角输出")
    constraints = result.get("constraints", [])
    if not constraints:
        print(f"  {RED}LLM 没输出 constraints, Constraints 视角 fail{RESET}")
    else:
        print(f"\n  共 {len(constraints)} 条约束:")
        for i, c in enumerate(constraints, 1):
            print(f"    {GREEN}{i}.{RESET} {c}")

    # 3) upstream_downstream (Systems Thinking 视角)
    banner("Systems Thinking 视角输出 (上下游)")
    ud = result.get("upstream_downstream", {})
    upstream = ud.get("upstream", []) if isinstance(ud, dict) else []
    downstream = ud.get("downstream", []) if isinstance(ud, dict) else []
    print(f"\n  {BOLD}上游 (upstream) {len(upstream)} 项:{RESET}")
    for u in upstream:
        print(f"    ← {u}")
    print(f"\n  {BOLD}下游 (downstream) {len(downstream)} 项:{RESET}")
    for d in downstream:
        print(f"    → {d}")

    # 4) antipatterns (Inversion + Pre-Mortem 视角)
    banner("Inversion + Pre-Mortem 视角输出 (反模式)")
    aps = result.get("extracted_antipatterns", [])
    print(f"\n  共 {len(aps)} 条反模式:")
    for i, ap in enumerate(aps, 1):
        print(f"    {GREEN}{i}.{RESET} {BOLD}{ap.get('scenario', '')}{RESET}")
        print(f"       → {ap.get('correct_action', '')}")

    # 5) skill_md_draft 渲染后的 SKILL.md
    banner("生成的 SKILL.md draft (前 60 行)")
    md = result.get("skill_md_draft", "")
    for i, line in enumerate(md.splitlines()[:60], 1):
        print(f"  {GRAY}{i:>3}{RESET}  {line}")
    if len(md.splitlines()) > 60:
        print(f"  {GRAY}... 共 {len(md.splitlines())} 行{RESET}")

    # 6) 跑 quick_validate 看是否合规
    banner("validator 校验结果")
    from app.skills.validators import quick_validate
    qr = quick_validate(md)
    print(f"\n  quick_validate: {'✓ 通过' if qr.ok else '✗ 失败'}")
    for e in qr.errors:
        print(f"    {RED}ERROR: {e}{RESET}")
    for w in qr.warnings:
        print(f"    {YELLOW}WARN:  {w}{RESET}")

    # 写到文件
    out_dir = Path(__file__).resolve().parent / "demo_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "report_skill_draft.md").write_text(md, encoding="utf-8")
    (out_dir / "report_full_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n  {GRAY}完整文件已写到 {out_dir}/report_*.{{md,json}}{RESET}")

    # 总结
    banner("总结")
    has_triage = bool(triage)
    has_constraints = len(constraints) >= 2
    has_upstream = len(upstream) >= 1
    has_aps = len(aps) >= 2
    print(f"\n  {'指标':<30}{'结果':>10}")
    print(f"  {'─' * 40}")
    print(f"  {'有 triage_decision':<30}{('✓' if has_triage else '✗'):>10}")
    print(f"  {'constraints 至少 2 条':<30}{('✓' if has_constraints else '✗'):>10}")
    print(f"  {'有 upstream/downstream':<30}{('✓' if has_upstream else '✗'):>10}")
    print(f"  {'antipatterns 至少 2 条':<30}{('✓' if has_aps else '✗'):>10}")
    print(f"  {'quick_validate 通过':<30}{('✓' if qr.ok else '✗'):>10}")

    all_pass = has_triage and has_constraints and has_upstream and has_aps and qr.ok
    if all_pass:
        print(f"\n  {GREEN}{BOLD}全部通过 ✓ 周报路径成功用上 4 阶段方法论{RESET}")
    else:
        print(f"\n  {YELLOW}部分未通过 — 可能 LLM 偷懒, 实际生产应该重试{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
