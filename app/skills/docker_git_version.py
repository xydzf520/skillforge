"""Expose Docker Gitea commit metadata for Skill Studio.

The live Skill editor reads skills from the local `skills-repo/` directory, while
the packaged production artifacts are tracked in the Docker Gitea bare repo.
This route bridges that gap so the UI can show the real Docker Git version.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, HTTPException

from app.auth.dependencies import require_state_active
from app.common.time_utils import isoformat_bjt, parse_bjt_datetime
from app.config import settings

_SKILL_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_CACHE_TTL_SECONDS = 20
_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def _setting(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _run_docker_git(args: list[str], timeout: float = 4.0) -> subprocess.CompletedProcess[str]:
    container = _setting("SKILLFORGE_GITEA_CONTAINER", "skillforge-gitea")
    return subprocess.run(
        ["docker", "exec", "--user", "git", container, *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def _git_args(*args: str) -> list[str]:
    bare_repo = _setting(
        "SKILLFORGE_GITEA_BARE_REPO",
        "/data/git/repositories/skillforge/skills-repo.git",
    )
    return ["git", f"--git-dir={bare_repo}", *args]


def _iso_from_epoch(epoch_text: str) -> str | None:
    try:
        return isoformat_bjt(datetime.fromtimestamp(int(epoch_text), timezone.utc))
    except Exception:
        return None


def _iso_from_manifest_time(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return isoformat_bjt(parse_bjt_datetime(value))
    except Exception:
        return value


def _load_package_manifest(skill_id: str, branch: str) -> dict[str, Any] | None:
    proc = _run_docker_git(_git_args("show", f"{branch}:{skill_id}.package.json"), timeout=4.0)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"parse_error": "invalid package manifest json"}
    return data if isinstance(data, dict) else None


def _load_path_commit(skill_id: str, branch: str) -> dict[str, Any] | None:
    paths = [
        f"skills/{skill_id}",
        f"{skill_id}.package.json",
        f"{skill_id}.zip",
        skill_id,
    ]
    proc = _run_docker_git(
        _git_args(
            "log",
            "-1",
            "--format=%H%x09%h%x09%ct%x09%an%x09%s",
            branch,
            "--",
            *paths,
        ),
        timeout=4.0,
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "git log failed").strip())
    line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if not line:
        return None
    full, short, epoch, author, subject = (line.split("\t", 4) + [""] * 5)[:5]
    repo_url = _setting("SKILLFORGE_GITEA_REPO_URL", "http://127.0.0.1:3000/skillforge/skills-repo")
    return {
        "commit": full,
        "short": short,
        "committed_at": _iso_from_epoch(epoch),
        "author": author,
        "subject": subject,
        "paths": paths,
        "commit_url": f"{repo_url.rstrip('/')}/commit/{full}" if full else None,
    }


def get_docker_git_version(skill_id: str) -> dict[str, Any]:
    if not _SKILL_ID_RE.match(skill_id):
        raise HTTPException(status_code=400, detail="invalid skill_id")

    now = time.time()
    cached = _CACHE.get(skill_id)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    branch = _setting("SKILLFORGE_GITEA_BRANCH", settings.SKILL_REPO_BRANCH or "main")
    repo_url = _setting("SKILLFORGE_GITEA_REPO_URL", "http://127.0.0.1:3000/skillforge/skills-repo")
    result: dict[str, Any] = {
        "skill_id": skill_id,
        "docker_git_enabled": True,
        "repo": "skillforge/skills-repo",
        "repo_url": repo_url,
        "branch": branch,
        "layout": "skills/{skill_id} + root package manifest",
    }

    try:
        commit = _load_path_commit(skill_id, branch)
        manifest = _load_package_manifest(skill_id, branch)
    except FileNotFoundError as exc:
        result.update({"available": False, "error": f"docker command not found: {exc}"})
    except subprocess.TimeoutExpired:
        result.update({"available": False, "error": "docker git query timed out"})
    except Exception as exc:
        result.update({"available": False, "error": str(exc)})
    else:
        result["available"] = bool(commit)
        if commit:
            result.update(commit)
        else:
            result["error"] = "no Docker Git commit found for this skill"
        if manifest:
            result["package_manifest"] = {
                "artifact": manifest.get("artifact"),
                "artifact_sha256": manifest.get("artifact_sha256"),
                "artifact_size_bytes": manifest.get("artifact_size_bytes"),
                "packaged_at": _iso_from_manifest_time(manifest.get("packaged_at")),
                "source_branch": manifest.get("source_branch"),
                "source_git_commit": manifest.get("source_git_commit"),
                "source_git_short": manifest.get("source_git_short"),
                "source_dirty": manifest.get("source_dirty"),
                "self_check_command": manifest.get("self_check_command"),
                "zip_self_check_command": manifest.get("zip_self_check_command"),
            }

    _CACHE[skill_id] = (now, result)
    return result


def register_docker_git_version_route(app: Any) -> None:
    path = "/api/skills/{skill_id}/docker-git-version"
    if any(getattr(route, "path", None) == path for route in app.router.routes):
        return

    def docker_git_version_endpoint(
        skill_id: str,
        current_user: Any = Depends(require_state_active),
    ) -> dict[str, Any]:
        return get_docker_git_version(skill_id)

    app.add_api_route(
        path,
        docker_git_version_endpoint,
        methods=["GET"],
        name="get_skill_docker_git_version",
    )

    # The SPA static mount lives at the end and can catch every path.  Keep this
    # API route before that mount even though it is registered after the compiled
    # application has been loaded.
    route = app.router.routes.pop()
    insert_at = len(app.router.routes)
    for index, existing in enumerate(app.router.routes):
        if getattr(existing, "name", "") == "vue" and getattr(existing, "path", "") in {"", "/"}:
            insert_at = index
            break
    app.router.routes.insert(insert_at, route)
