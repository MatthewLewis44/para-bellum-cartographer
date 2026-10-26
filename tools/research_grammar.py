"""Reference parser for the research prerequisite grammar (Sprint 13, AD-M37 section 5).

    from research_grammar import parse, resolve, render, Registry, GrammarError

The grammar is the research database's own (Grammar sheet, v1), adopted as
authored:

    expression := clause ( "," clause )*        ALL clauses must hold (AND)
    clause     := alternative ( "|" alternative )*   ANY alternative holds (OR)

Pipe binds tighter than comma, so an expression is in conjunctive normal form
and needs no parentheses. Parentheses are NOT operators: commas and pipes
inside them are part of a name (`licence:Baz Works (Qux, state)`). An EMPTY
cell means no prerequisite and parses to the empty expression, which always
holds; it is not the same as an unsatisfiable one.

Three contexts, because the same grammar carries different tokens:

    gate         Prerequisites and Cost reducers. A bare token is a technology
                 or doctrine id; design: flag: fielded: xp: focus: licence:
                 bureau: are allowed.
    acquisition  The Design bureau column. A bare token is a bureau of the
                 design's own nation; import: licence: seized: captured: are
                 allowed. Comma = joint design, pipe = competing bids.
    list         A plain comma list of names (Producers). No pipes, no prefixes.

IDS ARE OPAQUE (AD-M37 section 4). Nothing here reads an id's prefix, shape or
digits; a bare id is refused only for characters that would make it ambiguous
(whitespace, parentheses, a colon). A bare `QQ-10` is an id, `design:ZX-3` is a
design name of exactly the same shape, and only the token's position in the
grammar says which.

Two layers, kept apart like the content checker's:
  parse()    SYNTAX (codes G1xx). Raises the first GrammarError it meets.
  resolve()  RESOLUTION against the registries and rosters (codes G2xx, rule
             R1). Returns every unresolved token, so one cell reports all of
             its problems.

The refusal codes are data: content/research/grammar_codes.json. Tests assert
the CODE, never the prose.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODES_FILE = ROOT / "content" / "research" / "grammar_codes.json"
REGISTER = json.loads(CODES_FILE.read_text(encoding="utf-8"))["codes"]
RULES = {c["code"]: c["rule"] for c in REGISTER}

GATE, ACQUISITION, LIST = "gate", "acquisition", "list"
CONTEXT_PREFIXES = {
    GATE: frozenset({"design", "flag", "fielded", "xp", "focus", "licence", "bureau"}),
    ACQUISITION: frozenset({"import", "licence", "seized", "captured"}),
    LIST: frozenset(),
}
PREFIXES = frozenset().union(*CONTEXT_PREFIXES.values())

_PREFIX = re.compile(r"([A-Za-z][A-Za-z0-9_]*):")
_XP = re.compile(r"([a-z]+):([1-9][0-9]*)")
_ID_FORBIDDEN = re.compile(r"[\s():]")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class GrammarError(ValueError):
    def __init__(self, code: str, token: str, detail: str):
        assert code in RULES, f"{code} is not in {CODES_FILE.name}"
        self.code, self.token, self.detail = code, token, detail
        super().__init__(f"PB-{code} {RULES[code]}: {token!r}: {detail}")


@dataclass(frozen=True)
class Atom:
    """One alternative. kind is "id" (bare, gate), "name" (bare, acquisition or
    list) or the prefix word. value is the text after the prefix, verbatim."""
    kind: str
    value: str

    def __str__(self) -> str:
        return self.value if self.kind in ("id", "name") else f"{self.kind}:{self.value}"


Clause = tuple[Atom, ...]
Expression = tuple[Clause, ...]   # () is "no prerequisite", which always holds


def _split_top(text: str, sep: str) -> list[str]:
    """Split on sep outside parentheses. Balance is checked before this runs."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _check_balance(cell: str) -> None:
    depth = 0
    for ch in cell:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise GrammarError("G105", cell, "')' closes a parenthesis that was never opened")
    if depth:
        raise GrammarError("G105", cell, f"{depth} parenthesis left open")


def _atom(alt: str, context: str) -> Atom:
    m = _PREFIX.match(alt)
    if m:
        prefix, arg = m.group(1), alt[m.end():]
        if prefix not in PREFIXES:
            raise GrammarError("G106", alt, f"'{prefix}:' is not a grammar prefix "
                                            f"(known: {', '.join(sorted(PREFIXES))})")
        if prefix not in CONTEXT_PREFIXES[context]:
            allowed = ", ".join(sorted(CONTEXT_PREFIXES[context])) or "none"
            raise GrammarError("G107", alt, f"'{prefix}:' is not allowed in a {context} cell "
                                            f"(allowed: {allowed})")
        if arg == "":
            raise GrammarError("G108", alt, f"'{prefix}:' names nothing")
        if arg[0].isspace():
            raise GrammarError("G109", alt, "whitespace after the colon; names match exactly")
        if prefix == "xp" and not _XP.fullmatch(arg):
            raise GrammarError("G111", alt, "expected xp:<branch>:<positive integer>")
        return Atom(prefix, arg)
    if context == GATE:
        if _ID_FORBIDDEN.search(alt):
            raise GrammarError("G110", alt, "a bare id may not contain whitespace, '(' , ')' or ':'")
        return Atom("id", alt)
    return Atom("name", alt)


