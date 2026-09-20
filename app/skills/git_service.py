"""兼容层：保留 `app.skills.git_service` 旧导入路径。"""

from app.skills.core.git_service import GitService, git_service

__all__ = ["GitService", "git_service"]
