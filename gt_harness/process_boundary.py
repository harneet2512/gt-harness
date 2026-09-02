"""Process-level credential isolation for model-facing task tools."""

from __future__ import annotations

import os
import sys


def harden_process_secret_boundary() -> None:
    """Deny same-UID task children access to this process through ``/proc``."""

    if sys.platform != "linux":
        return
    import ctypes

    pr_set_dumpable = 4
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(pr_set_dumpable, 0, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), "prctl(PR_SET_DUMPABLE)")


__all__ = ["harden_process_secret_boundary"]
