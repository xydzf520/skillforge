"""
Playbook管理服务：基于文件系统的CRUD操作。
Playbook以YAML文件形式存储在 {SKILL_REPO_PATH}/playbooks/ 目录（与 Skill 同仓管理），
通过 git_service 统一做版本/tag/push。
"""

import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt
from app.config import settings

logger = logging.getLogger(__name__)

# Playbook文件存储目录（统一挂在 skills-repo 下，与 git_service 同仓）
PLAYBOOKS_DIR = Path(settings.SKILL_REPO_PATH) / "playbooks"

# 合法Playbook名称：字母、数字、中文、连字符、下划线
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9\u4e00-\u9fff\-_]{1,80}$")


def _sanitize_name(name: str) -> str:
    """
    校验并清理Playbook名称，防止路径穿越攻击。
    合法名称只允许字母、数字、中文、连字符、下划线。
    """
    if not name or not name.strip():
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "名称不能为空"})

    name = name.strip()

    # 阻止路径穿越
    if ".." in name or "/" in name or "\\" in name:
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "名称包含非法字符"})

    if not _NAME_PATTERN.match(name):
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "名称只允许字母、数字、中文、连字符、下划线，最长80字符"})

    # 最终防线：确保解析后的路径在PLAYBOOKS_DIR内
    resolved = (PLAYBOOKS_DIR / f"{name}.yaml").resolve()
    if not str(resolved).startswith(str(PLAYBOOKS_DIR.resolve())):
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "名称解析后超出允许目录"})

    return name


def _find_playbook_path(name: str) -> Path | None:
    """查找Playbook文件，优先 .yaml，其次 .yml"""
    yaml_path = PLAYBOOKS_DIR / f"{name}.yaml"
    if yaml_path.exists():
        return yaml_path
    yml_path = PLAYBOOKS_DIR / f"{name}.yml"
    if yml_path.exists():
        return yml_path
    return None


def _read_playbook_file(path: Path) -> dict:
    """读取并解析Playbook YAML文件"""
    try:
        content = path.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not isinstance(data, dict):
            raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "YAML内容不是合法的字典格式"})
        return data
    except yaml.YAMLError as e:
        logger.error("Playbook YAML解析失败: %s, 错误: %s", path, e)
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": f"YAML解析失败: {e}"})


def _get_file_meta(path: Path) -> dict:
    """获取文件元信息（修改时间、大小）"""
    stat = path.stat()
    return {
        "file_size": stat.st_size,
        "modified_at": isoformat_bjt(datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)),
    }


TEMPLATES_DIR = PLAYBOOKS_DIR / "templates"


async def list_templates() -> list[dict]:
    """扫描 playbooks/templates/ 目录，返回模板列表"""
    if not TEMPLATES_DIR.exists():
        return []

    result = []
    for f in sorted(TEMPLATES_DIR.glob("*.yaml")):
        try:
            data = _read_playbook_file(f)
            result.append({
                "file_name": f.stem,
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "steps_count": len(data.get("steps", [])),
                "steps": data.get("steps", []),
            })
        except AppError:
            pass
    return result


async def list_playbooks() -> list[dict]:
    """
    扫描playbooks/目录，返回所有Playbook元信息。
    按名称排序，解析失败的文件也会返回（标记错误信息）。
    """
    if not PLAYBOOKS_DIR.exists():
        return []

    # 收集所有YAML文件，去重（同名.yaml和.yml只取.yaml）
    seen_names: set[str] = set()
    files: list[Path] = []
    for ext in ("*.yaml", "*.yml"):
        for f in sorted(PLAYBOOKS_DIR.glob(ext)):
            if f.stem not in seen_names:
                seen_names.add(f.stem)
                files.append(f)

    result = []
    for f in files:
        item = {"file_name": f.stem}
        try:
            data = _read_playbook_file(f)
            meta = _get_file_meta(f)
            item.update({
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "department": data.get("department", ""),
                "steps_count": len(data.get("steps", [])),
                "trigger": data.get("trigger"),
                "sla_minutes": data.get("sla_minutes"),
                **meta,
            })
        except AppError:
            # 解析失败的文件也列出，标记错误
            meta = _get_file_meta(f)
            item.update({
                "name": f.stem,
                "description": "",
                "department": "",
                "steps_count": 0,
                "parse_error": True,
                **meta,
            })
        result.append(item)

    # 按名称排序
    result.sort(key=lambda x: x.get("name", ""))
    return result


async def get_playbook(name: str) -> dict:
    """
    读取单个Playbook，返回完整YAML数据和文件元信息。
    """
    name = _sanitize_name(name)
    path = _find_playbook_path(name)
    if not path:
        raise AppError("PLAYBOOK_NOT_FOUND", 404)

    data = _read_playbook_file(path)
    meta = _get_file_meta(path)

    return {
        "file_name": name,
        **data,
        "_meta": meta,
    }


