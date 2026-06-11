"""Run the pipeline with performance instrumentation.

Usage: uv run python run_with_metrics.py configs/<spec>.yaml

Reports total runtime, per-stage elapsed times (from pipeline stage_log),
output JSON size, and peak memory (Windows PeakWorkingSetSize — covers C
allocations from GDAL/numpy that tracemalloc would miss).
"""

import ctypes
import ctypes.wintypes
import sys
import time
from pathlib import Path

from wargame_cartographer.pipeline import run_pipeline


def peak_working_set_mb() -> float:
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # GetProcessMemoryInfo moved to kernel32 as K32GetProcessMemoryInfo
    # (Win7+); explicit argtypes/restype so the 64-bit HANDLE isn't
    # truncated (the bare-windll call silently failed and reported 0).
    fn = kernel32.K32GetProcessMemoryInfo
    fn.argtypes = [ctypes.wintypes.HANDLE,
                   ctypes.POINTER(PROCESS_MEMORY_COUNTERS),
                   ctypes.wintypes.DWORD]
    fn.restype = ctypes.wintypes.BOOL

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    handle = kernel32.GetCurrentProcess()
    if not fn(handle, ctypes.byref(counters), counters.cb):
        return 0.0
    return counters.PeakWorkingSetSize / (1024 * 1024)


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "configs/para_bellum_benelux_germany_test.yaml"

    t0 = time.perf_counter()
    result = run_pipeline(spec, status_callback=lambda m: print(f"  {m}", flush=True))
    total_s = time.perf_counter() - t0

    print("\n=== Performance ===")
    print(f"total runtime: {total_s:.1f} s ({total_s / 60:.1f} min)")
    print(f"hex count: {result['hex_count']}")

    print("\nper-stage:")
    for entry in result.get("stage_log", []):
        extras = {k: v for k, v in entry.items() if k not in ("stage", "elapsed_s")}
        kv = " ".join(f"{k}={v}" for k, v in extras.items())
        print(f"  {entry['stage']:<16} {entry['elapsed_s']:>8.2f} s  {kv}")

    json_path = result["output_files"].get("json")
    if json_path:
        size_mb = Path(json_path).stat().st_size / (1024 * 1024)
        print(f"\noutput JSON: {json_path} ({size_mb:.1f} MB)")

    print(f"peak memory (working set): {peak_working_set_mb():.0f} MB")

    print("\nbiome distribution:")
    for k, v in result["biome_distribution"].items():
        print(f"  {k:<18} {v}")


if __name__ == "__main__":
    main()
