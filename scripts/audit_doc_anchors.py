#!/usr/bin/env python3
"""Audit implementation anchors embedded in public markdown docs."""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / "docs"
DEFAULT_TOLERANCE = 25

ANCHOR_RE = re.compile(
    r"(?P<prefix><!--\s*impl:\s*§(?P<section>\d+(?:\.\d+)*)\s*->\s*"
    r"(?P<path>\S+)\s+(?P<symbol>[A-Za-z_][A-Za-z0-9_.-]*))"
    r"(?::(?P<line>\d+))?"
    r"(?P<suffix>\s+—\s*(?P<what>.*?)\s*-->)"
)
TOP_SECTION_RE = re.compile(r"^##\s+(?P<section>\d+)\.")
SUBSECTION_RE = re.compile(r"^###\s+§(?P<section>\d+(?:\.\d+)*)\b")
YAML_KEY_RE = re.compile(r"^(?P<indent>\s*)(?:-\s*)?(?P<key>[A-Za-z0-9_.-]+)\s*:")


@dataclass(frozen=True)
class Anchor:
    doc_path: Path
    line_no: int
    section: str
    target_path: str
    symbol: str
    line_hint: int | None
    description: str
    raw_line: str


@dataclass(frozen=True)
class Resolution:
    start_line: int
    end_line: int


@dataclass
class AuditIssue:
    anchor: Anchor
    message: str
    corrected_line: int | None = None


def _iter_doc_paths() -> list[Path]:
    return sorted(DOCS_DIR.glob("*.md"))


def _sections_for_doc(path: Path) -> set[str]:
    sections: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        top = TOP_SECTION_RE.match(line)
        if top:
            sections.add(top.group("section"))
            continue
        subsection = SUBSECTION_RE.match(line)
        if subsection:
            sections.add(subsection.group("section"))
    return sections


def _anchors_for_doc(path: Path) -> list[Anchor]:
    anchors: list[Anchor] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if "<!-- impl:" not in line:
            continue
        match = ANCHOR_RE.fullmatch(line.strip())
        if not match:
            anchors.append(
                Anchor(
                    doc_path=path,
                    line_no=line_no,
                    section="",
                    target_path="",
                    symbol="",
                    line_hint=None,
                    description="",
                    raw_line=line,
                )
            )
            continue
        hint = match.group("line")
        anchors.append(
            Anchor(
                doc_path=path,
                line_no=line_no,
                section=match.group("section"),
                target_path=match.group("path"),
                symbol=match.group("symbol"),
                line_hint=int(hint) if hint else None,
                description=match.group("what"),
                raw_line=line,
            )
        )
    return anchors


