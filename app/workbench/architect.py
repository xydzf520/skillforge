"""AI Skill Architect — 4 轮采访式创建服务。

不是自由聊天，而是结构化问卷：
  Round 1: 业务目标与动作 → goal + output_definition
  Round 2: 判定信号与数据源 → data_inputs + params
  Round 3: 反例与边界 → antipatterns + edge cases
  Round 4: 上线范围与指标 → deployment_config

每轮 AI 自动提取候选答案，用户点选/微调，不需要打字。
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Literal
import json
import logging

from app.common.ai import call_llm, call_llm_with_retry

logger = logging.getLogger(__name__)

RoundId = Literal["round1_goal", "round2_signals", "round3_boundaries", "round4_deployment"]


# ═══════════════════════════════════════════════════════
# v2.11.3 智能路由 — LLM 判定描述完整度 → 直接生成 / 采访
# ═══════════════════════════════════════════════════════

AssessTier = Literal["complete", "partial", "insufficient"]
RecommendedPath = Literal["direct_synthesize", "short_interview", "full_interview"]
SkillComplexity = Literal["simple", "moderate", "complex"]


@dataclass
class AssessResult:
    """LLM 对 Skill 描述完整度的评估。

    complete    → direct_synthesize: 直接合成，不采访
    partial     → short_interview:   精简采访（仅问缺的维度）
    insufficient→ full_interview:    完整 4 轮采访
    """
    tier: AssessTier
    recommended_path: RecommendedPath
    skill_complexity: SkillComplexity
    confidence: float
    covered_dimensions: list[str]
    missing_dimensions: list[str]
    rationale: str
    user_hint: str

    def to_dict(self) -> dict:
        return asdict(self)


def _coerce_tier(raw: object) -> AssessTier:
    s = str(raw or "").strip().lower()
    if s in ("complete", "partial", "insufficient"):
        return s  # type: ignore[return-value]
    return "insufficient"


def _coerce_path(raw: object) -> RecommendedPath:
    s = str(raw or "").strip().lower()
    if s in ("direct_synthesize", "short_interview", "full_interview"):
        return s  # type: ignore[return-value]
    return "full_interview"


def _coerce_complexity(raw: object) -> SkillComplexity:
    s = str(raw or "").strip().lower()
    if s in ("simple", "moderate", "complex"):
        return s  # type: ignore[return-value]
    return "moderate"


async def assess_description(description: str) -> AssessResult:
    """用 LLM 评估 Skill 描述完整度，决定直接合成还是采访。

    不走启发式兜底——LLM/网关/解析任一失败都抛 AppError，让调用方
    （前端）显式看到错误，不静默降级掩盖 bug。
    """
    from app.common.exceptions import AppError

    text = (description or "").strip()
    if not text:
        return AssessResult(
            tier="insufficient",
            recommended_path="full_interview",
            skill_complexity="moderate",
            confidence=1.0,
            covered_dimensions=[],
            missing_dimensions=["目的", "触发", "输入", "输出"],
            rationale="描述为空",
            user_hint="请先填写业务描述。",
        )

    from app.common.prompt_registry import prompt_registry
    try:
        system_prompt, _ = prompt_registry.build("architect_assess")
    except Exception as e:
        logger.error(f"architect_assess prompt 加载失败: {e}")
        raise AppError(
            "ARCHITECT_ASSESS_PROMPT_MISSING", 500,
            detail={"reason": f"prompt_registry build 失败: {e}"},
        ) from e

    try:
        resp = await call_llm(
            system=system_prompt,
            user=f"用户的 Skill 描述：\n\n{text}",
            max_tokens=800,
            temperature=0.1,
            json_mode=True,
            timeout=15,  # 评估必须快，不能像 synthesize 那样吃 30s
        )
    except Exception as e:
        logger.warning(f"architect_assess LLM 调用失败: {e}")
        raise AppError(
            "ARCHITECT_ASSESS_LLM_FAILED", 503,
            detail={"reason": str(e), "hint": "AI 评估服务调用失败，请检查 LiteLLM / DeepSeek 网关"},
        ) from e

    if not isinstance(resp, dict):
        logger.error(f"architect_assess LLM 返回非 dict: type={type(resp).__name__}")
        raise AppError(
            "ARCHITECT_ASSESS_BAD_RESPONSE", 502,
            detail={"reason": "LLM 返回非 JSON 对象", "preview": str(resp)[:200]},
        )

    try:
        tier = _coerce_tier(resp.get("tier"))
        path = _coerce_path(resp.get("recommended_path"))
        # 一致性：tier 强约束 path（防 LLM 自相矛盾）
        if tier == "complete":
            path = "direct_synthesize"
        elif tier == "insufficient":
            path = "full_interview"

        confidence = resp.get("confidence", 0.5)
        try:
            confidence_f = float(confidence)
        except (TypeError, ValueError):
            confidence_f = 0.5
        confidence_f = max(0.0, min(1.0, confidence_f))

        covered = resp.get("covered_dimensions") or []
        missing = resp.get("missing_dimensions") or []
        if not isinstance(covered, list):
            covered = []
        if not isinstance(missing, list):
            missing = []

        return AssessResult(
            tier=tier,
            recommended_path=path,
            skill_complexity=_coerce_complexity(resp.get("skill_complexity")),
            confidence=confidence_f,
            covered_dimensions=[str(x) for x in covered[:10]],
            missing_dimensions=[str(x) for x in missing[:10]],
            rationale=str(resp.get("rationale") or "").strip()[:300],
            user_hint=str(resp.get("user_hint") or "").strip()[:300],
        )
    except AppError:
        raise
    except Exception as e:
        logger.error(f"architect_assess 解析 LLM 返回失败: {e} raw={resp!r}")
        raise AppError(
            "ARCHITECT_ASSESS_PARSE_FAILED", 502,
            detail={"reason": str(e), "preview": str(resp)[:200]},
        ) from e


# ═══════════════════════════════════════════════════════
# §3.2 智能推断 — 规则化 fallback（LLM 失败时也能运行）
# ═══════════════════════════════════════════════════════

# 关键词 → 动作代价分类
_BLOCK_KEYWORDS = ["拦截", "拒绝", "封禁", "驳回", "禁止", "删除"]
_REVERSIBLE_KEYWORDS = ["调价", "调整", "维持", "推荐", "建议", "展示", "提示"]
_PARTIAL_KEYWORDS = ["暂停", "降级", "限制", "减少", "加预算", "降价"]

# 关键词 → 领域推断
_RISK_DOMAIN_KEYWORDS = ["风控", "审核", "安全", "欺诈", "异常", "黑名单"]
_RECO_DOMAIN_KEYWORDS = ["推荐", "排序", "投放", "展示", "流量"]
_FINANCE_KEYWORDS = ["订单", "金额", "支付", "退款", "预算", "结算"]


_VALID_ACTION_TYPES = {"judge", "recommend", "block", "workflow"}
_VALID_ACTION_COSTS = {"high", "medium", "low"}
_VALID_PRECISION_PREFS = {"high", "balanced", "recall"}
_VALID_THRESHOLD_SOURCES = {"business_constant", "data_derived"}


def _normalize_inferred(raw: dict, description: str) -> dict:
    """对 LLM 返回的 inferred 做字段归一化 + 默认值兜底,保证下游不会因脏字段炸。"""
    fallback = _infer_from_description_keywords(description)

    action_type = raw.get("action_type")
    if action_type not in _VALID_ACTION_TYPES:
        action_type = fallback["action_type"]

    action_cost = raw.get("action_cost")
    if action_cost not in _VALID_ACTION_COSTS:
        action_cost = fallback["action_cost"]

    precision_preference = raw.get("precision_preference")
    if precision_preference not in _VALID_PRECISION_PREFS:
        precision_preference = fallback["precision_preference"]

    must_have_branches = raw.get("must_have_branches")
    if not isinstance(must_have_branches, list) or not must_have_branches:
        must_have_branches = fallback["must_have_branches"]
    else:
        # 必须有 else_fallback
        if "else_fallback" not in must_have_branches:
            must_have_branches = ["else_fallback"] + list(must_have_branches)

    threshold_source = raw.get("threshold_source")
    if threshold_source not in _VALID_THRESHOLD_SOURCES:
        threshold_source = fallback["threshold_source"]

    monitoring_metrics = raw.get("monitoring_metrics")
    if not isinstance(monitoring_metrics, list) or not monitoring_metrics:
        monitoring_metrics = fallback["monitoring_metrics"]

    return {
        "action_type": action_type,
        "action_cost": action_cost,
        "precision_preference": precision_preference,
        "must_have_branches": must_have_branches,
        "threshold_source": threshold_source,
        "monitoring_metrics": monitoring_metrics,
    }


async def infer_from_description_llm(description: str) -> dict:
    """LLM 主路径:让 DeepSeek 从业务描述抽 5 项关键属性。

    失败时降级到 _infer_from_description_keywords 关键词版,
    所以调用方永远拿到合法 dict。

    替代旧 infer_from_description 同步关键词版本(后者保留供旧测试 + 兜底)。
    """
    text = (description or "").strip()
    if not text:
        return _infer_from_description_keywords("")

    system = """你是 SkillForge 的 Skill 架构师助手,任务:从业务描述抽取 6 项关键设计属性。

