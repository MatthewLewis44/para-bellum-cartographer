"""Convert the research database workbook to content JSON (Sprint 13, AD-M37).

    uv run python tools/research_convert.py --workbook X.xlsx --vocabulary Content/vocabulary.json \\
        --overlay overlay.json --ledger ledger.json --out DIR [--mint]

Three inputs, none of them in this repository (they are Matthew's catalogue and
live in the private game repository, D5):

  workbook   the research database, read and never written. "Convert, never retype."
  overlay    additions pending merge into the workbook (D4): the category fold,
             the covert flags the workbook lacks, flags held at the start,
             treaty restrictions, the active roster and its capacities, encoded
             effects, and the declared reading of any cell the workbook writes
             as prose.
  ledger     the append-only name -> id ledger for designs and vendors, which
             have no authored id (D4). A name keeps its id forever; a renamed
             design keeps its id because a rename entry is appended by hand.

and two outputs in --out: research.json (categories, every project in one id
space, the gate registries, the 1930 start, the treaty restrictions) and
bureau.json / vendor.json. With --mint, names absent from the ledger are given
new ids and the ledger is rewritten with the new entries APPENDED; without it,
an unminted name is a refusal (X109), so a new id is always a deliberate act.

The converter refuses rather than guesses, and writes nothing if anything is
refused: a partial content set is worse than none. Refusal codes are data,
content/research/convert_codes.json (X1xx) and grammar_codes.json (G1xx/G2xx).

What ships, and what does not:
  - Prerequisites, cost reducers, Design bureau, Producers and Doctrine role ship
    PRE-PARSED as CNF lists of {kind, value} atoms with ids qualified (D5); the
    raw string ships beside them as *_source, provenance only.
  - Ids are qualified by one fixed injective function, qualify(): lower case and
    '-' to '_' (`QQ-10` -> `pb:qq_10`). The authored string ships as
    authored_id, display only. Nothing here reads an id's prefix; the category
    comes from the category column alone (AD-M37 section 4).
  - Prose (effects, unlocks, notes, traits, treaty text) is NOT shipped in any
    field (D3, S-2). The workbook keeps it. An effect ships only when the
    overlay encodes it.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_grammar as rg  # noqa: E402

CODES_FILE = ROOT / "content" / "research" / "convert_codes.json"
RULES = {c["code"]: c["rule"] for c in json.loads(CODES_FILE.read_text(encoding="utf-8"))["codes"]}
NS = "pb"
HEADER_ROW = 4   # rows 1-3 are a title, a note and a blank on every data sheet
BASE = "base"    # the one declared non-integer Year: held by every nation before 1930

TAGS = ("DEMO", "PAPER", "COND")
_TAG = re.compile(r"[A-Z]{3}")    # the map's owner tags; not content ids
_CODE = re.compile(r"[A-Z]{2}")   # the two-letter codes the Base holders column uses

COLUMNS = {
    "Categories": ("Code", "Category"),
    "Technologies": ("ID", "Cat.", "Technology", "Year", "Prerequisites", "Cost reducers"),
    "Doctrines": ("ID", "Domain", "Branch", "Doctrine", "Year", "Base holders", "Prerequisites",
                  "Exclusive with"),
    "Designs": ("Nation", "Domain", "Type / role", "Design", "Variant of", "Year", "Design bureau",
                "Producers", "Prerequisites", "Doctrine role", "Cost reducers", "Tags"),
    "Bureaus": ("ID", "Nation", "Bureau", "Role", "Primary", "Secondary", "Active from"),
    "Vendors": ("Country", "Vendor", "Fields"),
    "Start 1930": ("Nation", "Technologies held beyond base", "Doctrines held"),
    "Treaties 1930": ("Nation",),
}
OVERLAY_FORMAT = "pb-research-overlay/1"
OVERLAY_KEYS = {"format", "_comment", "nations", "design_roles", "costs", "category_fold", "category_fallback", "flags", "flags_held",
                "restrictions", "bureaus", "effects", "readings"}
LEDGER_FORMAT = "pb-research-ledger/1"
LEDGER_KINDS = {"designs": "design", "vendors": "vendor"}   # section -> id stem
_LEDGER_ID = re.compile(r"pb:(design|vendor)_([0-9]{4})")
_ACTIVE_FROM = re.compile(r"([0-9]{4})(?: \(.+\))?")


def qualify(authored: str) -> str:
    """The one id function. Injective over the data (checked, X107), opaque, and
    never inverted: nothing derives anything from its result."""
    return f"{NS}:{authored.lower().replace('-', '_')}"


@dataclass
class Refusals:
    items: list[str] = field(default_factory=list)
    codes: collections.Counter = field(default_factory=collections.Counter)

    def add(self, code: str, where: str, token: object, detail: str) -> None:
        rule = RULES.get(code) or rg.RULES[code]
        self.codes[code] += 1
        self.items.append(f"{where}: PB-{code} {rule}: {token!r}: {detail}")

    def grammar(self, where: str, err: rg.GrammarError) -> None:
        self.codes[err.code] += 1
        self.items.append(f"{where}: {err}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------- workbook

def read_sheet(wb, name: str, bad: Refusals) -> list[tuple[str, dict]]:
    """[(row label, {column: value})] for the data rows of a sheet."""
    if name not in wb.sheetnames:
        bad.add("X101", name, name, "sheet not in the workbook")
        return []
    rows = list(wb[name].iter_rows(min_row=HEADER_ROW, values_only=True))
    header = rows[0] if rows else ()
    missing = [c for c in COLUMNS[name] if c not in header]
    for c in missing:
        bad.add("X102", name, c, f"column not in the header row (row {HEADER_ROW})")
    if missing:
        return []
    out = []
    for i, r in enumerate(rows[1:], start=HEADER_ROW + 1):
        if any(c is not None for c in r):
            out.append((f"{name}!row {i}", dict(zip(header, r))))
    return out


def read_registries(wb, bad: Refusals) -> dict[str, list]:
    """The Registries sheet: titled sections, each a header row then one entry per row."""
    found: dict[str, list] = {"flag": [], "fielded": [], "focus": [], "xp": []}
    if "Registries" not in wb.sheetnames:
        bad.add("X101", "Registries", "Registries", "sheet not in the workbook")
        return found
    section = None
    for i, r in enumerate(wb["Registries"].iter_rows(values_only=True), start=1):
        a = r[0]
        if a is None:
            continue
        for title, key in (("Flags", "flag"), ("Fielded", "fielded"), ("Focus", "focus"),
                           ("Experience", "xp")):
            if a.startswith(title):
                section = key
                break
        else:
            if a in ("id", "token") or section is None:
                continue
            if section == "xp":   # 'xp:land:1 … xp:land:3' declares a tier range
                m = re.fullmatch(r"xp:([a-z]+):([0-9]+) … xp:\1:([0-9]+)", a)
                if not m:
                    bad.add("X114", f"Registries!A{i}", a, "expected 'xp:<branch>:<n> … xp:<branch>:<m>'")
                    continue
                found["xp"] += [(m.group(1), t) for t in range(int(m.group(2)), int(m.group(3)) + 1)]
            elif section == "flag":
                found["flag"].append({"id": a, "type": r[1]})
            else:
                found[section].append(a)
    return found


def year(value: object, where: str, bad: Refusals) -> dict:
    """{"year": int|None, "year_declared": "base"|None}. Never coerced (D5)."""
    if isinstance(value, int) and not isinstance(value, bool):
        return {"year": value, "year_declared": None}
    if value == BASE:
        return {"year": None, "year_declared": BASE}
    bad.add("X103", where, value, f"expected an integer year or {BASE!r}")
    return {"year": None, "year_declared": None}


def code_list(cell: object, where: str, allowed, bad: Refusals, what: str) -> list[str]:
    """A plain comma list (Secondary, Tags, Exclusive with, Fields). Empty cell = []."""
    if cell is None or cell == "":
        return []
    out = []
    for raw in str(cell).split(","):
        tok = raw.strip()
        if tok not in allowed:
            bad.add("X114", where, tok, f"not a {what}")
        else:
            out.append(tok)
    return out


# ---------------------------------------------------------------- overlay and ledger

def load_pools(path: Path, bad: Refusals) -> frozenset:
    """The resource pools the game's code knows (Content/vocabulary.json). Read, never
    copied here, so a pool the engine does not have cannot be a study currency."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        pools = doc["pools"]
        if doc.get("format") != "pb-content/vocabulary/1" or not all(isinstance(p, str) for p in pools):
            raise ValueError("expected format pb-content/vocabulary/1 with a list of pool names")
        return frozenset(pools)
    except (OSError, ValueError, KeyError, TypeError) as e:
        bad.add("X115", str(path), path.name, f"cannot read the pools: {e}")
        return frozenset()


