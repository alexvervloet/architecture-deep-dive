#!/usr/bin/env python3
"""
measure.py: count what each requirement cost each variant.

    python ch01-provider-seam/measure.py

The chapter's claim is about diff size, so the diff is computed, not asserted.
Two counting decisions, both of which change the answer and both of which are
therefore stated rather than buried:

**Docstrings, comments, and blank lines are excluded.** Each version carries a
header explaining what changed, and those headers differ by design. Counting
them would mean the variant with more explanation "changed more", which would
be measuring my prose instead of the code. The raw count is printed alongside
so you can see how much the exclusion moved things.

**A file that was not opened counts as zero, and is reported separately.**
"app.py: not touched" is a different claim from "app.py: 2 lines changed", and
in a real codebase it is the difference between a review and a rubber stamp.

A modified line counts as two (one removed, one added), which is how diff
churn is normally counted. It inflates both variants equally, so the ratio
holds, but the absolute numbers are churn and not "lines of new code".
"""

from __future__ import annotations

import ast
import difflib
import sys
from pathlib import Path

CHAPTER = Path(__file__).parent
VARIANTS = ("inline", "seam")
VERSIONS = (
    ("v0_baseline", "the app as first written", "v0"),
    ("v1_claude", "req 1: swap OpenAI for Anthropic", "req 1"),
    ("v2_reliability", "req 2: timeout + retry with backoff", "req 2"),
    ("v3_cost", "req 3: per-request cost accounting", "req 3"),
    ("v4_feature", "req 4: a fourth model-backed feature", "req 4"),
    ("v5_streaming", "req 5: stream one call site", "req 5"),
)


def significant_lines(path: Path) -> list[str]:
    """Code only: no docstrings, no comment-only lines, no blanks."""
    source = path.read_text()
    tree = ast.parse(source)
    docstring_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None and node.body:
                first = node.body[0]
                docstring_lines.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
    out = []
    for i, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or i in docstring_lines:
            continue
        out.append(line.rstrip())
    return out


def raw_lines(path: Path) -> list[str]:
    return [ln.rstrip() for ln in path.read_text().splitlines()]


def diff_counts(old: list[str], new: list[str]) -> tuple[int, int]:
    added = removed = 0
    for line in difflib.unified_diff(old, new, lineterm="", n=0):
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return added, removed


def files_in(version_dir: Path) -> list[str]:
    return sorted(p.name for p in version_dir.glob("*.py"))


def loc(version_dir: Path) -> int:
    return sum(len(significant_lines(p)) for p in version_dir.glob("*.py"))


def main() -> int:
    print("Baseline size at v0 (significant lines of code)\n")
    for variant in VARIANTS:
        v0 = CHAPTER / variant / "v0_baseline"
        parts = ", ".join(f"{name} {len(significant_lines(v0 / name))}" for name in files_in(v0))
        print(f"  {variant:<7} {loc(v0):>3} lines   ({parts})")
    upfront = loc(CHAPTER / "seam" / "v0_baseline") - loc(CHAPTER / "inline" / "v0_baseline")
    plural = "line" if abs(upfront) == 1 else "lines"
    print(
        f"\n  The seam's upfront cost is {upfront} {plural}: provider.py adds almost exactly"
        f"\n  what app.py stops carrying."
    )

    print("\n\nCost of each requirement (significant lines changed, and files touched)\n")
    header = f"{'requirement':<34} {'inline':>20}   {'seam':>24}"
    print(header)
    print("-" * len(header))

    totals = {v: 0 for v in VARIANTS}
    cumulative: dict[str, list[int]] = {v: [] for v in VARIANTS}
    raw_totals = {v: 0 for v in VARIANTS}

    for (prev, _, _), (cur, label, _) in zip(VERSIONS, VERSIONS[1:]):
        cells = {}
        for variant in VARIANTS:
            old_dir, new_dir = CHAPTER / variant / prev, CHAPTER / variant / cur
            touched, changed, raw_changed = [], 0, 0
            for name in sorted(set(files_in(old_dir)) | set(files_in(new_dir))):
                old_file, new_file = old_dir / name, new_dir / name
                old = significant_lines(old_file) if old_file.exists() else []
                new = significant_lines(new_file) if new_file.exists() else []
                added, removed = diff_counts(old, new)
                if added or removed:
                    touched.append(name)
                    changed += added + removed
                old_raw = raw_lines(old_file) if old_file.exists() else []
                new_raw = raw_lines(new_file) if new_file.exists() else []
                r_added, r_removed = diff_counts(old_raw, new_raw)
                raw_changed += r_added + r_removed
            totals[variant] += changed
            raw_totals[variant] += raw_changed
            cumulative[variant].append(totals[variant])
            files = ", ".join(touched) if touched else "nothing"
            cells[variant] = f"{changed:>3} in {files}"
        print(f"{label:<34} {cells['inline']:>20}   {cells['seam']:>24}")

    print("-" * len(header))
    print(f"{'total churn, 5 requirements':<34} {totals['inline']:>20}   {totals['seam']:>24}")
    print(f"{'  including prose and blanks':<34} {raw_totals['inline']:>20}   {raw_totals['seam']:>24}")

    print("\n\nCumulative churn, with the seam's upfront lines counted as debt\n")
    print(f"  {'after':<8} {'inline':>8} {'seam':>8} {'seam ahead by':>15}")
    crossover = None
    for i, (_, _, short) in enumerate(VERSIONS[1:]):
        running_inline = cumulative["inline"][i]
        running_seam = upfront + cumulative["seam"][i]
        delta = running_inline - running_seam
        if crossover is None and delta > 0:
            crossover = short
        print(f"  {short:<8} {running_inline:>8} {running_seam:>8} {delta:>+15}")

    print()
    if crossover == VERSIONS[1][2]:
        print(f"  The seam is ahead from the first requirement onward. There is no")
        print(f"  break-even point to find, because its upfront cost was {upfront} {plural}.")
    elif crossover:
        print(f"  The seam is behind until {crossover}, and ahead from there on.")
    else:
        print("  The seam never repaid its upfront cost across these five requirements.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
