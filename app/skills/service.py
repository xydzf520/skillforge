"""Compatibility alias for ``app.skills.service``."""

import sys

from app.skills.lifecycle import service as _impl

sys.modules[__name__] = _impl
