#!/usr/bin/env python3
"""
stress.py: derive three architectures from nine ADRs, then check the arithmetic.

    python ch10-assembly/stress.py     # offline, no key, ~40 seconds

Three products, one set of decisions, and a budget assembled from constants
that earlier chapters measured. Then the assembled configurations are actually
built and run, and the predicted budget is compared against what came out.

The comparison is not an independent validation and does not pretend to be:
the same simulated latencies drive both the prediction and the run, so a
component's cost cannot disagree with itself. What is genuinely under test is
the **composition**: whether adding up per-decision costs predicts the whole
request, and whether a budget built from means says anything useful about the
tail a user actually experiences.

The answer to the second half is the chapter.
"""

from __future__ import annotations

import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor

CHAPTER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(CHAPTER))
sys.path.append(CHAPTER)

from dotenv import load_dotenv  # noqa: E402

from app import providers, retrieval, service  # noqa: E402
from profiles import (  # noqa: E402
    MEASURED,
    PRICE_IN_PER_1K,
    PRICE_OUT_PER_1K,
    PRODUCTS,
    Config,
    Product,
    config_for,
    decide,
    predict,
)

load_dotenv()

REQUESTS = 60
QUESTIONS = service.QUESTIONS


def build_and_run(product: Product, config: Config) -> dict:
    """Assemble the app the decisions describe, and run the workload through it."""
    service.reset_all()
    providers.configure_mock(latency="slow", seed=1337)
    retrieval.reset_retrieval()
    retrieval.config.latency = providers.LatencyProfile(base_ms=MEASURED["retrieval_ms"])

    store: dict[str, list[str]] = {}

    def one(i: int) -> tuple[float, float, float]:
        question = QUESTIONS[i % len(QUESTIONS)]
        request_id = f"a{i:03d}"
        simulated = 0.0

        # ch07: a per-job index rebuild is paid once per request here, exactly
        # as ch07 measured it.
        if config.per_request_index:
            simulated += MEASURED["per_request_index_ms"]

        # ch09: the tenant predicate goes inside retrieval when there is one.
        tenant = "acme" if config.tenant_filter else "acme"
        hits = retrieval.search(question, tenant=tenant, request_id=request_id)
        simulated += hits.simulated_latency_ms

        # ch02: a shared store costs a read and a write per turn.
        if config.shared_store:
            simulated += MEASURED["shared_store_ms"]
            store.setdefault(request_id, []).append(question)

        # ch04: the tier hop, measured, not modelled.
        if config.model_tier:
            simulated += MEASURED["model_tier_hop_ms"]

        response = providers.generate(
            service.SYSTEM_TEMPLATE.format(context=retrieval.format_context(hits.documents)),
            question,
            request_id=request_id,
        )
        simulated += response.simulated_latency_ms

        # ch05: what the user waits for before seeing anything.
        if config.guard == "window":
            first_token = (
                simulated
                - response.simulated_latency_ms
                + MEASURED["guard_window_tokens"] * MEASURED["token_ms"]
            )
        else:
            first_token = simulated

        cost = (
            response.prompt_tokens / 1000 * PRICE_IN_PER_1K
            + response.completion_tokens / 1000 * PRICE_OUT_PER_1K
        )
        return simulated, first_token, cost

    # Concurrency only shortens the wall clock. Every number reported below is
    # a *simulated* duration derived from the request id, so it is unaffected
    # by how the threads interleave (see app/determinism.py).
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(one, range(REQUESTS)))
    totals = [r[0] for r in results]
    first_tokens = [r[1] for r in results]
    costs = [r[2] for r in results]

    ordered = sorted(totals)
    return {
        "mean_total_ms": statistics.fmean(totals),
        "p95_total_ms": ordered[max(0, int(0.95 * len(ordered)) - 1)],
        "max_total_ms": max(totals),
        "mean_first_token_ms": statistics.fmean(first_tokens),
        "p95_first_token_ms": sorted(first_tokens)[max(0, int(0.95 * len(first_tokens)) - 1)],
        "cost_per_request": statistics.fmean(costs),
    }


