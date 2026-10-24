#!/usr/bin/env python3
"""PreToolUse hook: only bin/promote.py may write a case's scene_final.blend.

Blocks (exit 2, reason on stderr):
- Write/Edit/MultiEdit/NotebookEdit whose target file is named scene_final.blend;
- Codex apply_patch add/update/delete/move targets and added Python save code;
- shell commands whose write target is scene_final.blend: the destination of
  cp/rsync/install/ln, either side of mv, any argument of rm/unlink/touch/truncate/tee,
  dd of=, or a > / >> redirect;
- Python (in a Bash command or in written file content) that saves or copies to a path
  naming scene_final.blend, or calls save_mainfile() without a new path after opening it.
A command segment that runs the plugin's promote.py is allowed; the rest of the command is still checked.

Reading the final scene (hashing, listing, copying it elsewhere for a render derivative)
is allowed. This is a guardrail against mistakes, not a security boundary: a path held in
a variable is not seen, and promote re-measures every row anyway. Standard library only.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePath

FINAL = "scene_final.blend"
FILE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
DEST_LAST = {"cp", "rsync", "install", "ln"}
ANY_ARG = {"mv", "rm", "unlink", "touch", "truncate", "tee"}
SEPARATORS = {"&&", "||", ";", "|", "&", ";;", "|&", "(", ")"}
REDIRECT = re.compile(r">>?\s*['\"]?[^\s'\";|&]*" + re.escape(FINAL))
# Python calls whose destination is their last argument (or dst=/filepath=).
PY_DEST_CALLS = re.compile(r"\b(copyfile|copy2?|copytree|move|rename|replace|link|symlink|save_as_mainfile|"
                           r"save_mainfile)\s*\(")
# Python calls that write the path they are called on: <path>.write_bytes(...).
PY_RECEIVER_CALLS = re.compile(r"\.(write_bytes|write_text|symlink_to|hardlink_to|touch|unlink)\s*\(")
PY_RESAVE = re.compile(r"save_mainfile\s*\(\s*(\)|[^)]*\bcopy\s*=\s*False)")


def deny(reason: str) -> None:
    print(f"Blocked by worldagent: {reason} Only `bin/promote.py` (or /worldagent:promote) writes "
          f"{FINAL}, after re-measuring every requirement row and passing the gate.", file=sys.stderr)
    sys.exit(2)


def names_final(arg: str) -> bool:
    return PurePath(arg.split("=", 1)[-1]).name == FINAL


def call_args(text: str, open_paren: int) -> list[str]:
    """Top-level comma-separated arguments of the call whose '(' is at open_paren."""
    depth, args, current = 0, [], []
    for ch in text[open_paren:]:
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                break
        if ch == "," and depth == 1:
            args.append("".join(current))
            current = []
        else:
            current.append(ch)
    args.append("".join(current))
    return [a.strip() for a in args if a.strip()]


def python_writes_final(text: str) -> bool:
    for m in PY_DEST_CALLS.finditer(text):
        args = call_args(text, m.end() - 1)
        keyed = [a for a in args if re.match(r"(dst|filepath)\s*=", a)]
        positional = [a for a in args if not re.match(r"\w+\s*=", a)]
        dest = keyed[-1] if keyed else (positional[-1] if positional else "")
        if FINAL in dest:
            return True
    for m in PY_RECEIVER_CALLS.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        if FINAL in text[line_start:m.start()]:
            return True
    return FINAL in text and bool(PY_RESAVE.search(text))


def segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        tokens = command.split()
    out, segment = [], []
    for token in tokens:
        if token in SEPARATORS:
            if segment:
                out.append(segment)
            segment = []
        else:
            segment.append(token)
    if segment:
        out.append(segment)
    return out


def runs_promote(segment: list[str]) -> bool:
    """`[python3] .../promote.py ...`: the plugin's promote, the only allowed writer."""
    args = segment[1:] if PurePath(segment[0]).name.startswith("python") else segment
    return bool(args) and PurePath(args[0]).name == "promote.py"


def shell_writes_final(command: str) -> bool:
    if REDIRECT.search(command):
        return True
    for segment in segments(command):
        if runs_promote(segment):
            continue
        name = PurePath(segment[0]).name
        args = [a for a in segment[1:] if not a.startswith("-")]
        if name in DEST_LAST and args and names_final(args[-1]):
            return True
        if name in ANY_ARG and any(names_final(a) for a in args):
            return True
        if name == "dd" and any(a.startswith("of=") and names_final(a) for a in args):
            return True
        if python_writes_final(" ".join(segment)):
            return True
    return False


def text_of(tool_input: dict) -> str:
    parts = [tool_input.get("content"), tool_input.get("new_string"), tool_input.get("new_source")]
    parts += [e.get("new_string") for e in tool_input.get("edits") or [] if isinstance(e, dict)]
    return "\n".join(p for p in parts if isinstance(p, str))


def file_input(tool_input: dict) -> tuple[list[str], str]:
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return [target] if isinstance(target, str) else [], text_of(tool_input)


def patch_input(tool_input: dict) -> list[tuple[list[str], str]]:
    """Extract destinations and added code; removed code is not a new write."""
    patch = tool_input.get("command")
    if not isinstance(patch, str):
        return []
    entries, targets, added = [], [], []
    for line in patch.splitlines():
        match = re.match(r"^\*\*\* (Add File|Update File|Delete File|Move to): (.+)$", line)
        if match:
            if match.group(1) != "Move to" and targets:
                entries.append((targets, "\n".join(added)))
                targets, added = [], []
            targets.append(match.group(2).strip())
        elif targets and line.startswith("+"):
            added.append(line[1:])
    if targets:
        entries.append((targets, "\n".join(added)))
    return entries


def write_reason(tool: str, tool_input: dict) -> str | None:
    if tool in FILE_TOOLS or tool == "apply_patch":
        entries = patch_input(tool_input) if tool == "apply_patch" else [file_input(tool_input)]
        for targets, text in entries:
            for target in targets:
                if PurePath(target).name == FINAL:
                    return f"direct write to {target}."
            if python_writes_final(text):
                if tool == "apply_patch":
                    return f"written code saves or copies to {FINAL}."
                return f"{targets[0]} contains code that writes {FINAL}."
    elif tool == "Bash":
        command = tool_input.get("command")
        if isinstance(command, str) and FINAL in command and shell_writes_final(command):
            return "this command would write the final scene."
    return None


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        sys.exit(0)
    if not isinstance(event, dict) or not isinstance(event.get("tool_input"), dict):
        sys.exit(0)
    reason = write_reason(event.get("tool_name"), event["tool_input"])
    if reason:
        deny(reason)
    sys.exit(0)


if __name__ == "__main__":
    main()
