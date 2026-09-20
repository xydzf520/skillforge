"""演示: 同一个业务请求, 旧 prompt vs 新 4 阶段方法论 prompt 的对比效果。

跑两次真实 LLM 调用 + validator, 直观看到方法论引入后的区别:

  旧 prompt: 拍脑袋生成一个 SKILL.md
  新 prompt: Phase 0 triage + Phase 1 11 视角分析 → 更系统化的 SKILL.md

最后跑 quick_validate + structural_validate 看哪个更合规。
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


def banner(title: str, color: str = CYAN) -> None:
    print(f"\n{BOLD}{color}{'=' * 70}{RESET}")
    print(f"{BOLD}{color}  {title}{RESET}")
    print(f"{BOLD}{color}{'=' * 70}{RESET}")


def section(title: str) -> None:
    print(f"\n{BOLD}{YELLOW}── {title} ──{RESET}")


# ─────────────────────────────────────────
# 业务请求: EC 部门要一个 SKU 异常下架决策 Skill
# ─────────────────────────────────────────

BUSINESS_REQUEST = {
    "name": "SKU 异常下架决策",
    "department": "EC",
    "role": "ai_engineer",
    "trigger_type": "cron",
    "trigger_expression": "0 9 * * *",  # 每天 9 点
    "risk_level": "R2",
}

# ─────────────────────────────────────────
# 旧 prompt (改造前的 system prompt)
# ─────────────────────────────────────────

OLD_SYSTEM_PROMPT = """你是 Skill 内容生成器。根据用户需求生成 Skill 的内容。

