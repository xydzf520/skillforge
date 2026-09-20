"""Compatibility alias for ``app.skills.hermes_adapter``."""

import sys

from app.skills.integrations import hermes_adapter as _impl

sys.modules[__name__] = _impl
