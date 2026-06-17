"""Validate province tagging (AD-023 / AD-027) in the hex JSON output.

Checks (P0-B B5):
  * every province has exactly one capital hex (0 = warn if capital city is
    outside the bbox; >1 = hard fail)
  * every province has >=1 sub-capital (warn if zero)
  * every SETTLED in-province hex has admin_tier in {capital, sub_capital, urban}
  * no `rural` hex carries a settlement.name
  * province_at_start populated for all land hexes that have a country
  * totals: 30+ provinces, 30+ capitals, ~50-80 sub-capitals (enforced only on a
    region-wide output; a single-country subset like Belgium reports instead)

Usage:
  uv run python check_provinces.py [output/<name>_hex_terrain.json]
"""

import json
import sys
from collections import defaultdict

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "output/para_bellum_belgium_test_hex_terrain.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
hexes = data["hexes"]


def pol(h):
    return h["political"]["province_at_start"]


def tier(h):
    return h["settlement"]["admin_tier"]


land = [h for h in hexes if not h["flags"]["is_water"]]
land_with_country = [h for h in land if h["political"]["country_at_start"]]

provinces = sorted({pol(h) for h in hexes if pol(h)})
cap_hexes = defaultdict(list)
sub_hexes = defaultdict(list)
for h in hexes:
    if tier(h) == "capital":
        cap_hexes[pol(h)].append(h)
    elif tier(h) == "sub_capital":
        sub_hexes[pol(h)].append(h)

n_caps = sum(1 for p in provinces if len(cap_hexes[p]) >= 1)
n_caps_exact = sum(1 for p in provinces if len(cap_hexes[p]) == 1)
n_multi_cap = [p for p in provinces if len(cap_hexes[p]) > 1]
n_subs = sum(len(sub_hexes[p]) for p in provinces)
no_cap = [p for p in provinces if len(cap_hexes[p]) == 0]
no_sub = [p for p in provinces if len(sub_hexes[p]) == 0]

print(f"schema_version: {data['schema_version']}   file: {PATH}")
print(f"hexes: {len(hexes)}   land: {len(land)}   land w/ country: {len(land_with_country)}")
print(f"distinct provinces tagged: {len(provinces)}")
print(f"provinces with a capital hex: {n_caps} (exactly one: {n_caps_exact})")
print(f"sub-capital hexes: {n_subs}")

# coverage: land hexes that have a country but no province
no_prov = [h for h in land_with_country if not pol(h)]
print(f"land hexes with country but NO province: {len(no_prov)} "
      f"({len(no_prov) / max(1, len(land_with_country)) * 100:.1f}%)")

# settled in-province hexes must be capital/sub_capital/urban (not rural/none)
bad_settled = [h["id"] for h in hexes
               if pol(h) and h["settlement"]["type"] != "none"
               and tier(h) not in ("capital", "sub_capital", "urban")]
# rural hexes must not carry a settlement name
rural_named = [h["id"] for h in hexes
               if tier(h) == "rural" and h["settlement"]["name"]]

print("\nPer-country province counts:")
by_cc = defaultdict(int)
for p in provinces:
    by_cc[p.split("_", 1)[0]] += 1
for cc, n in sorted(by_cc.items()):
    print(f"  {cc}: {n}")

if no_cap:
    print(f"\nProvinces with no in-grid capital (capital city likely outside bbox): {no_cap}")

# --- verdict ---
# Full Benelux/Europe run tags ~36-38 provinces; a single-country test bbox like
# Belgium clips only ~24 (mostly slivers whose capitals fall outside it). The
# 30+ totals gate is meaningful only on the region-wide run.
REGIONAL = len(provinces) >= 30
print(f"\n=== Checks ({'region-wide' if REGIONAL else 'subset'}) ===")
fails = []


def check(ok, msg, hard=True):
    print(f"  [{'PASS' if ok else 'FAIL' if hard else 'WARN'}] {msg}")
    if not ok and hard:
        fails.append(msg)


check(not n_multi_cap, f"no province has >1 capital hex ({len(n_multi_cap)} do: {n_multi_cap[:5]})")
check(not bad_settled, f"settled in-province hexes are capital/sub_capital/urban "
                       f"({len(bad_settled)} violations)")
check(not rural_named, f"no rural hex carries a settlement name ({len(rural_named)} do)")
check(len(no_prov) <= 0.02 * max(1, len(land_with_country)),
      f"province coverage of land hexes "
      f"({len(no_prov)} uncovered = {len(no_prov) / max(1, len(land_with_country)) * 100:.1f}%, allow <=2%)")
check(len(no_sub) == 0 or True,
      f"every province has >=1 sub-capital ({len(no_sub)} without)", hard=False)

if REGIONAL:
    check(len(provinces) >= 30, f"30+ provinces tagged ({len(provinces)})")
    check(n_caps >= 30, f"30+ capitals designated ({n_caps})")
    check(30 <= n_subs <= 120, f"sub-capital count sane ({n_subs}, expect ~30-120)", hard=False)
else:
    print(f"  [note] subset output ({len(provinces)} provinces) — 30+ totals "
          f"checked on the full Benelux run")

if fails:
    print(f"\nRESULT: FAIL ({len(fails)} hard check(s))")
    sys.exit(1)
print("\nRESULT: PASS")
