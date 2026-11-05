"""Independent second implementation of the game's content contract (Sprint 12.5).

Reads a Para Bellum content directory, the same files the game's C# reader
loads, and refuses anything the contract forbids, with a STABLE CODE. The game
refuses the same files with the same codes. When the two implementations
disagree about a file, one of them is wrong. That disagreement is the point:
two independent readers agreeing is the strongest cheap evidence available.

    uv run python tools/content_check.py <Content-dir>            # validate
    uv run python tools/content_check.py <Content-dir> --resolve  # derived values (terms)
    uv run python tools/content_check.py <Content-dir> --applied  # applied values (layers)
    uv run python tools/content_check.py <Content-dir> --division # derived division stats

The contract is docs/design/CONTENT_MODEL.md in the game repository. When this
file and that document disagree, the document wins. The schemas this tool
applies are content/schemas/*.schema.json, a pinned copy of the authoritative
schemas that ship beside the content.

Every refusal is printed in the contract's error format:

    <file>: <CODE> <rule-name>: <json-path>: <detail>

Tests assert the CODE and the FILE, never the prose (CONTENT_MODEL section 10a).

Two layers, kept apart:
  1. JSON Schema, which checks structure (codes C1xx). If any file fails here,
     layer 2 does not run at all, so one typo cannot cascade into a dozen
     downstream errors. Fix the structure, then the cross-file rules report.
  2. Cross-file rules that a schema cannot express: namespaces, typed
     references, vocabulary membership, trait legality, supersedes, resolved
     values and templates (codes C2xx to C9xx).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "content" / "schemas"

# Content types: (file stem, collection key). Content/<namespace>/<stem>.json.
TYPES = (("class", "classes"), ("model", "models"), ("variant", "variants"),
         ("trait", "traits"), ("template", "templates"),
         ("facility", "facilities"), ("settlement", "settlements"),
         ("bureau", "bureaus"), ("vendor", "vendors"),
         ("claim", "claims"), ("policy", "policies"),
         ("squad", "squads"), ("company", "companies"), ("category", "categories"), ("bundle", "bundles"))
# Singleton rules files: flat objects, exactly ONE across every namespace (C907, C908).
SINGLETONS = ("rates", "combat", "economy", "population", "aggregation")
# Types whose rows are keyed on an engine name (facility kind, settlement type), not an id.
UNKEYED_TYPES = ("facility", "settlement")
# Sprint 13: the research database (research.json, ONE per content set: C907) and the GENERATED models
# (design_model.json, D28: model rows written by the pipeline, kept apart from the hand-authored model.json).
RESEARCH_FILE, DESIGN_MODEL_FILE = "research.json", "design_model.json"
CONTENT_FILES = tuple(f"{t}.json" for t, _ in TYPES) + tuple(f"{t}.json" for t in SINGLETONS)     + (RESEARCH_FILE, DESIGN_MODEL_FILE)
SCHEMAS_FOLDER = "schemas"   # Content/schemas/ is the contract's machine form, not a namespace
VOCABULARY_FILE = "vocabulary.json"
RESERVED_NAMESPACE = "player"   # player designs live in the save, never on disk

# THE RULE-CODE REGISTER is DATA: content/schemas/rule_codes.json, a pinned copy
# of the authoritative file that ships beside the content. Both implementations
# test that the codes they emit are exactly the ones it lists, so the interface
# between the two seats has one source and no copy in code.
RULE_CODES_FILE = SCHEMA_DIR / "rule_codes.json"
REGISTER = json.loads(RULE_CODES_FILE.read_text(encoding="utf-8"))["codes"]
RULES = {c["code"]: c["rule"] for c in REGISTER}
SHIPPED_NAMESPACE = "pb"   # project invariants (C8xx) hold OUR content to more than a mod's
AUTHORED_BASE = ROOT / "data" / "facilities" / "facilities_1930.csv"   # the shipped 1930 starting base (C801)
PROVINCE_DATA = ROOT / "data" / "boundaries" / "provinces_1930_metadata.json"   # the shipped province ids (C804)
UNPRICED_POOLS = ("money", "manpower")   # what economy.json prices cannot name: they are not extracted


@dataclass(frozen=True, order=True)
class Problem:
    file: str
    code: str
    path: str
    detail: str

    def __str__(self) -> str:
        return f"{self.file}: PB-{self.code} {RULES[self.code]}: {self.path}: {self.detail}"


# --- The modifier arithmetic (CONTENT_MODEL section 3, "The arithmetic, exactly") -------
# The signed layer order. A design's stat goes through every layer; only Base has
# writers in content (traits). The other five are present and neutral at 1000.
LAYERS = ("Base", "Facility", "Research", "Doctrine", "Policy", "CaptureState")   # Doctrine: its own layer (D20)
CLAMP_SOURCE = "clamp:non_negative"


def mul_div_nonneg(value: int, per_mille: int) -> int:
    """value x per_mille / 1000, truncating, for NON-NEGATIVE operands only.

    CONTENT_MODEL section 3 point 6: C# `/` truncates toward zero and Python `//`
    floors. The two agree ONLY because every resolved value is non-negative. This
    helper refuses a negative operand instead of silently rounding a different way
    from C#. Any future SIGNED quantity needs one rounding rule agreed in the
    contract before either side implements it."""
    if value < 0 or per_mille < 0:
        raise ValueError(f"signed operand ({value} x {per_mille}/1000): the two implementations round "
                         f"negatives differently, so the contract forbids them (section 3 point 6)")
    return value * per_mille // 1000


def apply_layers(base: int, base_contributions: list[tuple[str, int]]) -> tuple[int, list[dict]]:
    """Resolve one stat through the signed layers. Returns (value, layers), where each
    layer is {layer, per_mille, sources}.

    - The Base layer is 1000 + the SUM of its contributions; traits sum (point 2).
    - ONE truncating division per layer, after summing, never one per contribution
      (point 4).
    - A layer summing below 0 is held at 0, VISIBLY: a clamp:non_negative source
      whose delta is exactly what it added, so the layer still equals 1000 + the
      sum of its sources (point 5). A layer above 1000 is never capped."""
    if base < 0:
        raise ValueError(f"base {base} is negative; C602 should have refused it")
    out, value = [], base
    for name in LAYERS:
        sources = list(base_contributions) if name == "Base" else []
        pm = 1000 + sum(d for _, d in sources)
        if pm < 0:
            sources.append((CLAMP_SOURCE, -pm))
            pm = 0
        value = mul_div_nonneg(value, pm)
        out.append({"layer": name, "per_mille": pm, "sources": sources})
    return value, out


def _schema(stem: str) -> Draft202012Validator:
    schema = json.loads((SCHEMA_DIR / f"{stem}.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _json_path(parts) -> str:
    out = "$"
    for p in parts:
        out += f"[{p}]" if isinstance(p, int) else f".{p}"
    return out


def _schema_code(err) -> str:
    """Map a failing JSON Schema keyword onto the register."""
    kw = err.validator
    if kw == "required":
        return "C101"
    if kw == "type":
        want = err.validator_value
        if want == "integer" and isinstance(err.instance, float):
            return "C103"
        return "C102"
    if kw == "minimum":
        return "C104"
    if kw in ("additionalProperties", "propertyNames"):
        return "C105"
    if kw == "pattern":
        return "C106"
    if kw == "const" and list(err.absolute_path) == ["format"]:
        return "C107"
    if kw == "uniqueItems":
        return "C203"
    return "C102"   # oneOf, minLength and the rest: the value is not the declared shape


def c103_detail(v: float) -> str:
    """A C103 refusal names the FIX, not only the code (AD-M36: content people author by hand). An integral
    float is told the exact integer to write; a fraction is told to scale its unit, as the bureau homes do."""
    if v.is_integer():
        return f"{v!r} is written with a decimal point: write {int(v)} (content numbers are integers, C103)"
    return (f"{v!r} has a fractional part: content numbers are integers, so scale the unit until the value is "
            f"whole (as home coordinates ship in microdegrees, lon_e6) and write that integer (C103)")


def _schema_problems(rel: str, stem: str, doc) -> list[Problem]:
    out = []
    for err in sorted(_schema(stem).iter_errors(doc), key=lambda e: list(e.absolute_path)):
        # A failing `required` names its missing key in the message, not the path.
        path = _json_path(err.absolute_path)
        if err.validator == "required":
            # One error per missing key; name THAT key's path, as the C# reader does.
            m = re.match(r"'(.+?)' is a required property", err.message)
            path += f".{m.group(1)}" if m else ""
        code = _schema_code(err)
        out.append(Problem(rel, code, path, c103_detail(err.instance) if code == "C103" else err.message))
    return out


def _fractional_numbers(rel: str, doc, errs: list[Problem]) -> list[Problem]:
    """C103 is universal, as the C# reader enforces it, not only where a schema happens to declare "integer":
    a schema that declared "number" would otherwise let a fraction through on this side alone. The reading is
    the TOKEN's, never the value's: a number written with a fractional part, a decimal point or an exponent
    (any JSON float, so 3.0 too) is refused, as Newtonsoft's Float token is. Content numbers are integers by
    contract, so 3.0 is an authoring slip, and neither side has to judge float equality. One diagnosis per
    path: a path the schema already refused (C103, or a wrong type) is not reported again."""
    seen = {e.path for e in errs}
    out = []

    def walk(v, parts):
        if isinstance(v, float):
            path = _json_path(parts)
            if path not in seen:
                out.append(Problem(rel, "C103", path, c103_detail(v)))
        elif isinstance(v, dict):
            for key, x in v.items():
                walk(x, parts + [key])
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, parts + [i])

    walk(doc, [])
    return errs + out


ATOM_FIELDS = ("prerequisites", "cost_reducers", "acquisition", "doctrine_role")


def _padded(s) -> bool:
    return isinstance(s, str) and s != "" and (s[0].isspace() or s[-1].isspace())


def _whitespace_first(rel: str, doc, errs: list[Problem]) -> list[Problem]:
    """D9, ruled for EVERY atom kind: an atom value (or an xp branch) with leading or trailing whitespace
    is refused by its own code, C111, AHEAD of resolution, and that is the only problem reported for the
    value. A padded id would otherwise read as a malformed id (C106) and a padded nation tag as an unknown
    nation (C301): correct refusals, wrong diagnoses. Walks the raw document defensively, since it may
    have failed the schema elsewhere."""
    padded = []
    projects = doc.get("projects") if isinstance(doc, dict) else None
    for i, p in enumerate(projects if isinstance(projects, list) else []):
        for f in ATOM_FIELDS:
            clauses = p.get(f) if isinstance(p, dict) else None
            for c, clause in enumerate(clauses if isinstance(clauses, list) else []):
                for a, atom in enumerate(clause if isinstance(clause, list) else []):
                    v = atom.get("value") if isinstance(atom, dict) else None
                    if _padded(v):
                        padded.append(_json_path(["projects", i, f, c, a, "value"]))
                    elif isinstance(v, dict) and _padded(v.get("branch")):
                        padded.append(_json_path(["projects", i, f, c, a, "value", "branch"]))
    if not padded:
        return errs
    kept = [e for e in errs if e.path not in padded]
    return kept + [Problem(rel, "C111", path, "leading or trailing whitespace: atom values are matched exactly, "
                                             "never trimmed") for path in padded]


def _ns(id_: str) -> str:
    return id_.split(":", 1)[0]


class ContentSet:
    """A loaded content directory: the vocabulary, plus every type file of every
    namespace folder. `problems` holds everything refused, in register order."""

    def __init__(self, content_root: Path):
        self.root = Path(content_root)
        self.problems: list[Problem] = []
        self.vocab: dict = {}
        # stem -> list of (relative file, namespace, entry index, entry)
        self.entries: dict[str, list[tuple[str, str, int, dict]]] = {s: [] for s, _ in TYPES}
        self.singletons: dict[str, list[tuple[str, dict]]] = {t: [] for t in SINGLETONS}
        self.facility_headers: list[tuple[str, dict]] = []
        self.extensions: list[tuple[str, str, int, dict]] = []   # class.json entries carrying "extends"
        self.namespaces: set[str] = set()
        self.research: list[tuple[str, str, dict]] = []   # (file, namespace, research.json document)
        self.policy_starts: list[tuple[str, int, dict]] = []   # (file, index, policy.json start row)
        self._load()
        # Structure first. If ANY file fails the schema layer, its entries are
        # missing from the set, and every cross-file rule would cascade from that
        # hole (one missing key reading as a dozen dangling references). So the
        # cross-file layer runs only on a structurally sound set.
        if not any(p.code.startswith("C1") for p in self.problems):
            self._cross_file()
            self._project_invariants()
        self.problems.sort()

    # -- loading and the schema layer ------------------------------------------------

    def _read(self, path: Path, stem: str):
        rel = path.relative_to(self.root).as_posix()
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.problems.append(Problem(rel, "C108", "$", str(e)))
            return rel, None
        errs = _fractional_numbers(rel, doc, _schema_problems(rel, stem, doc))
        if stem == "research":
            errs = _whitespace_first(rel, doc, errs)
        self.problems += errs
        return rel, (None if errs else doc)

    def _research_shape(self, rel: str, doc: dict) -> None:
        """research.json rules a schema cannot express, checked in the STRUCTURAL phase (C1xx) exactly
        where the game's reader checks them (ContentReader.Research.cs): a cost reason that matches its
        source (D19), an effect whose form agrees with itself (E5, and no_target's one-line reason and
        needed target), and the era multiplier's floor and ordering (D24)."""
        def add(code, path, detail):
            self.problems.append(Problem(rel, code, _json_path(path), detail))
        for i, p in enumerate(doc["projects"]):
            src, reason = p["cost_source"], p["cost_reason"]
            if src == "override" and not reason:
                add("C101", ["projects", i, "cost_reason"], "an overridden cost carries its one-line reason")
            elif src == "rule" and reason is not None:
                add("C102", ["projects", i, "cost_reason"], "a rule-costed row carries no reason")
            e = p.get("effect")
            if isinstance(e, dict) and "no_target" in e:
                for k in ("needs", "reason"):
                    if not e["no_target"][k].strip():
                        add("C101", ["projects", i, "effect", "no_target", k], f"a no_target effect names its {k}")
            elif isinstance(e, dict):
                if e["gate_only"] and e["modifiers"]:
                    add("C102", ["projects", i, "effect", "modifiers"], "a gate-only effect writes no modifiers")
                elif not e["gate_only"] and not e["modifiers"]:
                    add("C102", ["projects", i, "effect", "modifiers"],
                        "an effect that is not gate-only writes at least one modifier")
        g, c = doc["era_multiplier"]["growth_per_year_per_mille"], doc["era_multiplier"]["cap_per_mille"]
        if g < 1000:
            add("C102", ["era_multiplier", "growth_per_year_per_mille"], f"{g} is below 1000: M(era) never discounts")
        elif c < g:
            add("C102", ["era_multiplier", "cap_per_mille"], f"{c} is below the growth {g}")

    def _load(self):
        vpath = self.root / VOCABULARY_FILE
        if not vpath.exists():
            self.problems.append(Problem(VOCABULARY_FILE, "C101", "$", "Content/vocabulary.json is missing"))
        else:
            _, doc = self._read(vpath, "vocabulary")
            self.vocab = doc or {}
        self.singletons: dict[str, list[tuple[str, dict]]] = {t: [] for t in SINGLETONS}
        self.facility_headers: list[tuple[str, dict]] = []   # (file, the facility.json document)
        for ns_dir in sorted(p for p in self.root.iterdir() if p.is_dir()):
            ns = ns_dir.name
            if ns == SCHEMAS_FOLDER:
                continue
            self.namespaces.add(ns)
            # D28 gap 1: nothing in a namespace folder is silently ignored. A misnamed
            # file (models.json) used to load nothing and say nothing (C109); a folder
            # with no content file at all defines nothing (C110). Non-JSON files
            # (README.md, Unity's .meta) are not content and stay ignored.
            jsons = sorted(f.name for f in ns_dir.iterdir() if f.is_file() and f.suffix == ".json")
            for name in jsons:
                if name not in CONTENT_FILES:
                    self.problems.append(Problem(f"{ns}/{name}", "C109", "$",
                                                 f'"{name}" is not a content file: the names are {", ".join(CONTENT_FILES)}'))
            if not any(n in CONTENT_FILES for n in jsons):
                self.problems.append(Problem(f"{ns}/", "C110", "$", f"Content/{ns}/ holds no content file, so it defines nothing"))
            for stem, key in TYPES:
                f = ns_dir / f"{stem}.json"
                if not f.exists():
                    continue
                rel, doc = self._read(f, stem)
                if doc is None:
                    continue
                if stem == "facility":
                    self.facility_headers.append((rel, doc))
                if stem == "policy":
                    self.policy_starts += [(rel, i, s) for i, s in enumerate(doc["start"])]
                for i, e in enumerate(doc[key]):
                    if stem == "model" and "production_template" in e:
                        self.problems.append(Problem(rel, "C105", _json_path(["models", i, "production_template"]),
                                                     "only a generated model (design_model.json) names a template"))
                    if stem == "bureau" and e["active"] and e["capacity"] is None:
                        self.problems.append(Problem(rel, "C101", _json_path(["bureaus", i, "capacity"]),
                                                     "an active bureau carries its capacity (D3)"))
                    if stem == "class" and "extends" in e:
                        self.extensions.append((rel, ns, i, e))
                    else:
                        self.entries[stem].append((rel, ns, i, e))
            for stem in SINGLETONS:
                f = ns_dir / f"{stem}.json"
                if f.exists():
                    rel, doc = self._read(f, stem)
                    if doc is not None:
                        self.singletons[stem].append((rel, doc))
            f = ns_dir / DESIGN_MODEL_FILE
            if f.exists():
                rel, doc = self._read(f, "model")   # the same rows as model.json (D28)
                if doc is not None:
                    for i, e in enumerate(doc["models"]):
                        if "production_template" not in e:
                            self.problems.append(Problem(rel, "C101", _json_path(["models", i, "production_template"]),
                                                         "a generated model names the template it copied (D29)"))
                        self.entries["model"].append((rel, ns, i, e))
            f = ns_dir / RESEARCH_FILE
            if f.exists():
                rel, doc = self._read(f, "research")
                if doc is not None:
                    self._research_shape(rel, doc)
                    self.research.append((rel, ns, doc))

    # -- the cross-file layer ----------------------------------------------------------

    def _add(self, rel, code, path, detail):
        self.problems.append(Problem(rel, code, path, detail))

    def _cross_file(self):
        stat_keys = set(self.vocab.get("stat_keys", []))
        pools = set(self.vocab.get("pools", []))
        coll = dict(TYPES)

        def where(stem, i, *rest):
            return _json_path([coll[stem], i, *rest])

        # Identity: namespace, reserved namespace, duplicates within a type.
        self.by_id: dict[str, dict[str, dict]] = {s: {} for s, _ in TYPES if s not in UNKEYED_TYPES}
        self.loc: dict[tuple[str, str], tuple[str, int]] = {}
        for stem, _ in TYPES:
            if stem in UNKEYED_TYPES:
                continue
            for rel, ns, i, e in self.entries[stem]:
                id_ = e["id"]
                if _ns(id_) == RESERVED_NAMESPACE:
                    self._add(rel, "C204", where(stem, i, "id"),
                              f'"{id_}": the player namespace is reserved for designs in a save')
                elif _ns(id_) != ns:
                    self._add(rel, "C202", where(stem, i, "id"),
                              f'"{id_}" is defined in the {ns} folder, so its namespace must be {ns}')
                if id_ in self.by_id[stem]:
                    first = self.loc[(stem, id_)]
                    self._add(rel, "C203", where(stem, i, "id"),
                              f'{stem} "{id_}" is already defined at {first[0]} {coll[stem]}[{first[1]}]')
                    continue
                self.by_id[stem][id_] = e
                self.loc[(stem, id_)] = (rel, i)
        # A DESIGN is a model or a variant, one id space (CONTENT_MODEL 10d).
        for vid in sorted(set(self.by_id["model"]) & set(self.by_id["variant"])):
            rel, i = self.loc[("variant", vid)]
            self._add(rel, "C205", where("variant", i, "id"),
                      f'"{vid}" is both a model and a variant; stock and production key on the design id')

        def ref(rel, path, id_, stem, *, also=None) -> bool:
            """Resolve a typed reference. False (and a problem) if it fails."""
            if id_ is None:
                return True
            if _ns(id_) not in self.namespaces:
                self._add(rel, "C201", path, f'"{id_}": no Content/{_ns(id_)}/ folder declares namespace {_ns(id_)}')
                return False
            if id_ in self.by_id.get(stem, {}):
                return True
            if also and id_ in self.by_id[also]:
                return False   # the caller reports the specific rule (variant-of-variant)
            self._add(rel, "C301", path, f'"{id_}" is not a defined {stem}')
            return False

        classes, models, variants, traits = (self.by_id[s] for s in ("class", "model", "variant", "trait"))
        # A model's unlock resolves against the research projects of ANY kind (D28).
        self.by_id["research"] = self._research_identity()

        # Classes: stat surface in the vocabulary; legal traits exist and fit the surface.
        for rel, _, i, c in self.entries["class"]:
            for k in c["stats"]:
                if k not in stat_keys:
                    self._add(rel, "C402", where("class", i, "stats", k), f'"{k}" is not an engine stat key')
            for j, t in enumerate(c["traits"]):
                if ref(rel, where("class", i, "traits", j), t, "trait"):
                    for m in traits[t]["modifiers"]:
                        if m["stat"] not in c["stats"]:
                            self._add(rel, "C502", where("class", i, "traits", j),
                                      f'trait "{t}" modifies "{m["stat"]}", which class "{c["id"]}" does not have')

        # Class EXTENSIONS (D28 gap 2): checked against their OWN file, then every class
        # gains the union of its extensions' traits after its own, in sorted order, so
        # the result does not depend on which folder loaded first.
        extra: dict[str, set[str]] = {}
        for rel, _, i, x in self.extensions:
            target = x["extends"]
            if not ref(rel, where("class", i, "extends"), target, "class"):
                continue
            for j, t in enumerate(x["traits"]):
                if not ref(rel, where("class", i, "traits", j), t, "trait"):
                    continue
                for m in traits[t]["modifiers"]:
                    if m["stat"] not in classes[target]["stats"]:
                        self._add(rel, "C502", where("class", i, "traits", j),
                                  f'trait "{t}" modifies "{m["stat"]}", which class "{target}" does not have')
                extra.setdefault(target, set()).add(t)
        for cid, adds in extra.items():
            own = classes[cid]["traits"]
            classes[cid]["traits"] = list(own) + sorted(t for t in adds if t not in own)

        # Traits: every modified stat is an engine stat key.
        for rel, _, i, t in self.entries["trait"]:
            for j, m in enumerate(t["modifiers"]):
                if m["stat"] not in stat_keys:
                    self._add(rel, "C402", where("trait", i, "modifiers", j, "stat"),
                              f'"{m["stat"]}" is not an engine stat key')

        def check_stats(rel, path, stats, cls):
            for k in stats:
                if k not in stat_keys:
                    self._add(rel, "C402", _json_path([*path, k]), f'"{k}" is not an engine stat key')
                elif cls is not None and k not in cls["stats"]:
                    self._add(rel, "C401", _json_path([*path, k]),
                              f'"{k}" is not on the stat surface of class "{cls["id"]}"')

        def check_pools(rel, path, cost):
            for p in cost:
                if p not in pools:
                    self._add(rel, "C403", _json_path([*path, p]), f'"{p}" is not a resource pool')

        def check_trait_legal(rel, path, t, cls):
            if cls is not None and t in traits and t not in cls["traits"]:
                self._add(rel, "C501", path, f'trait "{t}" is not legal on class "{cls["id"]}"')

        # Models.
        for rel, _, i, m in self.entries["model"]:
            cls = classes.get(m["class"]) if ref(rel, where("model", i, "class"), m["class"], "class") else None
            ref(rel, where("model", i, "unlock"), m["unlock"], "research")
            check_stats(rel, ["models", i, "stats"], m["stats"], cls)
            check_pools(rel, ["models", i, "cost"], m["cost"])
            for j, t in enumerate(m["traits"]):
                if ref(rel, where("model", i, "traits", j), t, "trait"):
                    check_trait_legal(rel, where("model", i, "traits", j), t, cls)
            sup = m["supersedes"]
            if sup is not None and ref(rel, where("model", i, "supersedes"), sup, "model"):
                if models[sup]["class"] != m["class"]:
                    self._add(rel, "C303", where("model", i, "supersedes"),
                              f'"{m["id"]}" ({m["class"]}) supersedes "{sup}" ({models[sup]["class"]}); '
                              f'supersedes is within one class')

        # Supersedes is acyclic (a self-loop is a cycle of one).
        reported = set()
        for start in sorted(models):
            seen, cur = [], start
            while cur is not None and cur in models and cur not in seen:
                seen.append(cur)
                cur = models[cur]["supersedes"]
            if cur is not None and cur in seen:
                loop = tuple(sorted(seen[seen.index(cur):]))
                if loop not in reported:
                    reported.add(loop)
                    rel, i = self.loc[("model", cur)]
                    self._add(rel, "C302", where("model", i, "supersedes"),
                              "supersedes loops: " + " -> ".join(seen[seen.index(cur):] + [cur]))

        # Variants: one level, deltas resolve non-negative, trait add/remove legal.
        for rel, _, i, v in self.entries["variant"]:
            mp = where("variant", i, "model")
            base = None
            if v["model"] in variants and v["model"] not in models:
                self._add(rel, "C601", mp, f'"{v["model"]}" is a variant; a variant\'s model must be a model '
                                            f'(one level: traits compose)')
            elif ref(rel, mp, v["model"], "model", also="variant"):
                base = models[v["model"]]
            cls = classes.get(base["class"]) if base else None
            check_stats(rel, ["variants", i, "stats"], v["stats"], cls)
            check_pools(rel, ["variants", i, "cost"], v["cost"])
            for j, t in enumerate(v["traits_add"]):
                if ref(rel, where("variant", i, "traits_add", j), t, "trait"):
                    check_trait_legal(rel, where("variant", i, "traits_add", j), t, cls)
            for j, t in enumerate(v["traits_remove"]):
                if ref(rel, where("variant", i, "traits_remove", j), t, "trait") and base \
                        and t not in base["traits"]:
                    self._add(rel, "C503", where("variant", i, "traits_remove", j),
                              f'"{base["id"]}" does not carry trait "{t}"')
            if base and cls:
                for k, d in v["stats"].items():
                    if k in cls["stats"]:
                        r = base["stats"].get(k, cls["stats"][k]) + d
                        if r < 0:
                            self._add(rel, "C602", where("variant", i, "stats", k), f"{k} resolves to {r}")
                for p, d in v["cost"].items():
                    r = base["cost"].get(p, 0) + d
                    if r < 0:
                        self._add(rel, "C602", where("variant", i, "cost", p), f"cost {p} resolves to {r}")
                for k, d in v["production"].items():
                    r = base["production"][k] + d
                    if r < 0:
                        self._add(rel, "C602", where("variant", i, "production", k), f"production {k} resolves to {r}")

        # Templates: at least one exists, and every class one requires has a design. Identity (namespace,
        # reserved namespace, duplicates) is the generic id check above.
        design_classes = {m["class"] for m in models.values()}
        symbols = set(self.vocab.get("counter_symbols", []))
        for rel, _, i, t in self.entries["template"]:
            if t["symbol"] not in symbols:
                self._add(rel, "C409", where("template", i, "symbol"), f'"{t["symbol"]}" is not a counter symbol the game can draw')
            for p in t["cost"]:
                if p not in pools:
                    self._add(rel, "C403", where("template", i, "cost", p), f'"{p}" is not a resource pool')
            for j, e in enumerate(t["equipment"]):
                if ref(rel, where("template", i, "equipment", j, "class"), e["class"], "class") \
                        and e["class"] not in design_classes:
                    self._add(rel, "C902", where("template", i, "equipment", j, "class"),
                              f'no model or variant belongs to class "{e["class"]}"')
        if not self.entries["template"]:
            self._add("template.json", "C915", "$.templates", "no namespace defines a division template")
        self._facilities_and_settlements(pools)
        self._research_graph(pools)
        self._claims()
        self._policies()
        self._population()
        self._formations(ref, where)

    # -- the research database (Sprint 13), mirroring ContentReader.Research.cs -----------

    def _formations(self, ref, where) -> None:
        """Four levels and no fifth: division > company > platoon (inline) > squad or vehicle (AD-M45)."""
        vocab_stats = {s["stat"]: s["source"] for s in self.vocab.get("division_stats", [])}
        authored = [s for s, src in vocab_stats.items() if src == "authored"]
        biomes = set(self.vocab.get("biomes", []))
        columns = set(self.vocab.get("division_columns", []))
        rules = set(self.vocab.get("aggregation_rules", []))
        designs = {**self.by_id["model"], **self.by_id["variant"]}

        def vehicle_model(id_):
            d = designs[id_]
            return self.by_id["model"][d["model"]] if "model" in d else d

        for rel, _, i, s in self.entries["squad"]:
            if s["officers"] > s["men"]:
                self._add(rel, "C606", where("squad", i, "officers"), f'{s["officers"]} officers in a squad of {s["men"]} men')
        for rel, _, i, c in self.entries["category"]:
            r = c["returns_per_mille"]
            if any(b > a for a, b in zip(r, r[1:])):
                self._add(rel, "C604", where("category", i, "returns_per_mille"), f"{r} rises: each further company must add no more than the last")
            for j, q in enumerate(c["requires"]):
                if ref(rel, where("category", i, "requires", j, "category"), q["category"], "category") and q["category"] == c["id"]:
                    self._add(rel, "C309", where("category", i, "requires", j, "category"), f'{c["id"]} requires itself, which is always met')
        for rel, _, i, c in self.entries["company"]:
            ref(rel, where("company", i, "category"), c["category"], "category")
            for key in ("platoons", "support_sections"):
                for j, p in enumerate(c[key]):
                    for k, e in enumerate(p["elements"]):
                        if "squad" in e:
                            ref(rel, where("company", i, key, j, "elements", k, "squad"), e["squad"], "squad")
                        elif e["vehicle"] in designs:
                            if "crew" not in designs[e["vehicle"]]["stats"] and "crew" not in vehicle_model(e["vehicle"])["stats"]:
                                self._add(rel, "C308", where("company", i, key, j, "elements", k, "vehicle"),
                                          f'"{e["vehicle"]}" authors no crew: write its crew on the model, 0 if it carries none')
                        else:
                            ref(rel, where("company", i, key, j, "elements", k, "vehicle"), e["vehicle"], "model")
            for k in c["stats"]:
                if k not in vocab_stats:
                    self._add(rel, "C410", where("company", i, "stats", k), f'"{k}" is not a division stat')
                elif vocab_stats[k] != "authored":
                    self._add(rel, "C415", where("company", i, "stats", k), f'"{k}" is {vocab_stats[k]}, not authored, so a company cannot set it')
            for k in authored:
                if k not in c["stats"]:
                    self._add(rel, "C411", where("company", i, "stats", k), f'no value for the authored stat "{k}"')
            for b in c["terrain"]:
                if b not in biomes:
                    self._add(rel, "C414", where("company", i, "terrain", b), f'"{b}" is not a biome')
        used_columns = set()
        for rel, _, i, t in self.entries["template"]:
            for j, s in enumerate(t["companies"]):
                ref(rel, where("template", i, "companies", j, "company"), s["company"], "company")
                used_columns.add(s["column"])
                if s["column"] not in columns:
                    self._add(rel, "C413", where("template", i, "companies", j, "column"), f'"{s["column"]}" is not a column of a division sheet')
        for rel, _, i, b in self.entries["bundle"]:
            if "extends" in b:
                self._add(rel, "C920", where("bundle", i, "extends"),
                          f'{b["id"]} tries to extend "{b["extends"]}": a bundle is sealed; list the companies yourself, or use the designer')
            for j, s in enumerate(b["companies"]):
                ref(rel, where("bundle", i, "companies", j, "company"), s["company"], "company")
        for rel, doc in self.singletons["aggregation"][:1]:
            seen = set()
            for i, r in enumerate(doc["rules"]):
                path = lambda *rest, i=i: _json_path(["rules", i, *rest])
                if r["stat"] not in vocab_stats:
                    self._add(rel, "C410", path("stat"), f'"{r["stat"]}" is not a division stat')
                elif r["stat"] in seen:
                    self._add(rel, "C203", path("stat"), f'"{r["stat"]}" already has a row')
                seen.add(r["stat"])
                if r["rule"] not in rules:
                    self._add(rel, "C412", path("rule"), f'"{r["rule"]}" is not an aggregation rule')
                if r["subset"] != "all" and (r["subset"] not in ("line", "support") or
                                             (self.entries["template"] and r["subset"] not in used_columns)):
                    self._add(rel, "C917", path("subset"), f'"{r["subset"]}" selects no company in any template')
                if r["rule"] == "average_lines_sum_support" and r["subset"] != "all":
                    self._add(rel, "C918", path("subset"), "this rule reads line and support, so its subset must be all")
                w = r.get("max_weight_per_mille")
                if r["rule"] == "max_mean_blend":
                    if w is None or w > 1000:
                        self._add(rel, "C919", path("max_weight_per_mille"), f"a blend needs a weight from 0 to 1000, not {w}")
                elif w is not None:
                    self._add(rel, "C919", path("max_weight_per_mille"), f'{r["rule"]} ignores a blend weight')
            for s in vocab_stats:
                if s not in seen:
                    self._add(rel, "C916", "$.rules", f'no row says how "{s}" is derived')

    # -- the division arithmetic: the second implementation of what the game computes --------

    def walk_company(self, company: dict, crew) -> dict[str, int]:
        """Men and officers by walking every platoon and support section to its squads and vehicle crew. A
        vehicle's passengers are seats, not men: whoever rides is counted as their own squad."""
        men = officers = 0
        for p in company["platoons"] + company["support_sections"]:
            for e in p["elements"]:
                n = p["count"] * e["count"]
                if "squad" in e:
                    s = self.by_id["squad"][e["squad"]]
                    men += n * s["men"]
                    officers += n * s["officers"]
                else:
                    men += n * crew(e["vehicle"])
        return {"men": men, "officers": officers}

    def division_figures(self) -> dict[str, dict]:
        """Every template's derived stats, by CONTENT_MODEL's exact arithmetic. All integer, with ONE truncating
        division per figure and none of its inputs already rounded: a blend is not computed from a rounded mean."""
        resolved = self.resolve()

        def crew(id_):
            return resolved[id_]["stats"]["crew"]["value"]   # C308 refused any vehicle with no authored crew

        rows = {r["stat"]: r for r in self.singletons["aggregation"][0][1]["rules"]}
        out = {}
        for t in sorted(self.by_id["template"].values(), key=lambda x: x["id"]):
            slots = [(self.by_id["company"][s["company"]], s["count"], s["column"]) for s in t["companies"]]
            walked = {id(c): self.walk_company(c, crew) for c, _, _ in slots}
            figures: dict = {}
            for st in self.vocab["division_stats"]:
                stat, source, rule = st["stat"], st["source"], rows[st["stat"]]
                if source == "table":
                    named = sorted({b for c, _, _ in slots for b in c["terrain"]})
                    figures[stat] = {b: _aggregate(rule, [(c["terrain"].get(b, 1000), n, col) for c, n, col in slots], 1000) for b in named}
                else:
                    value = (lambda c: walked[id(c)][stat]) if source == "walked" else (lambda c: c["stats"][stat])
                    figures[stat] = _aggregate(rule, [(value(c), n, col) for c, n, col in slots], 0)
            out[t["id"]] = figures
        return out

    def _research_identity(self) -> dict[str, dict]:
        """research.json's own ids (categories, projects, flags, treaties): well formed (schema, C106), in
        the file's namespace (C202), not reserved (C204), defined once (C203). Returns the projects by id."""
        self.r_categories, self.r_projects, self.r_flags, self.r_treaties = {}, {}, {}, {}
        for rel, ns, doc in self.research:
            for table, key, what in ((self.r_categories, "categories", "category"), (self.r_projects, "projects", "project")):
                for i, e in enumerate(doc[key]):
                    self._own_id(rel, ns, [key, i], e, table, what)
            for table, key, what in ((self.r_flags, "flags", "flag"), (self.r_treaties, "treaties", "treaty")):
                for i, e in enumerate(doc["registries"][key]):
                    self._own_id(rel, ns, ["registries", key, i], e, table, what)
        return self.r_projects

    def _own_id(self, rel, ns, path, e, table, what):
        id_ = e["id"]
        if _ns(id_) == RESERVED_NAMESPACE or ns == RESERVED_NAMESPACE:
            self._add(rel, "C204", _json_path([*path, "id"]), f'"{id_}": the player namespace is reserved for the save')
        elif _ns(id_) != ns:
            self._add(rel, "C202", _json_path([*path, "id"]), f'"{id_}" is defined in Content/{ns}/')
        if id_ in table:
            self._add(rel, "C203", _json_path([*path, "id"]), f'{what} "{id_}" is defined twice')
            return
        table[id_] = e

    def _research_graph(self, pools: set[str]) -> None:
        """Every research reference resolves to the registry its kind names (C301, or C201 first for an
        undeclared namespace); costs name research, doctrine or an engine pool (C403), each once (C203);
        exclusive doctrine pairs are symmetric (C304); one research.json per content set (C907)."""
        if not self.research:
            return
        bureaus, vendors = self.by_id["bureau"], self.by_id["vendor"]
        cats, projects, flags, treaties = self.r_categories, self.r_projects, self.r_flags, self.r_treaties
        fielded, focus, xp, start = set(), set(), set(), {}
        for _, _, doc in self.research:
            reg = doc["registries"]
            fielded |= {x["id"] for x in reg["fielded"]}   # {id, authored_id, name} rows
            focus |= {x["id"] for x in reg["focus"]}
            xp |= {(x["branch"], x["tier"]) for x in reg["xp"]}
            for s in doc["start"]:
                start.setdefault(s["nation"], s)

        def ref(rel, path, id_, exists, what) -> bool:
            if exists:
                return True
            if _ns(id_) not in self.namespaces:
                self._add(rel, "C201", _json_path(path),
                          f'"{id_}": no Content/{_ns(id_)}/ folder declares namespace {_ns(id_)}')
            else:
                self._add(rel, "C301", _json_path(path), f'"{id_}" is not a defined {what}')
            return False

        def is_kind(id_, kind):
            return id_ in projects and projects[id_]["kind"] == kind

        def atoms(rel, path, cnf):
            for c, clause in enumerate(cnf):
                for a, atom in enumerate(clause):
                    k, v, at = atom["kind"], atom["value"], [*path, c, a, "value"]
                    if k in ("technology", "doctrine", "design"):
                        ref(rel, at, v, is_kind(v, k), k)
                    elif k == "flag":
                        ref(rel, at, v, v in flags, "flag")
                    elif k == "fielded":
                        ref(rel, at, v, v in fielded, "fielded entry")
                    elif k == "focus":
                        ref(rel, at, v, v in focus, "focus")
                    elif k in ("bureau", "seized"):
                        ref(rel, at, v, v in bureaus, "bureau")
                    elif k in ("licence", "import"):
                        ref(rel, at, v, v in bureaus or v in vendors, "bureau or vendor")
                    elif k == "captured":
                        if v not in start:
                            self._add(rel, "C301", _json_path(at), f'"{v}" is not a nation with a start row')
                    elif k == "xp":
                        if (v["branch"], v["tier"]) not in xp:
                            self._add(rel, "C301", _json_path(at), f"{v['branch']}:{v['tier']} is not a defined xp tier")

        for rel, ns, doc in self.research:
            for i, c in enumerate(doc["categories"]):
                for k in ("folded_into", "fallback_into"):
                    if c[k] is not None:
                        ref(rel, ["categories", i, k], c[k], c[k] in cats, "category")
            for i, p in enumerate(doc["projects"]):
                base = ["projects", i]
                atoms(rel, [*base, "prerequisites"], p["prerequisites"])
                if p["kind"] != "doctrine":
                    atoms(rel, [*base, "cost_reducers"], p["cost_reducers"])
                seen = set()
                for j, cost in enumerate(p["cost"]):
                    cur = cost["currency"]
                    if cur not in ("research", "doctrine") and cur not in pools:
                        self._add(rel, "C403", _json_path([*base, "cost", j, "currency"]),
                                  f'"{cur}" is neither research, doctrine nor an engine pool')
                    elif cur in seen:
                        self._add(rel, "C203", _json_path([*base, "cost", j, "currency"]), f'"{cur}" is listed twice')
                    seen.add(cur)
                if p["kind"] == "technology":
                    ref(rel, [*base, "category"], p["category"], p["category"] in cats, "category")
                elif p["kind"] == "doctrine":
                    for j, x in enumerate(p["exclusive_with"]):
                        if ref(rel, [*base, "exclusive_with", j], x, is_kind(x, "doctrine"), "doctrine"):
                            if x == p["id"]:
                                self._add(rel, "C304", _json_path([*base, "exclusive_with", j]),
                                          f'"{x}" is exclusive with itself')
                            elif p["id"] not in projects[x]["exclusive_with"]:
                                self._add(rel, "C304", _json_path([*base, "exclusive_with", j]),
                                          f'"{p["id"]}" is exclusive with "{x}", which does not name it back')
                else:
                    atoms(rel, [*base, "acquisition"], p["acquisition"])
                    atoms(rel, [*base, "doctrine_role"], p["doctrine_role"])
                    if p["variant_of"] is not None:
                        ref(rel, [*base, "variant_of"], p["variant_of"], is_kind(p["variant_of"], "design"), "design")
                    if p["lead"] is not None:
                        ref(rel, [*base, "lead"], p["lead"], p["lead"] in bureaus, "bureau")
                    for j, b in enumerate(p["producers"]):
                        ref(rel, [*base, "producers", j], b, b in bureaus, "bureau")
            for i, s in enumerate(doc["start"]):
                for key, kind in (("technologies", "technology"), ("doctrines", "doctrine"), ("designs", "design")):
                    for j, x in enumerate(s[key]):
                        ref(rel, ["start", i, key, j], x, is_kind(x, kind), kind)
                for j, x in enumerate(s["flags"]):
                    ref(rel, ["start", i, "flags", j], x, x in flags, "flag")
            for i, r in enumerate(doc["restrictions"]):
                ref(rel, ["restrictions", i, "treaty"], r["treaty"], r["treaty"] in treaties, "treaty")
                if "category" in r["scope"]:
                    ref(rel, ["restrictions", i, "scope", "category"], r["scope"]["category"],
                        r["scope"]["category"] in cats, "category")
                if r["lifted_by"] is not None:
                    ref(rel, ["restrictions", i, "lifted_by"], r["lifted_by"], r["lifted_by"] in flags, "flag")
        self._effect_targets(ref)
        self._slice_encoded()
        for rel, _, doc in self.research:
            for tag, row in doc["ai"]["weights"].items():
                for c in row:
                    ref(rel, ["ai", "weights", tag, c], c, c in cats, "category")
            for tag, row in doc["ai"]["weight_inputs"].items():
                for c in row["bureaus"]:
                    ref(rel, ["ai", "weight_inputs", tag, "bureaus", c], c, c in cats, "category")
        for rel, _, i, b in self.entries["bureau"]:
            if b["active"] and b["home"] is None:
                self._add(rel, "C101", _json_path(["bureaus", i, "home"]), "an active bureau carries its home")
            ref(rel, ["bureaus", i, "primary"], b["primary"], b["primary"] in cats, "category")
            for j, c in enumerate(b["secondary"]):
                ref(rel, ["bureaus", i, "secondary", j], c, c in cats, "category")
        for rel, _, i, v in self.entries["vendor"]:
            for j, c in enumerate(v["fields"]):
                ref(rel, ["vendors", i, "fields", j], c, c in cats, "category")
        self._blame_duplicates([r for r, _, _ in self.research], "C907", "$",
                               lambda others: f"research.json is also defined in {', '.join(others)}: "
                                              f"there is one per content set")

    def _effect_targets(self, ref) -> None:
        """TARGET VALIDITY (D37): R11 is "non-null AND target-valid". An effect naming a stat its class lacks
        resolves, passes a non-null check, never lands, and says nothing. So every modifier's target must exist:
        the class (C106, C201/C301) and a stat on its surface (C402, C401); or an engine facility kind (C405)
        with a pool it outputs (C403), or, for power_supply, a kind that supplies power (C405). The order and
        codes are the game reader's (ContentReader.Research.cs, Unity 93bed30)."""
        classes, stat_keys = self.by_id["class"], set(self.vocab.get("stat_keys", []))
        kinds, pools = set(self.vocab.get("facility_kinds", [])), set(self.vocab.get("pools", []))
        rows = {f["kind"]: f for _, _, _, f in self.entries["facility"]}
        id_re = re.compile(r"^[a-z0-9_]+:[a-z0-9_]+$")
        for rel, _, doc in self.research:
            for i, pr in enumerate(doc["projects"]):
                e = pr.get("effect")
                if not isinstance(e, dict) or "modifiers" not in e:
                    continue
                for j, m in enumerate(e["modifiers"]):
                    at = ["projects", i, "effect", "modifiers", j]
                    if m["target"] == "design_stat":
                        if not id_re.match(m["scope"]):
                            self._add(rel, "C106", _json_path([*at, "scope"]), f'"{m["scope"]}" is not namespace:name')
                            continue
                        if not ref(rel, [*at, "scope"], m["scope"], m["scope"] in classes, "class"):
                            continue
                        if m["stat"] not in stat_keys:
                            self._add(rel, "C402", _json_path([*at, "stat"]), f'"{m["stat"]}" is not an engine stat key')
                        elif m["stat"] not in classes[m["scope"]]["stats"]:
                            self._add(rel, "C401", _json_path([*at, "stat"]), f'"{m["stat"]}" is not on class "{m["scope"]}"')
                        continue
                    if m["scope"] not in kinds:
                        self._add(rel, "C405", _json_path([*at, "scope"]), f'"{m["scope"]}" is not an engine facility kind')
                        continue
                    row = rows.get(m["scope"])
                    if row is None:
                        continue   # C903 reports the missing row
                    if m["target"] == "facility_output":
                        if m["pool"] not in pools:
                            self._add(rel, "C403", _json_path([*at, "pool"]), f'"{m["pool"]}" is not an engine pool')
                        elif m["pool"] not in row["output"]:
                            self._add(rel, "C403", _json_path([*at, "pool"]), f'{m["scope"]} does not output {m["pool"]}')
                    elif row["power_supply_per_tier"] <= 0:
                        self._add(rel, "C405", _json_path([*at, "scope"]), f'{m["scope"]} supplies no power')

    def _slice_encoded(self) -> None:
        """C909, the load-time backstop for R11 (D33, D37): every technology or doctrine in the R11 SUBJECT has an
        effect. The subject, as ruled: the prerequisite closure of every model whose unlock is a design study
        (technology, doctrine and design atoms in prerequisites, plus variant_of, recursively), plus every start
        holding, plus every doctrine with a non-empty exclusive_with. A declared gate_only or no_target IS encoded;
        only null is refused. Each refusal names the subject size, so an empty subject is visible (D32)."""
        projects = self.r_projects
        seen, stack = set(), [m["unlock"] for m in self.by_id["model"].values()
                              if m["unlock"] in projects and projects[m["unlock"]]["kind"] == "design"]
        while stack:
            pid = stack.pop()
            if pid in seen or pid not in projects:
                continue
            seen.add(pid)
            p = projects[pid]
            stack += [a["value"] for c in p["prerequisites"] for a in c if a["kind"] in ("technology", "doctrine", "design")]
            if p.get("variant_of"):
                stack.append(p["variant_of"])
        subject = {pid for pid in seen if projects[pid]["kind"] in ("technology", "doctrine")}
        for _, _, doc in self.research:
            for s in doc["start"]:
                subject |= set(s["technologies"]) | set(s["doctrines"])
        subject |= {pid for pid, p in projects.items() if p["kind"] == "doctrine" and p["exclusive_with"]}
        subject &= set(projects)
        unencoded = sorted(pid for pid in subject if projects[pid]["effect"] is None)
        self.slice_size, self.slice_unencoded = len(subject), len(unencoded)
        for rel, _, doc in self.research:
            for i, p in enumerate(doc["projects"]):
                if p["id"] in unencoded:
                    self._add(rel, "C909", _json_path(["projects", i, "effect"]),
                              f'{len(unencoded)} of {len(subject)} slice rows has no effect: {p["id"]}')

    def _blame_duplicates(self, files: list[str], code: str, path: str, detail):
        """A project-wide file defined more than once (C905, C907). ERROR ATTRIBUTION
        FOLLOWS AUTHORSHIP (D59): the base game is never blamed for a third party's
        file. With a shipped pb/ copy among them, every OTHER copy is blamed and pb/
        is not. With no pb/ copy, there is no right one to single out, so EVERY copy
        is named, ordered by file. Folder order is arbitrary with respect to
        authorship and is not the discriminator."""
        if len(files) < 2:
            return
        shipped = [f for f in files if f.startswith(SHIPPED_NAMESPACE + "/")]
        blamed = [f for f in files if f not in shipped] if shipped else sorted(files)
        for f in sorted(blamed):
            self._add(f, code, path, detail(sorted(x for x in files if x != f)))

    def _facilities_and_settlements(self, pools):
        """facility.json, settlement.json and the singleton rules files (Sprint 12.5 T5)."""
        kinds = list(self.vocab.get("facility_kinds", []))
        stypes = list(self.vocab.get("settlement_types", []))

        # Facility rules: once, in one facility.json across every namespace (C905).
        # Blame follows AUTHORSHIP (D59), not folder order: see _blame_duplicates.
        self._blame_duplicates([r for r, _ in self.facility_headers], "C905", "$.tier",
                               lambda others: f"the facility rules are also defined in {', '.join(others)}")
        header_file = next((r for r, _ in self.facility_headers if r.startswith(SHIPPED_NAMESPACE + "/")),
                           self.facility_headers[0][0] if self.facility_headers else None)
        self.facility_doc = next((d for r, d in self.facility_headers if r == header_file), None)

        # Rows: an engine kind (C405), each deposit produces (C904), pools (C403), once each (C203).
        rows: dict[str, tuple[str, int]] = {}
        for rel, _, i, f in self.entries["facility"]:
            if f["kind"] not in kinds:
                self._add(rel, "C405", _json_path(["facilities", i, "kind"]),
                          f'"{f["kind"]}" is not an engine facility kind')
                continue
            for j, d in enumerate(f["deposits"]):
                if d not in pools:
                    self._add(rel, "C403", _json_path(["facilities", i, "deposits", j]), f'"{d}" is not a resource pool')
            for part in ("cost", "output"):
                for pool in f[part]:
                    if pool not in pools:
                        self._add(rel, "C403", _json_path(["facilities", i, part, pool]), f'"{pool}" is not a resource pool')
            for d in f["deposits"]:
                if d in pools and d not in f["output"]:
                    self._add(rel, "C904", _json_path(["facilities", i, "output"]),
                              f'deposit "{d}" produces nothing: list it in output')
            if f["kind"] in rows:
                self._add(rel, "C203", _json_path(["facilities", i, "kind"]), f'facility kind {f["kind"]} has two rows')
                continue
            rows[f["kind"]] = (rel, i)
        facility_file = header_file or next((r for r, *_ in self.entries["facility"]), None) or "facility.json"
        if header_file is None:
            self._add(facility_file, "C903", "$.tier", "no facility.json defines the facility rules (tier, repair_per_mille)")
        for k in kinds:
            if k not in rows:
                self._add(facility_file, "C903", "$.facilities", f"no facility row for kind {k}")

        # Settlements: a type the map produces (C406), once each (C203), every type present (C906).
        srows: dict[str, tuple[str, int]] = {}
        for rel, _, i, r in self.entries["settlement"]:
            if r["type"] not in stypes:
                self._add(rel, "C406", _json_path(["settlements", i, "type"]),
                          f'"{r["type"]}" is not a settlement type the map produces')
                continue
            if r["type"] in srows:
                self._add(rel, "C203", _json_path(["settlements", i, "type"]), f'settlement type {r["type"]} has two rows')
                continue
            srows[r["type"]] = (rel, i)
        settlement_file = next((r for r, *_ in self.entries["settlement"]), None) or "settlement.json"
        for t in stypes:
            if t not in srows:
                self._add(settlement_file, "C906", "$.settlements", f"no settlement row for type {t}")

        # Singletons: exactly one each (C907 for a second copy, C908 for none).
        for t in SINGLETONS:
            copies = self.singletons[t]
            self._blame_duplicates([r for r, _ in copies], "C907", "$",
                                   lambda others, t=t: f"{t}.json is also defined in {', '.join(others)}: "
                                                       f"there is one per content set")
            if not copies:
                self._add(f"{t}.json", "C908", "$", f"no namespace defines {t}.json, and the game needs one")
        economy = self.singletons["economy"][0][1] if self.singletons["economy"] else None
        if economy:
            for pool in economy["dispersed_yield_per_hex"]:
                if pool not in pools:
                    self._add(self.singletons["economy"][0][0], "C403", f"$.dispersed_yield_per_hex.{pool}",
                              f'"{pool}" is not a resource pool')
            self._prices(economy, pools)

    def _prices(self, economy, pools):
        """economy.json prices_per_thousand: real resource pools only (C403), and every pool something produces (C914)."""
        rel = self.singletons["economy"][0][0]
        for pool in economy["prices_per_thousand"]:
            if pool not in pools or pool in UNPRICED_POOLS:
                self._add(rel, "C403", f"$.prices_per_thousand.{pool}", f'"{pool}" is not a priced resource pool: remove it from prices_per_thousand')
        for pool in economy["industry_point"]["cost"]:
            if pool not in pools:
                self._add(rel, "C403", f"$.industry_point.cost.{pool}", f'"{pool}" is not a resource pool')
        produced = {p for p, v in economy["dispersed_yield_per_hex"].items() if v > 0}
        for *_, f in self.entries["facility"]:
            produced |= {p for p, v in f["output"].items() if v > 0}
        for pool in sorted((produced & pools) - set(economy["prices_per_thousand"])):
            self._add(rel, "C914", f"$.prices_per_thousand.{pool}",
                      f'"{pool}" is produced but has no price: add it to prices_per_thousand, or extraction values it at nothing')

    def _claims(self):
        """claim.json and class.json's claim: engine roles (C408), one civilian and one construction (C913),
        and a class is built by a production claim (C306, C301)."""
        roles = set(self.vocab.get("claim_roles", []))
        count = {"civilian": [], "construction": []}
        for rel, _, i, c in self.entries["claim"]:
            if c["role"] not in roles:
                self._add(rel, "C408", _json_path(["claims", i, "role"]),
                          f'"{c["role"]}" is not an engine claim role: use one of {", ".join(sorted(roles))}')
            elif c["role"] in count:
                count[c["role"]].append(c["id"])
        file = next((r for r, *_ in self.entries["claim"]), "claim.json")
        for role, ids in count.items():
            if len(ids) != 1:
                self._add(file, "C913", "$.claims", f'{len(ids)} claims have role {role} ({", ".join(ids) or "none"}): '
                                                    f"there must be exactly one")
        for rel, _, i, c in self.entries["class"]:
            id_ = c["claim"]
            if id_ not in self.by_id["claim"]:
                self._add(rel, "C301", _json_path(["classes", i, "claim"]), f'"{id_}" is not a defined claim')
            elif self.by_id["claim"][id_]["role"] != "production":
                self._add(rel, "C306", _json_path(["classes", i, "claim"]),
                          f'"{id_}" has role {self.by_id["claim"][id_]["role"]}: a class is built by a production claim')

    def _policies(self):
        """policy.json: levels' ids and ownership (C202, C203, C305), targets (C407, C603, C910, C911), and who
        writes each engine target (C912)."""
        forms = {t["target"]: t["form"] for t in self.vocab.get("policy_targets", [])}
        own: dict[str, str] = {}
        writers: dict[str, list[str]] = {t: [] for t in forms}
        for rel, ns, i, p in self.entries["policy"]:
            ids = {lv["id"] for lv in p["levels"]}
            if p["default_level"] not in ids:
                self._add(rel, "C305", _json_path(["policies", i, "default_level"]),
                          f'"{p["default_level"]}" is not a level of {p["id"]}: use one of {", ".join(sorted(ids))}')
            shapes = []
            for j, lv in enumerate(p["levels"]):
                path = ["policies", i, "levels", j]
                if _ns(lv["id"]) != ns:
                    self._add(rel, "C202", _json_path([*path, "id"]),
                              f'"{lv["id"]}" is defined in the {ns} folder, so its namespace must be {ns}')
                if lv["id"] in own:
                    self._add(rel, "C203", _json_path([*path, "id"]), f'level "{lv["id"]}" is already defined in {own[lv["id"]]}')
                own.setdefault(lv["id"], p["id"])
                seen = set()
                for k, e in enumerate(lv["effects"]):
                    t, v = e["target"], e["value"]
                    where_ = _json_path([*path, "effects", k])
                    if t in seen:
                        self._add(rel, "C910", where_, f'level "{lv["id"]}" writes {t} twice: keep one')
                    seen.add(t)
                    if t not in forms:
                        self._add(rel, "C407", where_, f'"{t}" is not an engine policy target: use one of {", ".join(sorted(forms))}')
                    elif forms[t] == "share" and not 0 <= v <= 1000:
                        self._add(rel, "C603", where_, f"{v} is outside 0 to 1000: {t} is a share, per mille")
                    elif forms[t] == "modifier" and v < -1000:
                        self._add(rel, "C603", where_, f"{v} is below -1000: {t} is a modifier, and would drive its term negative")
                shapes.append(seen)
            if any(s != shapes[0] for s in shapes):
                self._add(rel, "C911", _json_path(["policies", i, "levels"]),
                          f'the levels of {p["id"]} write different targets: give every level the same ones')
            for t in set().union(*shapes) & set(forms):
                writers[t].append(p["id"])
        for t, who in writers.items():
            if not who or (forms[t] == "share" and len(who) > 1):
                file = next((r for r, *_ in self.entries["policy"]), "policy.json")
                self._add(file, "C912", "$.policies",
                          f'{t} has {len(who)} writers ({", ".join(who) or "none"}): '
                          + ("a share has exactly one" if who else "add a policy that writes it"))
        nations = self._population_nations()
        seen_nations = set()
        for rel, i, s in self.policy_starts:
            path = ["start", i]
            if nations is not None and s["nation"] not in nations:
                self._add(rel, "C307", _json_path([*path, "nation"]),
                          f'{s["nation"]} has no row in population.json nations: add one')
            if s["nation"] in seen_nations:
                self._add(rel, "C203", _json_path([*path, "nation"]), f'{s["nation"]} has two start rows')
            seen_nations.add(s["nation"])
            for pid, lid in s["levels"].items():
                p = self.by_id["policy"].get(pid)
                if p is None:
                    self._add(rel, "C301", _json_path([*path, "levels", pid]), f'"{pid}" is not a defined policy')
                elif lid not in {lv["id"] for lv in p["levels"]}:
                    self._add(rel, "C305", _json_path([*path, "levels", pid]), f'"{lid}" is not a level of {pid}')

    def _population_nations(self):
        docs = self.singletons["population"]
        return {n["nation"] for n in docs[0][1]["nations"]} if docs else None

    def _population(self):
        """population.json: one row per nation and province (C203), and a province's nation has a row (C307)."""
        if not self.singletons["population"]:
            return
        rel, doc = self.singletons["population"][0]
        nations: set[str] = set()
        for i, n in enumerate(doc["nations"]):
            if n["nation"] in nations:
                self._add(rel, "C203", _json_path(["nations", i, "nation"]), f'{n["nation"]} has two rows')
            nations.add(n["nation"])
        seen: set[str] = set()
        for i, p in enumerate(doc["provinces"]):
            if p["province"] in seen:
                self._add(rel, "C203", _json_path(["provinces", i, "province"]), f'{p["province"]} has two rows')
            seen.add(p["province"])
            if p["nation"] not in nations:
                self._add(rel, "C307", _json_path(["provinces", i, "nation"]),
                          f'{p["nation"]} has no row in nations: add one, with its growth')

    # -- project invariants (C8xx) ---------------------------------------------------------

    def _project_invariants(self):
        """Rules about OUR shipped content, not about content validity. The game
        loads these cases and reports them (a mod may legitimately do them); this
        gate refuses them for the pb namespace. Declared in rule_codes.json."""
        if self.problems:
            return   # invariants are judged on a content set that is otherwise valid
        self._base_browns_out()
        self._shipped_provinces()
        self._domains_and_claims()
        coll = dict(TYPES)
        for stem, _ in TYPES:
            if stem in UNKEYED_TYPES:
                continue
            for rel, ns, i, e in self.entries[stem]:
                if ns == SHIPPED_NAMESPACE and not str(e.get("name", "")).strip():
                    self._add(rel, "C802", _json_path([coll[stem], i, "name"]),
                              f'shipped entry "{e["id"]}" has no name; mods may omit one, pb: content may not')

    def _shipped_provinces(self):
        """PB-C804: a shipped population row names a province the pipeline's province data defines. Coverage
        the other way (every province on the MAP has a row) is the population converter's refusal: it reads
        the artifact, and this gate does not."""
        docs = [(r, d) for r, d in self.singletons["population"] if r.startswith(SHIPPED_NAMESPACE + "/")]
        if not docs or not PROVINCE_DATA.exists():
            return
        known = {p["province_id"] for p in json.loads(PROVINCE_DATA.read_text(encoding="utf-8"))["provinces"]}
        rel, doc = docs[0]
        for i, p in enumerate(doc["provinces"]):
            if p["province"] not in known:
                self._add(rel, "C804", _json_path(["provinces", i, "province"]),
                          f'"{p["province"]}" is not in {PROVINCE_DATA.name}: correct the id, or add the province there')

    def _domains_and_claims(self):
        """PB-C805 to C807: the research catalogue's design domains against the shipped claims. A claim is reached
        two ways, class -> claim (what the game reads) and domain -> claim (what the catalogue says), and these
        hold the two routes to one answer. Only the shipped set: a mod's research may name its own domains."""
        by_domain: dict[str, str] = {}
        for rel, ns, i, c in self.entries["claim"]:
            if ns != SHIPPED_NAMESPACE:
                continue
            for j, dom in enumerate(c.get("domains", [])):
                path = _json_path(["claims", i, "domains", j])
                if c["role"] != "production":
                    self._add(rel, "C806", path, f'"{dom}" is on a claim of role {c["role"]}: move it to a production claim')
                elif dom in by_domain:
                    self._add(rel, "C806", path, f'"{dom}" is also on {by_domain[dom]}: a domain has one claim')
                by_domain.setdefault(dom, c["id"])
        shipped = [(rel, doc) for rel, ns, doc in self.research if ns == SHIPPED_NAMESPACE]
        if not shipped:
            return
        rel, doc = shipped[0]
        domain_of = {}
        for i, p in enumerate(doc["projects"]):
            if p["kind"] != "design":
                continue
            domain_of[p["id"]] = p["domain"]
            if p["domain"] not in by_domain:
                self._add(rel, "C805", _json_path(["projects", i, "domain"]),
                          f'design domain "{p["domain"]}" is on no claim: add it to a production claim\'s domains')
        wanted: dict[str, set[str]] = {}
        for _, ns, _, m in self.entries["model"]:
            dom = domain_of.get(m.get("unlock"))
            if ns == SHIPPED_NAMESPACE and dom in by_domain:
                wanted.setdefault(m["class"], set()).add(by_domain[dom])
        for crel, _, i, c in self.entries["class"]:   # one refusal per class, where the fix is
            claim = c["claim"]
            for want in sorted(wanted.get(c["id"], set())):
                if claim != want:
                    self._add(crel, "C807", _json_path(["classes", i, "claim"]),
                              f"{c['id']} is built by {claim}, but its generated models' domains map to {want}: "
                              f"correct the class's claim or the claim's domains")

    def _base_browns_out(self):
        """PB-C801: under THIS content set's facility.json, the authored 1930 starting
        base (data/facilities/facilities_1930.csv, every row, by its authored nation)
        must leave no nation with supply below draw. The game loads such a content set
        and reports it; brownout is a legal modelled state. This gate refuses it,
        because it is a claim about OUR shipped base under OUR shipped balance file.
        C#'s synthetic C801 test proves the mechanism; this proves the shipped base.
        Neither substitutes for the other."""
        if self.facility_doc is None or not AUTHORED_BASE.exists():
            return
        rel = next(r for r, d in self.facility_headers if d is self.facility_doc)
        if not rel.startswith(SHIPPED_NAMESPACE + "/"):
            return
        import build_facilities_1930 as bf
        power = bf.PowerModel.from_facility_json(self.facility_doc)
        short = [f"{n} {s}<{d}" for n, (s, d) in bf.nation_power(bf.load_rows(AUTHORED_BASE), power).items() if s < d]
        if short:
            self._add(rel, "C801", "$.tier.step_per_mille",
                      "the authored 1930 base browns out under this power model: " + ", ".join(short))

    # -- derived values (the T2 cross-check) -------------------------------------------

    def resolve(self) -> dict[str, dict]:
        """Every DESIGN (model or variant) resolved to its authored values: the class
        defaults, overlaid by the model, plus the variant's deltas; the trait set
        after add/remove; cost; production rate. Each stat's terms are kept, so a
        variant's delta stays a named term (CONTENT_MODEL section 3: it is never
        pre-summed into the base). Trait modifiers are listed as contributions, not
        applied: how the Base layer combines them is the engine's arithmetic, and
        the snapshot compares terms first."""
        if self.problems:
            raise ValueError("resolve() needs a content set with no problems")
        classes, models = self.by_id["class"], self.by_id["model"]
        traits = self.by_id["trait"]
        out = {}

        def design(id_, cls, model, variant):
            trait_set = list(model["traits"])
            if variant:
                trait_set = [t for t in trait_set if t not in variant["traits_remove"]] + \
                            [t for t in variant["traits_add"] if t not in trait_set]
            stats = {}
            for k, default in sorted(cls["stats"].items()):
                terms = [("class" if k not in model["stats"] else "model", model["stats"].get(k, default))]
                if variant and k in variant["stats"]:
                    terms.append((f"variant:{variant['id']}", variant["stats"][k]))
                mods = [(f"trait:{t}", m["per_mille"]) for t in sorted(trait_set)
                        for m in traits[t]["modifiers"] if m["stat"] == k]
                base = sum(v for _, v in terms)
                value, layers = apply_layers(base, mods)
                stats[k] = {"base": base, "terms": terms, "modifiers": mods,
                            "value": value, "layers": layers}
            cost = dict(model["cost"])
            production = dict(model["production"])
            if variant:
                for p, d in variant["cost"].items():
                    cost[p] = cost.get(p, 0) + d
                for k, d in variant["production"].items():
                    production[k] += d
            return {"class": cls["id"], "model": model["id"], "traits": sorted(trait_set),
                    "stats": stats, "cost": dict(sorted(cost.items())),
                    "production": dict(sorted(production.items()))}

        for mid, m in models.items():
            out[mid] = design(mid, classes[m["class"]], m, None)
        for vid, v in self.by_id["variant"].items():
            m = models[v["model"]]
            out[vid] = design(vid, classes[m["class"]], m, v)
        return dict(sorted(out.items()))


