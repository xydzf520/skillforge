"""Compatibility alias for ``app.skills.publish_readiness``."""

import sys

from app.skills.lifecycle import publish_readiness as _impl

sys.modules[__name__] = _impl
