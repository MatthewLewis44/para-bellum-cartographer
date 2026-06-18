"""Run the streaming/tiled pipeline with metrics (Sprint 4 T7).

Usage: uv run python run_streaming.py configs/<spec>.yaml [river_scalerank_max]
       (the optional 2nd arg overrides river_scalerank_max for an AD-029
        threshold comparison; output is suffixed _sr<N>)
"""

import sys
import time

from wargame_cartographer.streaming import run_streaming_pipeline
from wargame_cartographer.memory import working_set_mb


def main():
    spec = sys.argv[1] if len(sys.argv) > 1 else "configs/para_bellum_belgium_test.yaml"
    sr = int(sys.argv[2]) if len(sys.argv) > 2 else None  # AD-029 threshold override
    t0 = time.perf_counter()
    result = run_streaming_pipeline(
        spec, status_callback=lambda m: print(f"  {m}", flush=True),
        scalerank_override=sr,
    )
    total = time.perf_counter() - t0
    print("\n=== Streaming performance ===")
    print(f"spec: {spec}")
    print(f"hex count: {result['hex_count']}  tiles: {result['tiles']}")
    print(f"total runtime: {total:.1f} s ({total / 60:.1f} min)")
    print(f"peak working set per tile: {result['tile_peak_mb']:.0f} MB (budget 4096)")
    print(f"peak working set global:   {result['global_peak_mb']:.0f} MB (budget 6144)")
    print(f"final working set:         {working_set_mb():.0f} MB")
    print(f"output: {result['output_json']}")


if __name__ == "__main__":
    main()
