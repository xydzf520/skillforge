"""Compatibility alias for ``app.skills.access``."""

import sys

from app.skills.core import access as _impl

sys.modules[__name__] = _impl
