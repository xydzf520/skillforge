"""Compatibility alias for ``app.skills.intelligence.methodology``."""

import sys

from app.skills.core import methodology as _impl

sys.modules[__name__] = _impl
