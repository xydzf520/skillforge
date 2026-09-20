"""Governed model deployment context for Skill runtime inputs."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select


MODEL_CONTEXT_INPUT_CHANNELS = (
    "skill_runtime_params",
    "node_schedule_snapshot",
    "training_model_deployment",
)
MODEL_CONTEXT_OUTPUT_CHANNELS = (
    "skill_output",
    "decision_log",
    "execution_artifact",
)


def _deployment_target_skill_ids(deployment: Any) -> list[str]:
    values = getattr(deployment, "target_skill_ids_json", None)
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for item in values:
        text = str(item or "").strip()
        if text:
            result.append(text[:100])
    return result[:20]


def _deployment_targets_skill(deployment: Any, skill_id: str) -> bool:
    return skill_id in set(_deployment_target_skill_ids(deployment))


def _deployment_rollout_percent(deployment: Any) -> int:
    try:
        value = int(getattr(deployment, "rollout_percent", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return min(max(value, 0), 100)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _deployment_rollout_bucket(skill_id: str, run_id: str | None, deployment_id: str | None) -> int:
    seed = f"{skill_id}|{run_id or ''}|{deployment_id or ''}"
    return int(_sha256_text(seed)[:8], 16) % 100


def _deployment_rollout_decision(deployment: Any, *, skill_id: str, run_id: str | None) -> dict[str, Any]:
    status = str(getattr(deployment, "status", "") or "").lower()
    rollout_percent = _deployment_rollout_percent(deployment)
    if status == "active":
        return {
            "rollout_selected": True,
            "rollout_bucket": None,
            "rollout_reason": "active",
        }
    if not run_id:
        return {
            "rollout_selected": False,
            "rollout_bucket": None,
            "rollout_reason": "canary_requires_run_id",
        }
    bucket = _deployment_rollout_bucket(skill_id, run_id, getattr(deployment, "id", None))
    selected = status == "canary" and rollout_percent > 0 and bucket < rollout_percent
    return {
        "rollout_selected": selected,
        "rollout_bucket": bucket,
        "rollout_reason": "canary_selected" if selected else "canary_not_selected",
    }


def _deployment_timestamp(deployment: Any) -> Any:
    return (
        getattr(deployment, "activated_at", None)
        or getattr(deployment, "updated_at", None)
        or getattr(deployment, "created_at", None)
    )


def _deployment_selection_rank(deployment: Any, *, skill_department: str | None = None) -> tuple[Any, ...]:
    department = str(getattr(deployment, "department", "") or "").strip()
    clean_skill_department = str(skill_department or "").strip()
    status = str(getattr(deployment, "status", "") or "").lower()
    timestamp = _deployment_timestamp(deployment)
    timestamp_rank = -float(timestamp.timestamp()) if timestamp and hasattr(timestamp, "timestamp") else 0
    return (
        0 if clean_skill_department and department == clean_skill_department else 1,
        0 if status == "canary" else 1,
        timestamp_rank,
        str(getattr(deployment, "id", "") or ""),
    )


async def deployment_candidates_for_skill(
    session: Any,
    *,
    skill_id: str,
    skill_department: str | None = None,
    run_id: str | None = None,
    limit: int = 100,
) -> list[tuple[Any, dict[str, Any]]]:
    """Return rollout-selected model deployments for one Skill in runtime order."""
    from app.training.models import TrainingModelDeployment

    rows = (
        await session.execute(
            select(TrainingModelDeployment)
            .where(TrainingModelDeployment.status.in_(["active", "canary"]))
            .order_by(
                TrainingModelDeployment.activated_at.desc().nullslast(),
                TrainingModelDeployment.updated_at.desc(),
                TrainingModelDeployment.id.desc(),
            )
            .limit(max(1, min(int(limit or 100), 500)))
        )
    ).scalars().all()
    candidates: list[tuple[tuple[Any, ...], Any, dict[str, Any]]] = []
    for deployment in rows:
        if not _deployment_targets_skill(deployment, skill_id):
            continue
        rollout_decision = _deployment_rollout_decision(deployment, skill_id=skill_id, run_id=run_id)
        if not rollout_decision.get("rollout_selected"):
            continue
        candidates.append((
            _deployment_selection_rank(deployment, skill_department=skill_department),
            deployment,
            rollout_decision,
        ))
    candidates.sort(key=lambda item: item[0])
    return [(deployment, rollout_decision) for _rank, deployment, rollout_decision in candidates]


def _parse_bridge_ops(instance: Any | None) -> set[str]:
    raw = getattr(instance, "bridge_capabilities_json", None) if instance is not None else None
    try:
        payload = json.loads(raw or "{}") if isinstance(raw, str) else (raw if isinstance(raw, dict) else {})
    except (TypeError, ValueError):
        payload = {}
    return {str(item or "").strip() for item in (payload.get("ops") or []) if str(item or "").strip()}


def _deployment_artifact_ref(deployment: Any) -> dict[str, Any]:
    value = getattr(deployment, "artifact_ref_json", None)
    return value if isinstance(value, dict) else {}


def _deployment_artifact_id(deployment: Any, artifact_ref: dict[str, Any]) -> str:
    return str(artifact_ref.get("id") or getattr(deployment, "artifact_id", "") or "")[:120]


def _deployment_artifact_sha256(artifact_ref: dict[str, Any]) -> str | None:
    value = str(artifact_ref.get("sha256") or "").strip()
    return value[:64] or None


def _deployment_target_gateway_id(deployment: Any, job: Any | None) -> str:
    value = str(getattr(deployment, "deployment_target_gateway_id", "") or "").strip()
    if value:
        return value[:100]
    artifact_ref = _deployment_artifact_ref(deployment)
    value = str(
        artifact_ref.get("deployment_target_gateway_id")
        or artifact_ref.get("target_gateway_id")
        or ""
    ).strip()
    if value:
        return value[:100]
    return str(getattr(job, "target_gateway_id", "") or "").strip()[:100] if job else ""


async def active_model_context_for_skill(
    session: Any,
    *,
    skill_id: str,
    skill_department: str | None = None,
    target_instance: Any | None = None,
    run_id: str | None = None,
    include_artifact_uri: bool = False,
) -> dict[str, Any] | None:
    """Return the active model deployment context to pass into a Skill runtime.

    The context is intentionally input-oriented: it identifies the approved
    deployment, the responsible training gateway, and whether this target node can
    call local inference. Raw prompts, generated text, credentials, and unrelated
    training samples are not included.
    """
    from app.training.models import TrainingJob

    candidates = await deployment_candidates_for_skill(
        session,
        skill_id=skill_id,
        skill_department=skill_department,
        run_id=run_id,
    )
    if not candidates:
        return None
    deployment, rollout_decision = candidates[0]
    job = await session.get(TrainingJob, getattr(deployment, "job_id", None))

    artifact_ref = _deployment_artifact_ref(deployment)
    training_gateway_id = str(getattr(job, "target_gateway_id", "") or "").strip() if job else ""
    target_gateway_id = _deployment_target_gateway_id(deployment, job)
    target_instance_id = str(getattr(target_instance, "id", "") or "").strip() if target_instance is not None else ""
    local_artifact_available = bool(target_gateway_id and target_instance_id and target_gateway_id == target_instance_id)
    bridge_ops = _parse_bridge_ops(target_instance)
    inference_op_available = "training.inference" in bridge_ops
    artifact_uri = str(artifact_ref.get("uri") or "").strip()
    artifact_uri_present = bool(artifact_uri)

    runtime_status = {
        "target_gateway_id": target_gateway_id or None,
        "training_gateway_id": (
            training_gateway_id
            if training_gateway_id and training_gateway_id != target_gateway_id
            else None
        ),
        "target_gateway_kind": (
            str(getattr(target_instance, "bridge_gateway_kind", "") or "").strip()
            if local_artifact_available
            else None
        ),
        "artifact_uri_present": artifact_uri_present,
        "local_artifact_available": local_artifact_available,
        "training_inference_op_available": inference_op_available,
        "inference_ready_hint": bool(local_artifact_available and artifact_uri_present and inference_op_available),
    }
    runtime_status = {key: value for key, value in runtime_status.items() if value not in (None, "", [], {})}

    deployment_payload: dict[str, Any] = {
        "model_deployment_id": str(getattr(deployment, "id", "") or "")[:80],
        "job_id": str(getattr(deployment, "job_id", "") or "")[:80],
        "department": str(getattr(deployment, "department", "") or "")[:50],
        "model_family": str(getattr(deployment, "model_family", "") or "")[:100],
        "deployment_runtime_profile": str(getattr(deployment, "deployment_runtime_profile", "") or "")[:80],
        "deployment_status": str(getattr(deployment, "status", "") or "")[:30],
        "rollout_percent": _deployment_rollout_percent(deployment),
        "rollout_selected": bool(rollout_decision.get("rollout_selected")),
        "rollout_bucket": rollout_decision.get("rollout_bucket"),
        "rollout_reason": str(rollout_decision.get("rollout_reason") or "")[:50],
        "artifact_id": _deployment_artifact_id(deployment, artifact_ref),
        "artifact_sha256": _deployment_artifact_sha256(artifact_ref),
        "target_skill_ids": _deployment_target_skill_ids(deployment),
        "run_id": str(run_id or "")[:80] or None,
        "runtime_status": runtime_status,
    }
    if include_artifact_uri and local_artifact_available and artifact_uri:
        deployment_payload["artifact_uri"] = artifact_uri[:500]

    deployment_payload = {
        key: value
        for key, value in deployment_payload.items()
        if value not in (None, "", [], {})
    }
    if not deployment_payload.get("model_deployment_id"):
        return None

    return {
        "active_model_deployment": deployment_payload,
        "model_deployment_id": deployment_payload["model_deployment_id"],
        "model_family": deployment_payload.get("model_family"),
        "deployment_status": deployment_payload.get("deployment_status"),
        "artifact_id": deployment_payload.get("artifact_id"),
        "artifact_sha256": deployment_payload.get("artifact_sha256"),
        "target_skill_ids": deployment_payload.get("target_skill_ids") or [],
        "runtime_status": runtime_status,
        "control": {
            "agent_contract": "skill_runtime_model_context.v1",
            "input_channels": list(MODEL_CONTEXT_INPUT_CHANNELS),
            "output_channels": list(MODEL_CONTEXT_OUTPUT_CHANNELS),
            "required_trace_fields": [
                "model_deployment_id",
                "model_family",
                "artifact_sha256",
            ],
            "raw_payload_returned": False,
        },
    }
