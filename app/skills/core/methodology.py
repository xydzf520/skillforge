"""SkillForge 4 阶段方法论 prompt 模板。

该模块是方法论 prompt 的共享定义层：
- `lifecycle` 直接消费这里，避免反向依赖 `intelligence`
- `app.skills.intelligence.methodology` 保留为兼容别名
"""

from __future__ import annotations

ELEVEN_LENSES = [
    ("First Principles", "本质上到底需要什么? 抛开既定流程, 这个 Skill 真正要解决的问题是什么"),
    ("Inversion", "什么情况会让 Skill 必然失败? 把这些反例写进反模式 (anti-patterns)"),
    ("Second-Order", "Skill 跑完之后的二阶影响是什么? 是否会触发其他流程, 是否需要回执"),
    ("Pre-Mortem", "假设 3 个月后这个 Skill 失败了, 最可能的原因是什么? 数据漂移? 业务规则变化? 边界场景"),
    ("Systems Thinking", "Skill 跟数据源 / 其他 Skill / 钉钉 / 审批人 是怎么交互的, 哪些是上下游依赖"),
    ("Devil's Advocate", "为什么不应该有这个 Skill? 是否在重复已有 Skill 能力, 是否过度自动化"),
    ("Constraints", "真正不可变的约束是什么 (合规 / 数据可获取性 / 钉钉 API 限频 / 执行人钉钉绑定)"),
    ("Pareto", "20% 的关键字段 / 步骤能覆盖 80% 的业务诉求, 不要堆砌"),
    ("Root Cause (5 Whys)", "为什么需要这个 Skill, 连问 5 次为什么, 找到真实业务驱动"),
    ("Comparative", "如果 reviewer 是一个有经验的同事而不是 LLM, 他/她会怎么做这件事, 跟 Skill 的方案有什么差异"),
    ("Opportunity Cost", "做这个 Skill 的代价是什么 (维护 / 培训 / 数据治理), 是否值得"),
]


PHASE_0_TRIAGE_PROMPT = """在生成 Skill 内容之前, 先做一次分类思考 (Phase 0 Triage):

判断这个 Skill 应该是哪种性质:
  USE_EXISTING  - 已有 Skill 能覆盖, 不需要新建 (建议查找现有 Skill 推荐)
  IMPROVE       - 现有 Skill 接近但缺一些字段 / 步骤 / 数据源, 应该改进而非新建
  CREATE_NEW    - 真正全新的业务能力, 没有现成 Skill 可借鉴
  COMPOSE       - 由多个现有 Skill 串联组成 (Playbook 而非单 Skill)

如果是 USE_EXISTING / COMPOSE, 在 description 中明确写出建议; 不要勉强生成新 Skill。
如果是 IMPROVE, 在 description 中说明要扩展哪个现有 Skill, 让审核人选择是否创建新版本。
如果是 CREATE_NEW, 进入 Phase 1 11 视角分析。
"""


def render_phase_1_prompt() -> str:
    """渲染 Phase 1 的 11 视角分析 prompt 段。"""
    lines = ["在生成 SKILL.md 之前, 用以下 11 个视角系统化分析需求 (Phase 1 11 Lenses):", ""]
    for i, (lens, question) in enumerate(ELEVEN_LENSES, 1):
        lines.append(f"  {i:>2}. {lens}: {question}")
    lines.append("")
    lines.append(
        "你不需要在 description 里逐条列出 11 个视角的答案; "
        "但你输出的 description / body 必须能让人感觉到你已经过这 11 个视角的系统化思考, "
        "而不是临时拍脑袋。"
    )
    lines.append(
        "特别要在 body 中明确列出: 反模式 (Inversion + Pre-Mortem)、"
        "约束 (Constraints)、上下游 (Systems Thinking)。"
    )
    return "\n".join(lines)


ENHANCED_SYSTEM_PROMPT = (
    """你是 SkillForge 的 Skill 内容生成器。SkillForge 是企业内部的 Skill 协作平台,
所有 Skill 跑在 OpenClaw / AIClaw 上, 由业务方在 Web 端编辑 + 审批,
执行结果通过钉钉互动卡片推送给个人执行人。

"""
    + PHASE_0_TRIAGE_PROMPT
    + "\n"
    + render_phase_1_prompt()
    + """

输出 JSON, 字段说明:
- triage_decision: 必填, 字符串, 取值 USE_EXISTING / IMPROVE / CREATE_NEW / COMPOSE
- triage_reason: 必填, 1-2 句话, 解释为什么是这个分类
- description: 必填, 一句话描述 + 触发条件, 格式 "做什么。Use when: 什么时候用; NOT for: 什么时候不用"
                不能含尖括号 < >, 不能超过 1024 字
- compatibility: 必填, 环境依赖, 如 "Requires curl and jq"
- body: SKILL.md 的 Markdown 正文, 包含:
  - H1 标题
  - "目的" 段落 (Root Cause + First Principles)
  - "执行步骤" (用表格或编号列表, 不要用长段落)
  - "约束" (Constraints 视角)
  - "反模式" / "Anti-Patterns" (Inversion + Pre-Mortem 视角)
  - "上下游" (Systems Thinking 视角, 列出依赖的数据源 / 其他 Skill)
  - 中文撰写, 行数不超过 1000 行

只输出 JSON, 不要输出其他内容。"""
)
