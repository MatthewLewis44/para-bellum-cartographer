"""Process memory measurement for the streaming pipeline's RAM budget (T6).

Reports the current working-set (RSS-equivalent) in MB. This captures the C
allocations from GDAL/GEOS/numpy that ``tracemalloc`` misses — and those (the
landuse GeoDataFrame, the SRTM raster) are exactly the structures the streaming
refactor bounds. Uses ``psutil`` if present, else a Windows ctypes fallback,
else returns 0.0 (measurement unavailable — budget checks become no-ops).
"""

from __future__ import annotations


def working_set_mb() -> float:
    """Current process working set / RSS in MB (0.0 if unavailable)."""
    try:
        import psutil  # type: ignore
        return psutil.Process().memory_info().rss / (1024 * 1024)
    except Exception:
        pass
    try:
        import ctypes
        import ctypes.wintypes

        class _PMC(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = k32.K32GetProcessMemoryInfo
        fn.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(_PMC), ctypes.wintypes.DWORD]
        fn.restype = ctypes.wintypes.BOOL
        c = _PMC()
        c.cb = ctypes.sizeof(_PMC)
        if fn(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            return c.WorkingSetSize / (1024 * 1024)
    except Exception:
        pass
    return 0.0
