"""The research validator, R1-R12, over the SHIPPED content JSON (Sprint 13, AD-M37, D17).

    uv run python tools/research_validate.py <dir holding research.json, bureau.json, vendor.json, design_model.json>

validate.py does not exist (Matthew, 2026-10-27), so this port is the REFERENCE
implementation, and the reading recorded beside each rule in
content/research/validate_codes.json is the specification. The workbook's
Validation sheet ("9 PASS, 0 failures") is not evidence and is not a target: if
this reports a failure, the data is the arbiter, and the row is named.

It reads the converter's OUTPUT, the same files the game reads, so the C# side
computes every rule over identical input. Parsing is not re-done here: the
converter refuses a cell that does not parse (G1xx).

It prints what it measured for every rule, then every failure with its row, and
exits non-zero if any rule fails.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_grammar as rg  # noqa: E402

CODES = {c["code"]: c for c in json.loads((ROOT / "content" / "research" / "validate_codes.json")
                                          .read_text(encoding="utf-8"))["codes"]}
ATOM_FIELDS = ("prerequisites", "cost_reducers", "acquisition", "doctrine_role")
PROJECT_KINDS = ("technology", "doctrine", "design")


def atoms(p: dict, fields=ATOM_FIELDS):
    for f in fields:
        for clause in p.get(f, []):
            for a in clause:
                yield f, a


class Validator:
    def __init__(self, research: dict, bureaus: list[dict], vendors: list[dict], models: list[dict]):
        self.r, self.bureaus, self.vendors, self.models = research, bureaus, vendors, models
        self.projects = {p["id"]: p for p in research["projects"]}
        self.bureau = {b["id"]: b for b in bureaus}
        self.vendor_ids = {v["id"] for v in vendors}
        reg = research["registries"]
        self.flags = {f["id"] for f in reg["flags"]}
        self.fielded, self.focus = set(reg["fielded"]), set(reg["focus"])
        self.xp = {(x["branch"], x["tier"]) for x in reg["xp"]}
        self.nations = {s["nation"] for s in research["start"]}
        self.failures: list[tuple[str, str, str, object, str]] = []   # code, where, token, detail
        self.measured: dict[str, str] = {}
        self.size: dict[str, int] = {}   # the rule's subject: n=0 passes vacuously (D32)

    def fail(self, code: str, where: str, token: object, detail: str) -> None:
        self.failures.append((code, where, token, detail))

    @staticmethod
    def label(p: dict) -> str:
        return f"{p['id']} ({p.get('authored_id') or p['name']})"

    # R1 -----------------------------------------------------------------
    def r1(self) -> None:
        n = 0
        kind_of = {pid: p["kind"] for pid, p in self.projects.items()}
        for p in self.r["projects"]:
            for f, a in atoms(p):
                n += 1
                k, v = a["kind"], a["value"]
                ok = {
                    "technology": lambda: kind_of.get(v) == "technology",
                    "doctrine": lambda: kind_of.get(v) == "doctrine",
                    "design": lambda: kind_of.get(v) == "design",
                    "flag": lambda: v in self.flags,
                    "fielded": lambda: v in self.fielded,
                    "focus": lambda: v in self.focus,
                    "xp": lambda: isinstance(v, dict) and (v.get("branch"), v.get("tier")) in self.xp,
                    "bureau": lambda: v in self.bureau,
                    "seized": lambda: v in self.bureau,
                    "licence": lambda: v in self.bureau or v in self.vendor_ids,
                    "import": lambda: v in self.bureau or v in self.vendor_ids,
                    "captured": lambda: v in self.nations,
                }.get(k)
                if ok is None:
                    self.fail("V101", f"{self.label(p)} {f}", k, "not an atom kind")
                elif not ok():
                    self.fail("V101", f"{self.label(p)} {f}", v, f"does not resolve as a {k}")
            if p["kind"] == "design":
                parent = p.get("variant_of")
                n += parent is not None
                if parent is not None and kind_of.get(parent) != "design":
                    self.fail("V101", f"{self.label(p)} variant_of", parent, "not a design")
                for b in p.get("producers", []):
                    n += 1
                    if b not in self.bureau:
                        self.fail("V101", f"{self.label(p)} producers", b, "not a bureau")
            for x in p.get("exclusive_with", []):
                n += 1
                if kind_of.get(x) != "doctrine":
                    self.fail("V101", f"{self.label(p)} exclusive_with", x, "not a doctrine")
        for i, x in enumerate(self.r["restrictions"]):
            if x["lifted_by"] is not None:
                n += 1
                if x["lifted_by"] not in self.flags:
                    self.fail("V101", f"restrictions[{i}] lifted_by", x["lifted_by"], "not a flag")
        self.measured["V101"] = f"{n} references resolved by kind"
        self.size["V101"] = n

    # R2 -----------------------------------------------------------------
    def r2(self) -> None:
        designs = [p for p in self.r["projects"] if p["kind"] == "design"]
        seen = collections.Counter(p["name"] for p in designs)
        for name, c in seen.items():
            if c > 1:
                self.fail("V102", "designs", name, f"design name used {c} times")
        for p in designs:
            try:
                expr = rg.parse(f"design:{p['name']}", rg.GATE)
                safe = len(expr) == 1 and len(expr[0]) == 1 and expr[0][0] == rg.Atom("design", p["name"])
            except rg.GrammarError:
                safe = False
            if not safe:
                self.fail("V102", self.label(p), p["name"], "not token-safe: design:<name> is not one design atom")
        per_nation = collections.Counter((b["nation"], b["name"]) for b in self.bureaus)
        for (nat, name), c in per_nation.items():
            if c > 1:
                self.fail("V102", f"bureaus of {nat}", name, f"bureau name used {c} times")
        self.measured["V102"] = f"{len(designs)} design names, {len(self.bureaus)} bureau names"
        self.size["V102"] = len(designs) + len(self.bureaus)

    # R3 -----------------------------------------------------------------
    def r3(self) -> None:
        edges = collections.defaultdict(set)
        for p in self.r["projects"]:
            for _, a in atoms(p, ("prerequisites",)):
                if a["kind"] in PROJECT_KINDS:
                    edges[p["id"]].add(a["value"])
            if p.get("variant_of"):
                edges[p["id"]].add(p["variant_of"])
        # iterative Tarjan: every strongly connected component of size > 1, or a self-loop
        index, low, on, stack, out, counter = {}, {}, set(), [], [], [0]
        for root in self.projects:
            if root in index:
                continue
            work = [(root, iter(sorted(edges[root])))]
            index[root] = low[root] = counter[0]; counter[0] += 1
            stack.append(root); on.add(root)
            while work:
                v, it = work[-1]
                w = next(it, None)
                if w is not None:
                    if w not in self.projects:
                        continue   # an unresolved edge is R1's
                    if w not in index:
                        index[w] = low[w] = counter[0]; counter[0] += 1
                        stack.append(w); on.add(w)
                        work.append((w, iter(sorted(edges[w]))))
                    elif w in on:
                        low[v] = min(low[v], index[w])
                    continue
                work.pop()
                if work:
                    low[work[-1][0]] = min(low[work[-1][0]], low[v])
                if low[v] == index[v]:
                    comp = []
                    while True:
                        x = stack.pop(); on.discard(x); comp.append(x)
                        if x == v:
                            break
                    if len(comp) > 1 or v in edges[v]:
                        out.append(sorted(comp))
        for comp in out:
            self.fail("V103", "cycle", comp, f"{len(comp)} project(s) depend on each other")
        self.measured["V103"] = f"{sum(map(len, edges.values()))} edges over {len(self.projects)} projects"
        self.size["V103"] = len(self.projects)

    # R4 -----------------------------------------------------------------
    def r4(self) -> None:
        def when(p: dict) -> float:
            return float("-inf") if p["year_declared"] == "base" else p["year"]
        n = 0
        for p in self.r["projects"]:
            mine = when(p)
            for ci, clause in enumerate(p.get("prerequisites", [])):
                n += 1
                times = [when(self.projects[a["value"]]) if a["kind"] in PROJECT_KINDS and a["value"] in self.projects
                         else float("-inf") for a in clause]
                if min(times) > mine:
                    later = [a["value"] for a in clause]
                    self.fail("V104", f"{self.label(p)} prerequisites[{ci}]", later,
                              f"every alternative is later than {p['year'] or 'base'}")
        self.measured["V104"] = f"{n} prerequisite clauses"
        self.size["V104"] = n

    # R5 -----------------------------------------------------------------
    def r5(self) -> None:
        n = 0
        for p in self.r["projects"]:
            if p["kind"] != "design":
                continue
            n += 1
            if not p["acquisition"]:
                self.fail("V105", self.label(p), None, "no design bureau and no acquisition")
            for _, a in atoms(p, ("acquisition",)):
                if a["kind"] == "bureau":
                    b = self.bureau.get(a["value"])
                    if b is not None and (b["nation"] != p["nation"] or b["role"] != "Lead"):
                        self.fail("V105", f"{self.label(p)} acquisition", a["value"],
                                  f"a {b['role']} of {b['nation']}, not a Lead of {p['nation']}")
            for bid in p["producers"]:
                b = self.bureau.get(bid)
                if b is not None and b["nation"] != p["nation"]:
                    self.fail("V105", f"{self.label(p)} producers", bid, f"a bureau of {b['nation']}, not {p['nation']}")
        self.measured["V105"] = f"{n} designs"
        self.size["V105"] = n

    # R6 -----------------------------------------------------------------
    def r6(self) -> None:
        uses = 0
        for p in self.r["projects"]:
            if p["kind"] != "design":
                continue
            vendor = any(a["value"] in self.vendor_ids for _, a in atoms(p) if isinstance(a["value"], str))
            uses += vendor
            if vendor != ("COND" in p["tags"]):
                self.fail("V106", self.label(p), p["tags"],
                          "uses an off-map vendor without COND" if vendor else "COND without an off-map vendor")
        self.measured["V106"] = f"{uses} designs use a vendor"
        self.size["V106"] = sum(p["kind"] == "design" for p in self.r["projects"])

    # R7 -----------------------------------------------------------------
    def r7(self) -> None:
        pairs = 0
        docs = {pid: p for pid, p in self.projects.items() if p["kind"] == "doctrine"}
        for pid, p in docs.items():
            for x in p["exclusive_with"]:
                pairs += 1
                if x == pid:
                    self.fail("V107", self.label(p), x, "exclusive with itself")
                elif x in docs and pid not in docs[x]["exclusive_with"]:
                    self.fail("V107", self.label(p), x, f"{x} does not list {pid} back")
        self.measured["V107"] = f"{pairs} exclusive_with entries"
        self.size["V107"] = pairs

    # R8 -----------------------------------------------------------------
    def r8(self) -> None:
        n = 0
        for s in self.r["start"]:
            held = set(s["technologies"]) | set(s["doctrines"])
            for pid in sorted(held):
                p = self.projects.get(pid)
                if p is None:
                    self.fail("V108", f"start {s['nation']}", pid, "holds a project that does not exist")
                    continue
                for ci, clause in enumerate(p["prerequisites"]):
                    ids = [a["value"] for a in clause if a["kind"] in ("technology", "doctrine")]
                    if ids:
                        n += 1
                        if not set(ids) & held:
                            self.fail("V108", f"start {s['nation']} {self.label(p)} prerequisites[{ci}]", ids,
                                      "held, but none of these is held")
        self.measured["V108"] = f"{n} id clauses of held projects"
        self.size["V108"] = n

    # R9 -----------------------------------------------------------------
    def r9(self) -> None:
        n = 0
        for p in self.r["projects"]:
            parent = self.projects.get(p.get("variant_of") or "")
            if parent is not None and parent["kind"] == "design":   # a non-design parent is R1's
                n += 1
                if parent["nation"] != p["nation"]:
                    self.fail("V109", self.label(p), p["variant_of"], f"parent is {parent['nation']}, not {p['nation']}")
        self.measured["V109"] = f"{n} variants"
        self.size["V109"] = n

    # R10 ----------------------------------------------------------------
    def r10(self) -> None:
        cats = {c["id"]: c for c in self.r["categories"]}
        techs = [p for p in self.projects.values() if p["kind"] == "technology"]
        disagree = 0
        for p in techs:
            c = cats.get(p["category"])
            if c is None or c["folded_into"] is not None:
                self.fail("V110", self.label(p), p["category"], "not a live category (unknown, or folded away)")
                continue
            # MEASUREMENT ONLY: does the authored id disagree with the category? Never used to decide one.
            if not p["authored_id"].startswith(c["authored_id"]):
                disagree += 1
        if techs and not disagree:
            self.fail("V110", "technologies", 0, "no id disagrees with its category: prefix-derivation would go "
                                                 "undetected (AD-M37 s4 asks for one such row, deliberately)")
        self.measured["V110"] = f"{len(techs)} technologies, {disagree} with an id that disagrees with its category"
        self.size["V110"] = len(techs)

    # R11 ----------------------------------------------------------------
    KEYS = {
        "technology": {"id", "authored_id", "kind", "name", "category", "year", "year_declared", "prerequisites",
                       "prerequisites_source", "cost_reducers", "cost_reducers_source", "effect", "cost",
                       "cost_source", "cost_reason"},
        "doctrine": {"id", "authored_id", "kind", "name", "domain", "branch", "year", "year_declared",
                     "base_holders", "prerequisites", "prerequisites_source", "exclusive_with", "effect", "cost",
                     "cost_source", "cost_reason"},
        "design": {"id", "kind", "name", "nation", "domain", "role", "variant_of", "year", "year_declared",
                   "acquisition", "acquisition_source", "lead", "producers", "prerequisites", "prerequisites_source",
                   "doctrine_role", "doctrine_role_source", "cost_reducers", "cost_reducers_source", "tags", "cost",
                   "cost_source", "cost_reason"},
    }
    TARGET_FIELD = {"design_stat": "stat", "facility_output": "pool", "power_supply": "pool"}

    def r11(self) -> None:
        for p in self.r["projects"]:
            extra = set(p) ^ self.KEYS.get(p["kind"], set())
            if extra:
                self.fail("V111", self.label(p), sorted(extra), "not exactly this kind's keys (prose enters by a field)")
        effects = 0
        for p in self.r["projects"]:
            e = p.get("effect")
            if p["kind"] == "design" or e is None:
                continue
            effects += 1
            if not (isinstance(e, dict) and set(e) == {"gate_only", "modifiers"} and isinstance(e["gate_only"], bool)
                    and isinstance(e["modifiers"], list)):
                self.fail("V111", self.label(p), e, "an effect is exactly {gate_only: bool, modifiers: [...]}")
                continue
            if e["gate_only"] == bool(e["modifiers"]):
                self.fail("V111", self.label(p), e, "gate_only true needs no modifiers; false needs at least one")
            for m in e["modifiers"]:
                want = self.TARGET_FIELD.get(m.get("target")) if isinstance(m, dict) else None
                if not (want and set(m) == {"target", "scope", want, "per_mille"} and isinstance(m["scope"], str)
                        and isinstance(m[want], str) and isinstance(m["per_mille"], int)
                        and not isinstance(m["per_mille"], bool) and m["per_mille"] != 0):
                    self.fail("V111", self.label(p), m, "a modifier is {target, scope, stat|pool, per_mille != 0}")
        per_design = collections.Counter(m["id"] for m in self.models)
        for mid, c in per_design.items():
            if c != 1:
                self.fail("V111", f"model {mid}", c, "a slice design has exactly one model")
        self.measured["V111"] = (f"{effects} technologies/doctrines with an effect"
                                 f"{' (this half VACUOUS: none encoded yet)' if effects == 0 else ''}, "
                                 f"{len(per_design)} design models")
        self.size["V111"] = effects + len(per_design)

    # R12 ----------------------------------------------------------------
    def r12(self) -> None:
        models = {m["id"]: m for m in self.models}
        for m in self.models:
            p = self.projects.get(m["id"])
            if p is None or p["kind"] != "design":
                self.fail("V112", f"model {m['id']}", m["id"], "not a design project")
                continue
            if m["unlock"] != m["id"]:
                self.fail("V112", f"model {self.label(p)}", m["unlock"], "unlock does not name its own study")
            if (m["year"], m["year_declared"]) != (p["year"], p["year_declared"]):
                self.fail("V112", f"model {self.label(p)}", (m["year"], m["year_declared"]),
                          f"year differs from its design's {(p['year'], p['year_declared'])} (historical, D30)")
            lead = self.bureau.get(p["lead"] or "")
            if lead is not None:
                if not (lead["active"] and lead["nation"] == p["nation"] and lead["role"] == "Lead"):
                    self.fail("V112", f"model {self.label(p)}", p["lead"], "lead is not an ACTIVE Lead of its own nation")
            elif not p["acquisition"] or any(a["kind"] not in ("seized", "captured") for _, a in atoms(p, ("acquisition",))):
                self.fail("V112", f"model {self.label(p)}", p["acquisition_source"],
                          "no lead, and not reachable by seizure or capture alone")
            sup = m["supersedes"]
            if sup is not None and (sup not in models or models[sup]["class"] != m["class"]):
                self.fail("V112", f"model {self.label(p)}", sup, "supersedes no model of the same class in the slice")
        self.measured["V112"] = f"{len(self.models)} models in the slice"
        self.size["V112"] = len(self.models)

    def run(self) -> list:
        for rule in (self.r1, self.r2, self.r3, self.r4, self.r5, self.r6, self.r7, self.r8, self.r9, self.r10, self.r11, self.r12):
            rule()
        return self.failures


def load(d: Path) -> Validator:
    def read(name):
        return json.loads((d / name).read_text(encoding="utf-8"))
    return Validator(read("research.json"), read("bureau.json")["bureaus"], read("vendor.json")["vendors"],
                     read("design_model.json")["models"])


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(__doc__)
    v = load(Path(argv[0]))
    failures = v.run()
    print(f"research_validate over {argv[0]}: {len(v.projects)} projects, {len(v.bureaus)} bureaus, "
          f"{len(v.vendor_ids)} vendors")
    by = collections.Counter(f[0] for f in failures)
    for code, c in CODES.items():
        n = v.size[code]
        verdict = "FAIL" if by[code] else "PASS"
        print(f"  {verdict} n={n}{' (VACUOUS: nothing to check)' if n == 0 else ''}  {c['rule']:<24} "
              f"{v.measured[code]}; {by[code]} failure(s)")
    for code, where, token, detail in failures:
        print(f"  {where}: PB-{code} {CODES[code]['rule']}: {token!r}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # -I ignores PYTHONIOENCODING
    sys.exit(main(sys.argv[1:]))