def _aggregate(row: dict, pairs: list[tuple[int, int, str]], baseline: int) -> int:
    """One stat over a division's slots, each (value, count, column). A slot counts once per company it holds."""
    rule = row["rule"]
    if rule == "average_lines_sum_support":
        line = [(v, n) for v, n, col in pairs if col == "line"]
        lines = sum(n for _, n in line)
        mean = sum(v * n for v, n in line) // lines if lines else baseline
        # Support moves the result away from its baseline, so a blank row adds nothing. The subtraction is the one
        # signed step, it divides nothing, and the result is held at 0 (CONTENT_MODEL section 3 point 6).
        return max(0, mean + sum((v - baseline) * n for v, n, col in pairs if col == "support"))
    chosen = [(v, n) for v, n, col in pairs if row["subset"] == "all" or col == row["subset"]]
    if not chosen:
        return baseline
    total, count = sum(v * n for v, n in chosen), sum(n for _, n in chosen)
    if rule == "sum":
        return total
    if rule == "average":
        return total // count
    if rule == "min":
        return min(v for v, _ in chosen)
    if rule == "max":
        return max(v for v, _ in chosen)
    w = row["max_weight_per_mille"]   # max_mean_blend, in one division
    return (max(v for v, _ in chosen) * w * count + total * (1000 - w)) // (1000 * count)