async def save_playbook(name: str, data: dict) -> dict:
    """
    保存Playbook YAML文件。
    如果文件已存在则覆盖（last write wins），不存在则创建。
    """
    name = _sanitize_name(name)

    # 基本字段校验
    if "name" not in data:
        data["name"] = name
    if "steps" not in data or not isinstance(data.get("steps"), list):
        raise AppError("PLAYBOOK_INVALID", 400, detail={"reason": "缺少steps字段或steps不是列表"})

    # 确保目录存在
    PLAYBOOKS_DIR.mkdir(parents=True, exist_ok=True)

    # 写入YAML文件
    file_path = PLAYBOOKS_DIR / f"{name}.yaml"
    yaml_content = yaml.dump(
        data,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )

    try:
        file_path.write_text(yaml_content, encoding="utf-8")
        logger.info("Playbook保存成功: %s", file_path)
    except IOError as e:
        logger.error("Playbook保存失败: %s, 错误: %s", file_path, e)
        raise AppError("PLAYBOOK_INVALID", 500, detail={"reason": f"文件写入失败: {e}"})

    # Git 版本追踪：commit Playbook 变更（只入库本次 Playbook 文件，避免 git add -A 误带无关变更）
    try:
        from app.skills.core.git_service import git_service
        rel_path = str(file_path.relative_to(git_service.repo_path))
        git_service.commit_all(f"Playbook '{name}' 更新", paths=[rel_path])
        logger.info("Playbook Git commit成功: %s", name)
    except Exception as e:
        # Git 失败不阻塞保存
        logger.warning("Playbook Git commit失败: %s, 错误: %s", name, e)

    # 如果同名.yml文件存在，删除以避免歧义
    yml_path = PLAYBOOKS_DIR / f"{name}.yml"
    if yml_path.exists():
        try:
            yml_path.unlink()
            logger.info("删除冗余的.yml文件: %s", yml_path)
        except IOError:
            pass

    return {
        "file_name": name,
        "message": "保存成功",
    }


async def delete_playbook(name: str) -> dict:
    """删除Playbook YAML文件"""
    name = _sanitize_name(name)
    path = _find_playbook_path(name)
    if not path:
        raise AppError("PLAYBOOK_NOT_FOUND", 404)

    try:
        path.unlink()
        logger.info("Playbook已删除: %s", path)
    except IOError as e:
        logger.error("Playbook删除失败: %s, 错误: %s", path, e)
        raise AppError("PLAYBOOK_INVALID", 500, detail={"reason": f"文件删除失败: {e}"})

    try:
        from app.skills.core.git_service import git_service
        rel_path = str(path.relative_to(git_service.repo_path))
        git_service.commit_all(f"Playbook '{name}' 删除", paths=[rel_path])
    except Exception as e:
        logger.warning("Playbook Git commit(delete)失败: %s", e)

    return {
        "file_name": name,
        "message": "删除成功",
    }


async def validate_playbook(data: dict) -> dict:
    """
    校验Playbook结构完整性。
    检查项：
      1. 必填字段（name, steps）
      2. 每个step有id和skill_id
      3. depends_on引用的step存在
      4. 无循环依赖
    返回 {valid: bool, errors: [...]}
    """
    errors: list[str] = []

    # 1. 必填字段检查
    if not data.get("name"):
        errors.append("缺少name字段")
    if not data.get("steps"):
        errors.append("缺少steps字段或steps为空")
        return {"valid": False, "errors": errors}

    steps = data["steps"]
    if not isinstance(steps, list):
        errors.append("steps必须是列表")
        return {"valid": False, "errors": errors}

    # 2. 检查每个step的必填字段
    step_ids: set[str] = set()
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            errors.append(f"步骤{i+1}: 不是合法的字典格式")
            continue
        if not step.get("id"):
            errors.append(f"步骤{i+1}: 缺少id字段")
        else:
            if step["id"] in step_ids:
                errors.append(f"步骤{i+1}: id '{step['id']}' 重复")
            step_ids.add(step["id"])
        if not step.get("skill_id") and not step.get("skill"):
            errors.append(f"步骤{i+1}({step.get('id', '?')}): 缺少skill_id字段")

    # 3. 检查depends_on引用
    for step in steps:
        if not isinstance(step, dict):
            continue
        deps = step.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        for dep in deps:
            if dep not in step_ids:
                errors.append(f"步骤'{step.get('id', '?')}': depends_on引用了不存在的步骤'{dep}'")

    # 4. 循环依赖检测（拓扑排序）
    if not errors:
        cycle_error = _detect_cycle(steps)
        if cycle_error:
            errors.append(cycle_error)

    return {
        "valid": len(errors) == 0,
        "errors": errors,
    }


def _detect_cycle(steps: list[dict]) -> str | None:
    """
    检测步骤之间的循环依赖。
    使用DFS染色法：白色(0)=未访问，灰色(1)=访问中，黑色(2)=已完成。
    """
    # 构建邻接表
    graph: dict[str, list[str]] = {}
    for step in steps:
        step_id = step.get("id", "")
        deps = step.get("depends_on", [])
        if isinstance(deps, str):
            deps = [deps]
        # 边方向：依赖 → 被依赖者指向依赖者（dep → step_id，表示dep完成后才能执行step_id）
        # 但检测循环只需要从step_id出发看deps
        graph[step_id] = deps

    color: dict[str, int] = {s.get("id", ""): 0 for s in steps}

    def dfs(node: str) -> str | None:
        color[node] = 1  # 灰色：正在访问
        for dep in graph.get(node, []):
            if dep not in color:
                continue
            if color[dep] == 1:
                return f"检测到循环依赖: {node} → {dep}"
            if color[dep] == 0:
                result = dfs(dep)
                if result:
                    return result
        color[node] = 2  # 黑色：已完成
        return None

    for step_id in graph:
        if color.get(step_id, 0) == 0:
            result = dfs(step_id)
            if result:
                return result

    return None
