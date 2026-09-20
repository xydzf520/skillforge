"""csv_file adapter render（v7 G5）。

把 rows + schema 渲染成 CSV 字符串；可选写入 data/raw/{directory}/{filename}.csv。
sandbox 模式（dry_run）下不写盘，只返回字符串内容。
"""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any


def render_csv(rows: list[dict[str, Any]], schema: dict[str, Any], *, dry_run: bool = True) -> dict:
    """渲染 csv 输出。

    返回：
        {
            "csv_text": "col1,col2\\nv1,v2\\n",
            "row_count": int,
            "filename": "...",
            "written_to": "...",  # 仅 dry_run=False 写盘时
        }
    """
    columns = list(schema.get("columns") or [])
    if not columns:
        raise ValueError("csv schema 必须含 columns 字段")

    delimiter = schema.get("delimiter") or ","
    encoding = schema.get("encoding") or "utf-8"
    filename = schema.get("filename") or "output.csv"
    directory = schema.get("directory") or ""

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=columns, delimiter=delimiter, extrasaction="ignore")
    writer.writeheader()
    for row in rows or []:
        writer.writerow({col: row.get(col, "") for col in columns})

    csv_text = buf.getvalue()
    result = {
        "csv_text": csv_text,
        "row_count": len(rows or []),
        "filename": filename,
        "encoding": encoding,
    }

    if not dry_run and filename:
        # 真发布时写到 data/raw/{directory}/
        base = Path("data/raw")
        if directory:
            base = base / directory
        base.mkdir(parents=True, exist_ok=True)
        out_path = base / filename
        out_path.write_text(csv_text, encoding=encoding)
        result["written_to"] = str(out_path)

    return result
