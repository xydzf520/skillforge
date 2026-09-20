"""Compatibility alias for ``app.skills.shadow_service``."""

import sys

from app.skills.lifecycle import shadow_service as _impl

sys.modules[__name__] = _impl
