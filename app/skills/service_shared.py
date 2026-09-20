"""Compatibility alias for ``app.skills.service_shared``."""

import sys

from app.skills.core import service_shared as _impl

sys.modules[__name__] = _impl
