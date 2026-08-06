#!/usr/bin/env python3
"""
fairness.py: prove the two variants are the same app before believing any diff.

    python ch01-provider-seam/fairness.py

measure.py counts lines. Line counts are easy to win dishonestly: drop a
feature, skip an error case, return a slightly worse answer, and the diff
shrinks. So this script loads all twelve builds and checks that every one of
them produces byte-identical output for the same inputs.

If this fails, the numbers in ADR.md are void, in the direction the failure
points. That is the only reason it exists, and it is why it runs before the
measurement in the chapter's README rather than after.

It also reports the cost ledger per version, which is where the interesting
divergence lives: not in whether the variants agree with each other (they do)
but in what streaming does to the meaning of the number.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

CHAPTER = Path(__file__).parent
sys.path.insert(0, str(CHAPTER.parent))

from app import providers, retrieval  # noqa: E402

VERSIONS = ("v0_baseline", "v1_claude", "v2_reliability", "v3_cost", "v4_feature", "v5_streaming")
VARIANTS = ("inline", "seam")

QUESTION = "How do I reset my password?"
BILLING = "Can I get a refund?"
TEXT = "The service was down for two hours this morning. Customers noticed."


def load(variant: str, version: str) -> types.ModuleType:
    """Import one build under a synthetic package name.

    The seam's app.py does `from . import provider`, so the directory has to
    look like a package. Rather than scattering __init__.py files that would
    then show up in the file counts, the package is built in memory here.
    """
    directory = CHAPTER / variant / version
    package = f"_ch01_{variant}_{version}"
    module = types.ModuleType(package)
    module.__path__ = [str(directory)]  # type: ignore[attr-defined]
    sys.modules[package] = module
    for name in ("provider", "app"):  # provider first: app imports it
        path = directory / f"{name}.py"
        if not path.exists():
            continue
        spec = importlib.util.spec_from_file_location(f"{package}.{name}", path)
        assert spec and spec.loader
        loaded = importlib.util.module_from_spec(spec)
        sys.modules[f"{package}.{name}"] = loaded
        spec.loader.exec_module(loaded)
    return sys.modules[f"{package}.app"]


def behaviour(app: types.ModuleType) -> tuple:
    """Everything a caller can observe, for one fixed set of inputs."""
    answer_text, sources = app.answer(QUESTION)
    results = [
        ("triage", app.triage(BILLING)),
        ("answer", answer_text),
        ("sources", ",".join(sources)),
        ("digest", app.digest(TEXT)),
    ]
    if hasattr(app, "escalate"):
        results.append(("escalate", app.escalate(QUESTION)))
    return tuple(results)


def spend_of(app: types.ModuleType) -> float | None:
    """Total spend the build reports, or None if it cannot report one."""
    if hasattr(app, "spend_usd"):
        return app.spend_usd()
    provider = sys.modules.get(f"{app.__name__.rsplit('.', 1)[0]}.provider")
    if provider is not None and hasattr(provider, "spend_usd"):
        return provider.spend_usd()
    return None


def main() -> int:
    providers.configure_mock(latency="instant", seed=1337)
    retrieval.reset_retrieval()

    print(f"Provider: {providers.describe()}\n")
    print("Behaviour check: all twelve builds, same inputs, same outputs?\n")

    reference = None
    failures = []
    spends: dict[str, dict[str, float | None]] = {}

    for version in VERSIONS:
        spends[version] = {}
        for variant in VARIANTS:
            app = load(variant, version)
            observed = behaviour(app)
            spends[version][variant] = spend_of(app)
            # v4 adds a feature, so its output tuple is longer. Compare only
            # the entries both builds have: the check is that no requirement
            # silently changed an answer, not that the app never grew.
            if reference is None:
                reference = observed
            common = min(len(reference), len(observed))
            if observed[:common] != reference[:common]:
                failures.append((variant, version, reference[:common], observed[:common]))

    if failures:
        print(f"FAIL  {len(failures)} build(s) diverged. The measurements are void.\n")
        for variant, version, expected, got in failures[:3]:
            print(f"  {variant}/{version}")
            for (k, a), (_, b) in zip(expected, got):
                if a != b:
                    print(f"    {k}: expected {a!r}\n       got      {b!r}")
        return 1

    assert reference is not None
    print(f"PASS  all 12 builds agree on every observable, across {len(VERSIONS)} versions.")
    for key, value in reference:
        shown = value if len(value) < 64 else value[:61] + "..."
        print(f"        {key:<9} {shown!r}")

    print("\n\nReported spend per build (USD, after the same four calls)\n")
    print(f"  {'version':<16} {'inline':>12} {'seam':>12}   note")
    for version in VERSIONS:
        inline_spend, seam_spend = spends[version]["inline"], spends[version]["seam"]
        if inline_spend is None and seam_spend is None:
            print(f"  {version:<16} {'n/a':>12} {'n/a':>12}   cannot report spend at all")
            continue
        note = ""
        if inline_spend is not None and seam_spend is not None:
            if abs(inline_spend - seam_spend) < 1e-12:
                note = "identical"
            else:
                note = f"differ by ${abs(inline_spend - seam_spend):.6f}"
        print(f"  {version:<16} ${inline_spend or 0:>11.6f} ${seam_spend or 0:>11.6f}   {note}")

    v4 = spends["v4_feature"]["seam"] or 0.0
    v5_seam = spends["v5_streaming"]["seam"] or 0.0
    v5_inline = spends["v5_streaming"]["inline"] or 0.0
    print()
    print("The v5 row is the finding, and it is not the one this chapter set out to")
    print("make. Text output stayed identical everywhere, so the comparison is fair.")
    print("The money did not. A streamed response carries no usage block, so both")
    print("variants fell back to estimating from characters, and the two estimates")
    print(f"disagree: ${v5_inline:.6f} against ${v5_seam:.6f}. Neither is a bug. The inline")
    print("version measures the context it built; the seam measures the full system")
    print("prompt it was handed. Both are defensible, and they are different numbers.")
    print()
    print(f"So the ledger went from exact and agreeing (${v4:.6f} at v4) to estimated")
    print("and diverging, one requirement later, with no error and no warning. A cost")
    print("dashboard reading either build would show a smooth line through that change.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
