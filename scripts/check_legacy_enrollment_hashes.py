#!/usr/bin/env python3
"""兼容审计文档中的脚本名，转发到 check_enrollment_token_legacy.py。"""

from __future__ import annotations

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).with_name("check_enrollment_token_legacy.py")),
        run_name="__main__",
    )
