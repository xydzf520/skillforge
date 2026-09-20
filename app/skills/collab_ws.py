"""Compatibility alias for ``app.skills.collab_ws``."""

import sys

from app.skills.integrations import collab_ws as _impl

sys.modules[__name__] = _impl
