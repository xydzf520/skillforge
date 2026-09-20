"""
分段式 Prompt Registry。

设计参考 aiclawcode/src/constants/systemPromptSections.ts 的思路：
- 所有 system prompt 集中注册（启动时从 app/common/prompts/*.md 自动加载）
- 支持版本号 + 默认版本切换 + feature gate
- 每个 prompt 有 hash，用于审计追溯（"本次审核用的哪版 prompt"）
- build(name, context) → (rendered_text, hash) 返回元组

使用方式：
    from app.common.prompt_registry import prompt_registry

    rendered, prompt_hash = prompt_registry.build(
        "agent_chat",
        context={"skill_name": "...", "skill_md_content": "..."},
    )
    # rendered 传给 LLM，prompt_hash 传给审计日志

文件命名规则：
    app/common/prompts/<name>@<version>.md
    例如：agent_chat@v1.md, reviewer@v2.md
"""

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger


# 仅匹配 {word} 形式的占位符（word 必须以字母/下划线开头）。
# 这样 JSON 例子里的 {"key": ...} 不会被误替换。
_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def safe_render(template: str, context: dict) -> str:
    """安全渲染：只替换 {word} 形式的占位符，缺失时保留原文。

    比 str.format() 安全，因为：
    - 不会因 JSON {} 中的内容崩溃
    - 不会因缺失 placeholder 抛 KeyError
    - 不会被恶意 {0!r} / {x.attr} 等格式语法利用
    """
    def replace(match: re.Match) -> str:
        key = match.group(1)
        value = context.get(key)
        if value is None:
            return match.group(0)  # 保留 {key} 原文
        return str(value)
    return _PLACEHOLDER_RE.sub(replace, template)


@dataclass
class PromptSection:
    """单个已注册的 prompt section"""

    name: str
    """逻辑名称，如 'agent_chat' / 'reviewer'"""

    content: str
    """Markdown 模板内容，可包含 {placeholder} 占位符"""

    version: str = "v1"
    """版本号，常见 v1/v2/v3"""

    priority: int = 0
    """优先级（多版本时高 priority 优先；目前未广泛使用）"""

    cached: bool = True
    """是否走 prompt cache（标记位，由调用方决定是否启用）"""

    gate: Callable[[dict], bool] | None = None
    """feature gate：(context) -> bool；返回 False 时该版本被跳过"""

    description: str = ""
    """人类可读描述，用于管理界面展示"""

    @property
    def hash(self) -> str:
        """基于 name@version + content 计算的 12 字符 hash

        历史说明（1.6.0 迁移）：
        - optimizer 旧路径用 md5 10 字符 (`hashlib.md5(prompt).hexdigest()[:10]`)
        - PromptRegistry 统一改为 sha256 12 字符
        - 跨版本无法直接对比 hash 值；旧 usage_logs 的 prompt_hash 仅作为历史快照
        - 同一 (name, version, content) 在 sha256 下哈希稳定，可用于跨链路定位
        """
        digest = hashlib.sha256(
            f"{self.name}@{self.version}\n{self.content}".encode("utf-8")
        ).hexdigest()
        return digest[:12]


