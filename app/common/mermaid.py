"""
Mermaid流程图语法生成：
  - playbook_to_mermaid()  — Playbook YAML → DAG图
  - steps_to_mermaid()     — SKILL.md 决策树 → 流程图
"""

import re

import yaml

from app.skills.core.parser import DecisionStep


def playbook_to_mermaid(playbook_yaml: str | dict) -> str:
    """
    将Playbook YAML转换为Mermaid graph TD语法。

    输入示例YAML:
      steps:
        - id: check, skill: EC-店铺-01
        - id: diagnose, skill: EC-店铺-02, depends_on: [check]
        - id: summary, depends_on: [check, diagnose]

    输出Mermaid:
      graph TD
        check[EC-店铺-01<br>晨间数据检查]
        diagnose[EC-店铺-02<br>异常归因]
        check --> diagnose
        check --> summary
        diagnose --> summary
    """
    if isinstance(playbook_yaml, str):
        try:
            data = yaml.safe_load(playbook_yaml)
        except yaml.YAMLError:
            return "graph TD\n  error[YAML解析失败]"
    else:
        data = playbook_yaml

    if not data or "steps" not in data:
        return "graph TD\n  empty[无步骤]"

    lines = ["graph TD"]
    steps = data["steps"]

    # 节点定义
    for step in steps:
        sid = step["id"]
        skill = step.get("skill", sid)
        desc = step.get("description", "")
        label = f"{skill}"
        if desc:
            label += f"<br>{desc}"
        lines.append(f"  {sid}[{label}]")

    # 连线（依赖关系）
    for step in steps:
        sid = step["id"]
        depends = step.get("depends_on", [])
        condition = step.get("condition", "")
        for dep in depends:
            if condition:
                # 带条件的连线
                short_cond = condition[:30] + "..." if len(condition) > 30 else condition
                lines.append(f"  {dep} -->|{short_cond}| {sid}")
            else:
                lines.append(f"  {dep} --> {sid}")

    return "\n".join(lines)


def execution_status_mermaid(playbook_yaml: str | dict, step_statuses: dict[str, str]) -> str:
    """
    生成带执行状态的Mermaid图（节点颜色区分状态）。
    step_statuses: {"check": "completed", "diagnose": "running", "summary": "pending"}
    """
    base = playbook_to_mermaid(playbook_yaml)

    # 添加样式定义
    style_lines = []
    status_styles = {
        "completed": "fill:#4caf50,stroke:#388e3c,color:#fff",
        "running": "fill:#2196f3,stroke:#1565c0,color:#fff",
        "failed": "fill:#f44336,stroke:#c62828,color:#fff",
        "pending": "fill:#e0e0e0,stroke:#9e9e9e,color:#333",
        "skipped": "fill:#ffeb3b,stroke:#f9a825,color:#333",
    }

    for step_id, status in step_statuses.items():
        style = status_styles.get(status, status_styles["pending"])
        style_lines.append(f"  style {step_id} {style}")

    if style_lines:
        base += "\n" + "\n".join(style_lines)

    return base


# ===== SKILL.md 决策树 → Mermaid =====


def _escape_mermaid(text: str, max_len: int = 40) -> str:
    """转义 Mermaid 特殊字符并截断过长文本"""
    text = text.replace("`", "")  # 反引号会破坏 Mermaid 语法
    text = text.replace("\u201c", "").replace("\u201d", "")  # 中文引号
    text = text.replace("\u2018", "").replace("\u2019", "")  # 中文单引号
    if len(text) > max_len:
        text = text[:max_len] + "..."
    return text.replace('"', "'")


