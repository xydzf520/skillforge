"""Compatibility alias for ``app.skills.intelligence.param_index``."""

import sys

from app.skills.core import param_index as _impl

sys.modules[__name__] = _impl
