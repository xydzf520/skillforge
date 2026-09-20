"""Governed collection API."""

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.collection.schemas import CollectionFetchBatchRequest, CollectionFetchRequest
from app.collection.service import DEFAULT_FETCH_COOKIE_WAIT_SECONDS, fetch_collection, fetch_collection_batch
from app.database import get_db


router = APIRouter()


@router.post("/fetch")
async def fetch(
    body: CollectionFetchRequest,
    x_run_token: str | None = Header(default=None, alias="X-Run-Token"),
    db: AsyncSession = Depends(get_db),
):
    return await fetch_collection(
        db,
        body,
        run_token=x_run_token,
        cookie_wait_seconds=DEFAULT_FETCH_COOKIE_WAIT_SECONDS,
    )


@router.post("/fetch-batch")
async def fetch_batch(
    body: CollectionFetchBatchRequest,
    x_run_token: str | None = Header(default=None, alias="X-Run-Token"),
    db: AsyncSession = Depends(get_db),
):
    return await fetch_collection_batch(db, body, run_token=x_run_token)
