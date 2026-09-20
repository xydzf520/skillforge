from fastapi import APIRouter, Body, Header
from pydantic import ValidationError

from app.common.exceptions import AppError
from app.intelligence.schemas import AnalyzeRequest
from app.intelligence.service import analyze_context


router = APIRouter()

_FORBIDDEN_RAW_FIELDS = {"model", "json", "json_mode", "max_tokens", "system", "user"}


@router.post("/analyze")
async def analyze(
    payload: dict = Body(...),
    x_run_token: str = Header("", alias="X-Run-Token"),
):
    forbidden = sorted(_FORBIDDEN_RAW_FIELDS.intersection(payload.keys()))
    if forbidden:
        raise AppError("MODEL_PARAM_NOT_ACCEPTED", 400, {"fields": forbidden})
    try:
        body = AnalyzeRequest.model_validate(payload)
    except ValidationError as exc:
        raise AppError("PARAM_INVALID", 400, {"errors": exc.errors()}) from exc
    return await analyze_context(body, run_token=x_run_token)