严格输出 JSON,字段如下:

{
  "action_type": "judge" | "recommend" | "block" | "workflow",
  "action_cost": "high" | "medium" | "low",
  "precision_preference": "high" | "balanced" | "recall",
  "must_have_branches": [<必须包含的分支类型>],
  "threshold_source": "business_constant" | "data_derived",
  "monitoring_metrics": [<推荐的监控指标>]
}

字段语义:
- action_type:
    - judge: 给业务方一个判定结果(R1/R2/R3 / 通过/拒绝),不真的执行动作
    - recommend: 建议性动作(调价/调预算/推荐 SKU),业务方可改可弃
    - block: 不可逆的拦截/拒绝/封禁/驳回/删除
    - workflow: 触发后续工作流(暂停/降级/限流/分配)

- action_cost:
    - high: 不可逆 + 影响大(资金/账户/合规)。例:封号、退款、合同终止
    - medium: 可补救但麻烦。例:暂停推广、调价、限流
    - low: 完全可逆。例:展示提示、加入人工复核队列、推荐排序

- precision_preference:
    - high: 宁可漏判(风控、合规、不可逆动作)
    - balanced: 默认
    - recall: 宁可误判(推荐、营销、提示类)

- must_have_branches: 必须包含的兜底/异常分支,可选值:
    - "else_fallback" (永远必须有)
    - "data_missing" (输入数据可能缺失/为空时)
    - "manual_review" (高代价动作必须有人工复核入口)
    - "rate_limited" (受限流约束的动作)