def division_snapshot(figures: dict[str, dict]) -> str:
    """One line per template-stat, sorted by template and in vocabulary order. A table stat has one line per
    biome any of its companies names. C# must write the same text."""
    lines = []
    for tid, stats in figures.items():
        for stat, v in stats.items():
            if isinstance(v, dict):
                lines += [f"{tid}.{stat}.{b} = {n}" for b, n in v.items()]
            else:
                lines.append(f"{tid}.{stat} = {v}")
    return "\n".join(lines) + "\n"


def snapshot(resolved: dict[str, dict]) -> str:
    """The canonical text form of resolve(): one line per design-stat, sorted. It is
    meant to be diffed against the same snapshot written by the C# reader."""
    lines = []
    for did, d in resolved.items():
        lines.append(f"{did} class={d['class']} model={d['model']} traits={','.join(d['traits']) or '-'} "
                     f"cost={','.join(f'{p}:{n}' for p, n in d['cost'].items()) or '-'} "
                     f"production={','.join(f'{k}:{n}' for k, n in d['production'].items())}")
        for k, s in d["stats"].items():
            terms = " ".join(f"{src}{v:+d}" if i else f"{src}={v}" for i, (src, v) in enumerate(s["terms"]))
            mods = " ".join(f"{src}{pm:+d}pm" for src, pm in s["modifiers"])
            lines.append(f"  {did}.{k} = {s['base']} [{terms}]" + (f" mods[{mods}]" if mods else ""))
    return "\n".join(lines) + "\n"