def load_overlay(path: Path, bad: Refusals) -> dict:
    try:
        ov = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        bad.add("X113", str(path), path.name, f"cannot read: {e}")
        return {}
    if not isinstance(ov, dict) or ov.get("format") != OVERLAY_FORMAT:
        bad.add("X113", "overlay", ov.get("format") if isinstance(ov, dict) else ov,
                f"expected format {OVERLAY_FORMAT!r}")
        return {}
    for k in sorted(set(ov) - OVERLAY_KEYS):
        bad.add("X113", "overlay", k, "unknown key")
    return ov


@dataclass
class Ledger:
    entries: dict[str, list[dict]]
    minted: int = 0

    @classmethod
    def load(cls, path: Path, bad: Refusals) -> "Ledger":
        if not path.exists():
            return cls(entries={s: [] for s in LEDGER_KINDS})
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            bad.add("X110", str(path), path.name, f"not JSON: {e}")
            return cls(entries={s: [] for s in LEDGER_KINDS})
        if raw.get("format") != LEDGER_FORMAT or set(raw) - {"format", "_comment", *LEDGER_KINDS}:
            bad.add("X110", "ledger", raw.get("format"), f"expected format {LEDGER_FORMAT!r} "
                    f"and sections {sorted(LEDGER_KINDS)}")
        entries = {s: list(raw.get(s, [])) for s in LEDGER_KINDS}
        for s, stem in LEDGER_KINDS.items():
            by_name: dict[str, str] = {}
            for n, e in enumerate(entries[s]):
                where = f"ledger.{s}[{n}]"
                if not isinstance(e, dict) or set(e) != {"id", "name"}:
                    bad.add("X110", where, e, "an entry is exactly {id, name}")
                    continue
                m = _LEDGER_ID.fullmatch(str(e["id"]))
                if not m or m.group(1) != stem:
                    bad.add("X110", where, e["id"], f"expected pb:{stem}_NNNN")
                if e["name"] in by_name and by_name[e["name"]] != e["id"]:
                    bad.add("X110", where, e["name"], f"already mapped to {by_name[e['name']]}")
                by_name[e["name"]] = e["id"]
        return cls(entries=entries)

    def lookup(self, section: str, name: str) -> str | None:
        for e in self.entries[section]:
            if e["name"] == name:
                return e["id"]
        return None

    def mint(self, section: str, name: str) -> str:
        stem = LEDGER_KINDS[section]
        used = [int(_LEDGER_ID.fullmatch(e["id"]).group(2)) for e in self.entries[section]
                if _LEDGER_ID.fullmatch(e["id"])]
        new = f"{NS}:{stem}_{max(used, default=0) + 1:04d}"
        self.entries[section].append({"id": new, "name": name})
        self.minted += 1
        return new

    def resolve(self, section: str, name: str, where: str, mint: bool, bad: Refusals) -> str | None:
        got = self.lookup(section, name)
        if got is None and mint:
            got = self.mint(section, name)
        if got is None:
            bad.add("X109", where, name, f"no {LEDGER_KINDS[section]} id in the ledger")
        return got

    def dump(self) -> str:
        doc = {"format": LEDGER_FORMAT,
               "_comment": "APPEND-ONLY name -> id ledger (D4). Never edit or remove an entry. To keep an "
                           "id across a rename, append {id: <existing id>, name: <new name>}.",
               **self.entries}
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


