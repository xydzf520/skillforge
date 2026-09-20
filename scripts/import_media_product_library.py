"""Import a governed directory of transparent PNGs into one project library.

This command deliberately goes through the same ProjectRunAsset upload and
MediaLibraryAsset service paths as the UI.  It never writes a library row with
raw SQL, so permission checks, immutable file hashes, media probes, learning
events and audit metadata remain intact.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.auth.models import User  # noqa: E402
from app.database import async_session_factory  # noqa: E402
from app.media.product_assets import import_product_library_asset  # noqa: E402
from app.projects import service as project_service  # noqa: E402
from app.projects.models import ProjectRun  # noqa: E402


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import transparent product PNGs into the governed media library")
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--run-id", required=True)
    user = parser.add_mutually_exclusive_group(required=True)
    user.add_argument("--user-id")
    user.add_argument("--username")
    parser.add_argument("--batch", default="product-transparent-v4.1.0")
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, object]:
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        raise SystemExit(f"directory not found: {directory}")
    files = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() == ".png")
    if not files:
        raise SystemExit(f"no PNG files found: {directory}")

    async with async_session_factory() as db:
        run = await db.get(ProjectRun, args.run_id)
        if run is None:
            raise SystemExit(f"project run not found: {args.run_id}")
        condition = User.id == args.user_id if args.user_id else User.username == args.username
        operator = (await db.execute(select(User).where(condition).limit(1))).scalar_one_or_none()
        if operator is None:
            raise SystemExit("operator not found")

        created = 0
        updated = 0
        classified = 0
        pending = 0
        items: list[dict[str, object]] = []
        for path in files:
            uploaded = await project_service.upload_project_run_asset(
                db,
                operator,
                run.id,
                file_name=path.name,
                mime_type="image/png",
                content=path.read_bytes(),
                metadata={
                    "source": "governed_product_library_import",
                    "source_directory": directory.name,
                    "import_batch": args.batch,
                },
            )
            source_asset_id = str(uploaded["asset"]["id"])
            row, was_created = await import_product_library_asset(
                db,
                operator,
                run,
                source_asset_id=source_asset_id,
                import_batch=args.batch,
                source_directory=directory.name,
            )
            created += int(was_created)
            updated += int(not was_created)
            classified += int(bool(row.auto_reference_eligible))
            pending += int(not row.auto_reference_eligible)
            items.append({
                "file": path.name,
                "asset_id": row.id,
                "source_asset_id": row.source_asset_id,
                "product_key": row.product_key,
                "view_type": row.view_type,
                "auto_reference_eligible": row.auto_reference_eligible,
                "deduped_upload": bool(uploaded.get("deduped")),
            })
        await db.commit()
    return {
        "ok": True,
        "directory": str(directory),
        "batch": args.batch,
        "total": len(items),
        "created": created,
        "updated": updated,
        "classified": classified,
        "pending_confirmation": pending,
        "items": items,
    }


def main() -> None:
    print(json.dumps(asyncio.run(_run(_arguments())), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