def steps_to_mermaid(steps: list[DecisionStep]) -> str:
    """
    将 SkillStructured.steps 渲染为 Mermaid flowchart TD 语法。
    步骤节点 → 圆角矩形（主色），条件节点 → 菱形，结论节点 → 圆角矩形（语义色）。
    配色对齐 SkillForge Design System。
    """
    if not steps:
        return "flowchart TD\n  empty[无决策步骤]"

    lines = ["flowchart TD"]

    step_ids = []   # 收集步骤节点 id
    cond_ids = []   # 收集条件节点 id
    green_ids = []
    yellow_ids = []
    red_ids = []
    default_ids = []

    for step in steps:
        sid = f"step_{step.id}" if step.id else f"step_{steps.index(step) + 1}"
        step_label = _escape_mermaid(step.name or sid, 35)
        # 步骤节点：带序号标题的圆角矩形
        lines.append(f'  {sid}("{sid}: {step_label}")')
        step_ids.append(sid)

        if not step.branches:
            continue

        for bi, branch in enumerate(step.branches):
            bid = f"{sid}_b{bi}"
            cid = f"{sid}_c{bi}"

            # 条件节点：菱形
            cond_label = _escape_mermaid(branch.condition or f"条件{bi + 1}", 35)
            lines.append(f"  {bid}{{{{{cond_label}}}}}")
            cond_ids.append(bid)

            # 连线：step → 第一个条件，后续条件链式
            if bi == 0:
                lines.append(f"  {sid} --> {bid}")
            else:
                prev_bid = f"{sid}_b{bi - 1}"
                lines.append(f'  {prev_bid} -->|"否"| {bid}')

            # 结论节点：圆角矩形
            conclusion_text = _escape_mermaid(branch.conclusion or "结论", 25)
            action_text = _escape_mermaid(branch.action, 30) if branch.action else ""
            conclusion_label = conclusion_text
            if action_text:
                conclusion_label += f"<br/>{action_text}"
            lines.append(f'  {cid}["{conclusion_label}"]')
            lines.append(f'  {bid} -->|"是"| {cid}')

            # 结论颜色分类
            c = branch.conclusion or ""
            if "绿" in c:
                green_ids.append(cid)
            elif "黄" in c:
                yellow_ids.append(cid)
            elif "红" in c:
                red_ids.append(cid)
            else:
                default_ids.append(cid)

            # 跳转到下一步
            if branch.next_step:
                next_sid = f"step_{branch.next_step}"
                lines.append(f"  {cid} -.-> {next_sid}")

    # ── classDef：对齐 SkillForge 设计系统 ──
    lines.append("")
    # 步骤节点：主色蓝
    lines.append(
        "  classDef stepNode fill:#165DFF,stroke:#0E42D2,color:#fff,"
        "font-weight:600,rx:8,ry:8"
    )
    # 条件节点：浅灰蓝
    lines.append(
        "  classDef condNode fill:#F2F3F5,stroke:#C9CDD4,color:#1D2129,"
        "font-size:12px"
    )
    # 结论：绿灯
    lines.append(
        "  classDef green fill:#00B42A,stroke:#009A29,color:#fff,"
        "rx:6,ry:6,font-weight:500"
    )
    # 结论：黄灯
    lines.append(
        "  classDef yellow fill:#FF7D00,stroke:#D25F00,color:#fff,"
        "rx:6,ry:6,font-weight:500"
    )
    # 结论：红灯
    lines.append(
        "  classDef red fill:#F53F3F,stroke:#CB2634,color:#fff,"
        "rx:6,ry:6,font-weight:500"
    )
    # 默认结论
    lines.append(
        "  classDef defaultConc fill:#E8F3FF,stroke:#BEDAFF,color:#165DFF,"
        "rx:6,ry:6"
    )

    # 批量应用 class
    if step_ids:
        lines.append(f"  class {','.join(step_ids)} stepNode")
    if cond_ids:
        lines.append(f"  class {','.join(cond_ids)} condNode")
    if green_ids:
        lines.append(f"  class {','.join(green_ids)} green")
    if yellow_ids:
        lines.append(f"  class {','.join(yellow_ids)} yellow")
    if red_ids:
        lines.append(f"  class {','.join(red_ids)} red")
    if default_ids:
        lines.append(f"  class {','.join(default_ids)} defaultConc")

    return "\n".join(lines)


def build_step_line_map(skill_md: str, steps: list[DecisionStep]) -> dict[str, int]:
    """
    构建 step_id → 行号 映射，供前端双向联动。
    支持多种步骤标题格式：
      - ### step_1: 检查ROI      （下划线格式）
      - ### Step 1: 检查ROI       （空格格式）
      - ### 步骤 1: 检查ROI       （中文格式）
      - ### 步骤1：检查ROI        （无空格）
    用 word boundary 精确匹配，避免 step_1 误匹配 step_10。
    """
    line_map: dict[str, int] = {}
    for i, line in enumerate(skill_md.split("\n"), start=1):
        for step in steps:
            step_num = step.id
            if not step_num:
                continue
            num_escaped = re.escape(step_num)
            # 匹配 step_N / Step N / 步骤 N（精确边界，不误匹配 step_10）
            pattern = (
                rf'(?:'
                rf'step[_\s]{num_escaped}'    # step_1 或 step 1
                rf'|步骤\s*{num_escaped}'     # 步骤1 或 步骤 1
                rf')(?:\s|[:：.。]|$)'         # 后接分隔符或行尾
            )
            if re.search(pattern, line, re.IGNORECASE):
                key = f"step_{step_num}"
                if key not in line_map:
                    line_map[key] = i
    return line_map
