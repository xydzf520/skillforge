"""Compatibility alias for ``app.skills.cross_skill_conflict``."""

import sys

from app.skills.lifecycle import cross_skill_conflict as _impl

sys.modules[__name__] = _impl
