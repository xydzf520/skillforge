"""Compatibility alias for ``app.skills.service_quality``."""

import sys

from app.skills.tooling import service_quality as _impl

sys.modules[__name__] = _impl
