
"""
ch08/shapes.py: five ways to put a prompt change in front of users.

    ship        deploy to everyone, immediately
    gate        run the eval suite first, deploy only if it passes
    canary      deploy to 10% and watch the complaint rate
    shadow      run the candidate on all traffic, serve the old one, compare
    gate+canary the two that catch different things, stacked

Each shape answers the same stream of production requests and reports the
same four numbers, of which only the first is about users:

    bad served      wrong answers that reached a person
    detected at     the request index where the shape noticed, if it did
    extra calls     model calls beyond one per request
    shipped         whether a good change actually got out, and when

The last column is there to stop this being a one-sided chapter. Any shape can
score zero bad answers by never shipping anything, so a rollout process has to
be judged on what it lets through as well as on what it stops.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from versions import V1, Response, Version, answer, run_suite

CANARY_FRACTION = 10  # serve the candidate to every 10th request
CANARY_MIN_SAMPLES = 30
CANARY_MARGIN = 0.06  # candidate complaint rate must exceed control by this
SHADOW_MIN_SAMPLES = 40
SHADOW_DISAGREE_MARGIN = 0.10


@dataclass
class Rollout:
    shape: str
    candidate: str
    bad_served: int = 0
    served: int = 0
    detected_at: int | None = None
    extra_calls: int = 0
    shipped_at: int | None = None
    rolled_back: bool = False
    blocked_before_deploy: bool = False
    note: str = ""
    prompt_tokens: int = 0
    events: list[str] = field(default_factory=list)


def _serve(rollout: Rollout, result: Response) -> None:
    rollout.served += 1
    rollout.prompt_tokens += result.prompt_tokens
    if not result.correct:
        rollout.bad_served += 1


def ship(candidate: Version, workload, requests: int) -> Rollout:
    """Deploy to everyone at request zero. The control, and still common."""
    rollout = Rollout("ship", candidate.label, shipped_at=0)
    for i in range(requests):
        question, gold = workload[i % len(workload)]
        _serve(rollout, answer(candidate, question, gold, f"r{i:04d}"))
    rollout.note = "no mechanism to notice anything"
    return rollout


def gate(candidate: Version, workload, requests: int, suite, suite_name: str) -> Rollout:
    """Run the eval suite offline, then deploy only if it did not regress.

    Precise and cheap, and blind to everything outside the suite. Offline it
    has gold labels, so a regression it *can* see it sees immediately, with no
    users involved and no waiting for volume.
    """
    rollout = Rollout(f"gate ({suite_name})", candidate.label)
    baseline_passed, total = run_suite(V1, suite)
    candidate_passed, _ = run_suite(candidate, suite)
    rollout.extra_calls += 2 * total
    rollout.events.append(f"suite: v1 {baseline_passed}/{total}, candidate {candidate_passed}/{total}")

    if candidate_passed < baseline_passed:
        rollout.blocked_before_deploy = True
        rollout.detected_at = 0
        rollout.note = f"blocked before deploy: {candidate_passed}/{total} against {baseline_passed}/{total}"
        for i in range(requests):
            question, gold = workload[i % len(workload)]
            _serve(rollout, answer(V1, question, gold, f"r{i:04d}"))
        return rollout

    rollout.shipped_at = 0
    for i in range(requests):
        question, gold = workload[i % len(workload)]
        _serve(rollout, answer(candidate, question, gold, f"r{i:04d}"))
    rollout.note = f"suite passed ({candidate_passed}/{total}), shipped to everyone"
    return rollout


def canary(candidate: Version, workload, requests: int) -> Rollout:
    """Serve the candidate to a tenth of traffic and watch the complaints.

    The signal here is a thumbs-down, not a gold label, because that is what
    production actually has. It is noisy in both directions, so the shape
    cannot react to one bad response: it needs enough samples for the rate to
    mean something, and that wait is paid in wrong answers.
    """
    rollout = Rollout("canary 10%", candidate.label)
    canary_total = canary_down = control_total = control_down = 0
    active = True

    for i in range(requests):
        question, gold = workload[i % len(workload)]
        on_canary = active and (i % CANARY_FRACTION == 0)
        version = candidate if on_canary else V1
        result = answer(version, question, gold, f"r{i:04d}")
        _serve(rollout, result)
        if rollout.shipped_at is None and on_canary:
            rollout.shipped_at = i  # first exposure, not full rollout

        if on_canary:
            canary_total += 1
            canary_down += int(result.thumbs_down)
        else:
            control_total += 1
            control_down += int(result.thumbs_down)

        if active and canary_total >= CANARY_MIN_SAMPLES:
            canary_rate = canary_down / canary_total
            control_rate = control_down / max(1, control_total)
            if canary_rate > control_rate + CANARY_MARGIN:
                active = False
                rollout.detected_at = i
                rollout.rolled_back = True
                rollout.events.append(
                    f"rolled back at request {i}: canary {canary_rate:.0%} vs control {control_rate:.0%}"
                )
    if rollout.rolled_back:
        rollout.note = f"rolled back after {canary_total} canary samples"
    else:
        rollout.note = f"still at {CANARY_FRACTION}% after {requests} requests, no signal"
    return rollout


def shadow(candidate: Version, workload, requests: int) -> Rollout:
    """Run the candidate on every request, serve the old one, compare.

    Zero user exposure and double the model calls. What it can see is that the
    two versions *disagree*, which is not the same as knowing the new one is
    worse: without labels, disagreement is a flag for a human, not a verdict.
    That limitation is the reason this shape does not simply win.
    """
    rollout = Rollout("shadow", candidate.label)
    disagreements = compared = 0

    for i in range(requests):
        question, gold = workload[i % len(workload)]
        served = answer(V1, question, gold, f"r{i:04d}")
        _serve(rollout, served)

        shadowed = answer(candidate, question, gold, f"shadow-{i:04d}")
        rollout.extra_calls += 1
        compared += 1
        if shadowed.text.strip() != served.text.strip():
            disagreements += 1

        if rollout.detected_at is None and compared >= SHADOW_MIN_SAMPLES:
            rate = disagreements / compared
            if rate > SHADOW_DISAGREE_MARGIN:
                rollout.detected_at = i
                rollout.events.append(
                    f"flagged at request {i}: {rate:.0%} of responses differ"
                )
    rate = disagreements / max(1, compared)
    if rollout.detected_at is not None:
        rollout.note = f"flagged {rate:.0%} disagreement, never promoted, needs a human"
    else:
        rollout.note = f"{rate:.0%} disagreement, safe to promote"
        rollout.shipped_at = requests  # promoted only after the window
    return rollout


def gate_then_canary(candidate: Version, workload, requests: int, suite, suite_name: str) -> Rollout:
    """The two that fail differently, stacked.

    The gate is precise and sampled; the canary is complete and noisy. Neither
    subsumes the other, which is the argument for paying for both.
    """
    rollout = Rollout(f"gate ({suite_name}) + canary", candidate.label)
    baseline_passed, total = run_suite(V1, suite)
    candidate_passed, _ = run_suite(candidate, suite)
    rollout.extra_calls += 2 * total

    if candidate_passed < baseline_passed:
        rollout.blocked_before_deploy = True
        rollout.detected_at = 0
        rollout.note = "gate caught it, no user ever saw the candidate"
        for i in range(requests):
            question, gold = workload[i % len(workload)]
            _serve(rollout, answer(V1, question, gold, f"r{i:04d}"))
        return rollout

    inner = canary(candidate, workload, requests)
    inner.shape = rollout.shape
    inner.extra_calls += rollout.extra_calls
    inner.note = f"gate passed, then {inner.note}"
    return inner
