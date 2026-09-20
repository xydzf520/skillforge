"""Compatibility alias for ``app.skills.service_runtime``."""

import sys

from app.skills.lifecycle import service_runtime as _impl

sys.modules[__name__] = _impl
