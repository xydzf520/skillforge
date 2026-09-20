"""Skill ID 生成 / 校验中心模块（v2.8.0）。

统一所有 skill_id 的生成规则，避免多处各写各的导致：
- 测试 id 混入生产（`imported-{Date.now().toString(36)}` 这种 e2e 产物）
- Fork / Import / Manual create 各用自己的前缀
- 校验逻辑分散在 service_shared / router / asset 里

## 前缀白名单

所有"自动生成"的 id 都应命中这里的前缀；业务可读 id（如 `EC-投放-01`）
不用前缀直接命中 SKILL_ID_PATTERN 即可。

- `imp-*`  — 从 zip / 外部系统导入
- `fork-*` — 从其它 Skill / 模板 fork
- `test-*` — e2e / 自动化测试专用；**生产库不应出现**

## 命名格式
- 手写业务 id：`EC-投放-01` / `my-skill` 等（≤ 50 字符 + CJK 允许）
- 自动生成：`<prefix>-<uuid6>` 或 `<slug>-<uuid6>`
"""

from __future__ import annotations

import re
from uuid import uuid4

from pypinyin import lazy_pinyin


SKILL_ID_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff\-_]{1,50}$")

# 自动生成的前缀白名单（业务 id 不必命中）
AUTO_PREFIXES = {
    "imp-": "import",
    "fork-": "fork",
    "test-": "e2e_test",
}

# 从 department 中文名生成短缩写（2~3 字） —— 用于 import id 可读性
_DEPT_ABBR_MAX = 4


def _slug_from_text(text: str, max_len: int = 40) -> str:
    """中文 → 拼音 → ascii slug；英文 → 清洗。

    产出 `[a-z0-9-]+`，长度 ≤ `max_len`，为空时返回 "skill"。
    """
    value = "-".join(lazy_pinyin((text or "").strip()))
    value = re.sub(r"[^a-zA-Z0-9\-]", "-", value)
    value = re.sub(r"-{2,}", "-", value).strip("-").lower()
    return value[:max_len] or "skill"


def _dept_abbr(department: str) -> str:
    """部门短码，ascii 化并截取前 _DEPT_ABBR_MAX 字符。"""
    slug = _slug_from_text(department or "unknown", max_len=_DEPT_ABBR_MAX * 3)
    return slug[:_DEPT_ABBR_MAX] or "dept"


# ─── 生成入口 ──────────────────────────────────────────────


def gen_from_name(name: str, department: str = "") -> str:
    """从 Skill 名字生成 id（对话创建 / Architect / 模板生成 Skill 用）。

    示例：("投诉分级", "客服部") → "tou-su-fen-ji-a1b2c3"
    """
    slug = _slug_from_text(name)
    return f"{slug}-{uuid4().hex[:6]}"


def gen_from_import(source_hint: str = "", department: str = "") -> str:
    """从外部系统 import 时生成 id。

    格式：`imp-{dept_abbr}-{uuid8}`；生产库识别这个前缀即可区分。
    """
    dept = _dept_abbr(department)
    return f"imp-{dept}-{uuid4().hex[:8]}"


def gen_from_fork(parent_id: str) -> str:
    """从已有 Skill / 模板 fork 时生成 id。

    格式：`fork-{parent_slug}-{uuid6}`，能从 id 反查 parent。
    """
    parent_slug = _slug_from_text(parent_id, max_len=16) or "parent"
    return f"fork-{parent_slug}-{uuid4().hex[:6]}"


def gen_for_test() -> str:
    """e2e / 自动化测试专用。任何命中 `test-*` 的 id **不应出现在生产库**。

    前端 e2e 也应该用后端生成的 id（或用 `test-` 前缀），避免污染生产。
    """
    return f"test-{uuid4().hex[:10]}"


# ─── 校验 / 分类 ──────────────────────────────────────────────


def validate_skill_id(skill_id: str) -> str:
    """统一校验入口。不合法 raise AppError("SKILL_ID_INVALID", 400)。"""
    if not skill_id or not SKILL_ID_PATTERN.match(skill_id):
        from app.common.exceptions import AppError

        raise AppError("SKILL_ID_INVALID", 400, {"detail": f"非法 skill_id: {skill_id!r}"})
    if ".." in skill_id or "/" in skill_id or "\\" in skill_id:
        from app.common.exceptions import AppError

        raise AppError("SKILL_ID_INVALID", 400, {"detail": "skill_id 包含危险字符"})
    return skill_id


def classify_skill_id(skill_id: str) -> str:
    """给定 id 返回来源分类：`import` / `fork` / `e2e_test` / `manual`。

    用于：数据卫生面板、观测埋点、清理脏数据扫描。

    约定：同名老格式 `imported-*` 分两种：
    - 全 hex（8 字符）视作**真实** zip import（老 router_assets 产物）
    - 包含非 hex 字母（g-z）的视作 **e2e 测试遗留**（Date.now().toString(36)）
    """
    for prefix, label in AUTO_PREFIXES.items():
        if skill_id.startswith(prefix):
            return label
    # 优先判严格的 hex 格式（老 zip import）
    if re.match(r"^imported-[0-9a-f]{8}$", skill_id):
        return "import"
    # 再判 base36（含 g-z，说明是 Date.now().toString(36)）
    if re.match(r"^imported-[a-z0-9]{8}$", skill_id):
        return "e2e_test"
    return "manual"


def is_e2e_test_id(skill_id: str) -> bool:
    """判定是否 e2e 测试残留（生产库不应出现的）。"""
    return classify_skill_id(skill_id) == "e2e_test"