def parse(cell: str | None, context: str) -> Expression:
    """Parse one cell. None and "" are the empty expression (no prerequisite)."""
    if context not in CONTEXT_PREFIXES:
        raise ValueError(f"unknown context {context!r}")
    if cell is None or cell == "":
        return ()
    if not isinstance(cell, str):
        raise TypeError(f"a grammar cell is text, got {type(cell).__name__}: {cell!r}")
    if cell.strip() == "":
        raise GrammarError("G101", cell, "whitespace only; leave the cell empty for no prerequisite")
    if _CONTROL.search(cell):
        raise GrammarError("G102", cell, "control character (an in-cell line break or tab?)")
    _check_balance(cell)
    clauses = []
    for raw_clause in _split_top(cell, ","):
        if raw_clause.strip() == "":
            raise GrammarError("G103", cell, "a comma with nothing on one side")
        alts = _split_top(raw_clause, "|")
        if context == LIST and len(alts) > 1:
            raise GrammarError("G112", raw_clause.strip(), "a plain list has no alternatives")
        atoms = []
        for raw_alt in alts:
            alt = raw_alt.strip()
            if alt == "":
                raise GrammarError("G104", raw_clause.strip(), "a pipe with nothing on one side")
            atoms.append(_atom(alt, context))
        clauses.append(tuple(atoms))
    return tuple(clauses)


def render(expr: Expression) -> str:
    """The canonical spelling: ", " between clauses, " | " between alternatives.
    render(parse(cell)) == cell for every cell already in canonical form."""
    return ", ".join(" | ".join(str(a) for a in clause) for clause in expr)


@dataclass(frozen=True)
class Registry:
    """The closed vocabulary a parsed expression resolves against."""
    ids: frozenset[str] = frozenset()          # technology and doctrine ids
    designs: frozenset[str] = frozenset()      # exact design names
    flags: frozenset[str] = frozenset()
    fielded: frozenset[str] = frozenset()
    focus: frozenset[str] = frozenset()
    xp: frozenset[tuple[str, int]] = frozenset()   # (branch, tier)
    bureaus: Mapping[str, frozenset[str]] = field(default_factory=dict)   # nation -> names
    vendors: frozenset[str] = frozenset()
    nations: frozenset[str] = frozenset()

    def any_bureau(self, name: str) -> bool:
        return any(name in names for names in self.bureaus.values())


def resolve(expr: Expression, context: str, registry: Registry,
            nation: str | None = None) -> list[GrammarError]:
    """Every token in expr that the registry does not declare (rule R1).
    nation is the owning row's nation; bare names in acquisition and list cells
    resolve against that nation's roster only."""
    problems: list[GrammarError] = []

    def miss(code: str, atom: Atom, detail: str) -> None:
        problems.append(GrammarError(code, str(atom), detail))

    for clause in expr:
        for a in clause:
            k, v = a.kind, a.value
            if k == "id":
                if v not in registry.ids:
                    miss("G201", a, "no technology or doctrine has this id")
            elif k == "design":
                if v not in registry.designs:
                    miss("G202", a, "no design has exactly this name")
            elif k == "flag":
                if v not in registry.flags:
                    miss("G203", a, "not declared in the flag registry")
            elif k == "fielded":
                if v not in registry.fielded:
                    miss("G204", a, "not declared in the fielded registry")
            elif k == "focus":
                if v not in registry.focus:
                    miss("G205", a, "not declared in the focus registry")
            elif k == "xp":
                branch, tier = v.split(":")
                if (branch, int(tier)) not in registry.xp:
                    miss("G206", a, "branch or tier outside the experience registry")
            elif k == "bureau":
                if not registry.any_bureau(v):
                    miss("G207", a, "no bureau of any nation has this name")
            elif k in ("licence", "import"):
                if not registry.any_bureau(v) and v not in registry.vendors:
                    miss("G208", a, "neither a bureau of any nation nor an off-map vendor")
            elif k == "seized":
                if not registry.any_bureau(v):
                    miss("G209", a, "no bureau of any nation has this name")
            elif k == "captured":
                if v not in registry.nations:
                    miss("G210", a, "not a nation in the data")
            elif k == "name":
                if nation is None:
                    raise ValueError(f"resolving a {context} cell needs the row's nation")
                if v not in registry.bureaus.get(nation, frozenset()):
                    miss("G207", a, f"no bureau of {nation} has this name")
            else:   # parse() cannot produce this; a new prefix needs a branch here
                raise AssertionError(f"unhandled atom kind {k!r}")
    return problems