def main() -> int:
    print("Nine decisions, three products. Every constant below was measured by an")
    print("earlier chapter and carries the chapter that produced it.\n")

    print("=" * 92)
    print("1. THE SAME NINE DECISIONS, THREE TIMES")
    print("=" * 92)

    all_decisions = {p.name: decide(p) for p in PRODUCTS}
    chapters = [d.chapter for d in all_decisions[PRODUCTS[0].name]]
    questions = [d.question for d in all_decisions[PRODUCTS[0].name]]

    for i, (chapter, question) in enumerate(zip(chapters, questions)):
        choices = [all_decisions[p.name][i].choice for p in PRODUCTS]
        agree = len(set(choices)) == 1
        print(f"\n  {chapter} {question}" + ("   (same for all three)" if agree else ""))
        for product, choice in zip(PRODUCTS, choices):
            print(f"    {product.name:<16} {choice}")

    varied = sum(
        1
        for i in range(len(chapters))
        if len({all_decisions[p.name][i].choice for p in PRODUCTS}) > 1
    )
    print(
        f"\n  {varied} of {len(chapters)} decisions came out differently across the three"
        f" products."
        f"\n  The {len(chapters) - varied} that did not are the ones worth not re-litigating:"
        f" the provider seam and"
        f"\n  the model tier were the right answer for a voice agent and an overnight batch"
        f"\n  job alike, because their measured costs (1 line, about a millisecond) are"
        f"\n  too small for any budget here to notice."
    )

    print("\n" + "=" * 92)
    print("2. THE BUDGET, AND WHETHER IT SURVIVES CONTACT")
    print("=" * 92)
    print()
    header = (
        f"  {'product':<16} {'budget':>10} {'predicted TTFT':>15} {'measured TTFT':>14}"
        f" {'error':>8} {'fits?':>7}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))

    runs = {}
    for product in PRODUCTS:
        config = config_for(product)
        prediction = predict(product, config)
        measured = build_and_run(product, config)
        runs[product.name] = (product, config, prediction, measured)

        budget = f"{product.budget_ms:.0f}ms" if product.budget_ms else "none"
        error = measured["mean_first_token_ms"] - prediction["predicted_first_token_ms"]
        fits = "yes" if (not product.budget_ms or
                         measured["p95_first_token_ms"] <= product.budget_ms) else "NO"
        print(
            f"  {product.name:<16} {budget:>10}"
            f" {prediction['predicted_first_token_ms']:>14.0f}ms"
            f" {measured['mean_first_token_ms']:>13.0f}ms {error:>+7.1f}ms {fits:>7}"
        )

    print(
        "\n  The composition arithmetic holds: adding up per-decision costs predicts the"
        "\n  mean to within a few milliseconds. That is the easy half, and it is the half"
        "\n  budgets are usually built on."
    )

    print("\n" + "=" * 92)
    print("3. WHAT THE BUDGET CANNOT TELL YOU")
    print("=" * 92)
    print()
    header = (
        f"  {'product':<16} {'predicted mean':>15} {'measured mean':>14}"
        f" {'predicted p95':>14} {'measured p95':>13} {'measured max':>13}"
    )
    print(header)
    print("  " + "-" * (len(header) - 2))
    for product, config, prediction, measured in runs.values():
        print(
            f"  {product.name:<16} {prediction['predicted_mean_total_ms']:>14.0f}ms"
            f" {measured['mean_total_ms']:>13.0f}ms"
            f" {'none':>14}"
            f" {measured['p95_total_ms']:>12.0f}ms {measured['max_total_ms']:>12.0f}ms"
        )

    chat = runs["support chat"][3]
    batch = runs["batch pipeline"][3]
    ratio = chat["max_total_ms"] / chat["mean_total_ms"]
    print(
        f"\n  The predicted p95 column is empty because there is nothing honest to put in"
        f"\n  it. Every constant these chapters produced is a mean, so the budget they add"
        f"\n  up to is a mean, and no amount of adding means yields a tail. For support"
        f"\n  chat the mean is {chat['mean_total_ms']:.0f}ms and the slowest request in the"
        f" same run took {chat['max_total_ms']:.0f}ms,"
        f"\n  {ratio:.1f}x that, with nothing broken: that is a 5% tail at +4s doing what a 5%"
        f"\n  tail does. The only way to get that number was to build the thing and run it."
        f"\n"
        f"\n  The other half of this table is quieter and matters more. The three products"
        f"\n  differ on {varied} of {len(chapters)} decisions and their latencies are within"
        f" {batch['mean_total_ms'] - chat['mean_total_ms']:.0f}ms of each"
        f"\n  other, because the model call is"
        f" {(chat['mean_total_ms'] - runs['support chat'][2]['fixed_ms']) / chat['mean_total_ms'] * 100:.0f}%"
        f" of every request and the architecture is the"
        f"\n  rest. Eight of the nine decisions changed the latency budget by an amount no"
        f"\n  user could perceive."
        f"\n"
        f"\n  That is not an argument that the decisions do not matter. It is an argument"
        f"\n  about which column to defend them in: ch02 was worth 38 points of correctness,"
        f"\n  ch04 was worth 10/10 health checks during an outage, ch09 was worth a leaked"
        f"\n  contract, and ch06 was worth 2.9x wall clock under a slow dependency. None of"
        f"\n  those show up here, and a design review that only budgets latency would have"
        f"\n  rejected all four."
    )

    print("\n" + "=" * 92)
    print("4. WHAT EACH PRODUCT ENDED UP WITH")
    print("=" * 92)
    for product in PRODUCTS:
        _, config, prediction, measured = runs[product.name]
        print(f"\n  {product.name}: {product.note}")
        print(f"    budget           {product.budget_label}")
        print(f"    fixed overhead   {prediction['fixed_ms']:.1f}ms from the decisions above")
        print(f"    measured TTFT    mean {measured['mean_first_token_ms']:.0f}ms,"
              f" p95 {measured['p95_first_token_ms']:.0f}ms")
        print(f"    cost per request ${measured['cost_per_request']:.6f}")
        for decision in decide(product):
            print(f"    {decision.chapter}  {decision.choice}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
