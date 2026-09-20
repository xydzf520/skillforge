"""Hermes Agent 兼容适配器。

将 SkillForge 格式的 SKILL.md 转换为 Hermes Agent 可加载的格式，
生成 SKILL.hermes.md + _hermes_entry.py CLI 入口。

用法：
    from app.skills.integrations.hermes_adapter import generate_hermes_files
    generate_hermes_files(skill_id)  # 在 skills-repo/{skill_id}/ 下生成文件
"""

from __future__ import annotations

from pathlib import Path

import yaml
from loguru import logger

from app.config import settings
from app.skills.core.parser import skill_parser


def generate_hermes_files(skill_id: str) -> dict:
    """为指定 Skill 生成 Hermes Agent 兼容文件。

    生成：
      - SKILL.hermes.md — Hermes 格式的 Skill 定义
      - scripts/_hermes_entry.py — CLI 入口包装

    Returns:
        {"hermes_md": bool, "cli_entry": bool, "files": list[str]}
    """
    skill_dir = Path(settings.SKILL_REPO_PATH) / skill_id
    if not skill_dir.exists():
        return {"hermes_md": False, "cli_entry": False, "files": [], "error": "skill_dir not found"}

    skill_md_path = skill_dir / "SKILL.md"
    if not skill_md_path.exists():
        return {"hermes_md": False, "cli_entry": False, "files": [], "error": "SKILL.md not found"}

    skill_md = skill_md_path.read_text(encoding="utf-8")
    parsed = skill_parser.parse(skill_md)
    fm = parsed.frontmatter

    generated_files = []

    # 1. 生成 SKILL.hermes.md
    hermes_md = _build_hermes_skill_md(skill_id, parsed, fm, skill_dir)
    hermes_path = skill_dir / "SKILL.hermes.md"
    hermes_path.write_text(hermes_md, encoding="utf-8")
    generated_files.append("SKILL.hermes.md")

    # 2. 生成 scripts/_hermes_entry.py
    scripts_dir = skill_dir / "scripts"
    py_scripts = list(scripts_dir.glob("*.py")) if scripts_dir.exists() else []
    # 排除已有的 _hermes_entry.py 和 __pycache__
    py_scripts = [s for s in py_scripts if not s.name.startswith("_") and not s.name.startswith(".")]

    if py_scripts:
        entry_path = scripts_dir / "_hermes_entry.py"
        entry_content = _build_cli_entry(skill_id, py_scripts)
        entry_path.write_text(entry_content, encoding="utf-8")
        entry_path.chmod(0o755)
        generated_files.append("scripts/_hermes_entry.py")

    logger.info("Hermes 兼容文件已生成: skill={} files={}", skill_id, generated_files)
    return {"hermes_md": True, "cli_entry": bool(py_scripts), "files": generated_files}


