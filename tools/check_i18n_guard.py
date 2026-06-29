#!/usr/bin/env python3
"""Diff-scoped i18n hard gate for frontend files.

Checks:
1) Hardcoded user-facing strings in changed gui/src + frontend/src .vue/.js/.ts files.
2) Locale key parity between de.json and en.json when locale files are changed.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

TARGET_FILE_RE = re.compile(r"^(gui/src|frontend/src)/.+\.(vue|js|ts)$")
LOCALE_FILE_RE = re.compile(r"^(gui|frontend)/src/locales/(de|en)\.json$")
TEMPLATE_TAG_RE = re.compile(r"</?template\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(
    r"(?<![:\w-])\b(label|title|placeholder|alt|aria-label|helper-text|tooltip|caption|headline|confirm-text|cancel-text|no-data-text|loading-text)\s*=\s*(['\"])(.*?)\2"
)
BOUND_ATTR_RE = re.compile(
    r"(?:^|\s)(?::|v-bind:)(label|title|placeholder|alt|aria-label|helper-text|tooltip|caption|headline|confirm-text|cancel-text|no-data-text|loading-text)\s*=\s*(['\"])(.*?)\2",
    re.DOTALL,
)
HTML_TAG_RE = re.compile(r"</?[\w:-]+(?:\s+[^<>]*)?/?>")
TEXT_NODE_RE = re.compile(r">([^<{][^<]*)<")
RAW_TRANSLATION_TEXT_RE = re.compile(r"(?:\$t|(?<![\w$])t)\s*\(")
UI_CALL_RE = re.compile(r"\b(?:alert|confirm|prompt|toast(?:\.[A-Za-z_][A-Za-z0-9_]*)?|notify(?:\.[A-Za-z_][A-Za-z0-9_]*)?)\s*\(\s*(['\"`])(.+?)\1")
ERROR_RE = re.compile(r"\b(?:throw\s+new\s+Error|new\s+Error)\s*\(\s*(['\"`])(.+?)\1")
ASSIGN_RE = re.compile(r"\b(?:errorMessage|warningMessage|successMessage|message|label|title|placeholder|tooltip|caption)\s*[:=]\s*(['\"`])(.*?)\1")
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass
class Violation:
    path: str
    line: int
    kind: str
    snippet: str


class Allowlist:
    def __init__(self, path: Path):
        self.literal: set[str] = set()
        self.patterns: list[re.Pattern[str]] = []
        if not path.exists():
            return
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("re:"):
                self.patterns.append(re.compile(line[3:]))
            else:
                self.literal.add(line)

    def contains(self, text: str) -> bool:
        value = " ".join(text.split())
        if value in self.literal:
            return True
        return any(p.search(value) for p in self.patterns)


def run_git_diff(repo_root: Path, base: str | None, head: str | None) -> list[str]:
    candidates: list[list[str]] = []
    if base and head:
        candidates.append(["git", "diff", "--name-only", f"{base}...{head}"])
        candidates.append(["git", "diff", "--name-only", base, head])
    candidates.extend(
        [
            ["git", "diff", "--name-only", "origin/main...HEAD"],
            ["git", "diff", "--name-only", "main...HEAD"],
            ["git", "diff", "--name-only", "HEAD~1...HEAD"],
        ]
    )

    for cmd in candidates:
        proc = subprocess.run(cmd, cwd=repo_root, text=True, capture_output=True)
        if proc.returncode == 0:
            files = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
            if files:
                return sorted(set(files))
            return []

    tried = " | ".join(" ".join(c) for c in candidates)
    raise RuntimeError(f"Could not determine changed files via git diff ({tried})")


def flatten_keys(data: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in data.items():
        joined = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            keys.update(flatten_keys(value, joined))
        else:
            keys.add(joined)
    return keys


def has_letters(text: str) -> bool:
    return re.search(r"[A-Za-zÄÖÜäöüß]", text) is not None


def normalize_text(text: str) -> str:
    return " ".join(text.strip().split())


def is_technical_token(text: str) -> bool:
    compact = text.strip()
    if compact.startswith(("http://", "https://", "/", "./", "../", "#")):
        return True
    if compact.startswith("[") and compact.endswith("]"):
        return True
    if compact.upper() == compact and re.fullmatch(r"[A-Z0-9_./:+-]+", compact):
        return True
    if re.fullmatch(r"[A-Za-z0-9_./:+-]+", compact):
        return True
    return False


def should_flag(candidate: str, allowlist: Allowlist, *, allow_technical_tokens: bool = True) -> bool:
    text = normalize_text(candidate)
    if len(text) < 2:
        return False
    if not has_letters(text):
        return False
    if "{{" in text or "}}" in text:
        return False
    if text.startswith(("$t(", "t(")):
        return False
    if not has_letters(strip_translated_template_interpolations(text)):
        return False
    if not has_letters(strip_template_interpolations(text)):
        return False
    if allow_technical_tokens and is_technical_token(text):
        return False
    if allowlist.contains(text):
        return False
    return True


def strip_translated_template_interpolations(text: str) -> str:
    result: list[str] = []
    idx = 0
    while idx < len(text):
        if text.startswith("${", idx):
            end = find_balanced_end(text, idx + 1, "{", "}")
            if end is not None:
                expression = text[idx + 2 : end]
                if re.search(r"(?:\$t|(?<![\w$])t)\s*\(", expression):
                    idx = end + 1
                    continue

        result.append(text[idx])
        idx += 1

    return "".join(result)


def strip_template_interpolations(text: str) -> str:
    result: list[str] = []
    idx = 0
    while idx < len(text):
        if text.startswith("${", idx):
            end = find_balanced_end(text, idx + 1, "{", "}")
            if end is not None:
                idx = end + 1
                continue

        result.append(text[idx])
        idx += 1

    return "".join(result)


def find_balanced_end(text: str, open_idx: int, open_char: str, close_char: str) -> int | None:
    depth = 0
    quote: str | None = None
    escaped = False

    for idx in range(open_idx, len(text)):
        char = text[idx]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == open_char:
            depth += 1
        elif char == close_char:
            depth -= 1
            if depth == 0:
                return idx

    return None


def add_violations_from_matches(
    *,
    path: str,
    line: int,
    kind: str,
    matches: Iterable[re.Match[str]],
    candidate_group: int,
    allowlist: Allowlist,
    sink: list[Violation],
) -> None:
    for match in matches:
        candidate = match.group(candidate_group)
        if should_flag(candidate, allowlist):
            sink.append(Violation(path=path, line=line, kind=kind, snippet=normalize_text(candidate)))
            continue

        seen: set[str] = set()
        for start, literal in iter_string_literals(candidate):
            snippet = normalize_text(literal)
            if snippet in seen:
                continue
            if is_translation_key_argument(candidate, start):
                continue
            if should_flag(literal, allowlist, allow_technical_tokens=False):
                sink.append(Violation(path=path, line=line, kind=kind, snippet=snippet))
                seen.add(snippet)


def is_translation_key_argument(expression: str, start: int) -> bool:
    call_start = find_enclosing_translation_call(expression, start)
    if call_start is None:
        return False
    closing_paren = find_balanced_end(expression, call_start, "(", ")")
    if closing_paren is None:
        return False
    first_arg_end = find_first_argument_end(expression, call_start + 1, closing_paren)
    return start < first_arg_end


def find_first_argument_end(expression: str, start: int, end: int) -> int:
    depth = 0
    quote: str | None = None
    escaped = False

    for idx in range(start, end):
        char = expression[idx]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            return idx

    return end


def find_enclosing_translation_call(expression: str, start: int) -> int | None:
    for match in re.finditer(r"(?:\$t|(?<![\w$])t)\s*\(", expression):
        open_paren = expression.rfind("(", match.start(), match.end())
        closing_paren = find_balanced_end(expression, open_paren, "(", ")")
        if closing_paren is not None and open_paren < start < closing_paren:
            return open_paren
    return None


def is_bound_literal_technical(text: str) -> bool:
    compact = text.strip()
    return compact.startswith(("http://", "https://", "/", "./", "../", "#"))


def iter_template_literal_chunks(expression: str, idx: int) -> tuple[int, list[tuple[int, str]]]:
    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    current_start = idx + 1
    escaped = False
    idx += 1

    while idx < len(expression):
        char = expression[idx]
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "`":
            chunks.append((current_start, "".join(current)))
            return idx, chunks
        elif char == "$" and idx + 1 < len(expression) and expression[idx + 1] == "{":
            chunks.append((current_start, "".join(current)))
            current = []
            idx += 2
            inner_start = idx
            depth = 1
            inner_quote: str | None = None
            inner_escaped = False
            while idx < len(expression) and depth > 0:
                inner = expression[idx]
                if inner_escaped:
                    inner_escaped = False
                elif inner == "\\":
                    inner_escaped = True
                elif inner_quote:
                    if inner == inner_quote:
                        inner_quote = None
                elif inner in {"'", '"', "`"}:
                    inner_quote = inner
                elif inner == "{":
                    depth += 1
                elif inner == "}":
                    depth -= 1
                idx += 1
            inner_expression = expression[inner_start : max(inner_start, idx - 1)]
            chunks.extend((inner_start + start, literal) for start, literal in iter_string_literals(inner_expression))
            current_start = idx
            continue
        else:
            current.append(char)
        idx += 1

    chunks.append((current_start, "".join(current)))
    return idx, chunks


def iter_string_literals(expression: str) -> Iterable[tuple[int, str]]:
    idx = 0
    quote_chars = {"'", '"', "`"}
    while idx < len(expression):
        quote = expression[idx]
        if quote not in quote_chars:
            idx += 1
            continue

        start = idx
        if quote == "`":
            end, chunks = iter_template_literal_chunks(expression, idx)
            yield from chunks
            idx = end + 1
            continue

        idx += 1
        chars: list[str] = []
        escaped = False
        while idx < len(expression):
            char = expression[idx]
            if escaped:
                chars.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                yield start, "".join(chars)
                break
            else:
                chars.append(char)
            idx += 1
        idx += 1


def has_top_level_conditional_after(expression: str, idx: int) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False

    while idx < len(expression):
        char = expression[idx]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "?" and depth == 0:
            return True
        idx += 1

    return False


def has_top_level_translated_logical_after(expression: str, idx: int) -> bool:
    depth = 0
    quote: str | None = None
    escaped = False

    while idx < len(expression):
        char = expression[idx]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote:
            if char == quote:
                quote = None
        elif char in {"'", '"', "`"}:
            quote = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and expression.startswith(("&&", "||"), idx):
            return re.search(r"(?:\$t|(?<![\w$])t)\s*\(", expression[idx + 2 :]) is not None
        idx += 1

    return False


def is_non_rendered_condition_literal(expression: str, start: int, literal: str) -> bool:
    literal_end = start + len(literal) + 2
    return has_top_level_conditional_after(expression, literal_end) or has_top_level_translated_logical_after(expression, literal_end)


def strip_comments_preserve_lines(text: str) -> str:
    return COMMENT_RE.sub(lambda match: "\n" * match.group(0).count("\n"), text)


def add_violations_from_bound_attrs(
    *,
    path: str,
    line: int,
    matches: Iterable[re.Match[str]],
    allowlist: Allowlist,
    sink: list[Violation],
) -> None:
    for match in matches:
        expression = match.group(3).strip()
        for start, literal in iter_string_literals(expression):
            if is_translation_key_argument(expression, start):
                continue
            if is_bound_literal_technical(literal):
                continue
            if is_non_rendered_condition_literal(expression, start, literal):
                continue
            if should_flag(literal, allowlist, allow_technical_tokens=False):
                sink.append(Violation(path=path, line=line, kind="template-bound-attr", snippet=normalize_text(literal)))


def iter_visible_template_text(line: str, in_interpolation: bool, in_tag: bool, tag_quote: str | None) -> Iterable[str]:
    idx = 0
    chunk: list[str] = []
    quote = tag_quote

    while idx < len(line):
        if in_tag:
            if quote:
                if line[idx] == quote:
                    quote = None
            elif line[idx] in {"'", '"'}:
                quote = line[idx]
            elif line[idx] == ">":
                in_tag = False
            idx += 1
            continue

        if in_interpolation:
            if line.startswith("}}", idx):
                in_interpolation = False
                idx += 2
            else:
                idx += 1
            continue

        if line.startswith("{{", idx):
            if chunk:
                yield "".join(chunk)
                chunk = []
            in_interpolation = True
            idx += 2
            continue

        if line[idx] == "<":
            if chunk:
                yield "".join(chunk)
                chunk = []
            in_tag = True
            quote = None
            idx += 1
            continue

        chunk.append(line[idx])
        idx += 1

    if chunk:
        yield "".join(chunk)


def raw_translation_text_violation(
    path: str, line_no: int, line: str, in_interpolation: bool, in_tag: bool, tag_quote: str | None
) -> Violation | None:
    for chunk in iter_visible_template_text(line, in_interpolation, in_tag, tag_quote):
        text = normalize_text(chunk)
        if RAW_TRANSLATION_TEXT_RE.search(text):
            return Violation(path=path, line=line_no, kind="template-text", snippet=text)
    return None


def template_blocks(content: str) -> list[tuple[int, int, int, int, int]]:
    blocks: list[tuple[int, int, int, int, int]] = []
    stack: list[tuple[int, int]] = []
    for match in TEMPLATE_TAG_RE.finditer(content):
        tag = match.group(0)
        if tag.startswith("</"):
            if not stack:
                continue
            outer_start, body_start = stack.pop()
            if not stack:
                start_line = content.count("\n", 0, body_start) + 1
                blocks.append((body_start, match.start(), start_line, outer_start, match.end()))
        elif not tag.endswith("/>"):
            stack.append((match.start(), match.end()))
    return blocks


def strip_template_blocks(content: str, blocks: list[tuple[int, int, int, int, int]]) -> str:
    stripped = content
    for _start, _end, _line, outer_start, outer_end in reversed(blocks):
        stripped = stripped[:outer_start] + "\n" * stripped.count("\n", outer_start, outer_end) + stripped[outer_end:]
    return stripped


def update_tag_state(in_tag: bool, tag_quote: str | None, line: str) -> tuple[bool, str | None]:
    quote = tag_quote
    for char in line:
        if in_tag:
            if quote:
                if char == quote:
                    quote = None
            elif char in {"'", '"'}:
                quote = char
            elif char == ">":
                in_tag = False
        elif char == "<":
            in_tag = True
            quote = None
    return in_tag, quote


def scan_vue(path: str, content: str, allowlist: Allowlist) -> list[Violation]:
    violations: list[Violation] = []

    blocks = template_blocks(content)
    for start, end, start_line, _outer_start, _outer_end in blocks:
        tpl = content[start:end]
        tpl_without_comments = strip_comments_preserve_lines(tpl)
        for match in BOUND_ATTR_RE.finditer(tpl_without_comments):
            line = start_line + tpl_without_comments.count("\n", 0, match.start())
            add_violations_from_bound_attrs(
                path=path,
                line=line,
                matches=[match],
                allowlist=allowlist,
                sink=violations,
            )

        interpolation_depth = 0
        in_tag = False
        tag_quote: str | None = None
        for idx, raw_line in enumerate(tpl_without_comments.splitlines(), start=start_line):
            line = raw_line
            if not line.strip():
                continue
            if violation := raw_translation_text_violation(path, idx, line, interpolation_depth > 0, in_tag, tag_quote):
                violations.append(violation)
            add_violations_from_matches(
                path=path,
                line=idx,
                kind="template-attr",
                matches=ATTR_RE.finditer(line),
                candidate_group=3,
                allowlist=allowlist,
                sink=violations,
            )
            add_violations_from_matches(
                path=path,
                line=idx,
                kind="template-text",
                matches=TEXT_NODE_RE.finditer(line),
                candidate_group=1,
                allowlist=allowlist,
                sink=violations,
            )
            interpolation_depth = max(0, interpolation_depth + line.count("{{") - line.count("}}"))
            in_tag, tag_quote = update_tag_state(in_tag, tag_quote, line)

    stripped = strip_template_blocks(content, blocks)
    for line_no, raw_line in enumerate(stripped.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-ui-call",
            matches=UI_CALL_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-error",
            matches=ERROR_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-assign",
            matches=ASSIGN_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )

    return violations


def scan_script(path: str, content: str, allowlist: Allowlist) -> list[Violation]:
    violations: list[Violation] = []
    for line_no, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-ui-call",
            matches=UI_CALL_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-error",
            matches=ERROR_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
        add_violations_from_matches(
            path=path,
            line=line_no,
            kind="script-assign",
            matches=ASSIGN_RE.finditer(line),
            candidate_group=2,
            allowlist=allowlist,
            sink=violations,
        )
    return violations


def scan_hardcoded_strings(repo_root: Path, files: list[str], allowlist: Allowlist) -> list[Violation]:
    violations: list[Violation] = []
    for rel_path in files:
        path = repo_root / rel_path
        if not path.exists() or not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        if rel_path.endswith(".vue"):
            violations.extend(scan_vue(rel_path, content, allowlist))
        else:
            violations.extend(scan_script(rel_path, content, allowlist))
    return sorted(violations, key=lambda v: (v.path, v.line, v.kind, v.snippet))


def check_locale_pair(repo_root: Path, app: str) -> list[str]:
    locale_root = repo_root / app / "src" / "locales"
    de_path = locale_root / "de.json"
    en_path = locale_root / "en.json"
    try:
        de = json.loads(de_path.read_text(encoding="utf-8"))
        en = json.loads(en_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{app}: invalid JSON in locale files ({exc})"]

    if not isinstance(de, dict) or not isinstance(en, dict):
        return [f"{app}: locale root must be a JSON object in both de.json and en.json"]

    de_keys = flatten_keys(de)
    en_keys = flatten_keys(en)
    errors: list[str] = []

    for key in sorted(de_keys - en_keys):
        errors.append(f"{app}/src/locales/en.json is missing key: {key}")
    for key in sorted(en_keys - de_keys):
        errors.append(f"{app}/src/locales/de.json is missing key: {key}")

    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff-scoped i18n hard gate")
    parser.add_argument("--base", help="Base git ref for diff", default=None)
    parser.add_argument("--head", help="Head git ref for diff", default=None)
    parser.add_argument("--allowlist", default="tools/i18n-allowlist.txt", help="Allowlist file path")
    parser.add_argument("files", nargs="*", help="Explicit files to scan instead of deriving from git diff")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[1]

    if args.files:
        changed_files = sorted(set(args.files))
    else:
        changed_files = run_git_diff(repo_root, args.base, args.head)

    if not changed_files:
        print("i18n guard: no changed files in diff; nothing to do.")
        return 0

    scan_files = [p for p in changed_files if TARGET_FILE_RE.match(p)]
    touched_apps = {m.group(1) for p in changed_files if (m := LOCALE_FILE_RE.match(p))}

    if not scan_files and not touched_apps:
        print("i18n guard: no relevant frontend/i18n files in diff; nothing to do.")
        return 0

    allowlist = Allowlist(repo_root / args.allowlist)
    violations = scan_hardcoded_strings(repo_root, scan_files, allowlist)

    locale_errors: list[str] = []
    for app in sorted(touched_apps):
        locale_errors.extend(check_locale_pair(repo_root, app))

    if violations:
        print("i18n guard: hardcoded user-facing strings detected in changed files:")
        for v in violations:
            print(f"  {v.path}:{v.line}: [{v.kind}] {v.snippet}")

    if locale_errors:
        print("i18n guard: locale consistency errors:")
        for err in locale_errors:
            print(f"  {err}")

    if violations or locale_errors:
        print("i18n guard: FAILED")
        return 1

    print(f"i18n guard: OK (scanned {len(scan_files)} changed frontend files; locale-app-checks: {', '.join(sorted(touched_apps)) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
