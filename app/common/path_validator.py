"""公共路径校验工具。

防止路径穿越（path traversal）攻击，供所有需要校验相对路径的模块复用。
"""
from pathlib import Path

from app.common.exceptions import AppError


def validate_file_path(relative_path: str, base_dir: Path) -> Path:
    """校验相对路径是否在 base_dir 内，防止路径穿越。

    Args:
        relative_path: 相对于 base_dir 的文件路径
        base_dir: 允许访问的根目录

    Returns:
        resolve 后的绝对路径

    Raises:
        AppError("PARAM_INVALID", 400): 路径非法或越界
    """
    if not relative_path or relative_path.startswith(("/", "..")) or "/../" in relative_path:
        raise AppError("PARAM_INVALID", 400, {"detail": "非法文件路径"})
    target = (base_dir / relative_path).resolve()
    try:
        target.relative_to(base_dir.resolve())
    except ValueError:
        raise AppError("PARAM_INVALID", 400, {"detail": "路径越界"})
    return target