def applied_snapshot(resolved: dict[str, dict]) -> str:
    """Applied values: each design-stat's final value and every layer it passed
    through, with each layer's per-mille and its sources (a clamp shows as its own
    source). A separate file from snapshot(), which C# already matches byte for
    byte, so adding values does not move that agreement."""
    lines = []
    for did, d in resolved.items():
        for k, s in d["stats"].items():
            parts = []
            for L in s["layers"]:
                src = ",".join(f"{n}{v:+d}" for n, v in L["sources"])
                parts.append(f"{L['layer']}={L['per_mille']}" + (f"({src})" if src else ""))
            lines.append(f"{did}.{k} = {s['value']} base={s['base']} layers[{' '.join(parts)}]")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    cs = ContentSet(Path(argv[1]))
    for p in cs.problems:
        print(p)
    if cs.problems:
        print(f"REFUSED: {len(cs.problems)} problem(s) in {cs.root}")
        return 1
    if "--division" in argv:
        sys.stdout.write(division_snapshot(cs.division_figures()))
    elif "--applied" in argv:
        sys.stdout.write(applied_snapshot(cs.resolve()))
    elif "--resolve" in argv:
        sys.stdout.write(snapshot(cs.resolve()))
    else:
        n = {s: len(cs.entries[s]) for s, _ in TYPES}
        print(f"OK: {cs.root} — " + ", ".join(f"{v} {k}" for k, v in n.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