- threshold_source:
    - business_constant: 业务方拍板的常量(ROI > 1.5、库存 < 10)
    - data_derived: 从历史数据统计推导(P95 延迟、近 30 天均值)

- monitoring_metrics: 推荐监控的指标,可选:
    - false_positive_rate / manual_review_rate / click_through_rate
    - adoption_rate / failure_rate / latency_p95 / cost_per_call

不要解释,只输出 JSON。"""

    try:
        result = await call_llm(
            system=system,
            user=text,
            json_mode=True,
            temperature=0.2,
            max_tokens=400,
            call_source="workbench.architect.infer",
        )
    except Exception as e:
        logger.warning(f"infer_from_description_llm: call_llm 异常,降级关键词版: {e}")
        return _infer_from_description_keywords(text)

    if not isinstance(result, dict):
        logger.warning("infer_from_description_llm: LLM 返回非 dict,降级")
        return _infer_from_description_keywords(text)

    return _normalize_inferred(result, text)


def _infer_from_description_keywords(description: str) -> dict:
    """关键词版兜底实现。仅在 LLM 不可用 / 网络故障时使用。

    保留是为了:
      1. 离线 / CI / 单元测试可以无依赖跑通
      2. LLM 调用失败时给一个不会崩的默认 dict
    """
    desc = description.lower() if description else ""

    # 1. 动作代价（关键词匹配）
    if any(kw in desc for kw in _BLOCK_KEYWORDS):
        action_type = "block"
        action_cost = "high"
    elif any(kw in desc for kw in _PARTIAL_KEYWORDS):
        action_type = "workflow"
        action_cost = "medium"
    elif any(kw in desc for kw in _REVERSIBLE_KEYWORDS):
        action_type = "recommend"
        action_cost = "low"
    else:
        action_type = "judge"
        action_cost = "medium"

    # 2. 精确 vs 召回偏好
    is_risk = any(kw in desc for kw in _RISK_DOMAIN_KEYWORDS)
    is_reco = any(kw in desc for kw in _RECO_DOMAIN_KEYWORDS)
    if action_cost == "high" or is_risk:
        precision_preference = "high"   # 风控/拦截 → 宁可漏判
    elif is_reco and action_cost == "low":
        precision_preference = "recall"  # 推荐类 → 宁可误判
    else:
        precision_preference = "balanced"

    # 3. 必须包含的分支（§3.2 完备性检查）
    must_have_branches = ["else_fallback"]        # 所有 Skill 都要 else 兜底
    if any(kw in desc for kw in _FINANCE_KEYWORDS):
        must_have_branches.append("data_missing")    # 金融/订单场景：数据缺失分支
    if action_cost == "high":
        must_have_branches.append("manual_review")   # 高代价动作：人工复核支路

    # 4. 阈值来源
    if "阈值" in desc or "threshold" in desc or "比例" in desc or "ratio" in desc:
        threshold_source = "data_derived"  # 明确提到阈值 → 通常来自数据统计
    else:
        threshold_source = "business_constant"

    # 5. 监控指标
    monitoring_metrics = []
    if is_risk or action_cost == "high":
        monitoring_metrics.append("false_positive_rate")  # 误伤率
        monitoring_metrics.append("manual_review_rate")
    if is_reco:
        monitoring_metrics.append("click_through_rate")  # CTR
        monitoring_metrics.append("adoption_rate")
    if not monitoring_metrics:
        monitoring_metrics = ["adoption_rate", "failure_rate"]

    return {
        "action_type": action_type,
        "action_cost": action_cost,
        "precision_preference": precision_preference,
        "must_have_branches": must_have_branches,
        "threshold_source": threshold_source,
        "monitoring_metrics": monitoring_metrics,
    }


# 同步接口保留:供旧调用方 / 离线测试 / LLM 不可用时使用。
# 新代码请用 infer_from_description_llm。
def infer_from_description(description: str) -> dict:
    """同步接口(关键词版)。LLM 版本请用 infer_from_description_llm。"""
    return _infer_from_description_keywords(description)


@dataclass
class InterviewQuestion:
    """一个采访问题。"""
    id: str                          # 问题 ID
    prompt: str                      # 问题文本
    kind: Literal["single", "multi", "text", "tags"]
    candidates: list[str] = field(default_factory=list)
    default: str | list[str] = ""
    hint: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class InterviewRound:
    """一轮采访。"""
    id: RoundId
    title: str
    description: str
    questions: list[InterviewQuestion] = field(default_factory=list)
    inferred: dict = field(default_factory=dict)   # AI 从输入中预填的答案
    # AI 调用失败回退到 fallback 问题集时填入 (异常类型 / 消息), 便于前端/日志追溯
    fallback_reason: str | None = None

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "questions": [q.to_dict() for q in self.questions],
            "inferred": self.inferred,
        }
        if self.fallback_reason:
            d["fallback_reason"] = self.fallback_reason
        return d


# F3: ARCHITECT_SYSTEM_PROMPT 已迁移到 app/common/prompts/architect@v1.md
def _get_architect_prompt() -> tuple[str, str]:
    try:
        from app.common.prompt_registry import prompt_registry
        return prompt_registry.build("architect")
    except KeyError:
        return "你是 SkillForge Skill 创建向导。请通过结构化采访收集信息。", ""


ROUND_CONFIG = {
    "round1_goal": {
        "title": "业务目标与动作",
        "description": "确定这个 Skill 判定什么、产生什么动作、如何衡量成功",
        "focus": "goal + output_definition + 动作性质",
    },
    "round2_signals": {
        "title": "判定信号与数据源",
        "description": "明确依赖什么数据、哪些变量决定分支、阈值怎么来",
        "focus": "data_inputs + params + 阈值来源",
    },
    "round3_boundaries": {
        "title": "反例与边界",
        "description": "识别容易误判的场景、冲突规则优先级、兜底行为",
        "focus": "antipatterns + edge cases + 兜底分支",
    },
    "round4_deployment": {
        "title": "上线范围与指标",
        "description": "确定初始覆盖范围、观察窗口、监控指标、回滚条件",
        "focus": "部署配置 + 监控指标",
    },
}


@dataclass
class InterviewSession:
    """采访会话状态。"""
    description: str = ""            # 用户初始描述
    current_round: int = 1
    answers: dict = field(default_factory=dict)   # 历史轮次答案 {round_id: {q_id: answer}}
    skill_draft: dict = field(default_factory=dict)  # 逐轮累积的 Skill 草稿


async def start_interview(description: str) -> InterviewRound:
    """启动采访：用户输入业务描述，生成 Round 1 问题。"""
    return await _generate_round("round1_goal", description, answers={}, skill_draft={})


async def next_round(
    current_round_id: RoundId,
    session: InterviewSession,
) -> InterviewRound | None:
    """进入下一轮采访。返回 None 表示采访结束。"""
    round_order = ["round1_goal", "round2_signals", "round3_boundaries", "round4_deployment"]
    try:
        idx = round_order.index(current_round_id)
    except ValueError:
        return None
    if idx >= len(round_order) - 1:
        return None
    next_id = round_order[idx + 1]
    return await _generate_round(next_id, session.description, session.answers, session.skill_draft)


async def _generate_round(
    round_id: RoundId,
    description: str,
    answers: dict,
    skill_draft: dict,
) -> InterviewRound:
    """生成某一轮的问题。"""
    config = ROUND_CONFIG[round_id]

    user_prompt = f"""# 用户的业务描述
{description}

