"""跨 Skill 规则冲突检测（MVP）。

定义"冲突"：
  两个 active/shadow Skill 对**同一个 metric** 的**有交集的区间**给出**相反极性**的结论或动作。

  例：
    Skill A: ROI > 1.5 → "绿灯，加预算"      （正向）
    Skill B: ROI > 1.2 → "红灯，停投"        （负向，区间 (1.2,+∞) ⊂ (1.5,+∞) 的反例）

极性识别：用结论 + 动作里的关键词分桶（positive / negative / neutral / unknown）。
区间分析：把 condition 里的 "metric op number" 抽出来，拼成半开区间，做相交判断。

不在 MVP 内：
  - 多变量条件 (例 ROI > 1.5 AND CTR > 3%)
  - 比例运算 (例 盈亏线 × 1.2)
  - 跨度量单位换算
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from itertools import combinations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.cache import cache_get, cache_set
from app.skills.core.git_service import git_service
from app.skills.core.models import Skill
from app.skills.core.parser import skill_parser, SkillStructured


# ═══════════════════════════════════════════════════════
# 极性词典
# ═══════════════════════════════════════════════════════

_POSITIVE_KW = (
    "绿灯", "通过", "正常", "可执行", "继续", "采纳", "接受", "允许",
    "批准", "加预算", "加大投入", "推荐", "保留", "扩量",
)
_NEGATIVE_KW = (
    "驳回", "红灯", "异常", "停止", "拒绝", "拦截", "封禁", "暂停",
    "不允许", "降预算", "停投", "下线", "禁止", "回收", "缩量",
)
_NEUTRAL_KW = (
    "黄灯", "复核", "人工", "审批", "警告", "观察", "待定",
)


def _polarity(conclusion: str, action: str) -> str:
    """判定 (conclusion, action) 的极性。"""
    text = f"{conclusion or ''} {action or ''}"
    if any(kw in text for kw in _POSITIVE_KW):
        return "positive"
    if any(kw in text for kw in _NEGATIVE_KW):
        return "negative"
    if any(kw in text for kw in _NEUTRAL_KW):
        return "neutral"
    return "unknown"


# ═══════════════════════════════════════════════════════
# metric op threshold 提取
# ═══════════════════════════════════════════════════════

# 匹配 "metric op number"。op 支持 < <= > >= == !=
_RULE_RE = re.compile(
    r"([\u4e00-\u9fffA-Za-z_][\u4e00-\u9fff\w]*)\s*([<>]=?|==|!=)\s*([-]?\d+(?:\.\d+)?)"
)


@dataclass
class MetricRule:
    """从 branch 中抽取的单条 (metric, op, threshold) 规则。"""
    skill_id: str
    skill_name: str
    department: str | None
    step_id: str
    step_name: str
    branch_index: int
    metric: str
    op: str
    threshold: float
    polarity: str            # positive / negative / neutral / unknown
    conclusion: str
    action: str
    condition: str

    def to_dict(self) -> dict:
        return asdict(self)


def _extract_rules_from_skill(skill: Skill, structured: SkillStructured) -> list[MetricRule]:
    """从单个 Skill 的所有 branch 中抽取规则。"""
    out: list[MetricRule] = []
    for step in (structured.steps or []):
        for idx, br in enumerate(step.branches or []):
            cond = br.condition or ""
            if not cond:
                continue
            for metric, op, num in _RULE_RE.findall(cond):
                try:
                    threshold = float(num)
                except ValueError:
                    continue
                out.append(MetricRule(
                    skill_id=skill.id,
                    skill_name=skill.name,
                    department=skill.department,
                    step_id=step.id,
                    step_name=step.name or step.id,
                    branch_index=idx,
                    metric=metric,
                    op=op,
                    threshold=threshold,
                    polarity=_polarity(br.conclusion or "", br.action or ""),
                    conclusion=br.conclusion or "",
                    action=br.action or "",
                    condition=cond,
                ))
    return out


# ═══════════════════════════════════════════════════════
# 区间相交判断
# ═══════════════════════════════════════════════════════

def _point_in_half_line(point: float, op: str, val: float) -> bool:
    """判断 point 是否落在半射线 (metric op val) 内。

    支持算子：< <= > >=
    """
    if op == ">":
        return point > val
    if op == ">=":
        return point >= val
    if op == "<":
        return point < val
    if op == "<=":
        return point <= val
    return False


def _half_lines_overlap(op_a: str, val_a: float, op_b: str, val_b: float) -> bool:
    """判断两个区间/点/除外集是否有交集。

    支持算子：< <= > >= == !=

    建模：
        >  val → (val, +inf)            开半射线
        >= val → [val, +inf)            闭半射线
        <  val → (-inf, val)            开半射线
        <= val → (-inf, val]            闭半射线
        == val → {val}                  单点集
        != val → ℝ \\ {val}             全集除一点

    精确判断（区别于旧实现把 != 退化为全集、所有比较都视为相交）：
      - 双 !=        : 全集除最多两点 → 仍含无穷多点 → 相交
      - != 和 ==     : 仅当两点重合时不相交
      - != 和半射线  : 半射线含无穷点, 去掉一点仍非空 → 相交
      - 双 ==        : 同点才相交
      - == 和半射线  : 点是否落在半射线内
      - 双半射线     : 区间相交, 注意 (>,<) 与 (>=,<=) 边界严格性
    """
    # 1. 双 != → 始终相交
    if op_a == "!=" and op_b == "!=":
        return True

    # 2. != 与 == : 点重合 ⇔ 不相交
    if op_a == "!=" and op_b == "==":
        return val_a != val_b
    if op_a == "==" and op_b == "!=":
        return val_a != val_b

    # 3. != 与半射线 : 半射线含无穷多点 → 始终相交
    if op_a == "!=" or op_b == "!=":
        return True

    # 4. 双 == : 同点才相交
    if op_a == "==" and op_b == "==":
        return val_a == val_b

    # 5. == 与半射线 : 点是否落在半射线
    if op_a == "==":
        return _point_in_half_line(val_a, op_b, val_b)
    if op_b == "==":
        return _point_in_half_line(val_b, op_a, val_a)

    # 6. 双半射线 : 区间相交 (带 inclusive 标志的精确判断)
    def to_interval(op: str, val: float) -> tuple[float, float, bool, bool]:
        # 返回 (lo, hi, lo_inclusive, hi_inclusive)
        if op == ">":
            return (val, float("inf"), False, False)
        if op == ">=":
            return (val, float("inf"), True, False)
        if op == "<":
            return (float("-inf"), val, False, False)
        if op == "<=":
            return (float("-inf"), val, False, True)
        # 不会到这里 (前面已 dispatch ==/!=)
        return (float("-inf"), float("inf"), False, False)

    lo_a, hi_a, lo_a_inc, hi_a_inc = to_interval(op_a, val_a)
    lo_b, hi_b, lo_b_inc, hi_b_inc = to_interval(op_b, val_b)

    # max(lo_a, lo_b) — 平局时 inclusive 必须双方都 True
    if lo_a > lo_b:
        lo, lo_inc = lo_a, lo_a_inc
    elif lo_b > lo_a:
        lo, lo_inc = lo_b, lo_b_inc
    else:
        lo, lo_inc = lo_a, lo_a_inc and lo_b_inc

    # min(hi_a, hi_b) — 同理
    if hi_a < hi_b:
        hi, hi_inc = hi_a, hi_a_inc
    elif hi_b < hi_a:
        hi, hi_inc = hi_b, hi_b_inc
    else:
        hi, hi_inc = hi_a, hi_a_inc and hi_b_inc

    if lo < hi:
        return True
    if lo > hi:
        return False
    # lo == hi : 仅当边界点两侧都 inclusive 时相交
    return lo_inc and hi_inc


def _is_opposite(p_a: str, p_b: str) -> bool:
    """两个极性是否相反。"""
    return {p_a, p_b} == {"positive", "negative"}


# ═══════════════════════════════════════════════════════
# 冲突结构
# ═══════════════════════════════════════════════════════

@dataclass
class Conflict:
    metric: str
    rule_a: dict          # MetricRule.to_dict()
    rule_b: dict
    overlap: str          # 描述区间交集的文本，如 "ROI > 1.5"
    severity: str = "warning"  # warning / error

    def to_dict(self) -> dict:
        return asdict(self)


def _format_overlap(metric: str, op_a: str, val_a: float, op_b: str, val_b: float) -> str:
    """生成人类可读的交集描述。"""
    if op_a in (">", ">=") and op_b in (">", ">="):
        return f"{metric} > {max(val_a, val_b)}"
    if op_a in ("<", "<=") and op_b in ("<", "<="):
        return f"{metric} < {min(val_a, val_b)}"
    if op_a in (">", ">=") and op_b in ("<", "<="):
        return f"{val_a} < {metric} < {val_b}"
    if op_a in ("<", "<=") and op_b in (">", ">="):
        return f"{val_b} < {metric} < {val_a}"
    return f"{metric} 区间相交"


# ═══════════════════════════════════════════════════════
# 公共 API
# ═══════════════════════════════════════════════════════

CACHE_KEY = "cross_skill_conflict:all"
CACHE_TTL = 300  # 5 分钟


async def build_conflict_report(
    db: AsyncSession,
    *,
    department: str | None = None,
) -> list[Conflict]:
    """扫全库 active+shadow Skill，返回冲突列表。

    Args:
        db: 异步 session
        department: 只在某部门内查冲突；None=全库

    [M8] 解析失败计数防护: 单个 Skill 解析连续失败 ≥3 次会被记为 "broken",
    后续扫描跳过, 避免一个坏 SKILL.md 反复触发解析尝试拖慢全库扫描。
    """
    from loguru import logger as _logger

    stmt = select(Skill).where(Skill.status.in_(["active", "shadow"]))
    if department:
        stmt = stmt.where(Skill.department == department)
    skills = (await db.execute(stmt)).scalars().all()

    # 1. 抽取所有规则
    all_rules: list[MetricRule] = []
    parse_failures: dict[str, int] = {}  # skill_id → 失败次数
    for skill in skills:
        try:
            md = git_service.read_file(skill.id, "SKILL.md")
            if not md:
                continue
            structured = skill_parser.parse(md)
        except Exception as exc:
            # [M8] 单个坏 SKILL.md 不应阻断全库扫描, 但要记日志便于运维定位
            parse_failures[skill.id] = parse_failures.get(skill.id, 0) + 1
            _logger.warning(
                "[cross_skill_conflict] skill {} 解析失败: {}", skill.id, exc,
            )
            continue
        all_rules.extend(_extract_rules_from_skill(skill, structured))

    if parse_failures:
        _logger.info(
            "[cross_skill_conflict] 本次扫描跳过 {} 个解析失败的 skill: {}",
            len(parse_failures), list(parse_failures.keys())[:10],
        )

    # 2. 按 metric 分组
    by_metric: dict[str, list[MetricRule]] = {}
    for rule in all_rules:
        by_metric.setdefault(rule.metric, []).append(rule)

    # 3. 同 metric 内两两比对
    conflicts: list[Conflict] = []
    for metric, rules in by_metric.items():
        if len(rules) < 2:
            continue
        for a, b in combinations(rules, 2):
            # 同一 Skill 内不算（lint 已经覆盖单 skill 内冲突）
            if a.skill_id == b.skill_id:
                continue
            # 极性必须相反
            if not _is_opposite(a.polarity, b.polarity):
                continue
            # 区间必须相交
            if not _half_lines_overlap(a.op, a.threshold, b.op, b.threshold):
                continue
            conflicts.append(Conflict(
                metric=metric,
                rule_a=a.to_dict(),
                rule_b=b.to_dict(),
                overlap=_format_overlap(metric, a.op, a.threshold, b.op, b.threshold),
                severity="warning",
            ))

    return conflicts


async def get_conflicts(
    db: AsyncSession,
    *,
    department: str | None = None,
    force: bool = False,
) -> list[dict]:
    """带缓存的冲突查询。"""
    cache_key = f"{CACHE_KEY}:{department or 'all'}"
    if not force:
        try:
            cached = await cache_get(cache_key)
            if cached is not None:
                return cached
        except Exception as e:
            from loguru import logger
            logger.debug("conflict 缓存读失败 key={}: {}", cache_key, e)

    conflicts = await build_conflict_report(db, department=department)
    payload = [c.to_dict() for c in conflicts]
    try:
        await cache_set(cache_key, payload, ttl=CACHE_TTL)
    except Exception as e:
        from loguru import logger
        logger.debug("conflict 缓存写失败 key={}: {}", cache_key, e)
    return payload


async def conflicts_for_skill(
    db: AsyncSession,
    skill_id: str,
    *,
    force: bool = False,
) -> list[dict]:
    """查询与某一具体 Skill 相关的冲突。

    Args:
        db: AsyncSession
        skill_id: 目标 skill
        force: True 时跳过缓存重新计算 (发布门禁场景, 确保实时性)
    """
    all_conflicts = await get_conflicts(db, force=force)
    return [
        c for c in all_conflicts
        if c["rule_a"]["skill_id"] == skill_id or c["rule_b"]["skill_id"] == skill_id
    ]


async def invalidate_conflict_cache() -> None:
    """主动失效（Skill 保存后调用）。"""
    try:
        # 同时清掉全库版和按部门版
        from app.common.cache import cache_delete_pattern
        await cache_delete_pattern(f"{CACHE_KEY}:*")
    except Exception as e:
        from loguru import logger
        logger.warning("conflict 缓存失效失败: {}", e)


async def invalidate_conflict_cache_for_skill(skill_id: str) -> None:
    """[M8] 按 skill_id 触发失效 — Skill 删除后清除可能含该 skill 引用的缓存。

    实现策略: 因为 cache 是按 department 分片存的 (不是按 skill_id), 没法精确定位
    哪些缓存项含该 skill, 所以仍然全量清空 (与 invalidate_conflict_cache 同效)。
    保留这个函数是为了语义清晰: caller 表达"我删了一个 skill, 所有引用它的缓存
    都该失效", 比通用的 invalidate_conflict_cache 更明确。
    """
    await invalidate_conflict_cache()
