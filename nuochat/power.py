import ctypes
from ctypes import wintypes


ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
_kernel32 = ctypes.windll.kernel32
_kernel32.SetThreadExecutionState.argtypes = [wintypes.DWORD]
_kernel32.SetThreadExecutionState.restype = wintypes.DWORD


def set_sleep_inhibition(enabled):
    flags = ES_CONTINUOUS | (ES_SYSTEM_REQUIRED if enabled else 0)
    return bool(_kernel32.SetThreadExecutionState(flags))
