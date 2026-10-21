"""Independent second implementation of the game's content contract (Sprint 12.5).

Reads a Para Bellum content directory, the same files the game's C# reader
loads, and refuses anything the contract forbids, with a STABLE CODE. The game
refuses the same files with the same codes. When the two implementations
disagree about a file, one of them is wrong. That disagreement is the point:
two independent readers agreeing is the strongest cheap evidence available.

    uv run python tools/content_check.py <Content-dir>            # validate
    uv run python tools/content_check.py <Content-dir> --resolve  # derived values (terms)
    uv run python tools/content_check.py <Content-dir> --applied  # applied values (layers)

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
         ("facility", "facilities"), ("settlement", "settlements"))
# Singleton rules files: flat objects, exactly ONE across every namespace (C907, C908).
SINGLETONS = ("rates", "combat", "economy")
# Types whose rows are keyed on an engine name (unit type, facility kind, settlement type), not an id.
UNKEYED_TYPES = ("template", "facility", "settlement")
CONTENT_FILES = tuple(f"{t}.json" for t, _ in TYPES) + tuple(f"{t}.json" for t in SINGLETONS)
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
# writers in content (traits). The other four are present and neutral at 1000.
LAYERS = ("Base", "Facility", "Research", "Policy", "CaptureState")
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


def _schema_problems(rel: str, stem: str, doc) -> list[Problem]:
    out = []
    for err in sorted(_schema(stem).iter_errors(doc), key=lambda e: list(e.absolute_path)):
        # A failing `required` names its missing key in the message, not the path.
        path = _json_path(err.absolute_path)
        if err.validator == "required":
            # One error per missing key; name THAT key's path, as the C# reader does.
            m = re.match(r"'(.+?)' is a required property", err.message)
            path += f".{m.group(1)}" if m else ""
        out.append(Problem(rel, _schema_code(err), path, err.message))
    return out


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
        self.namespaces: set[str] = set()
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
        errs = _schema_problems(rel, stem, doc)
        self.problems += errs
        return rel, (None if errs else doc)

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
            for stem, key in TYPES:
                f = ns_dir / f"{stem}.json"
                if not f.exists():
                    continue
                rel, doc = self._read(f, stem)
                if doc is None:
                    continue
                if stem == "facility":
                    self.facility_headers.append((rel, doc))
                for i, e in enumerate(doc[key]):
                    self.entries[stem].append((rel, ns, i, e))
            for stem in SINGLETONS:
                f = ns_dir / f"{stem}.json"
                if f.exists():
                    rel, doc = self._read(f, stem)
                    if doc is not None:
                        self.singletons[stem].append((rel, doc))

    # -- the cross-file layer ----------------------------------------------------------

    def _add(self, rel, code, path, detail):
        self.problems.append(Problem(rel, code, path, detail))

    def _cross_file(self):
        stat_keys = set(self.vocab.get("stat_keys", []))
        pools = set(self.vocab.get("pools", []))
        unit_types = list(self.vocab.get("unit_types", []))
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

        # Templates: one row per engine unit type; every class has a design.
        seen_ut: dict[str, tuple[str, int]] = {}
        design_classes = {m["class"] for m in models.values()}
        any_template_file = None
        for rel, _, i, t in self.entries["template"]:
            any_template_file = any_template_file or rel
            ut = t["unit_type"]
            if ut not in unit_types:
                self._add(rel, "C404", where("template", i, "unit_type"), f'"{ut}" is not an engine unit type')
            elif ut in seen_ut:
                self._add(rel, "C203", where("template", i, "unit_type"),
                          f'unit type "{ut}" already has a template at {seen_ut[ut][0]} templates[{seen_ut[ut][1]}]')
            else:
                seen_ut[ut] = (rel, i)
            for p in t["cost"]:
                if p not in pools:
                    self._add(rel, "C403", where("template", i, "cost", p), f'"{p}" is not a resource pool')
            for j, e in enumerate(t["equipment"]):
                if ref(rel, where("template", i, "equipment", j, "class"), e["class"], "class") \
                        and e["class"] not in design_classes:
                    self._add(rel, "C902", where("template", i, "equipment", j, "class"),
                              f'no model or variant belongs to class "{e["class"]}"')
        for ut in unit_types:
            if ut not in seen_ut:
                self._add(any_template_file or "template.json", "C901", "$.templates",
                          f'no template for unit type "{ut}"')
        self._facilities_and_settlements(pools)

    def _facilities_and_settlements(self, pools):
        """facility.json, settlement.json and the singleton rules files (Sprint 12.5 T5)."""
        kinds = list(self.vocab.get("facility_kinds", []))
        stypes = list(self.vocab.get("settlement_types", []))

        # Facility rules: once, in one facility.json across every namespace (C905).
        header_file = None
        for rel, _ in self.facility_headers:
            if header_file is None:
                header_file = rel
            else:
                self._add(rel, "C905", "$.tier", f"the facility rules are already defined in {header_file}")
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
            for rel, _ in copies[1:]:
                self._add(rel, "C907", "$", f"{t}.json is already defined in {copies[0][0]}: there is one per content set")
            if not copies:
                self._add(f"{t}.json", "C908", "$", f"no namespace defines {t}.json, and the game needs one")
        economy = self.singletons["economy"][0][1] if self.singletons["economy"] else None
        if economy:
            for pool in economy["dispersed_yield_per_hex"]:
                if pool not in pools:
                    self._add(self.singletons["economy"][0][0], "C403", f"$.dispersed_yield_per_hex.{pool}",
                              f'"{pool}" is not a resource pool')

    # -- project invariants (C8xx) ---------------------------------------------------------

    def _project_invariants(self):
        """Rules about OUR shipped content, not about content validity. The game
        loads these cases and reports them (a mod may legitimately do them); this
        gate refuses them for the pb namespace. Declared in rule_codes.json."""
        if self.problems:
            return   # invariants are judged on a content set that is otherwise valid
        self._base_browns_out()
        coll = dict(TYPES)
        for stem, _ in TYPES:
            if stem in UNKEYED_TYPES:
                continue
            for rel, ns, i, e in self.entries[stem]:
                if ns == SHIPPED_NAMESPACE and not str(e.get("name", "")).strip():
                    self._add(rel, "C802", _json_path([coll[stem], i, "name"]),
                              f'shipped entry "{e["id"]}" has no name; mods may omit one, pb: content may not')

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
    if "--applied" in argv:
        sys.stdout.write(applied_snapshot(cs.resolve()))
    elif "--resolve" in argv:
        sys.stdout.write(snapshot(cs.resolve()))
    else:
        n = {s: len(cs.entries[s]) for s, _ in TYPES}
        print(f"OK: {cs.root} — " + ", ".join(f"{v} {k}" for k, v in n.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
