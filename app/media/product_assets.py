"""Governed product-image import and deterministic recommendation.

The recommender is intentionally rule based: it may use business product/SKU
for retrieval, but only the asset visibly selected by the user is allowed to
enter an H3 request.  No prompt text is rewritten here.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.common.exceptions import AppError
from app.common.time_utils import isoformat_bjt, now_bjt
from app.projects import service as project_service
from app.projects.models import ProjectRun, ProjectRunAsset

from .models import MediaLibraryAsset


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "001": ("001", "零零一"),
    "001磁吸": ("001磁吸", "001 磁吸"),
    "超快感": ("超快感", "超快噶"),
    "魔力玻玻": ("魔力玻玻", "魔法情趣"),
    "AIR隐薄": ("air隐薄", "air 隐薄", "air空气套", "air 空气套", "空气套"),
    "铂金三合一": ("铂金三合一", "铂金"),
    "持久三合一": ("持久三合一",),
    "持久战甲三合一": ("持久战甲三合一", "战甲三合一"),
    "水光薄至润四合一": ("水光薄至润四合一", "水光薄", "至润四合一"),
    "草莓粒粒": ("草莓粒粒", "草莓"),
    "经典延时": ("经典延时",),
    "超薄延时": ("超薄延时",),
    "情趣延时": ("情趣延时",),
    "凸点螺纹凉感装": ("凸点螺纹凉感装", "凉感装"),
    "凸点螺纹": ("凸点螺纹",),
    "润薄玻尿酸": ("润薄玻尿酸",),
    "水润玻尿酸": ("水润玻尿酸",),
    "Q弹大颗粒": ("q弹大颗粒", "Q弹大颗粒"),
    "大胆爱吧": ("大胆爱吧",),
    "热感": ("热感",),
    "战甲延时": ("战甲延时",),
    "持久": ("持久",),
    "超薄装": ("超薄装",),
}

VIEW_KEYWORDS: dict[str, tuple[str, ...]] = {
    "front": ("正面", "包装展示", "包装正对镜头", "正对镜头"),
    "side": ("侧面", "侧放", "包装侧边", "包装侧视图"),
    "back": ("背面", "包装说明", "背面说明"),
    "unit": ("散片", "单片", "裸片", "贴手", "手背", "拿在手", "手持单片"),
    "open_pack": ("开盖", "撕开", "开封", "拆开"),
    "detail": ("细节", "水晶感", "特写"),
    "logo": ("logo", "标志"),
}


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:24]}"


def _normalized(value: str) -> str:
    return re.sub(r"[\s_\-—·（）()]+", "", value).casefold()


def _product_matches(text: str) -> list[tuple[str, str]]:
    normalized = _normalized(text)
    matches: list[tuple[str, str]] = []
    # Longest aliases win so 001磁吸 does not collapse into generic 001.
    aliases = sorted(
        ((product, alias) for product, values in PRODUCT_ALIASES.items() for alias in values),
        key=lambda item: len(_normalized(item[1])),
        reverse=True,
    )
    occupied: list[tuple[int, int]] = []
    for product, alias in aliases:
        needle = _normalized(alias)
        start = normalized.find(needle)
        if start < 0:
            continue
        end = start + len(needle)
        if any(start >= left and end <= right for left, right in occupied):
            continue
        matches.append((product, alias))
        occupied.append((start, end))
    return matches


def classify_product_asset_name(file_name: str) -> dict[str, Any]:
    stem = Path(file_name).stem.strip()
    normalized = _normalized(stem)
    matches = _product_matches(stem)
    product_key = matches[0][0] if matches else None
    aliases = list(PRODUCT_ALIASES.get(product_key, ())) if product_key else []

    if "logo" in normalized:
        view_type = "logo"
    elif any(word in normalized for word in ("开盖", "撕开", "开封")):
        view_type = "open_pack"
    elif any(word in normalized for word in ("单片", "裸片")):
        view_type = "unit"
    elif "背" in normalized:
        view_type = "back"
    elif "侧" in normalized:
        view_type = "side"
    elif any(word in normalized for word in ("正面", "正装")) or re.search(
        r"(?:^|[\s_\-—·])正(?:\d|[\s_\-—·（）()]|$)", stem.casefold()
    ):
        view_type = "front"
    elif any(word in normalized for word in ("水晶感", "细节", "特写")):
        view_type = "detail"
    elif stem.startswith("图像合成"):
        view_type = "composite"
    elif product_key:
        # Product/package file names without an explicit side/back/unit suffix
        # are the canonical pack shot in this governed source folder.
        view_type = "front"
    else:
        view_type = None

    count_match = re.search(r"(?<!\d)(\d{1,2})\s*(?:只|片|枚|个)装?", stem)
    package_count = int(count_match.group(1)) if count_match else None
    reliable = bool(product_key and view_type and view_type not in {"logo", "composite"})
    if stem == "裸片" or stem.startswith("图像合成"):
        reliable = False
    return {
        "product_key": product_key,
        "view_type": view_type,
        "package_count": package_count,
        "aliases": aliases,
        "auto_reference_eligible": reliable,
        "classification_status": "classified" if reliable else "needs_confirmation",
    }


def _validate_png(content: bytes) -> dict[str, Any]:
    if not content.startswith(PNG_SIGNATURE):
        raise AppError("MEDIA_PRODUCT_ASSET_NOT_PNG", 422)
    try:
        with Image.open(io.BytesIO(content)) as image:
            image.verify()
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            has_alpha = "A" in image.getbands() or "transparency" in image.info
            if width < 64 or height < 64:
                raise AppError("MEDIA_PRODUCT_ASSET_TOO_SMALL", 422, {"width": width, "height": height})
            # The source directory contains several valid PNG pack shots with a
            # flattened background. Record alpha truthfully instead of dropping
            # them from the promised full import.
            return {"width": width, "height": height, "has_alpha": has_alpha, "mode": image.mode}
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AppError("MEDIA_PRODUCT_ASSET_INVALID", 422) from exc


def _thumbnail_webp(content: bytes) -> bytes:
    with Image.open(io.BytesIO(content)) as image:
        image = image.convert("RGBA")
        image.thumbnail((720, 720), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, "WEBP", quality=84, method=6)
        return output.getvalue()


async def import_product_library_asset(
    db: AsyncSession,
    user: User,
    run: ProjectRun,
    *,
    source_asset_id: str,
    import_batch: str,
    source_directory: str = "产品透明图",
) -> tuple[MediaLibraryAsset, bool]:
    source = await db.get(ProjectRunAsset, source_asset_id)
    if source is None or source.project_id != run.project_id:
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": source_asset_id})
    if source.department_id and run.department_id and source.department_id != run.department_id:
        raise AppError("PROJECT_ASSET_NOT_FOUND", 404, {"asset_id": source_asset_id})
    if str(source.mime_type or "").lower() != "image/png":
        raise AppError("MEDIA_PRODUCT_ASSET_NOT_PNG", 422, {"asset_id": source.id})

    content = project_service._project_run_asset_abs_path(source).read_bytes()  # noqa: SLF001
    image_meta = _validate_png(content)
    classification = classify_product_asset_name(source.file_name)
    thumbnail = await project_service.upload_project_run_asset(
        db,
        user,
        run.id,
        file_name=f"{Path(source.file_name).stem}-thumb.webp",
        mime_type="image/webp",
        content=_thumbnail_webp(content),
        metadata={
            "source": "material_product_asset_thumbnail",
            "source_asset_id": source.id,
            "source_sha256": source.sha256,
        },
    )
    thumbnail_id = _text(_dict(thumbnail.get("asset")).get("id"), 50) or None
    department_id = str(run.department_id or "931765248")
    row = (
        await db.execute(
            select(MediaLibraryAsset).where(
                MediaLibraryAsset.department_id == department_id,
                (
                    (MediaLibraryAsset.source_asset_id == source.id)
                    | (MediaLibraryAsset.sha256 == source.sha256)
                ),
            ).limit(1)
        )
    ).scalar_one_or_none()
    created = row is None
    now = now_bjt()
    if row is None:
        row = MediaLibraryAsset(
            id=_new_id("mla"),
            department_id=department_id,
            source_asset_id=source.id,
            name=source.file_name,
            media_type="image",
            sha256=source.sha256,
            created_by=str(user.id),
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    row.product_key = classification["product_key"]
    row.source_asset_id = source.id
    row.sha256 = source.sha256
    row.view_type = classification["view_type"]
    row.package_count = classification["package_count"]
    row.aliases_json = classification["aliases"]
    row.thumbnail_asset_id = thumbnail_id
    row.auto_reference_eligible = bool(classification["auto_reference_eligible"])
    row.import_batch = _text(import_batch, 120)
    row.status = "active"
    row.tags_json = list(dict.fromkeys(filter(None, [
        classification["product_key"], classification["view_type"], "部门产品图", "透明底",
    ])))
    row.rights_json = {"policy": "department_owned", "confirmed": True, "source": source_directory}
    row.scan_json = {
        "status": "passed",
        "png_signature": True,
        "alpha_channel": bool(image_meta["has_alpha"]),
        "alpha_warning": None if image_meta["has_alpha"] else "flattened_png_background",
    }
    row.metadata_json = {
        **dict(row.metadata_json or {}),
        **image_meta,
        "source_directory": source_directory,
        "original_file_name": source.file_name,
        "classification_status": classification["classification_status"],
        "imported_at": isoformat_bjt(now),
    }
    row.updated_at = now
    await db.flush()
    return row, created


def _view_intent(text: str) -> tuple[str | None, list[str]]:
    normalized = _normalized(text)
    for view_type in ("open_pack", "back", "unit", "side", "front", "detail"):
        matched = [word for word in VIEW_KEYWORDS[view_type] if _normalized(word) in normalized]
        if matched:
            return view_type, matched
    return None, []


async def recommend_product_assets(
    db: AsyncSession,
    run: ProjectRun,
    payload: dict[str, Any],
) -> dict[str, Any]:
    business = _dict(payload.get("business"))
    visual_prompt = _text(payload.get("visual_prompt"), 7000)
    script = _text(payload.get("script"), 7000)
    retrieval_text = " ".join(filter(None, [
        _text(business.get("product"), 240),
        _text(business.get("sku"), 240),
        visual_prompt,
        script,
    ]))
    product_hits = _product_matches(retrieval_text)
    products = list(dict.fromkeys(product for product, _ in product_hits))
    requested_view, view_words = _view_intent(retrieval_text)
    locked_ids = {
        _text(value, 50) for value in (payload.get("locked_asset_ids") or []) if _text(value, 50)
    }

    conditions = [
        MediaLibraryAsset.department_id == str(run.department_id or "931765248"),
        MediaLibraryAsset.status == "active",
        MediaLibraryAsset.media_type == "image",
    ]
    rows = (await db.execute(select(MediaLibraryAsset).where(*conditions))).scalars().all()
    sources = {
        item.id: item for item in (
            await db.execute(select(ProjectRunAsset).where(
                ProjectRunAsset.id.in_([row.source_asset_id for row in rows])
            ))
        ).scalars().all()
    } if rows else {}
    thumbnails = {
        item.id: item for item in (
            await db.execute(select(ProjectRunAsset).where(
                ProjectRunAsset.id.in_([row.thumbnail_asset_id for row in rows if row.thumbnail_asset_id])
            ))
        ).scalars().all()
    } if rows else {}

    scored: list[tuple[float, MediaLibraryAsset, list[str]]] = []
    for row in rows:
        reasons: list[str] = []
        score = 0.0
        if row.id in locked_ids:
            score += 100
            reasons.append("用户已锁定")
        if products and row.product_key in products:
            score += 60 if products[0] == row.product_key else 45
            reasons.append(f"命中产品 {row.product_key}")
        elif products:
            continue
        if requested_view and row.view_type == requested_view:
            score += 25
            reasons.append(f"动作/镜头要求匹配 {requested_view}")
        elif requested_view and row.view_type == "front":
            score += 4
        elif not requested_view and row.view_type == "front":
            score += 10
            reasons.append("未指定视图，优先正面包装")
        if row.auto_reference_eligible:
            score += 8
        if _dict(row.scan_json).get("status") == "passed":
            score += 4
        if _dict(row.rights_json).get("confirmed") is True:
            score += 3
        if score > 0:
            scored.append((score, row, reasons))
    scored.sort(key=lambda item: (-item[0], item[1].name))

    conflict = len(products) > 1
    top_score = scored[0][0] if scored else 0
    auto_selected = bool(scored and not conflict and products and top_score >= 70)
    items = []
    for score, row, reasons in scored[:12]:
        source = sources.get(row.source_asset_id)
        thumbnail = thumbnails.get(row.thumbnail_asset_id)
        items.append({
            "id": row.id,
            "source_asset_id": row.source_asset_id,
            "name": row.name,
            "product_key": row.product_key,
            "view_type": row.view_type,
            "package_count": row.package_count,
            "role": "product_detail" if row.view_type in {"unit", "detail"} else "product_packshot",
            "confidence": round(min(1.0, score / 100), 3),
            "matched_keywords": list(dict.fromkeys([*[alias for _, alias in product_hits], *view_words])),
            "reason": "；".join(reasons),
            "locked": row.id in locked_ids,
            "thumbnail": project_service.serialize_run_asset(thumbnail, include_download_url=True) if thumbnail else None,
            "source": project_service.serialize_run_asset(source, include_download_url=True) if source else None,
        })
    return {
        "product_matches": products,
        "view_intent": requested_view,
        "conflict": conflict,
        "low_confidence": not auto_selected,
        "auto_selected_asset_id": items[0]["id"] if auto_selected else None,
        "items": items,
        "policy": "material-product-reference-v1",
    }
