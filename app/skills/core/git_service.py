"""
Git操作封装：用GitPython管理skills-repo的版本。
所有Skill文件变更通过此模块进行commit/diff/log/tag/revert。
"""

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from git import Repo

from app.config import settings
from app.common.time_utils import BJT, isoformat_bjt, now_bjt

# 合法的 Git commit ref 格式：hash / HEAD~N / tag / 分支名
_SAFE_REF_RE = re.compile(r"^[a-fA-F0-9]{4,40}$|^HEAD(~\d+)?$|^v[\d.]+$|^[a-zA-Z][\w\-./]*$")


def _bjt_commit_dates() -> tuple[str, str]:
    """Return display and Git metadata timestamps in Beijing time."""
    timestamp = now_bjt()
    display = isoformat_bjt(timestamp) or timestamp.isoformat()
    git_date = timestamp.replace(tzinfo=BJT).strftime("%Y-%m-%dT%H:%M:%S%z")
    return display, git_date


def _validate_commit_ref(ref: str) -> str:
    """校验 Git commit ref，防止命令注入"""
    if not ref or not _SAFE_REF_RE.match(ref):
        from app.common.exceptions import AppError
        raise AppError("PARAM_INVALID", 400, {"detail": f"非法的 Git ref: {ref}"})
    # 额外阻止 -- 开头（git 参数注入）
    if ref.startswith("-"):
        from app.common.exceptions import AppError
        raise AppError("PARAM_INVALID", 400, {"detail": f"非法的 Git ref: {ref}"})
    return ref


