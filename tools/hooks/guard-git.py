#!/usr/bin/env python
"""PreToolUse guard: refuse the two git commands this repository's rules forbid.

Wired from .claude/settings.json. Reads the hook payload on stdin; exit code 2 blocks the tool call
and shows the message on stderr to Claude.

Blocked here:
  * git commit --amend — amending a commit that has been pushed rewrites public history, and this
    repository is public. Make a new commit.
  * git push --force / -f / --force-with-lease — a force push to a public remote discards commits
    other clones may already hold.

Everything else passes through untouched. This is a backstop for the rules in CLAUDE.md, not a
substitute for reading them.
"""
import json
import re
import sys

AMEND = re.compile(r"\bgit\b(?:(?!\bgit\b).)*\bcommit\b(?:(?!\bgit\b).)*--amend", re.S)
FORCE_PUSH = re.compile(r"\bgit\b(?:(?!\bgit\b).)*\bpush\b(?:(?!\bgit\b).)*"
                        r"(--force\b|--force-with-lease\b|\s-f\b)", re.S)

try:
    payload = json.load(sys.stdin)
except Exception:
    sys.exit(0)  # never block on a payload we cannot read

command = str((payload.get("tool_input") or {}).get("command") or "")
if not command:
    sys.exit(0)

if AMEND.search(command):
    sys.stderr.write(
        "Blocked: git commit --amend is forbidden in this repository.\n"
        "This repository is public; amending rewrites history other clones may hold. Make a NEW "
        "commit instead. If history genuinely must be rewritten, stop and ask Matthew.\n")
    sys.exit(2)

if FORCE_PUSH.search(command):
    sys.stderr.write(
        "Blocked: force-pushing is forbidden here.\n"
        "This repository is public and its history is shared. Push normally, or stop and ask "
        "Matthew.\n")
    sys.exit(2)

sys.exit(0)
