"""
app/service.py: the reference app every chapter rebuilds.

One request: retrieve, ask the model, sometimes call a tool, answer. Roughly
250 lines of behaviour that half the LLM apps in production are a bigger
version of.

Read this once and you have read the app. That is the requirement, because
each chapter rewrites its *shape* and the reader has to be able to hold both
versions in their head at the same time. Anything that makes this file grow is
a chapter's problem, not this file's.

The rule that makes the measurements mean anything: **chapters change the
shape, never the behaviour.** Same corpus, same prompt, same tool, same
answers. When two variants score differently on correctness, it is because of
where the boundaries were drawn, not because one of them got a better prompt.

Session state lives in a module-level dict here, which is the default that
ch02 will demonstrate is wrong the moment there are two workers. Starting from
the shape that most prototypes actually have is deliberate: the chapter has
something real to break, and the reader recognizes their own code in it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from app import providers, retrieval, tools
from app.corpus import DOCUMENTS_BY_ID

SYSTEM_TEMPLATE = """You are the support assistant for a SaaS product.
Answer only from the context below. If the context does not contain the answer,
say you do not know and point the user at support@example.com.

CONTEXT:
{context}"""

# In-process, unbounded, and lost on restart. Every one of those is a decision
# this repo eventually charges for.
SESSIONS: dict[str, list[dict]] = {}


def reset_sessions() -> None:
    SESSIONS.clear()


@dataclass
class Timings:
    """Measured wall clock and simulated intent, side by side, always.

    Reporting only the measured number would make the repo's headline claim
    (reproducible stressors) untestable, since wall clock never repeats
    exactly. Reporting only the simulated number would let a real bottleneck
    hide. So both, everywhere, and any gap between them is the harness's own
    overhead, which is a number worth watching when a chapter claims a
    structure is cheap.
    """

    retrieval_ms: float = 0.0
    provider_ms: float = 0.0
    tool_ms: float = 0.0
    total_ms: float = 0.0
    simulated_ms: float = 0.0


@dataclass
class Answer:
    request_id: str
    text: str
    sources: tuple[str, ...] = ()
    tenant: str = "acme"
    provider_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    timings: Timings = field(default_factory=Timings)
    degraded: bool = False
    degraded_reason: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _wants_ticket(question: str) -> bool:
    """Deterministic routing, standing in for the model deciding to use a tool.

    A real app lets the model choose. Here the choice is a keyword rule, so
    that tool-call counts are identical across variants and a difference in
    tool traffic is always attributable to the architecture rather than to the
    model changing its mind between runs. The agents dive is where model-driven
    routing belongs; letting it vary here would put noise underneath every
    chapter's headline number.
    """
    q = question.lower()
    return any(w in q for w in ("open a ticket", "escalate", "talk to a human", "file a bug"))


def handle(
    question: str,
    *,
    tenant: str = "acme",
    session_id: str = "default",
    request_id: str = "req",
    timeout_ms: float | None = None,
) -> Answer:
    """One request, start to finish, in the simplest shape that works.

    No retries, no fallback, no queue, no cache. Every one of those is a
    chapter, and adding any of them here in advance would rob a chapter of its
    baseline. This function is deliberately the version a competent engineer
    writes on day one.
    """
    answer = Answer(request_id=request_id, text="", tenant=tenant)
    started = time.perf_counter()

    history = SESSIONS.setdefault(session_id, [])
    history.append({"role": "user", "content": question})

    try:
        hits = retrieval.search(question, tenant=tenant, request_id=request_id)
        answer.timings.retrieval_ms = hits.latency_ms
        answer.timings.simulated_ms += hits.simulated_latency_ms
        answer.sources = tuple(d.doc_id for d in hits.documents)
        context = retrieval.format_context(hits.documents)

        response = providers.generate(
            SYSTEM_TEMPLATE.format(context=context),
            question,
            request_id=request_id,
            timeout_ms=timeout_ms,
        )
        answer.provider_calls += 1
        answer.timings.provider_ms += response.latency_ms
        answer.timings.simulated_ms += response.simulated_latency_ms
        answer.prompt_tokens += response.prompt_tokens
        answer.completion_tokens += response.completion_tokens
        answer.text = response.text

        if _wants_ticket(question):
            result = tools.open_ticket(question, tenant=tenant, request_id=request_id)
            answer.timings.tool_ms += result.latency_ms
            answer.timings.simulated_ms += result.simulated_latency_ms
            answer.text = f"{answer.text} I have {result.output}."

        history.append({"role": "assistant", "content": answer.text})

    except (
        retrieval.RetrievalUnavailable,
        tools.ToolUnavailable,
        providers.TransientProviderError,
    ) as exc:
        # The day-one shape: a dependency fails, the request fails. Chapter 6
        # is the argument about whether that is bad, and the answer is not the
        # obvious one, because the alternative can answer confidently from no
        # sources at all.
        answer.error = f"{type(exc).__name__}: {exc}"

    answer.timings.total_ms = (time.perf_counter() - started) * 1000.0
    return answer


def turns(session_id: str = "default") -> int:
    """How many messages this session remembers. Chapter 2 watches this number
    go to zero on a request that lands on the wrong worker."""
    return len(SESSIONS.get(session_id, []))


def reset_all() -> None:
    """Put every module back to its default. Stressors call this between runs,
    because a fault profile that leaks from one run into the next produces the
    single most confusing class of bad measurement in this repo."""
    providers.reset_mock()
    retrieval.reset_retrieval()
    tools.reset_tools()
    reset_sessions()


def answered_from(text: str, doc_id: str) -> bool:
    """Did this answer actually come out of that document?

    Not "was the right document retrieved". Retrieval returns three documents
    and the gold one is usually among them even when the query is wrong, so
    scoring on the retrieved set reports success while the user is being told
    something else. Because the mock is extractive, provenance is checkable
    exactly rather than judged.

    Lives here rather than in a chapter because two chapters need the same
    definition of correct, and two definitions would make their numbers
    incomparable. Chapter 6 in particular depends on it being strict: an
    answer served from a stale cache is *not* supported by the current
    document, and must not score as correct.
    """
    document = DOCUMENTS_BY_ID.get(doc_id)
    if document is None:
        return False
    return text.rstrip(".").strip().lower() in document.text.lower()


def gold_source(question: str) -> str:
    """The document a correct answer to a canned question must come from.

    Correctness in this repo is "did the answer come from the right document",
    not "does the text look good". A judge model would put a second
    non-deterministic system underneath every measurement, and then a chapter
    claiming a 4% correctness delta could not say whether the architecture or
    the judge moved. See AUTHORING-LESSONS on holding the judge constant; the
    cheapest way to hold it constant is not to have one.
    """
    return _GOLD.get(question.strip().lower(), "")


_GOLD: dict[str, str] = {
    "how do i reset my password?": "doc-01",
    "how long is the password reset link valid?": "doc-01",
    "what happens if i lose my two-factor device?": "doc-02",
    "can i get a refund?": "doc-03",
    "how long do refunds take to post?": "doc-03",
    "how do i cancel my subscription?": "doc-04",
    "does cancelling delete my data?": "doc-04",
    "how do i export my data?": "doc-05",
    "what plans are available?": "doc-06",
    "what are the api rate limits?": "doc-07",
    "which plan includes sso?": "doc-08",
    "where do i find service status?": "doc-09",
    "how long are audit logs retained?": "doc-10",
}

QUESTIONS: tuple[str, ...] = tuple(
    q for q in (
        "How do I reset my password?",
        "How long is the password reset link valid?",
        "What happens if I lose my two-factor device?",
        "Can I get a refund?",
        "How long do refunds take to post?",
        "How do I cancel my subscription?",
        "Does cancelling delete my data?",
        "How do I export my data?",
        "What plans are available?",
        "What are the API rate limits?",
        "Which plan includes SSO?",
        "Where do I find service status?",
        "How long are audit logs retained?",
    )
)