# 历史采访答案
{json.dumps(answers, ensure_ascii=False, indent=2) if answers else "（首轮）"}

# 当前 Skill 草稿
{json.dumps(skill_draft, ensure_ascii=False, indent=2) if skill_draft else "（空）"}

# 当前轮次
轮次 ID: {round_id}
标题: {config["title"]}
关注点: {config["focus"]}
说明: {config["description"]}

请生成这一轮的 2-4 个关键问题。每个问题要有 3-5 个候选答案。
重点：从用户描述和历史答案中推断，不要问已经明确的事。
"""

    architect_system, _arch_hash = _get_architect_prompt()
    try:
        resp = await call_llm(
            system=architect_system,
            user=user_prompt,
            max_tokens=1500,
            temperature=0.3,
            json_mode=True,
        )

        if not resp or not isinstance(resp, dict):
            return await _fallback_round(round_id, description, reason="LLM 返回空响应或非字典")

        questions = []
        for q in resp.get("questions", []):
            if not isinstance(q, dict):
                continue
            try:
                questions.append(InterviewQuestion(
                    id=q.get("id", f"q{len(questions)+1}"),
                    prompt=q.get("prompt", ""),
                    kind=q.get("kind", "single"),
                    candidates=q.get("candidates", []),
                    default=q.get("default", ""),
                    hint=q.get("hint", ""),
                ))
            except (TypeError, ValueError):
                continue

        return InterviewRound(
            id=round_id,
            title=resp.get("title", config["title"]),
            description=resp.get("description", config["description"]),
            questions=questions,
            inferred=resp.get("inferred", {}),
        )
    except Exception as e:
        logger.warning(f"Architect generate_round 失败: {e}")
        return await _fallback_round(round_id, description, reason=f"{type(e).__name__}: {e}")


async def _fallback_round(
    round_id: RoundId,
    description: str,
    reason: str | None = None,
) -> InterviewRound:
    """AI 生成 round 失败时的降级问题集。

    inferred 字段优先调 LLM(infer_from_description_llm),失败再降级关键词。
    这样即便 round 生成挂了,inferred 仍能拿到 LLM 质量的属性。
    """
    config = ROUND_CONFIG[round_id]

    fallback_questions = {
        "round1_goal": [
            InterviewQuestion(
                id="q1", prompt="这个 Skill 的核心判定对象是？", kind="single",
                candidates=["广告/投放", "用户/账户", "订单/交易", "数据/指标", "其他"],
                default="", hint="从业务描述中判断",
            ),
            InterviewQuestion(
                id="q2", prompt="动作的代价有多高？", kind="single",
                candidates=["可逆（调价/维持）", "部分可逆（暂停/降级）", "不可逆（删除/拒绝）"],
                default="", hint="动作越不可逆，要求的精度越高",
            ),
            InterviewQuestion(
                id="q3", prompt="偏向精确还是召回？", kind="single",
                candidates=["高精确（宁可漏判）", "平衡", "高召回（宁可误判）"],
                default="平衡", hint="风控类偏精确，推荐类偏召回",
            ),
        ],
        "round2_signals": [
            InterviewQuestion(
                id="q1", prompt="依赖哪些数据源？", kind="multi",
                candidates=["CSV 上传", "内部 API", "外部 API", "数据库", "实时流"],
                default=[], hint="",
            ),
            InterviewQuestion(
                id="q2", prompt="关键判定变量有几个？", kind="single",
                candidates=["1-2 个", "3-5 个", "6 个以上"],
                default="3-5 个", hint="变量越多越需要拆分多个 step",
            ),
            InterviewQuestion(
                id="q3", prompt="阈值来源？", kind="single",
                candidates=["业务设定（固定）", "历史数据推导", "A/B 测试", "需要可配"],
                default="需要可配", hint="",
            ),
        ],
        "round3_boundaries": [
            InterviewQuestion(
                id="q1", prompt="数据缺失时怎么办？", kind="single",
                candidates=["默认通过", "默认拒绝", "人工复核", "报错退出"],
                default="人工复核", hint="决定兜底分支行为",
            ),
            InterviewQuestion(
                id="q2", prompt="已知容易误判的场景？", kind="tags",
                candidates=[], default=[], hint="列出 2-3 个反例帮助 AI 规避",
            ),
            InterviewQuestion(
                id="q3", prompt="与其他 Skill 有冲突时谁优先？", kind="single",
                candidates=["此 Skill 优先", "另一 Skill 优先", "需要人工介入"],
                default="需要人工介入", hint="",
            ),
        ],
        "round4_deployment": [
            InterviewQuestion(
                id="q1", prompt="初始上线范围？", kind="single",
                candidates=["影子运行 7 天", "小流量 10%", "全量上线", "仅测试环境"],
                default="影子运行 7 天", hint="建议先影子再推正",
            ),
            InterviewQuestion(
                id="q2", prompt="关键监控指标？", kind="multi",
                candidates=["误判率", "采纳率", "延迟", "Token 消耗", "分支覆盖率"],
                default=["误判率", "采纳率"], hint="",
            ),
            InterviewQuestion(
                id="q3", prompt="回滚条件？", kind="single",
                candidates=["误判率 > 5%", "一致率 < 80%", "人工介入", "不自动回滚"],
                default="误判率 > 5%", hint="",
            ),
        ],
    }

    # §3.2 inferred 优先 LLM,失败时函数内部已自动降级到关键词版
    inferred = await infer_from_description_llm(description) if description else {}

    return InterviewRound(
        id=round_id,
        title=config["title"],
        description=config["description"],
        questions=fallback_questions.get(round_id, []),
        inferred=inferred,
        fallback_reason=reason,
    )


async def synthesize_skill_from_interview(
    description: str,
    answers: dict,
) -> dict:
    """根据完整的采访答案合成 Skill 骨架。"""
    synth_prompt = f"""基于以下采访结果，生成一个完整的 Skill 骨架（JSON 格式）：