# ---------------------------------------------------------------- conversion

@dataclass
class Convert:
    wb: object
    overlay: dict
    ledger: Ledger
    mint: bool
    bad: Refusals = field(default_factory=Refusals)
    pools: frozenset = frozenset()   # material currencies, from the game's vocabulary

    def __post_init__(self) -> None:
        """The nation table is DATA (overlay.nations), so a new nation is an overlay row,
        not a code change: workbook name -> (map owner tag, Base holders code)."""
        self.nations: dict[str, tuple[str, str]] = {}
        raw = self.overlay.get("nations")
        if not isinstance(raw, dict) or not raw:
            self.bad.add("X113", "overlay.nations", raw, "a non-empty {name: {tag, code}} table is required")
            return
        for name, v in raw.items():
            if not (isinstance(v, dict) and set(v) == {"tag", "code"}
                    and _TAG.fullmatch(str(v["tag"])) and _CODE.fullmatch(str(v["code"]))):
                self.bad.add("X113", f"overlay.nations.{name}", v, "expected {tag: 'ABC', code: 'AB'}")
                continue
            self.nations[name] = (v["tag"], v["code"])
        for i, label in ((0, "tag"), (1, "code")):
            seen = collections.Counter(t[i] for t in self.nations.values())
            for t, c in seen.items():
                if c > 1:
                    self.bad.add("X106", "overlay.nations", t, f"{label} used by {c} nations")
        self.nation_by_code = {code: name for name, (_, code) in self.nations.items()}

    # -- atoms ------------------------------------------------------------
    def atoms(self, cell, context, where, nation=None) -> tuple[list, str | None]:
        """(CNF atom list, provenance string) for one grammar cell."""
        try:
            expr = rg.parse(cell, context)
        except rg.GrammarError as err:
            self.bad.grammar(where, err)
            return [], cell
        unresolved = rg.resolve(expr, context, self.registry, nation)
        for err in unresolved:
            self.bad.grammar(where, err)
        if unresolved:
            return [], cell
        return [[self.atom(a, context, nation, where) for a in clause] for clause in expr], \
            (cell if cell not in (None, "") else None)

    def atom(self, a: rg.Atom, context: str, nation: str | None, where: str) -> dict:
        k, v = a.kind, a.value
        if k == "id":   # by TABLE MEMBERSHIP, never by prefix
            return {"kind": "technology" if v in self.tech_ids else "doctrine", "value": qualify(v)}
        if k == "design":
            return {"kind": "design", "value": self.design_ids[v]}
        if k in ("flag", "fielded", "focus"):
            return {"kind": k, "value": qualify(v)}
        if k == "xp":
            branch, tier = v.split(":")
            return {"kind": "xp", "value": {"branch": branch, "tier": int(tier)}}
        if k in ("bureau", "seized"):
            return {"kind": k, "value": self.bureau_any(v)}
        if k in ("licence", "import"):
            return {"kind": k, "value": self.bureau_any(v) if self.registry.any_bureau(v)
                    else self.vendor_ids[v]}
        if k == "captured":
            return {"kind": "captured", "value": self.nations[v][0]}
        if k == "name":   # bare: a bureau of the row's own nation
            return {"kind": "bureau", "value": self.bureau_ids[(nation, v)]}
        raise AssertionError(f"{where}: unhandled atom kind {k!r}")

    def bureau_any(self, name: str) -> str:
        hits = [bid for (_, n), bid in self.bureau_ids.items() if n == name]
        if len(hits) != 1:   # R2 makes this unreachable today; it is checked, not assumed
            raise AssertionError(f"bureau name {name!r} resolves {len(hits)} ways")
        return hits[0]

    # -- the run ------------------------------------------------------------
    def run(self) -> dict[str, dict]:
        bad, ov = self.bad, self.overlay
        sheets = {s: read_sheet(self.wb, s, bad) for s in COLUMNS}
        regs = read_registries(self.wb, bad)
        if bad.items:
            return {}

        # categories, the fold and the pre-registered fallback (a column edit, nothing else)
        cats = {}
        for where, row in sheets["Categories"]:
            if row["Code"] == "Total" and row["Category"] is None:   # the sheet's sum row
                continue
            if not row["Code"] or not row["Category"]:
                bad.add("X114", where, row["Code"], "a category row needs a Code and a Category")
                continue
            cats[row["Code"]] = row["Category"]
        fold = ov.get("category_fold", {})
        fallback = ov.get("category_fallback", {})
        for name, m in (("category_fold", fold), ("category_fallback", fallback)):
            for a, b in m.items():
                for c in (a, b):
                    if c not in cats:
                        bad.add("X105", f"overlay.{name}", c, "not a category code")

        def cat(code, where):
            if code not in cats:
                bad.add("X105", where, code, "not a category code")
                return None
            return qualify(fold.get(code, code))

        # nations
        def nation(name, where):
            if name not in self.nations:
                bad.add("X104", where, name, "not in overlay.nations")
                return None
            return name

        # tables and their authored ids
        techs, docs = sheets["Technologies"], sheets["Doctrines"]
        self.tech_ids = {r["ID"] for _, r in techs}
        self.doc_ids = {r["ID"] for _, r in docs}
        for table, rows in (("Technologies", techs), ("Doctrines", docs), ("Bureaus", sheets["Bureaus"])):
            seen = collections.Counter(r["ID"] for _, r in rows)
            for i, n in seen.items():
                if n > 1:
                    bad.add("X106", table, i, f"appears {n} times")
        for i in sorted(self.tech_ids & self.doc_ids):
            bad.add("X108", "Technologies/Doctrines", i, "both a technology and a doctrine")
        all_authored = [r["ID"] for _, r in techs] + [r["ID"] for _, r in docs]
        for table, ids in (("projects", all_authored), ("Bureaus", [r["ID"] for _, r in sheets["Bureaus"]]),
                           ("Categories", list(cats))):
            q = collections.defaultdict(set)
            for i in ids:
                q[qualify(i)].add(i)
            for qid, src in q.items():
                if len(src) > 1:
                    bad.add("X107", table, qid, f"qualifies {sorted(src)}")

        # bureaus (names unique per nation, R2)
        self.bureau_ids = {}
        for where, r in sheets["Bureaus"]:
            n = nation(r["Nation"], where)
            key = (n, r["Bureau"])
            if key in self.bureau_ids:
                bad.add("X106", where, r["Bureau"], f"bureau name repeated in {n}")
            self.bureau_ids[key] = qualify(r["ID"])
        by_name = collections.Counter(n for (_, n) in self.bureau_ids)
        for n, c in by_name.items():
            if c > 1:
                bad.add("X108", "Bureaus", n, f"bureau name in {c} nations; a bureau:/licence:/seized: "
                                              "token could not say which")

        # vendors and designs through the ledger
        self.vendor_ids = {}
        for where, r in sheets["Vendors"]:
            if r["Vendor"] in self.vendor_ids:
                bad.add("X106", where, r["Vendor"], "vendor name repeated")
            self.vendor_ids[r["Vendor"]] = self.ledger.resolve("vendors", r["Vendor"], where, self.mint, bad)
        self.design_ids = {}
        for where, r in sheets["Designs"]:
            if r["Design"] in self.design_ids:
                bad.add("X106", where, r["Design"], "design name repeated")
            self.design_ids[r["Design"]] = self.ledger.resolve("designs", r["Design"], where, self.mint, bad)

        # registries, with the overlay's added flags
        flags = list(regs["flag"])
        known_flags = {f["id"] for f in flags}
        for n, f in enumerate(ov.get("flags", [])):
            where = f"overlay.flags[{n}]"
            if not isinstance(f, dict) or set(f) != {"id", "type"}:
                bad.add("X113", where, f, "a flag is exactly {id, type}")
                continue
            if f["id"] in known_flags:
                bad.add("X106", where, f["id"], "flag already declared")
            known_flags.add(f["id"])
            flags.append(f)
        bureaus_by_nation = collections.defaultdict(set)
        for (n, b) in self.bureau_ids:
            bureaus_by_nation[n].add(b)
        self.registry = rg.Registry(
            ids=frozenset(self.tech_ids | self.doc_ids), designs=frozenset(self.design_ids),
            flags=frozenset(known_flags), fielded=frozenset(regs["fielded"]),
            focus=frozenset(regs["focus"]), xp=frozenset(regs["xp"]),
            bureaus={n: frozenset(v) for n, v in bureaus_by_nation.items()},
            vendors=frozenset(self.vendor_ids), nations=frozenset(self.nations))
        if bad.items:
            return {}

        # The design role vocabulary is CLOSED (D20 follow-up, PB-SIM13): a restriction scopes
        # on exact role strings, so a role reworded on one row would silently lift it there.
        # Every row's role must be declared, and every declared role must be used.
        roles = ov.get("design_roles")
        if not (isinstance(roles, list) and roles and all(isinstance(x, str) for x in roles)):
            bad.add("X113", "overlay.design_roles", roles, "a non-empty list of role strings is required")
            return {}
        declared = set(roles)
        used = set()
        for where, r in sheets["Designs"]:
            used.add(r["Type / role"])
            if r["Type / role"] not in declared:
                bad.add("X114", f"{where} Type / role", r["Type / role"], "not in overlay.design_roles")
        for x in sorted(declared - used):
            bad.add("X112", "overlay.design_roles", x, "declared but no design has this role")
        if len(roles) != len(declared):
            bad.add("X106", "overlay.design_roles", sorted(x for x in declared if roles.count(x) > 1),
                    "role declared twice")

        readings = ov.get("readings", {})
        effects = ov.get("effects", {})
        projects = []

        for where, r in techs:
            pre, pre_src = self.atoms(r["Prerequisites"], rg.GATE, f"{where} Prerequisites")
            cr, cr_src = self.atoms(r["Cost reducers"], rg.GATE, f"{where} Cost reducers")
            projects.append({"id": qualify(r["ID"]), "authored_id": r["ID"], "kind": "technology",
                             "name": r["Technology"], "category": cat(r["Cat."], f"{where} Cat."),
                             **year(r["Year"], f"{where} Year", bad),
                             "prerequisites": pre, "prerequisites_source": pre_src,
                             "cost_reducers": cr, "cost_reducers_source": cr_src,
                             "effect": effects.get(r["ID"])})

        base_readings = readings.get("base_holders", {})
        for where, r in docs:
            y = year(r["Year"], f"{where} Year", bad)
            holders = self.base_holders(r, y, where, base_readings.get(r["ID"]))
            pre, pre_src = self.atoms(r["Prerequisites"], rg.GATE, f"{where} Prerequisites")
            excl = code_list(r["Exclusive with"], f"{where} Exclusive with", self.doc_ids, bad, "doctrine id")
            projects.append({"id": qualify(r["ID"]), "authored_id": r["ID"], "kind": "doctrine",
                             "name": r["Doctrine"], "domain": r["Domain"], "branch": r["Branch"], **y,
                             "base_holders": holders,
                             "prerequisites": pre, "prerequisites_source": pre_src,
                             "exclusive_with": [qualify(x) for x in excl],
                             "effect": effects.get(r["ID"])})

        for where, r in sheets["Designs"]:
            n = nation(r["Nation"], f"{where} Nation")
            if n is None:
                continue
            acq, acq_src = self.atoms(r["Design bureau"], rg.ACQUISITION, f"{where} Design bureau", n)
            prod, prod_src = self.atoms(r["Producers"], rg.LIST, f"{where} Producers", n)
            pre, pre_src = self.atoms(r["Prerequisites"], rg.GATE, f"{where} Prerequisites")
            role, role_src = self.atoms(r["Doctrine role"], rg.GATE, f"{where} Doctrine role")
            cr, cr_src = self.atoms(r["Cost reducers"], rg.GATE, f"{where} Cost reducers")
            parent = r["Variant of"]
            if parent not in (None, "") and parent not in self.design_ids:
                bad.add("G202", f"{where} Variant of", parent, "no design has exactly this name")
            # READING (recorded): the lead is the first alternative of the first clause when
            # that is a bureau of the design's own nation; an acquisition first means no lead.
            lead = acq[0][0]["value"] if acq and acq[0][0]["kind"] == "bureau" else None
            projects.append({"id": self.design_ids[r["Design"]], "kind": "design", "name": r["Design"],
                             "nation": self.nations[n][0], "domain": r["Domain"], "role": r["Type / role"],
                             "variant_of": self.design_ids.get(parent) if parent else None,
                             **year(r["Year"], f"{where} Year", bad),
                             "acquisition": acq, "acquisition_source": acq_src, "lead": lead,
                             "producers": [a["value"] for clause in prod for a in clause],
                             "prerequisites": pre, "prerequisites_source": pre_src,
                             "doctrine_role": role, "doctrine_role_source": role_src,
                             "cost_reducers": cr, "cost_reducers_source": cr_src,
                             "tags": code_list(r["Tags"], f"{where} Tags", TAGS, bad, "tag")})

        era = self.apply_costs(projects)
        ids = collections.Counter(p["id"] for p in projects)
        for i, c in ids.items():
            if c > 1:
                bad.add("X107", "projects", i, f"{c} projects share this id")
        project_by_authored = {p["authored_id"]: p for p in projects if "authored_id" in p}

        for k, v in effects.items():
            if k not in project_by_authored:
                bad.add("X112", "overlay.effects", k, "not a technology or doctrine id")
            elif not (isinstance(v, dict) and set(v) == {"gate_only", "modifiers"}
                      and isinstance(v["gate_only"], bool) and isinstance(v["modifiers"], list)):
                bad.add("X113", f"overlay.effects.{k}", v, "an effect is exactly {gate_only: bool, modifiers: []}")

        # bureaus out, with the roster cut (a FLAG; all rows kept) and capacity
        roster = ov.get("bureaus", {})
        authored_bureaus = {r["ID"] for _, r in sheets["Bureaus"]}
        for k in roster:
            if k not in authored_bureaus:
                bad.add("X112", "overlay.bureaus", k, "not a bureau id")
        bureaus = []
        for where, r in sheets["Bureaus"]:
            entry = roster.get(r["ID"], {})
            if set(entry) - {"active", "capacity"}:
                bad.add("X113", f"overlay.bureaus.{r['ID']}", sorted(entry), "keys are active, capacity")
            active = entry.get("active", False) is True
            capacity = entry.get("capacity")
            if active and not (isinstance(capacity, int) and not isinstance(capacity, bool) and capacity > 0):
                bad.add("X111", f"overlay.bureaus.{r['ID']}", capacity, "an active bureau needs a positive integer capacity")
            m = _ACTIVE_FROM.fullmatch(str(r["Active from"]))
            if not m:
                bad.add("X114", f"{where} Active from", r["Active from"], "expected '<year>' or '<year> (<note>)'")
            # READING (recorded): Active from is its leading year; the parenthesis is prose
            bureaus.append({"id": qualify(r["ID"]), "authored_id": r["ID"], "nation": self.nations.get(r["Nation"], (None,))[0],
                            "name": r["Bureau"], "role": r["Role"], "primary": cat(r["Primary"], f"{where} Primary"),
                            "secondary": [cat(c, f"{where} Secondary") for c in
                                          code_list(r["Secondary"], f"{where} Secondary", cats, bad, "category code")],
                            "active_from": int(m.group(1)) if m else None,
                            "active": active, "capacity": capacity if active else None})

        vendors = [{"id": self.vendor_ids[r["Vendor"]], "name": r["Vendor"], "country": r["Country"],
                    "fields": [cat(c, f"{where} Fields") for c in
                               code_list(r["Fields"], f"{where} Fields", cats, bad, "category code")]}
                   for where, r in sheets["Vendors"]]

        start = self.start_state(sheets["Start 1930"], projects, ov.get("flags_held", {}), known_flags, nation)
        restrictions = self.restrictions(ov.get("restrictions", []), cats, known_flags, sheets["Designs"])

        categories = [{"id": qualify(c), "authored_id": c, "name": name,
                       "folded_into": qualify(fold[c]) if c in fold else None,
                       "fallback_into": qualify(fallback[c]) if c in fallback else None}
                      for c, name in cats.items()]
        registries = {"flags": [{"id": qualify(f["id"]), "authored_id": f["id"], "type": f["type"]} for f in flags],
                      "fielded": [qualify(f) for f in regs["fielded"]],
                      "focus": [qualify(f) for f in regs["focus"]],
                      "xp": [{"branch": b, "tier": t} for b, t in regs["xp"]]}
        return {
            "research.json": {"format": "pb-content/research/1", "categories": categories,
                              "projects": projects, "registries": registries,
                              "start": start, "restrictions": restrictions,
                              "era_multiplier": era},
            "bureau.json": {"format": "pb-content/bureau/1", "bureaus": bureaus},
            "vendor.json": {"format": "pb-content/vendor/1", "vendors": vendors},
        }

    def apply_costs(self, projects: list[dict]) -> dict | None:
        """Bake each project's PRE-ERA cost (D19, D24): the flat base for its kind, times the
        variant fraction for a variant, or a per-project override with a one-line reason.
        Ships cost, cost_source ("rule" | "override") and cost_reason on every project, and
        returns era_multiplier: the constants of the ONE function the engine evaluates at
        runtime, M(start date), whose shape PB-SIM13 owns. Every number here is a placeholder
        that the pacing test will set."""
        bad, c = self.bad, self.overlay.get("costs")
        keys = {"_comment", "technology", "doctrine", "design", "variant_fraction_per_mille",
                "era_multiplier", "overrides"}
        if not isinstance(c, dict) or set(c) - keys or keys - {"_comment", "overrides"} - set(c):
            bad.add("X113", "overlay.costs", sorted(c) if isinstance(c, dict) else c,
                    f"costs needs {sorted(keys - {'_comment', 'overrides'})}, optionally overrides")
            return None

        def is_int(v, lo):
            return isinstance(v, int) and not isinstance(v, bool) and v >= lo

        def entries(v, kind, where) -> list[dict] | None:
            """A cost list, checked against the currencies its kind may use (PB-SIM13)."""
            if not (isinstance(v, list) and v and all(isinstance(e, dict) and set(e) == {"currency", "amount"}
                                                      for e in v)):
                bad.add("X113", where, v, "a cost is a non-empty list of {currency, amount}")
                return None
            ok = True
            cur = [e["currency"] for e in v]
            for e in v:
                if not is_int(e["amount"], 0):
                    bad.add("X113", where, e, "amount is an integer >= 0")
                    ok = False
                if e["currency"] not in {"research", "doctrine"} | self.pools:
                    bad.add("X112", where, e["currency"], "not research, doctrine or a vocabulary pool")
                    ok = False
            for x in sorted({x for x in cur if cur.count(x) > 1}):
                bad.add("X106", where, x, "currency appears twice in one cost")
                ok = False
            want = {"technology": ["research"], "doctrine": ["doctrine"]}.get(kind)
            if want is not None and cur != want:
                bad.add("X113", where, cur, f"a {kind} costs exactly {want}")
                ok = False
            if kind == "design" and not ({"research", "money"} <= set(cur)
                                         and set(cur) & (self.pools - {"money"}) and "doctrine" not in cur):
                bad.add("X113", where, cur, "a design study costs research, money and at least one material pool")
                ok = False
            return v if ok else None

        base = {k: entries(c[k], k, f"overlay.costs.{k}") for k in ("technology", "doctrine", "design")}
        frac = c["variant_fraction_per_mille"]
        if not (is_int(frac, 1) and frac < 1000):
            bad.add("X113", "overlay.costs.variant_fraction_per_mille", frac, "an integer 1..999: a variant is cheaper")
        era = c["era_multiplier"]
        if not (isinstance(era, dict) and set(era) == {"growth_per_year_per_mille", "cap_per_mille"}
                and is_int(era["growth_per_year_per_mille"], 1000) and is_int(era["cap_per_mille"], 1000)
                and era["growth_per_year_per_mille"] <= era["cap_per_mille"]):
            bad.add("X113", "overlay.costs.era_multiplier", era,
                    "exactly {growth_per_year_per_mille, cap_per_mille}, integers >= 1000, growth <= cap")
        overrides = c.get("overrides", {})
        key = {p.get("authored_id", p["name"]): p for p in projects}   # authored id, or a design's name
        shared = collections.Counter(p.get("authored_id", p["name"]) for p in projects)
        for k, o in overrides.items():
            where = f"overlay.costs.overrides.{k}"
            if shared[k] > 1:   # a design name spelled exactly like an id (ids are opaque, D5)
                bad.add("X108", where, k, "names both a design and a technology or doctrine")
            elif k not in key:
                bad.add("X112", where, k, "not a technology id, doctrine id or design name")
            elif not (isinstance(o, dict) and set(o) == {"cost", "reason"}
                      and isinstance(o["reason"], str) and o["reason"].strip()):
                bad.add("X113", where, o, "an override is {cost, reason} with a one-line reason (D19)")
            else:
                entries(o["cost"], key[k]["kind"], f"{where}.cost")
        if bad.items:
            return None
        for p in projects:
            o = overrides.get(p.get("authored_id", p["name"]))
            if o is not None:
                p.update(cost=o["cost"], cost_source="override", cost_reason=o["reason"])
            else:
                b = base[p["kind"]]
                if p.get("variant_of"):
                    b = [{"currency": e["currency"], "amount": e["amount"] * frac // 1000} for e in b]
                p.update(cost=[dict(e) for e in b], cost_source="rule", cost_reason=None)
        return dict(era)

    def base_holders(self, r: dict, y: dict, where: str, reading) -> list[str]:
        """Base holders: who starts with a base doctrine. Blank = every nation (sheet note).
        Meaningful only on a base doctrine; a cell the codes cannot read needs a declared
        reading in the overlay (readings.base_holders.<id>: [nation names])."""
        cell = r["Base holders"]
        if y["year_declared"] != BASE:
            if cell not in (None, ""):
                self.bad.add("X114", f"{where} Base holders", cell, "only a base doctrine has base holders")
            return []
        if reading is not None:
            bad = [n for n in reading if n not in self.nations]
            for n in bad:
                self.bad.add("X112", f"overlay.readings.base_holders.{r['ID']}", n, "not a nation")
            return [self.nations[n][0] for n in reading if n in self.nations]
        if cell in (None, ""):
            return [tag for tag, _ in self.nations.values()]
        names = code_list(cell, f"{where} Base holders", self.nation_by_code, self.bad, "two-letter nation code")
        return [self.nations[self.nation_by_code[c]][0] for c in names]

    def start_state(self, rows, projects, flags_held, known_flags, nation) -> list[dict]:
        """The complete 1930 holding per nation, resolved here so the engine derives nothing:
        every base technology, the base doctrines it is a holder of, the listed holdings,
        and the covert flags the overlay says it holds."""
        bad = self.bad
        base_techs = [p["id"] for p in projects if p["kind"] == "technology" and p["year_declared"] == BASE]
        out = []
        listed = set()
        for k in flags_held:
            if k not in self.nations:
                bad.add("X112", "overlay.flags_held", k, "not a nation")
        for where, r in rows:
            n = nation(r["Nation"], f"{where} Nation")
            if n is None:
                continue
            listed.add(n)
            tag = self.nations[n][0]
            techs = code_list(r["Technologies held beyond base"], f"{where} Technologies held",
                              self.tech_ids, bad, "technology id")
            docs = code_list(r["Doctrines held"], f"{where} Doctrines held", self.doc_ids, bad, "doctrine id")
            base_docs = [p["id"] for p in projects if p["kind"] == "doctrine"
                         and p["year_declared"] == BASE and tag in p["base_holders"]]
            fl = flags_held.get(n, [])
            for f in fl:
                if f not in known_flags:
                    bad.add("X112", f"overlay.flags_held.{n}", f, "not a declared flag")
            out.append({"nation": tag,
                        "technologies": sorted(set(base_techs) | {qualify(t) for t in techs}),
                        "doctrines": sorted(set(base_docs) | {qualify(d) for d in docs}),
                        "flags": [qualify(f) for f in fl]})
        for n in sorted(set(self.nations) - listed):
            bad.add("X104", "Start 1930", n, "nation has no start-state row")
        return out

    def restrictions(self, rows, cats, known_flags, designs) -> list[dict]:
        """(nation, scope, lifted_by) rows (D4). Scope is a category code, or design types:
        exact 'Type / role' strings, because a submarine is a slice of NAV, not a category
        (D3), and the workbook spells it two ways."""
        bad, out = self.bad, []
        types = {r["Type / role"] for _, r in designs}
        for n, x in enumerate(rows):
            where = f"overlay.restrictions[{n}]"
            if not isinstance(x, dict) or set(x) != {"nation", "scope", "lifted_by"} \
                    or not isinstance(x["scope"], dict) or len(x["scope"]) != 1:
                bad.add("X113", where, x, "a restriction is {nation, scope: {category | design_types}, lifted_by}")
                continue
            if x["nation"] not in self.nations:
                bad.add("X112", where, x["nation"], "not a nation")
                continue
            (kind, val), = x["scope"].items()
            if kind == "category":
                if val not in cats:
                    bad.add("X112", where, val, "not a category code")
                    continue
                scope = {"category": qualify(self.overlay.get("category_fold", {}).get(val, val))}
            elif kind == "design_types":
                unknown = [t for t in val if t not in types] if isinstance(val, list) and val else [val]
                for t in unknown:
                    bad.add("X112", where, t, "not a design Type / role")
                if unknown:
                    continue
                scope = {"design_types": list(val)}
            else:
                bad.add("X113", where, kind, "scope is category or design_types")
                continue
            # null = no covert channel exists: only the treaty's end lifts it (a deferred Focus)
            lifted = x["lifted_by"]
            if lifted is not None and lifted not in known_flags:
                bad.add("X112", where, lifted, "not a declared flag (null if no covert channel exists)")
                continue
            out.append({"nation": self.nations[x["nation"]][0], "scope": scope,
                        "lifted_by": qualify(lifted) if lifted is not None else None})
        return out


def dump(doc: dict) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--workbook", required=True, type=Path)
    ap.add_argument("--vocabulary", required=True, type=Path,
                    help="the game's Content/vocabulary.json: its pools are the material currencies")
    ap.add_argument("--overlay", required=True, type=Path)
    ap.add_argument("--ledger", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--mint", action="store_true", help="append ledger ids for names it lacks")
    a = ap.parse_args(argv)

    bad = Refusals()
    overlay = load_overlay(a.overlay, bad)
    ledger = Ledger.load(a.ledger, bad)
    print(f"workbook {a.workbook}  sha256 {sha256(a.workbook)}")
    print(f"overlay  {a.overlay}  sha256 {sha256(a.overlay) if a.overlay.exists() else '(missing)'}")
    print(f"ledger   {a.ledger}  sha256 {sha256(a.ledger) if a.ledger.exists() else '(new)'}")
    print(f"vocabulary {a.vocabulary}  sha256 {sha256(a.vocabulary) if a.vocabulary.exists() else '(missing)'}")
    pools = load_pools(a.vocabulary, bad)
    files = {}
    if not bad.items:
        conv = Convert(openpyxl.load_workbook(a.workbook, data_only=True), overlay, ledger, a.mint, bad, pools)
        files = conv.run()
    if bad.items:
        print(f"REFUSED - {len(bad.items)} refusal(s), nothing written. By code: "
              + ", ".join(f"{c} {n}" for c, n in sorted(bad.codes.items())))
        for line in bad.items:
            print(f"  {line}")
        return 1
    a.out.mkdir(parents=True, exist_ok=True)
    for name, doc in files.items():
        (a.out / name).write_text(dump(doc), encoding="utf-8", newline="\n")
    if ledger.minted:
        a.ledger.write_text(ledger.dump(), encoding="utf-8", newline="\n")
    r = files["research.json"]
    kinds = collections.Counter(p["kind"] for p in r["projects"])
    b = files["bureau.json"]["bureaus"]
    print(f"wrote {', '.join(files)} to {a.out}")
    print(f"  projects {len(r['projects'])} ({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}), "
          f"categories {len(r['categories'])}, flags {len(r['registries']['flags'])}, "
          f"start {len(r['start'])} nations, restrictions {len(r['restrictions'])}")
    print(f"  bureaus {len(b)} ({sum(x['active'] for x in b)} active), "
          f"vendors {len(files['vendor.json']['vendors'])}, "
          f"effects encoded {sum(p.get('effect') is not None for p in r['projects'])}")
    print(f"  ledger: {ledger.minted} id(s) minted" + (f", written to {a.ledger}" if ledger.minted else ""))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # -I ignores PYTHONIOENCODING
    sys.exit(main())
