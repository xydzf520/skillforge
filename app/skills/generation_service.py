"""Compatibility alias for ``app.skills.generation_service``."""

import sys

from app.skills.intelligence import generation_service as _impl

sys.modules[__name__] = _impl
