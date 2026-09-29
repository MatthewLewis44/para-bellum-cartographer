---
name: osm-cache
description: Protect the OSM download cache before regenerating a map. Use before any pipeline run that refetches OSM data, when a cache part may be expiring, or when investigating why a regenerated artifact differs from the shipped one.
---

# The OSM cache is a one-way door

**Read this before starting any regeneration run.** This has cost the project provenance twice.

The Para Bellum OSM layers are cached as `.gpkg` parts keyed by bbox hash under
`~/wargame-cartographer/cache/osm_pb/`, with a 30-day TTL by default. When a part ages past the TTL,
a run **silently begins a cold Overpass refetch and overwrites the cached part in place**. There is
no archive and no way back: the snapshot that produced every artifact shipped from it is gone.

In Sprint 10 that destroyed the settlements snapshot behind the shipped eastern artifact — 50
settlement parts and 11 of 50 waterways parts were rewritten before the run was killed. Byte
identity stopped being an achievable definition of done, and churn had to be **measured** instead
(115 of 18,719 hexes, 0.61%, settlement tagging only).

## The standing rule

**Every regeneration run pins the TTL:**

```bash
PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120 uv run python run_streaming.py configs/<spec>.yaml
```

The lever only narrows the window. It does not fix the defect — nothing archives the cache before a
refetch — so before a large run:

1. **Check the age of the parts** you are about to depend on.
2. **Copy the cache directory aside** if the run matters for provenance. A copy is cheap next to a
   destroyed snapshot.
3. Pin the TTL on the command line, every time.
4. If a refetch did happen, **measure the churn** against the previous artifact with
   `compare_hex_outputs.py` and report the figure rather than claiming the output is unchanged.

## The other cache trap

The cache key is the **bbox hash only — it ignores the query content**. Change an Overpass query and
the cache still answers with data fetched under the old one. Clear the affected parts by hand after
any query change, or you will validate a change that never ran.

## Fetching at all

`overpass-api.de` returns **HTTP 406 without a User-Agent**. The layer fetches already send one; new
code must too. The upstream ports fetch does not, which is why `port` is false everywhere.
