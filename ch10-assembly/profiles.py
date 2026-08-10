"""
ch10/profiles.py: three products, one set of decisions, three different answers.

Every constant in this file was measured by an earlier chapter. Nothing here is
estimated, and each one carries the chapter that produced it, so a reader can
go and check the number rather than take it. That is the whole idea of the
assembly: the nine ADRs are not nine opinions, they are nine measurements, and
a target architecture is what you get when you apply them to a budget.

The three products are deliberately far apart:

  support chat     a person is watching tokens appear. 1.5s to first token.
  batch pipeline   nobody is waiting. 10,000 documents overnight, cost rules.
  voice agent      a person is listening to silence. 300ms to first audio.

Same nine decisions. The answers differ on six of them and agree on three,
and the three they agree on are as informative as the six they do not: a
decision that comes out the same for a batch job and a voice agent is a
decision you can stop re-litigating.
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Measured constants, with the chapter that measured them
# ---------------------------------------------------------------------------

MEASURED = {
    # ch04: in-process 1.31ms vs tiered 2.75ms, timed around the model call.
    "model_tier_hop_ms": 1.45,
    # ch02: sqlite 0.47ms per turn against an in-process dict's 0.008ms.
    "shared_store_ms": 0.47,
    "in_process_store_ms": 0.008,
    # ch07: a 12-document rebuild costs 36ms; write-through costs nothing
    # on the read path.
    "per_request_index_ms": 36.0,
    "write_through_index_ms": 0.0,
    # ch05: 25ms per token, and a hold of 8 tokens contained every violation
    # in that corpus.
    "token_ms": 25.0,
    "guard_window_tokens": 8,
    # ch03: a poll round trip, for the queued shape.
    "poll_overhead_ms": 1.0,
    # The reference app's own dependencies, from app/retrieval.py and the
    # 'slow' provider profile in app/providers.py.
    "retrieval_ms": 20.0,
    "model_mean_ms": 1300.0,
}

# Deliberately absent: any tail constant. No chapter in this repo measured a
# p95 for a component in isolation, because none of them needed one. That
# omission is not tidiness, it is the finding in section 3: a budget assembled
# from these constants can predict a mean and cannot predict a tail, and the
# tail is what a user complains about. An earlier draft of this file carried a
# "model_p95_ms": 3000 alongside the others as though it had been measured. It
# had not. It was removed rather than sourced, because inventing a constant in
# the one file whose premise is that every constant was measured would have
# been the worst possible place to invent one.

PRICE_IN_PER_1K = 0.00025
PRICE_OUT_PER_1K = 0.00125


@dataclass(frozen=True)
class Product:
    name: str
    budget_ms: float  # what the user actually waits for
    budget_label: str
    human_waiting: bool
    multi_tenant: bool
    conversational: bool
    freshness: str  # "daily" | "static" | "hourly"
    wrong_answer_cost: str  # "low" | "high"
    note: str


PRODUCTS = (
    Product(
        "support chat",
        1500,
        "1.5s to first token",
        human_waiting=True,
        multi_tenant=True,
        conversational=True,
        freshness="daily",
        wrong_answer_cost="low",
        note="a person is watching tokens appear",
    ),
    Product(
        "batch pipeline",
        0,
        "no interactive budget; cost and throughput rule",
        human_waiting=False,
        multi_tenant=False,
        conversational=False,
        freshness="static",
        wrong_answer_cost="high",
        note="10,000 documents overnight, output is reviewed later",
    ),
    Product(
        "voice agent",
        300,
        "300ms to first audio",
        human_waiting=True,
        multi_tenant=True,
        conversational=True,
        freshness="daily",
        wrong_answer_cost="high",
        note="a person is listening to silence",
    ),
)


@dataclass
class Decision:
    chapter: str
    question: str
    choice: str
    because: str


def decide(product: Product) -> list[Decision]:
    """Apply the nine ADRs to one product.

    This function is the chapter. Everything else measures whether its
    arithmetic holds up.
    """
    decisions: list[Decision] = []

    # ch01: the seam cost 1 line upfront and 95 against 192 over five changes.
    decisions.append(Decision(
        "ch01", "provider seam",
        "one seam",
        "upfront cost measured at 1 line; nothing here argues for inline",
    ))

    # ch02: in-process state broke at 62% correct with 4 workers, 96% even
    # with sticky routing after a restart.
    if not product.conversational:
        choice, why = "no conversation state", "single-shot requests, nothing to remember"
    elif product.name == "voice agent":
        choice, why = (
            "in-process, pinned to the call",
            "a call is one connection to one worker for its whole life, so the "
            "ch02 failure (a follow-up landing elsewhere) cannot occur; losing "
            "the call loses the state either way",
        )
    else:
        choice, why = (
            "shared store",
            "ch02 measured 62% correct at 4 workers with an in-process dict, "
            "and 0.47ms is under 0.1% of the request",
        )
    decisions.append(Decision("ch02", "conversation state", choice, why))

    # ch03: all three shapes answered 15/32 under overload; the difference was
    # what happened to the other 17.
    if not product.human_waiting:
        choice, why = "queue", "nothing is waiting; ch03 showed queueing preserves 32/32 results"
    elif product.budget_ms < 500:
        choice, why = (
            "sync, and shed early",
            "ch03 measured shedding at p50 1254ms against sync's 3002ms; at this "
            "budget a queued job id is not an answer",
        )
    else:
        choice, why = (
            "sync with streaming, shed when deep",
            "a person is watching; ch03's shed row had the best p50 and half the spend",
        )
    decisions.append(Decision("ch03", "request shape", choice, why))

    # ch04: 1.45ms, 0.13% of a realistic request, buys 10/10 health checks
    # during a model outage.
    decisions.append(Decision(
        "ch04", "model tier",
        "separate tier",
        "1.45ms measured, and ch04's in-process build scored 0/10 on health "
        "checks when the model died",
    ))

    # ch05: window(8) contained everything at 200ms TTFT against buffering's
    # 1875ms.
    if not product.human_waiting:
        choice, why = "buffer, then guard", "no first-token budget to protect; buffering is strictly safer"
    elif product.budget_ms < 500:
        choice, why = (
            f"window({MEASURED['guard_window_tokens']}), and re-derive it",
            f"ch05's 8-token hold costs {MEASURED['guard_window_tokens'] * MEASURED['token_ms']:.0f}ms "
            f"of a {product.budget_ms:.0f}ms budget, which is most of it",
        )
    else:
        choice, why = (
            f"window({MEASURED['guard_window_tokens']})",
            "ch05: same containment as buffering at 200ms instead of 1875ms",
        )
    decisions.append(Decision("ch05", "guard placement", choice, why))

    # ch06: the fallback took availability 0% -> 100% and correctness to 17%.
    if product.wrong_answer_cost == "high" and not product.human_waiting:
        choice, why = (
            "hard fail, retry the item later",
            "ch06 measured 20/24 confidently stale answers from the fallback; a "
            "batch item can simply be retried, so there is nothing to buy",
        )
    else:
        choice, why = (
            "tiered fallback + breaker, degradation labelled",
            "ch06: the breaker cut wall clock 2.9x on a sustained slow dependency; "
            "the fallback's 17% correctness is why the tier must be labelled",
        )
    decisions.append(Decision("ch06", "degradation", choice, why))

    # ch07: write-through matched per-request correctness at 4 embedding calls
    # against 60.
    if product.freshness == "static":
        choice, why = (
            "index once, per job",
            "the corpus is the job's input; ch07's staleness question does not arise",
        )
    else:
        choice, why = (
            "write through + slow scheduled rebuild",
            "ch07: 60/60 correct for 4 embedding calls against per-request's 60, "
            "plus a timer so a missed write has a ceiling",
        )
    decisions.append(Decision("ch07", "indexing", choice, why))

    # ch08: the gate is a coin flip on its sample; the canary needed 300
    # requests to see anything.
    if not product.human_waiting:
        choice, why = (
            "gate + shadow",
            "ch08's canary needed 30 samples of live feedback; a batch job has no "
            "users to canary on, and shadow's double spend is affordable offline",
        )
    else:
        choice, why = (
            "gate + canary",
            "ch08: the gate caught nothing when its suite missed (129/600 served), "
            "the canary caught it at a cost of 8",
        )
    decisions.append(Decision("ch08", "rollout", choice, why))

    # ch09: filtering after the model leaked; at k=1 filtering after retrieval
    # lost exactly as many answers as the naive design leaked.
    if not product.multi_tenant:
        choice, why = "no tenant filter", "one tenant; the boundary does not exist"
    else:
        choice, why = (
            "filter inside retrieval",
            "ch09: filtering after the model leaked another tenant's contract "
            "terms verbatim; filtering after retrieval is safe but conditional on k",
        )
    decisions.append(Decision("ch09", "tenant boundary", choice, why))

    return decisions


@dataclass
class Config:
    """The runnable form of a decision list."""

    shared_store: bool
    model_tier: bool
    guard: str  # "buffer" | "window"
    per_request_index: bool
    tenant_filter: bool
    streaming: bool


def config_for(product: Product) -> Config:
    return Config(
        shared_store=product.conversational and product.name != "voice agent",
        model_tier=True,
        guard="window" if product.human_waiting else "buffer",
        per_request_index=product.freshness == "static",
        tenant_filter=product.multi_tenant,
        streaming=product.human_waiting,
    )


def predict(product: Product, config: Config) -> dict:
    """Add up the measured constants. This is the budget arithmetic, and the
    stressor exists to find out how wrong it is."""
    fixed = MEASURED["retrieval_ms"]
    if config.model_tier:
        fixed += MEASURED["model_tier_hop_ms"]
    fixed += MEASURED["shared_store_ms"] if config.shared_store else 0.0
    if config.per_request_index:
        fixed += MEASURED["per_request_index_ms"]

    if config.guard == "window":
        first_token = fixed + MEASURED["guard_window_tokens"] * MEASURED["token_ms"]
    else:
        first_token = fixed + MEASURED["model_mean_ms"]

    return {
        "fixed_ms": fixed,
        "predicted_first_token_ms": first_token,
        "predicted_mean_total_ms": fixed + MEASURED["model_mean_ms"],
        "within_budget": (first_token <= product.budget_ms) if product.budget_ms else True,
    }