def _add_symbol(
    mapping: defaultdict[str, list[Resolution]],
    name: str,
    node: ast.AST,
) -> None:
    line = int(getattr(node, "lineno", 0) or 0)
    end = int(getattr(node, "end_lineno", line) or line)
    if line > 0:
        mapping[name].append(Resolution(line, max(line, end)))


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.symbols: defaultdict[str, list[Resolution]] = defaultdict(list)
        self.stack: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self.stack, node.name])
        _add_symbol(self.symbols, qualified, node)
        if not self.stack:
            _add_symbol(self.symbols, node.name, node)
        self.stack.append(node.name)
        for child in node.body:
            self.visit(child)
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        qualified = ".".join([*self.stack, node.name])
        _add_symbol(self.symbols, qualified, node)
        if not self.stack:
            _add_symbol(self.symbols, node.name, node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._visit_assignment_target(target, node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._visit_assignment_target(node.target, node)

    def _visit_assignment_target(self, target: ast.AST, node: ast.AST) -> None:
        if not isinstance(target, ast.Name):
            return
        qualified = ".".join([*self.stack, target.id])
        _add_symbol(self.symbols, qualified, node)
        if not self.stack:
            _add_symbol(self.symbols, target.id, node)


def _resolve_python_symbol(path: Path, symbol: str) -> Resolution | str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return f"could not parse Python file: {exc}"
    visitor = _PythonSymbolVisitor()
    visitor.visit(tree)
    matches = visitor.symbols.get(symbol, [])
    if not matches:
        return f"symbol not found: {symbol}"
    unique = {(match.start_line, match.end_line) for match in matches}
    if len(unique) > 1:
        return f"ambiguous symbol: {symbol}"
    return matches[0]


def _yaml_id_line(lines: list[str], symbol: str) -> int | None:
    quoted = re.escape(symbol)
    id_re = re.compile(rf"^\s*(?:-\s*)?id:\s*['\"]?{quoted}['\"]?(?:\s|$|#)")
    for idx, line in enumerate(lines, 1):
        if id_re.search(line):
            return idx
    return None


def _yaml_key_line(lines: list[str], symbol: str) -> int | None:
    key_re = re.compile(rf"^\s*(?:-\s*)?{re.escape(symbol)}\s*:")
    for idx, line in enumerate(lines, 1):
        if key_re.match(line):
            return idx
    return None


def _yaml_path_line(lines: list[str], symbol: str) -> int | None:
    stack: list[tuple[int, str]] = []
    for idx, line in enumerate(lines, 1):
        match = YAML_KEY_RE.match(line)
        if not match:
            continue
        indent = len(match.group("indent"))
        key = match.group("key")
        while stack and indent <= stack[-1][0]:
            stack.pop()
        current = [item[1] for item in stack] + [key]
        if ".".join(current) == symbol:
            return idx
        stack.append((indent, key))
    return None


def _resolve_yaml_symbol(path: Path, symbol: str) -> Resolution | str:
    lines = path.read_text(encoding="utf-8").splitlines()
    line = _yaml_id_line(lines, symbol)
    if line is None and "." in symbol:
        line = _yaml_path_line(lines, symbol)
    if line is None:
        line = _yaml_key_line(lines, symbol)
    if line is None:
        return f"YAML symbol not found: {symbol}"
    return Resolution(line, line)


def _resolve_symbol(path: Path, symbol: str) -> Resolution | str:
    if path.suffix == ".py":
        return _resolve_python_symbol(path, symbol)
    if path.suffix in {".yaml", ".yml"}:
        return _resolve_yaml_symbol(path, symbol)
    text = path.read_text(encoding="utf-8", errors="ignore")
    line = next(
        (idx for idx, value in enumerate(text.splitlines(), 1) if symbol in value),
        None,
    )
    if line is None:
        return f"symbol text not found: {symbol}"
    return Resolution(line, line)


def _check_hint(
    anchor: Anchor,
    resolution: Resolution,
    tolerance: int,
) -> AuditIssue | None:
    if anchor.line_hint is None:
        return None
    low = max(1, resolution.start_line - tolerance)
    high = resolution.end_line + tolerance
    if low <= anchor.line_hint <= high:
        return None
    return AuditIssue(
        anchor=anchor,
        message=(
            f"line hint {anchor.line_hint} outside resolved "
            f"{resolution.start_line}-{resolution.end_line}"
        ),
        corrected_line=resolution.start_line,
    )


def audit(tolerance: int = DEFAULT_TOLERANCE) -> tuple[list[AuditIssue], int]:
    issues: list[AuditIssue] = []
    anchor_count = 0
    for doc_path in _iter_doc_paths():
        sections = _sections_for_doc(doc_path)
        for anchor in _anchors_for_doc(doc_path):
            anchor_count += 1
            if not anchor.section:
                issues.append(anchor_issue(anchor, "malformed impl anchor"))
                continue
            if anchor.section not in sections:
                issues.append(anchor_issue(anchor, f"section §{anchor.section} not found"))
                continue
            target = ROOT / anchor.target_path
            if not target.exists():
                issues.append(anchor_issue(anchor, f"target path not found: {anchor.target_path}"))
                continue
            resolution = _resolve_symbol(target, anchor.symbol)
            if isinstance(resolution, str):
                issues.append(anchor_issue(anchor, resolution))
                continue
            hint_issue = _check_hint(anchor, resolution, tolerance)
            if hint_issue is not None:
                issues.append(hint_issue)
    return issues, anchor_count


def anchor_issue(anchor: Anchor, message: str) -> AuditIssue:
    return AuditIssue(anchor=anchor, message=message)


def _fixed_line(line: str, corrected_line: int) -> str:
    match = ANCHOR_RE.fullmatch(line.strip())
    if match is None:
        return line
    fixed = f"{match.group('prefix')}:{corrected_line}{match.group('suffix')}"
    leading = line[: len(line) - len(line.lstrip())]
    return leading + fixed


def fix(issues: list[AuditIssue]) -> int:
    fixes_by_doc: dict[Path, dict[int, int]] = defaultdict(dict)
    for issue in issues:
        if issue.corrected_line is not None:
            fixes_by_doc[issue.anchor.doc_path][issue.anchor.line_no] = issue.corrected_line
    for doc_path, replacements in fixes_by_doc.items():
        lines = doc_path.read_text(encoding="utf-8").splitlines()
        for line_no, corrected in replacements.items():
            lines[line_no - 1] = _fixed_line(lines[line_no - 1], corrected)
        doc_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sum(len(items) for items in fixes_by_doc.values())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--tolerance", type=int, default=DEFAULT_TOLERANCE)
    args = parser.parse_args(argv)

    issues, anchor_count = audit(tolerance=args.tolerance)
    if args.fix:
        fixed = fix(issues)
        if fixed:
            issues, anchor_count = audit(tolerance=args.tolerance)
        print(f"doc anchor audit: fixed {fixed} drifted line hint(s)")

    if issues:
        print(f"doc anchor audit: FAIL ({len(issues)} issue(s), {anchor_count} anchor(s))")
        for issue in issues:
            rel_doc = issue.anchor.doc_path.relative_to(ROOT)
            print(
                f"{rel_doc}:{issue.anchor.line_no}: "
                f"§{issue.anchor.section or '?'} "
                f"{issue.anchor.target_path or '?'} "
                f"{issue.anchor.symbol or '?'}: {issue.message}"
            )
        return 1
    print(f"doc anchor audit: PASS ({anchor_count} anchor(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
