"""Compatibility alias for ``app.skills.ai_service``."""

import sys

from app.skills.intelligence import ai_service as _impl

sys.modules[__name__] = _impl
