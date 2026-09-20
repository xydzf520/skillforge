"""Availability of the optional authoring runtime, separate from model access.

The public edition has retired its old bundled CLI. No replacement adapter has
passed integration acceptance yet. Stored settings must not reactivate it.
Keep this boundary until an explicit adapter implements the platform contract.
"""

from app.common.exceptions import AppError

RUNTIME_UNAVAILABLE = "CODING_AGENT_RUNTIME_UNAVAILABLE"
RUNTIME_MESSAGE = (
    "旧编程运行时已移除，替代 Harness 尚未接入验收。"
    "当前不能启动编程会话；普通模型调用、手动编辑和 Skill 审核不受此限制。"
)


def runtime_status() -> dict:
    return {
        "available": False,
        "runtime": "none",
        "state": "adapter_pending",
        "reason": RUNTIME_MESSAGE,
        "recommended_adapter": "opencode",
        "evaluation_doc": "docs/public/HARNESS_REPLACEMENT.md",
    }


def require_authoring_runtime() -> None:
    """Fail before credentials, workspace creation or external process launch."""
    raise AppError(RUNTIME_UNAVAILABLE, 503, {"detail": RUNTIME_MESSAGE})