# 用户描述
{description}

# 采访答案
{json.dumps(answers, ensure_ascii=False, indent=2)}

# 输出格式

{{
  "meta": {{
    "name": "简短名称",
    "description": "一句话目标",
    "department": "...",
    "trigger_type": "manual|schedule|event",
    "risk_level": "R1|R2|R3|R4"
  }},
  "goal": "详细业务目标描述",
  "rules": [
    {{
      "id": "step_1",
      "name": "步骤名",
      "branches": [
        {{"condition": "...", "conclusion": "...", "action": "...", "next_step": null}},
        {{"condition": "其他情况", "conclusion": "默认", "action": "...", "next_step": null}}
      ]
    }}
  ],
  "params": [
    {{"name": "参数名", "default_value": 1.0, "description": "说明"}}
  ],
  "output_table": [
    {{"name": "字段名", "format": "text", "recipient": "接收人", "approval_level": ""}}
  ],
  "test_cases": [
    {{"name": "正常case", "input_data": {{}}, "expected_output": {{}}}}
  ],
  "antipatterns": [
    {{"scenario": "误判场景", "correct_action": "正确做法", "source": ""}}
  ]
}}

要求：
1. 每个 step 必须有兜底分支（"其他情况"）
2. magic number 必须抽成 params
3. 至少 3 个测试用例覆盖主要分支
4. 至少 2 个反例
5. 根据动作代价设置 risk_level
"""

    try:
        # v2.8.1 C5：用 call_llm_with_retry —— 5xx/超时自动退避重试 + JSON 解析失败后带提示再试一次
        resp = await call_llm_with_retry(
            system="你是 SkillForge 的 Skill 骨架生成器，严格按 JSON 格式输出。",
            user=synth_prompt,
            max_tokens=3000,
            temperature=0.3,
            json_mode=True,
            max_retries=2,
            description="skill_synthesize",
        )
        if isinstance(resp, dict):
            return resp
    except Exception as e:
        logger.warning(f"synthesize_skill_from_interview 失败: {e}")

    # 降级：返回最小骨架
    return {
        "meta": {"name": "新 Skill", "description": description[:100]},
        "goal": description,
        "rules": [{
            "id": "step_1", "name": "判断",
            "branches": [
                {"condition": "待定义", "conclusion": "通过", "action": "", "next_step": None},
                {"condition": "其他情况", "conclusion": "默认", "action": "", "next_step": None},
            ],
        }],
        "params": [],
        "output_table": [],
        "test_cases": [],
        "antipatterns": [],
    }
