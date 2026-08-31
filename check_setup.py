#!/usr/bin/env python3
"""
check_setup.py: your first stop. Makes no API call and costs nothing.

    python check_setup.py                 # the offline path (all you need)
    secrun python check_setup.py          # also checks a real provider's key

This repo needs less than its siblings: the default path is offline, so if
Python and python-dotenv are present, every measurement in the repo will run.
"""

import sys

OK, BAD = "ok  ", "FAIL"


def main() -> int:
    problems = []

    # 3.11 rather than 3.10 because that is the floor CI actually exercises.
    # Nothing here uses 3.11-only syntax, so 3.10 will probably work; "probably"
    # is not what this file is for.
    version = sys.version_info
    if version >= (3, 11):
        print(f"{OK} python {version.major}.{version.minor}.{version.micro}")
    else:
        print(f"{BAD} python {version.major}.{version.minor}: this repo needs 3.11+")
        problems.append("upgrade python to 3.11 or newer")

    try:
        import dotenv  # noqa: F401

        print(f"{OK} python-dotenv installed")
    except ImportError:
        print(f"{BAD} python-dotenv missing")
        problems.append("pip install -r requirements.txt")

    try:
        from app import providers, service  # noqa: F401
        from stress import harness  # noqa: F401

        print(f"{OK} app and stress packages import")
    except Exception as exc:  # pragma: no cover
        print(f"{BAD} import failed: {exc}")
        problems.append("run this from the repo root")
        return _report(problems)

    from dotenv import load_dotenv

    load_dotenv()
    print(f"{OK} provider: {providers.describe()}")

    if providers.provider_name() != "mock":
        missing = providers.missing_keys()
        if missing:
            print(f"{BAD} {', '.join(missing)} not on the environment (use secrun)")
            problems.append("run under secrun, or set PROVIDER=mock")

    # The one check worth actually running: determinism. Everything this repo
    # claims rests on it, so verify it here rather than trusting it.
    from app.determinism import draw

    if draw(7, "a", 1) == draw(7, "a", 1) and draw(7, "a", 1) != draw(7, "a", 2):
        print(f"{OK} deterministic draws are stable and label-sensitive")
    else:
        print(f"{BAD} determinism check failed")
        problems.append("app/determinism.py is broken; no measurement here is trustworthy")

    return _report(problems)


def _report(problems: list[str]) -> int:
    print()
    if not problems:
        print("Ready. Start with: python examples/00_reference_app.py")
        return 0
    print("Fix these:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
