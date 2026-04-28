"""Project management — discovery, instances, VCS."""
from mycode.project.instance import InstanceContext, ProjectInfo, current, current_or_none, provide, set_context

__all__ = ["ProjectInfo", "InstanceContext", "current", "current_or_none", "set_context", "provide"]
