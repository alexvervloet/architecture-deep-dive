"""
ch08/versions.py: two prompt changes that look identical on the ticket.

Both candidates below are "reduce token spend on the context". One is safe and
one breaks a fifth of the answers, and nothing about the diff tells you which
is which. That is the premise of the chapter: rollout machinery exists because
you cannot tell by reading.

    v1          the current production version
    v2_trim     shorter system preamble, full context. Cheaper, same answers.
    v2_truncate context clipped to 120 characters per document. Cheaper, and
                wrong for every question whose answer is not near the top of
                its document.

The regression is deliberately *partial*. A change that breaks everything is
caught by any process at all, including no process, and a chapter built on one
would prove nothing. This one breaks 3 of 14 questions, which is small enough
to slip through a sampled eval suite and large enough to matter to users.

Production feedback is modelled as a thumbs-down, not as a gold label. That
distinction is the reason the chapter has more than one answer: offline you
know exactly which responses are wrong, and online you have a noisy signal
that needs volume before it means anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from app import providers, retrieval, service
from app.determinism import draws_below

FULL_PREAMBLE = """You are the support assistant for a SaaS product.
Answer only from the context below. If the context does not contain the answer,
say you do not know and point the user at support@example.com.

CONTEXT:
{context}"""

# Same instruction, fewer tokens. The kind of change that shows up in a
# cost-reduction sprint and is genuinely fine.
TRIM_PREAMBLE = """Answer only from the context.

CONTEXT:
{context}"""


@dataclass(frozen=True)
class Version:
    label: str
    preamble: str
    context_chars: int | None  # None = whole document

    def context(self, documents) -> str:
        return "\n".join(
            f"[{d.doc_id}] {d.text if self.context_chars is None else d.text[: self.context_chars]}"
            for d in documents
        )


V1 = Version("v1 (current)", FULL_PREAMBLE, None)
V2_TRIM = Version("v2-trim", TRIM_PREAMBLE, None)
V2_TRUNCATE = Version("v2-truncate", FULL_PREAMBLE, 120)

CANDIDATES = (V2_TRIM, V2_TRUNCATE)


@dataclass
class Response:
    text: str
    correct: bool
    prompt_tokens: int
    thumbs_down: bool


# Wrong answers get complained about far more often than right ones, and
# neither rate is anywhere near certain. These two numbers are what make
# detection a question of sample size rather than a question of noticing.
DOWN_IF_WRONG = 0.60
DOWN_IF_RIGHT = 0.03


def answer(version: Version, question: str, gold: str, request_id: str) -> Response:
    hits = retrieval.search(question, request_id=request_id)
    response = providers.generate(
        version.preamble.format(context=version.context(hits.documents)),
        question,
        request_id=request_id,
    )
    correct = service.answered_from(response.text, gold)
    rate = DOWN_IF_RIGHT if correct else DOWN_IF_WRONG
    return Response(
        text=response.text,
        correct=correct,
        prompt_tokens=response.prompt_tokens,
        thumbs_down=draws_below(1337, rate, "feedback", request_id),
    )


# The production workload. Three of these fourteen break under v2_truncate.
WORKLOAD: tuple[tuple[str, str], ...] = (
    ("How do I reset my password?", "doc-01"),
    ("How long is the password reset link valid?", "doc-01"),
    ("What happens if I lose my two-factor device?", "doc-02"),
    ("Can I get a refund?", "doc-03"),
    ("How long do refunds take to post?", "doc-03"),
    ("How do I cancel my subscription?", "doc-04"),
    ("Does cancelling delete my data?", "doc-04"),
    ("How do I get a downloadable archive of my data?", "doc-05"),
    ("How much is the Team plan per user?", "doc-06"),
    ("What are the API rate limits?", "doc-07"),
    ("What happens if I exceed the rate limits?", "doc-07"),
    ("Which plan includes SSO?", "doc-08"),
    ("How quickly are incidents posted after detection?", "doc-09"),
    ("How long are audit logs retained?", "doc-10"),
)

# Two eval suites, both six questions, both plausible. Suite A is what you get
# by writing down the questions people ask most; suite B is the same size and
# happens to include one of the three the regression breaks.
#
# Running both is the point. A single suite would let this chapter claim either
# "the gate works" or "the gate is useless" depending on which one it shipped,
# and both claims would be about the sample rather than about the gate.
SUITE_A = tuple(WORKLOAD[i] for i in (0, 1, 2, 3, 4, 5))
SUITE_B = tuple(WORKLOAD[i] for i in (0, 2, 4, 6, 9, 12))


def run_suite(version: Version, suite) -> tuple[int, int]:
    """Offline eval: gold labels are available here, which is the whole point
    of running it offline. Returns (passed, total)."""
    passed = 0
    for i, (question, gold) in enumerate(suite):
        result = answer(version, question, gold, f"eval-{version.label}-{i}")
        passed += int(result.correct)
    return passed, len(suite)


def configure() -> None:
    """This chapter is not about latency, so retrieval is made free.

    Left at its default 15ms, 600 requests times five rollout shapes times two
    candidates spends most of a minute sleeping to no purpose. Nothing measured
    here depends on it.
    """
    providers.configure_mock(latency="instant", seed=1337)
    retrieval.reset_retrieval()
    retrieval.config.latency = providers.LatencyProfile(base_ms=0.0)