输出 JSON，字段说明：
- description: 一句话描述+触发条件，格式"做什么。Use when: 什么时候用"
- compatibility: 环境依赖，如 "Requires curl and jq"
- body: SKILL.md 的 Markdown 正文，包含标题、步骤说明、真实可执行的 bash/curl 命令、参考表格
  - 必须用真实免费 API（Open-Meteo、ip-api.com、exchangerate-api、GitHub API、date.nager.at、coingecko 等）
  - 必须包含 ```bash 代码块
  - 中文撰写

只输出 JSON，不要输出其他内容。"""


async def call_with_prompt(label: str, system_prompt: str) -> dict:
    """调真实 LLM, 返回 raw JSON。"""
    from app.common.ai import call_llm

    user = (
        f"Skill 名称: {BUSINESS_REQUEST['name']}\n"
        f"部门: {BUSINESS_REQUEST['department']}\n"
        f"角色: {BUSINESS_REQUEST['role']}\n"
        f"触发方式: {BUSINESS_REQUEST['trigger_type']}\n"
        f"触发表达式: {BUSINESS_REQUEST['trigger_expression']}\n"
        f"风险等级: {BUSINESS_REQUEST['risk_level']}\n"
        "\n"
        "请生成此 Skill。"
    )
    print(f"  {GRAY}[{label}] 调用 LLM (deepseek-chat)...{RESET}")
    try:
        result = await call_llm(
            system=system_prompt,
            user=user,
            max_tokens=2500,
            temperature=0.3,
            json_mode=True,
            timeout=120,
        )
    except Exception as exc:
        print(f"  {RED}[{label}] LLM 调用失败: {exc}{RESET}")
        return {}

    if not isinstance(result, dict):
        print(f"  {RED}[{label}] LLM 返回非 dict: {type(result)}{RESET}")
        return {}
    return result


def render_md(slug: str, raw: dict) -> str:
    """用 service.py 的 _render_skill_md 渲染 (跟生产代码一样的路径)。"""
    from app.skills.lifecycle.service import _render_skill_md

    desc = str(raw.get("description", "占位"))
    compat = str(raw.get("compatibility", "Requires nothing"))
    body = str(raw.get("body", "# 占位\n\n占位\n"))

    triage = str(raw.get("triage_decision", "")).strip().upper()
    if triage and triage != "CREATE_NEW":
        desc = f"[Phase 0 建议: {triage}] {desc}"

    return _render_skill_md(
        slug=slug,
        description=desc,
        compatibility=compat,
        department=BUSINESS_REQUEST["department"],
        risk_level=BUSINESS_REQUEST["risk_level"],
        body=body,
    )


def validate_md(label: str, md: str) -> dict:
    """跑 quick_validate + 行数 / 章节统计。"""
    from app.skills.validators import quick_validate

    qr = quick_validate(md)
    line_count = len(md.splitlines())

    # 章节关键词命中数
    sections_hit = sum(
        1
        for kw in [
            "目的", "执行步骤", "约束", "反模式", "anti-pattern",
            "Anti-Pattern", "上下游", "依赖",
        ]
        if kw in md
    )
    has_h1 = md.split("---", 2)[-1].strip().startswith("#")

    print(f"\n  {BOLD}{label} 校验结果:{RESET}")
    print(f"    行数:        {line_count}")
    print(f"    H1 标题:     {'✓' if has_h1 else '✗'}")
    print(f"    章节命中:    {sections_hit}/8 (目的/执行步骤/约束/反模式/上下游/依赖)")
    print(f"    quick_validate: {'✓ 通过' if qr.ok else '✗ 失败'}")
    if qr.errors:
        for e in qr.errors:
            print(f"      {RED}ERROR: {e}{RESET}")
    if qr.warnings:
        for w in qr.warnings:
            print(f"      {YELLOW}WARN:  {w}{RESET}")

    return {
        "ok": qr.ok,
        "line_count": line_count,
        "sections_hit": sections_hit,
        "has_h1": has_h1,
        "errors": qr.errors,
        "warnings": qr.warnings,
    }


async def main():
    banner("Demo: 4 阶段方法论 vs 旧 prompt 对比", CYAN)

    section("业务请求 (两次都用相同的请求)")
    for k, v in BUSINESS_REQUEST.items():
        print(f"  {k:<22}: {v}")

    # ── 第一轮: 旧 prompt ──
    banner("【对照组】旧 prompt (拍脑袋式)", GRAY)
    old_raw = await call_with_prompt("旧", OLD_SYSTEM_PROMPT)
    if not old_raw:
        print(f"{RED}LLM 调用失败, 无法对比{RESET}")
        return

    print(f"\n  {BOLD}LLM 返回的字段:{RESET}")
    for key in old_raw:
        val_preview = str(old_raw[key])[:80]
        print(f"    {key:<22}: {val_preview}")

    old_md = render_md("sku-takedown-decision", old_raw)
    section("生成的 SKILL.md (前 50 行)")
    for i, line in enumerate(old_md.splitlines()[:50], 1):
        print(f"  {GRAY}{i:>3}{RESET}  {line}")
    if len(old_md.splitlines()) > 50:
        print(f"  {GRAY}... 共 {len(old_md.splitlines())} 行{RESET}")

    old_metrics = validate_md("[旧 prompt]", old_md)

    # ── 第二轮: 新 4 阶段 prompt ──
    banner("【实验组】新 4 阶段方法论 prompt", GREEN)
    from app.skills.intelligence.methodology import ENHANCED_SYSTEM_PROMPT

    new_raw = await call_with_prompt("新", ENHANCED_SYSTEM_PROMPT)
    if not new_raw:
        print(f"{RED}LLM 调用失败, 无法对比{RESET}")
        return

    print(f"\n  {BOLD}LLM 返回的字段:{RESET}")
    for key in new_raw:
        val_preview = str(new_raw[key])[:80]
        print(f"    {key:<22}: {val_preview}")

    new_md = render_md("sku-takedown-decision", new_raw)
    section("生成的 SKILL.md (前 50 行)")
    for i, line in enumerate(new_md.splitlines()[:50], 1):
        print(f"  {GRAY}{i:>3}{RESET}  {line}")
    if len(new_md.splitlines()) > 50:
        print(f"  {GRAY}... 共 {len(new_md.splitlines())} 行{RESET}")

    new_metrics = validate_md("[新 4 阶段]", new_md)

    # ── 对比 ──
    banner("数字对比", CYAN)
    print(f"\n  {'指标':<24}{'旧 prompt':>15}{'新 4 阶段':>15}")
    print(f"  {'─' * 54}")
    print(f"  {'通过 quick_validate':<24}{('✓' if old_metrics['ok'] else '✗'):>15}{('✓' if new_metrics['ok'] else '✗'):>15}")
    print(f"  {'行数':<24}{old_metrics['line_count']:>15}{new_metrics['line_count']:>15}")
    print(f"  {'章节命中 (满 8)':<24}{old_metrics['sections_hit']:>15}{new_metrics['sections_hit']:>15}")
    print(f"  {'H1 标题':<24}{('✓' if old_metrics['has_h1'] else '✗'):>15}{('✓' if new_metrics['has_h1'] else '✗'):>15}")
    print(f"  {'输出 triage 字段':<24}{('✓' if 'triage_decision' in old_raw else '✗'):>15}{('✓' if 'triage_decision' in new_raw else '✗'):>15}")

    if "triage_decision" in new_raw:
        print(f"\n  {BOLD}新 prompt 的 Phase 0 triage 决定:{RESET} {new_raw['triage_decision']}")
        print(f"  {BOLD}原因:{RESET} {new_raw.get('triage_reason', '')[:200]}")

    # ── 保存到文件供检查 ──
    out_dir = Path(__file__).resolve().parent / "demo_output"
    out_dir.mkdir(exist_ok=True)
    (out_dir / "old_prompt_skill.md").write_text(old_md, encoding="utf-8")
    (out_dir / "new_prompt_skill.md").write_text(new_md, encoding="utf-8")
    (out_dir / "old_raw.json").write_text(json.dumps(old_raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "new_raw.json").write_text(json.dumps(new_raw, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  {GRAY}完整文件已写到 {out_dir}/{RESET}")


if __name__ == "__main__":
    asyncio.run(main())
