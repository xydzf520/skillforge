"""Compatibility alias for ``app.skills.validation_service``."""

import sys

from app.skills.tooling import validation_service as _impl

sys.modules[__name__] = _impl