class GitService:
    """Skill Git仓库操作服务"""

    def __init__(self, repo_path: str | None = None):
        self._repo_path = Path(repo_path or settings.SKILL_REPO_PATH)
        self._repo: Repo | None = None
        # [M7] 按 skill_id 隔离的 asyncio.Lock — 防止 TOCTOU:
        # 例如 aiclaw 反向导入: has_uncommitted_changes 检查与 write_file/commit_all 之间
        # 若另一并发请求修改了同一 skill 工作树, 后到的 import 会覆盖前一个的未入库编辑
        self._skill_locks: dict[str, asyncio.Lock] = {}
        self._skill_locks_lock = asyncio.Lock()  # 保护 _skill_locks 字典本身

    _MAX_LOCKS = 200  # 锁字典上限，超过时淘汰最早的

    async def _get_skill_lock(self, skill_id: str) -> asyncio.Lock:
        """惰性创建按 skill_id 隔离的 asyncio.Lock。超过上限时淘汰未持有的旧锁。"""
        async with self._skill_locks_lock:
            lock = self._skill_locks.get(skill_id)
            if lock is not None:
                return lock
            # 淘汰未持有的旧锁
            if len(self._skill_locks) >= self._MAX_LOCKS:
                to_remove = [k for k, v in self._skill_locks.items() if not v.locked()]
                for k in to_remove[:len(self._skill_locks) - self._MAX_LOCKS + 1]:
                    del self._skill_locks[k]
            lock = asyncio.Lock()
            self._skill_locks[skill_id] = lock
            return lock

    @asynccontextmanager
    async def skill_advisory_lock(self, skill_id: str):
        """[M7] async context manager — 包住 read+check+write+commit 序列, 杜绝 TOCTOU。

        用法:
            async with git_service.skill_advisory_lock(skill_id):
                if git_service.has_uncommitted_changes(skill_id):
                    raise ...
                git_service.write_file(skill_id, "SKILL.md", new_md)
                git_service.commit_all("...", user_id, skill_id=skill_id)
        """
        lock = await self._get_skill_lock(skill_id)
        async with lock:
            yield

    @property
    def repo(self) -> Repo:
        if self._repo is None:
            self._repo_path.mkdir(parents=True, exist_ok=True)
            git_dir = self._repo_path / ".git"
            if not git_dir.exists():
                from app.common.exceptions import AppError

                raise AppError(
                    "SKILL_GIT_REPO_MISSING",
                    500,
                    {"detail": f"Skill Git 仓库未初始化: {self._repo_path}"},
                )
            self._repo = Repo(self._repo_path, search_parent_directories=False)
            worktree = Path(self._repo.working_tree_dir or "").resolve()
            if worktree != self._repo_path.resolve():
                from app.common.exceptions import AppError

                raise AppError(
                    "SKILL_GIT_REPO_INVALID",
                    500,
                    {"detail": f"Skill Git 仓库根目录异常: {worktree}"},
                )
            # 设置Git用户信息（避免commit报错）
            with self._repo.config_writer() as cw:
                cw.set_value("user", "name", settings.GIT_USER_NAME)
                cw.set_value("user", "email", settings.GIT_USER_EMAIL)
        return self._repo

    @property
    def repo_path(self) -> Path:
        return self._repo_path

    def skill_dir(self, skill_id: str) -> Path:
        """获取某个Skill的目录路径"""
        return self._repo_path / skill_id

    def skill_exists(self, skill_id: str) -> bool:
        """检查Skill目录是否存在"""
        return self.skill_dir(skill_id).is_dir()

    # 不应该出现在 skill 文件清单里的噪声 — 跟 coding_agent inventory 保持一致
    _SKIP_DIR_NAMES = {
        "__pycache__", ".git", "node_modules", ".pytest_cache",
        ".venv", "venv", "dist", "build", ".idea", ".vscode", ".mypy_cache",
    }
    _SKIP_FILE_SUFFIXES = {".pyc", ".pyo", ".so", ".o", ".class"}

    @classmethod
    def _is_noise_path(cls, path: Path, base: Path) -> bool:
        """判断路径是否是构建产物 / 缓存噪声, 不应进入文件清单。"""
        try:
            rel_parts = path.relative_to(base).parts
        except ValueError:
            return True
        if any(p in cls._SKIP_DIR_NAMES for p in rel_parts):
            return True
        if any(p.startswith(".") for p in rel_parts):
            return True
        if path.suffix in cls._SKIP_FILE_SUFFIXES:
            return True
        return False

    def list_skill_files(self, skill_id: str, subdir: str = "") -> list[str]:
        """列出Skill目录下某个子目录的所有文件（相对路径）。

        会自动过滤 __pycache__ / .pyc / node_modules 等构建产物 / 缓存,
        避免它们污染前端文件树和 GET /api/skills/{id} 响应。
        """
        skill_root = self.skill_dir(skill_id)
        base = skill_root / subdir
        if not base.is_dir():
            return []
        return sorted([
            str(f.relative_to(skill_root))
            for f in base.rglob("*")
            if f.is_file() and not self._is_noise_path(f, skill_root)
        ])

    def list_skill_tree(self, skill_id: str) -> list[dict]:
        """列出Skill目录的完整树形结构（文件+目录），返回嵌套结构"""
        base = self.skill_dir(skill_id)
        if not base.is_dir():
            return []

        def build_tree(directory: Path) -> list[dict]:
            items = []
            for entry in sorted(directory.iterdir(), key=lambda e: (e.is_file(), e.name)):
                if entry.name.startswith("."):
                    continue
                # 过滤 __pycache__ / .pyc 等构建噪声（和 list_skill_files 保持一致）
                if self._is_noise_path(entry, base):
                    continue
                rel = str(entry.relative_to(base))
                if entry.is_dir():
                    children = build_tree(entry)
                    # 空目录（全被过滤后）不显示
                    if children:
                        items.append({
                            "name": entry.name,
                            "path": rel,
                            "type": "dir",
                            "children": children,
                        })
                else:
                    items.append({
                        "name": entry.name,
                        "path": rel,
                        "type": "file",
                    })
            return items

        return build_tree(base)

    def list_skills(self) -> list[str]:
        """列出所有Skill目录名"""
        return sorted([
            d.name for d in self._repo_path.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ])

    def _validate_relative_path(self, relative_path: str) -> None:
        """校验相对路径，防止路径穿越（用于 git show / git checkout 等非文件系统操作）"""
        if ".." in relative_path or relative_path.startswith("/") or relative_path.startswith("\\"):
            from app.common.exceptions import AppError
            raise AppError("SKILL_FILE_NOT_FOUND", 404)

    def _safe_path(self, skill_id: str, relative_path: str) -> Path:
        """验证文件路径在skill目录内，防止路径穿越"""
        from app.common.path_validator import validate_file_path
        skill_dir = self.skill_dir(skill_id).resolve()
        try:
            return validate_file_path(relative_path, skill_dir)
        except Exception:
            # 保持原有错误码兼容性（404 而非 400）
            from app.common.exceptions import AppError
            raise AppError("SKILL_FILE_NOT_FOUND", 404)

    def read_file(
        self, skill_id: str, relative_path: str, *, errors: str = "strict"
    ) -> str | None:
        """读取Skill下的某个文件内容。

        errors='strict' (默认): UTF-8 严格模式, SKILL.md / policy_pack.yaml 等
            必须是合法 UTF-8 的关键文件用这个, 失败会 raise UnicodeDecodeError。
        errors='replace': 容错模式, 用于 get_skill 遍历目录时, 遇到 PNG/字体/PDF
            等二进制不崩 (字符变 replacement char 是预期, 二进制文件本来就不该 text 展示)。
        """
        file_path = self._safe_path(skill_id, relative_path)
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8", errors=errors)

    def write_file(self, skill_id: str, relative_path: str, content: str) -> Path:
        """写入Skill下的某个文件"""
        file_path = self._safe_path(skill_id, relative_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def delete_file(self, skill_id: str, relative_path: str) -> None:
        """删除Skill下的某个文件"""
        file_path = self._safe_path(skill_id, relative_path)
        if not file_path.exists():
            from app.common.exceptions import AppError
            raise AppError("SKILL_FILE_NOT_FOUND", 404)
        file_path.unlink()
        # 清理空目录
        parent = file_path.parent
        skill_dir = self.skill_dir(skill_id).resolve()
        while parent != skill_dir and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent

    def rename_file(self, skill_id: str, old_path: str, new_path: str) -> Path:
        """重命名/移动Skill下的文件"""
        src = self._safe_path(skill_id, old_path)
        dst = self._safe_path(skill_id, new_path)
        if not src.exists():
            from app.common.exceptions import AppError
            raise AppError("SKILL_FILE_NOT_FOUND", 404)
        dst.parent.mkdir(parents=True, exist_ok=True)
        src.rename(dst)
        return dst

    def create_dir(self, skill_id: str, relative_path: str) -> Path:
        """在Skill下创建目录"""
        dir_path = self._safe_path(skill_id, relative_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        return dir_path

    def delete_skill_dir(self, skill_id: str) -> None:
        """删除Skill目录及其所有文件。"""
        import shutil
        skill_dir = self.skill_dir(skill_id)
        if skill_dir.exists():
            shutil.rmtree(skill_dir)

    def create_skill_dir(self, skill_id: str) -> Path:
        """创建Skill目录结构"""
        skill_dir = self.skill_dir(skill_id)
        (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
        (skill_dir / "tests").mkdir(parents=True, exist_ok=True)
        (skill_dir / "data" / "sample").mkdir(parents=True, exist_ok=True)
        return skill_dir

    def has_uncommitted_changes(self, skill_id: str) -> bool:
        """检查指定 skill 目录是否存在未提交的修改（含 untracked / modified / staged）。

        用于反向导入前的安全检查：DB 没记录但工作区残留目录时，必须确认 git 是 clean 的，
        否则可能覆盖正在编辑的内容。
        """
        repo = self.repo
        rel_prefix = f"{skill_id}/"
        # modified / staged
        for item in repo.index.diff(None):
            if item.a_path and item.a_path.startswith(rel_prefix):
                return True
        for item in repo.index.diff("HEAD"):
            if item.a_path and item.a_path.startswith(rel_prefix):
                return True
        # untracked
        for path in repo.untracked_files:
            if path.startswith(rel_prefix):
                return True
        return False

    def commit(self, skill_id: str, message: str, author: str = "SkillForge") -> str:
        """
        将指定Skill目录的所有变更commit。
        返回commit hash。
        """
        repo = self.repo
        skill_dir_rel = skill_id  # 相对于repo根目录

        # 添加该Skill目录下的所有变更
        repo.index.add([skill_dir_rel])
        # 也添加被删除的文件
        deleted = [item.a_path for item in repo.index.diff(None) if item.change_type == "D"]
        for d in deleted:
            if d.startswith(skill_dir_rel + "/"):
                repo.index.remove([d])

        # 检查是否有实际变更
        if not repo.index.diff("HEAD") and not repo.untracked_files:
            return ""  # 无变更

        timestamp_text, git_date = _bjt_commit_dates()
        commit_obj = repo.index.commit(
            f"[{skill_id}] {message}\n\nAuthor: {author}\nTimestamp: {timestamp_text}",
            author_date=git_date,
            commit_date=git_date,
        )
        return commit_obj.hexsha

    def commit_all(
        self,
        message: str,
        author: str = "SkillForge",
        skill_id: str | None = None,
        paths: list[str] | None = None,
        validate: bool = True,
    ) -> str:
        """提交变更。

        - 指定 ``skill_id``：仅 add 该 Skill 目录。
        - 指定 ``paths``：仅 add 给定路径（字符串相对于 repo 根）。
        - 两者都未指定：保留历史 ``git add -A`` 行为，但打 warning
          提示调用方该传 paths — 全仓库提交会把无关变更也带进来。

        默认 commit 之前会跑 tripleyak 风格的结构校验:
          - frontmatter 必填字段 + 命名 / 长度
          - SKILL.md 行数 (硬上限 1000)
          - references/ scripts/ 结构
        校验失败会 raise SKILL_VALIDATION_FAILED, 阻止 commit, 防止劣质 Skill 进入历史。
        AI 草稿自动保存可传 ``validate=False`` 做 draft checkpoint；审核/发布链路仍应保持默认校验。
        """
        # ── pre-commit hook: 结构校验 ──
        if validate and skill_id and self.skill_exists(skill_id):
            from app.common.exceptions import AppError
            from app.skills.validators import structural_validate

            report = structural_validate(self.skill_dir(skill_id))
            if not report.ok:
                from loguru import logger as _logger
                _logger.warning(
                    "Skill {} 结构校验未通过, 拒绝 commit:\n{}",
                    skill_id, report.format_text(),
                )
                raise AppError(
                    "SKILL_VALIDATION_FAILED",
                    422,
                    {"detail": report.to_detail()},
                )

        repo = self.repo
        scoped_paths: list[str] = []
        if skill_id:
            # 只 add 指定 Skill 目录，防止误提交其他变更
            skill_dir_rel = str(self.skill_dir(skill_id).relative_to(self._repo_path))
            scoped_paths = [skill_dir_rel]
            repo.git.add("-A", "--", skill_dir_rel)
        elif paths:
            scoped_paths = [str(path) for path in paths]
            repo.git.add("-A", "--", *scoped_paths)
        else:
            from loguru import logger as _logger
            _logger.warning(
                "git_service.commit_all 未传 skill_id / paths — "
                "回退到 git add -A（会把仓库内所有改动一并入库，谨慎使用）"
            )
            repo.git.add(A=True)

        if scoped_paths:
            try:
                staged_changes = []
                for path in scoped_paths:
                    staged_changes.extend(repo.index.diff("HEAD", paths=path))
            except Exception:
                try:
                    staged_changes = list(repo.index.diff("HEAD"))
                except Exception:
                    staged_changes = list(repo.index.entries.values())
            if not staged_changes:
                return ""
        elif not repo.is_dirty(untracked_files=True):
            return ""

        timestamp_text, git_date = _bjt_commit_dates()
        commit_message = f"{message}\n\nAuthor: {author}\nTimestamp: {timestamp_text}"
        if scoped_paths:
            with repo.git.custom_environment(
                GIT_AUTHOR_DATE=git_date,
                GIT_COMMITTER_DATE=git_date,
                TZ="CST-8",
            ):
                repo.git.commit("-m", commit_message, "--", *scoped_paths)
            return repo.head.commit.hexsha

        commit_obj = repo.index.commit(
            commit_message,
            author_date=git_date,
            commit_date=git_date,
        )
        return commit_obj.hexsha

    def tag(self, tag_name: str, message: str = "") -> None:
        """创建Git tag（审核通过后打版本标签）"""
        self.repo.create_tag(tag_name, message=message or tag_name)

    def delete_tag(self, tag_name: str) -> bool:
        """删除本地 Git tag（push 失败时回滚用）。成功返回 True。"""
        try:
            self.repo.delete_tag(tag_name)
            return True
        except Exception:
            return False

    def log(self, skill_id: str | None = None, max_count: int = 20) -> list[dict]:
        """
        查看提交历史。
        如果指定skill_id，只返回该Skill相关的commits。
        """
        repo = self.repo
        kwargs = {"max_count": max_count}
        if skill_id:
            kwargs["paths"] = skill_id

        commits = list(repo.iter_commits(**kwargs))
        return [
            {
                "hash": c.hexsha[:8],
                "hash_full": c.hexsha,
                "message": c.message.strip().split("\n")[0],
                "author": c.author.name,
                "date": isoformat_bjt(datetime.fromtimestamp(c.committed_date, timezone.utc)),
            }
            for c in commits
        ]

    def diff(self, skill_id: str | None = None, commit_a: str = "HEAD~1", commit_b: str = "HEAD") -> str:
        """
        获取两个commit之间的diff文本。
        """
        _validate_commit_ref(commit_a)
        _validate_commit_ref(commit_b)
        repo = self.repo
        try:
            diff_text = repo.git.diff(commit_a, commit_b, "--", skill_id or ".")
        except Exception:
            diff_text = ""
        return diff_text

    def diff_working(self, skill_id: str | None = None) -> str:
        """获取工作区和HEAD之间的diff"""
        repo = self.repo
        try:
            if skill_id:
                return repo.git.diff("HEAD", "--", skill_id)
            return repo.git.diff("HEAD")
        except Exception:
            return ""

    @classmethod
    def _is_noise_relative(cls, rel_path: str) -> bool:
        """跟 _is_noise_path 同样的过滤规则, 但接受字符串相对路径 (用于 git diff 输出)。"""
        from pathlib import PurePosixPath
        try:
            parts = PurePosixPath(rel_path).parts
        except Exception:
            return False
        if any(p in cls._SKIP_DIR_NAMES for p in parts):
            return True
        if any(p.startswith(".") for p in parts):
            return True
        suffix = ""
        if "." in parts[-1]:
            suffix = "." + parts[-1].rsplit(".", 1)[-1]
        if suffix in cls._SKIP_FILE_SUFFIXES:
            return True
        return False

    def diff_summary(
        self, skill_id: str, commit_a: str = "HEAD", commit_b: str = "HEAD~1"
    ) -> dict:
        """两个 commit 之间的差异统计 + 每文件状态。

        用于"恢复某个版本"前给用户的预览：
        - 哪些文件会被新增 / 修改 / 删除
        - 总共增加 / 删除多少行
        - "恢复后将多出 / 少掉哪些文件"

        语义: a → b 的变化（a 是当前, b 是目标版本）
        - status='A' (added in b vs a) — 恢复后会**新增**这个文件
        - status='D' (deleted in b vs a) — 恢复后会**删除**这个文件
        - status='M' (modified) — 恢复后内容会回滚

        返回:
            {
                "from": "abc1234", "to": "def5678",
                "files": [
                    {"path": "scripts/x.py", "status": "D",
                     "insertions": 0, "deletions": 120},
                    ...
                ],
                "total_files": 3,
                "total_insertions": 50, "total_deletions": 200,
            }
        """
        _validate_commit_ref(commit_a)
        _validate_commit_ref(commit_b)
        repo = self.repo
        files: list[dict] = []
        total_ins = 0
        total_del = 0
        try:
            # numstat 格式: insertions\tdeletions\tpath
            numstat = repo.git.diff(
                commit_a, commit_b, "--numstat", "--", skill_id or ".",
            )
            # name-status 格式: A/M/D/R\tpath
            name_status = repo.git.diff(
                commit_a, commit_b, "--name-status", "--", skill_id or ".",
            )
            status_map: dict[str, str] = {}
            for line in name_status.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    status_map[parts[-1]] = parts[0][0]  # 取首字母 A/M/D/R
            for line in numstat.splitlines():
                parts = line.split("\t")
                if len(parts) < 3:
                    continue
                ins_str, del_str, path = parts[0], parts[1], parts[2]
                # 跳过 noise (__pycache__/.pyc/node_modules 等), 跟前端文件树过滤一致
                if self._is_noise_relative(path):
                    continue
                # 二进制文件 numstat 用 "-" 表示
                ins = int(ins_str) if ins_str.isdigit() else 0
                dele = int(del_str) if del_str.isdigit() else 0
                # [L1] status 默认值修复 — 拿不到时记 warning + 标 'unknown',
                # 不再静默用 'M' 误导用户 (可能把"新增"显示成"修改")
                status = status_map.get(path)
                if status is None:
                    from loguru import logger as _logger
                    _logger.warning(
                        "[git_service.diff_summary] name-status 缺少 path={!r}, "
                        "name_status=\\n{}", path, name_status[:500],
                    )
                    status = "unknown"
                files.append({
                    "path": path,
                    "status": status,
                    "insertions": ins,
                    "deletions": dele,
                })
                total_ins += ins
                total_del += dele
        except Exception as exc:
            from loguru import logger as _logger
            _logger.warning(
                "[git_service.diff_summary] failed for skill={} a={} b={}: {}",
                skill_id, commit_a, commit_b, exc,
            )
        return {
            "from": str(commit_a),
            "to": str(commit_b),
            "files": files,
            "total_files": len(files),
            "total_insertions": total_ins,
            "total_deletions": total_del,
        }

    def list_files_at_commit(
        self, skill_id: str, commit: str = "HEAD"
    ) -> list[str]:
        """列出 skill 目录在指定 commit 时的所有文件 (相对路径, 含 skill_id 前缀)。

        用 git ls-tree -r 走 git object, 不依赖工作区状态。
        """
        _validate_commit_ref(commit)
        repo = self.repo
        try:
            out = repo.git.ls_tree("-r", "--name-only", commit, skill_id)
            return [line.strip() for line in out.splitlines() if line.strip()]
        except Exception:
            return []

    def revert_file(self, skill_id: str, relative_path: str, commit: str = "HEAD") -> None:
        """将某个文件恢复到指定commit的版本"""
        _validate_commit_ref(commit)
        self._validate_relative_path(relative_path)
        repo = self.repo
        file_path = f"{skill_id}/{relative_path}"
        repo.git.checkout(commit, "--", file_path)

    def get_file_at_commit(self, skill_id: str, relative_path: str, commit: str) -> str | None:
        """获取某个文件在指定commit时的内容"""
        _validate_commit_ref(commit)
        self._validate_relative_path(relative_path)
        repo = self.repo
        file_path = f"{skill_id}/{relative_path}"
        try:
            return repo.git.show(f"{commit}:{file_path}")
        except Exception:
            return None

    def tags(self, prefix: str = "") -> list[str]:
        """列出所有标签，可按前缀筛选"""
        all_tags = [t.name for t in self.repo.tags]
        if prefix:
            all_tags = [t for t in all_tags if t.startswith(prefix)]
        return sorted(all_tags, reverse=True)


# 全局实例
git_service = GitService()
