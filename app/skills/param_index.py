"""Compatibility alias for ``app.skills.param_index``."""

import sys

from app.skills.intelligence import param_index as _impl

sys.modules[__name__] = _impl
