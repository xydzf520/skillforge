"""Compatibility alias for ``app.skills.id_gen``."""

import sys

from app.skills.core import id_gen as _impl

sys.modules[__name__] = _impl