class PromptRegistry:
    """全局 prompt 注册表。

    单例：通过模块底部的 `prompt_registry` 访问。
    """

    def __init__(self):
        # name → [section, section, ...]（多个版本）
        self._sections: dict[str, list[PromptSection]] = {}
        # name → 当前默认版本号
        self._default_versions: dict[str, str] = {}

    # ===== 注册 =====

    def register(self, section: PromptSection) -> None:
        """注册一个 PromptSection。同 name 的多个版本可重复调用。

        默认版本规则：第一个被注册的版本就是默认版本。
        """
        existing = self._sections.setdefault(section.name, [])
        # 防止重复注册相同 (name, version)
        for idx, existing_section in enumerate(existing):
            if existing_section.version == section.version:
                # 同 name + version，覆盖（开发期热更新友好）
                existing[idx] = section
                logger.debug(
                    f"Prompt 已覆盖: {section.name}@{section.version} hash={section.hash}"
                )
                return
        existing.append(section)
        # 第一次注册时设为默认
        if section.name not in self._default_versions:
            self._default_versions[section.name] = section.version
        logger.info(
            f"Prompt 已注册: {section.name}@{section.version} hash={section.hash}"
        )

    def load_from_dir(self, prompts_dir: Path) -> int:
        """从目录批量加载 *.md 文件。

        文件名格式：
            <name>@<version>.md     例如 agent_chat@v1.md
            <name>.md               没有 @ 时默认 version=v1

        返回成功加载的数量。
        """
        if not prompts_dir.exists():
            logger.warning(f"Prompts 目录不存在: {prompts_dir}")
            return 0

        loaded_count = 0
        for md_file in sorted(prompts_dir.glob("*.md")):
            # 跳过 README
            if md_file.stem.upper() == "README":
                continue
            stem = md_file.stem
            if "@" in stem:
                name, version = stem.split("@", 1)
            else:
                name, version = stem, "v1"

            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"读取 prompt 文件失败 {md_file}: {e}")
                continue

            self.register(PromptSection(
                name=name,
                content=content,
                version=version,
            ))
            loaded_count += 1

        logger.info(f"PromptRegistry: 从 {prompts_dir} 加载了 {loaded_count} 个 prompt")
        return loaded_count

    # ===== 查询与构建 =====

    def build(
        self,
        name: str,
        context: dict | None = None,
        version: str | None = None,
    ) -> tuple[str, str]:
        """根据 name + 可选 version 构建最终 prompt。

        Args:
            name: prompt 名称
            context: 替换 {placeholder} 的字典
            version: 显式指定版本；不传则用默认版本

        Returns:
            (rendered_text, prompt_hash) 元组
            - rendered_text: 替换占位符后的最终文本
            - prompt_hash: 12 字符 hash，用于审计日志

        Raises:
            KeyError: prompt 名称或版本不存在
        """
        sections = self._sections.get(name)
        if not sections:
            raise KeyError(f"Prompt 未注册: {name}")

        target_version = version or self._default_versions.get(name)
        if target_version is None:
            raise KeyError(f"Prompt 无默认版本: {name}")

        matched = [s for s in sections if s.version == target_version]
        if not matched:
            raise KeyError(f"Prompt 版本未找到: {name}@{target_version}")
        section = matched[0]

        # Feature gate（可选）
        if section.gate is not None:
            try:
                gate_passed = section.gate(context or {})
            except Exception as e:
                logger.warning(f"Prompt {name}@{target_version} feature gate 抛异常: {e}")
                gate_passed = False

            if not gate_passed:
                # 回退到 v1（如果存在且不是当前版本）
                fallback = next(
                    (s for s in sections if s.version == "v1"),
                    None,
                )
                if fallback and fallback is not section:
                    section = fallback
                # 否则保留当前版本（gate 失败的版本仍然渲染）

        # 渲染占位符（用安全替换，避免 JSON {} 冲突）
        rendered = safe_render(section.content, context or {})
        return rendered, section.hash

    def get_section(self, name: str, version: str | None = None) -> PromptSection | None:
        """直接获取 PromptSection 对象（用于管理界面）"""
        sections = self._sections.get(name)
        if not sections:
            return None
        target_version = version or self._default_versions.get(name)
        return next((s for s in sections if s.version == target_version), None)

    # ===== 管理 =====

    def set_default(self, name: str, version: str) -> None:
        """切换某 prompt 的默认版本"""
        sections = self._sections.get(name)
        if not sections:
            raise KeyError(f"Prompt 未注册: {name}")
        if not any(s.version == version for s in sections):
            raise KeyError(f"Prompt 版本不存在: {name}@{version}")
        self._default_versions[name] = version
        logger.info(f"Prompt 默认版本已切换: {name} → {version}")

    def list_all(self) -> list[dict]:
        """列出所有已注册 prompt 的元数据，供管理界面调用"""
        result = []
        for name in sorted(self._sections.keys()):
            sections = self._sections[name]
            result.append({
                "name": name,
                "default_version": self._default_versions.get(name, ""),
                "versions": [
                    {
                        "version": s.version,
                        "hash": s.hash,
                        "description": s.description,
                        "priority": s.priority,
                        "cached": s.cached,
                        "has_gate": s.gate is not None,
                        "content_preview": (
                            s.content[:200] + "..."
                            if len(s.content) > 200 else s.content
                        ),
                    }
                    for s in sections
                ],
            })
        return result

    def clear(self) -> None:
        """清空所有注册（仅测试用）"""
        self._sections.clear()
        self._default_versions.clear()


# 全局单例
prompt_registry = PromptRegistry()


def init_registry() -> int:
    """在 app.main lifespan 中调用。从默认路径加载所有 prompt 文件。"""
    base_dir = Path(__file__).resolve().parent
    prompts_dir = base_dir / "prompts"
    return prompt_registry.load_from_dir(prompts_dir)