def _build_hermes_skill_md(skill_id: str, parsed, fm: dict, skill_dir: Path) -> str:
    """构建 Hermes 格式的 SKILL.md 内容。"""
    parts = []

    # Hermes frontmatter
    hermes_fm = {
        "name": fm.get("name", skill_id),
        "description": fm.get("description", ""),
    }

    # 平台声明
    hermes_fm["platforms"] = ["linux"]

    # metadata.hermes 配置
    hermes_meta = {
        "requires_toolsets": ["terminal", "file"],
    }

    # 从 params 生成 config 变量
    params = fm.get("params", {})
    if isinstance(params, dict):
        config_vars = []
        for key, spec in params.items():
            if isinstance(spec, dict):
                config_vars.append({
                    "key": key,
                    "description": spec.get("description", ""),
                    "default": str(spec.get("default", "")),
                })
            else:
                config_vars.append({"key": key, "default": str(spec)})
        if config_vars:
            hermes_meta["config"] = config_vars

    hermes_fm["metadata"] = {"hermes": hermes_meta}

    # 输出 frontmatter
    parts.append("---")
    parts.append(yaml.dump(hermes_fm, allow_unicode=True, default_flow_style=False, sort_keys=False).strip())
    parts.append("---")
    parts.append("")

    # 标题
    name = fm.get("name", skill_id)
    parts.append(f"# {name}")
    parts.append("")

    # 目的
    if parsed.purpose:
        parts.append(parsed.purpose)
        parts.append("")

    # 可用脚本
    scripts_dir = skill_dir / "scripts"
    if scripts_dir.exists():
        py_files = [f.name for f in scripts_dir.glob("*.py") if not f.name.startswith("_")]
        if py_files:
            parts.append("## 可用脚本")
            parts.append("")
            parts.append("通过终端工具执行以下脚本：")
            parts.append("")
            for pf in sorted(py_files):
                parts.append(f"```bash")
                parts.append(f"python3 scripts/{pf} --help")
                parts.append(f"```")
                parts.append("")

    # 决策规则（从 steps 转换为自然语言指令）
    if parsed.steps:
        parts.append("## 决策规则")
        parts.append("")
        for step in parsed.steps:
            parts.append(f"### Step {step.id}: {step.name}")
            parts.append("")
            if step.description:
                parts.append(step.description)
                parts.append("")
            for branch in step.branches:
                line = f"- 如果 {branch.condition}"
                if branch.conclusion:
                    line += f" → **{branch.conclusion}**"
                parts.append(line)
                if branch.action:
                    parts.append(f"  - 动作: {branch.action}")
                if branch.next_step:
                    parts.append(f"  - 然后执行 Step {branch.next_step}")
            parts.append("")

    # 参数说明
    if isinstance(params, dict) and params:
        parts.append("## 参数")
        parts.append("")
        parts.append("| 参数 | 类型 | 说明 | 默认值 |")
        parts.append("|------|------|------|--------|")
        for key, spec in params.items():
            if isinstance(spec, dict):
                parts.append(f"| `{key}` | {spec.get('type', 'string')} | {spec.get('description', '')} | {spec.get('default', '')} |")
            else:
                parts.append(f"| `{key}` | string | | {spec} |")
        parts.append("")

    # 反例
    if parsed.antipatterns:
        parts.append("## 注意事项（反例）")
        parts.append("")
        for ap in parsed.antipatterns:
            parts.append(f"- **不要**: {ap.scenario}")
            parts.append(f"  **正确做法**: {ap.correct_action}")
        parts.append("")

    # 参考文档
    refs_dir = skill_dir / "references"
    if refs_dir.exists():
        ref_files = [f.name for f in refs_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
        if ref_files:
            parts.append("## 参考资料")
            parts.append("")
            for rf in sorted(ref_files):
                parts.append(f"- `references/{rf}`")
            parts.append("")

    return "\n".join(parts)


def _build_cli_entry(skill_id: str, py_scripts: list[Path]) -> str:
    """生成 _hermes_entry.py CLI 入口。"""
    script_names = [s.name for s in py_scripts]
    default_script = script_names[0] if script_names else "main.py"

    return f'''#!/usr/bin/env python3
"""Hermes Agent CLI 入口 — 自动生成，勿手动修改。

将 SkillForge 脚本包装为命令行工具，兼容 Hermes Agent 的 terminal 工具调用。
可用脚本: {', '.join(script_names)}

用法:
    python3 scripts/_hermes_entry.py --script analyze.py --params '{{"date": "2026-01-01"}}'
    python3 scripts/_hermes_entry.py --list
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
SKILL_DIR = SCRIPTS_DIR.parent
AVAILABLE_SCRIPTS = {sorted(script_names)}


def load_and_run(script_name: str, params: dict) -> dict:
    """动态加载并执行指定脚本。"""
    script_path = SCRIPTS_DIR / script_name
    if not script_path.exists():
        return {{"error": f"脚本不存在: {{script_name}}", "available": list(AVAILABLE_SCRIPTS)}}

    spec = importlib.util.spec_from_file_location("skill_script", script_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 兼容两种入口
    if hasattr(module, "main"):
        return module.main(params)
    elif hasattr(module, "execute"):
        return module.execute(params)
    else:
        return {{"error": f"脚本 {{script_name}} 缺少 main() 或 execute() 入口"}}


def main():
    parser = argparse.ArgumentParser(description="{skill_id} - Hermes Agent CLI 入口")
    parser.add_argument("--script", "-s", default="{default_script}", help="要执行的脚本名")
    parser.add_argument("--params", "-p", default="{{}}", help="JSON 格式参数")
    parser.add_argument("--list", "-l", action="store_true", help="列出可用脚本")
    args = parser.parse_args()

    if args.list:
        print(json.dumps({{"skill": "{skill_id}", "scripts": sorted(AVAILABLE_SCRIPTS)}}, ensure_ascii=False, indent=2))
        return

    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as e:
        print(json.dumps({{"error": f"参数 JSON 解析失败: {{e}}"}}, ensure_ascii=False))
        sys.exit(1)

    result = load_and_run(args.script, params)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
'''
