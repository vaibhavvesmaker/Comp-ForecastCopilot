"""
The AI Narrative Layer — Layer 3 of the architecture described in the
README, and the only layer in this system allowed to call an LLM.

Hard boundary, enforced by this module's own type signatures, not just
a comment: every public method here accepts CALCULATION-ENGINE OUTPUTS
(CommissionRunResult, ForecastRunResult, ScenarioSet, WhatIfResult,
HealthReport) — never Deal, RepQuota, or CommissionPlanRule. There is no
code path in this file that can see a raw deal record or hallucinate a
number that didn't come out of Layer 0-2's deterministic math. The model
is handed pre-computed audit trails as text and asked to explain them in
plain English; it never performs arithmetic, and it never has write
access to any payout, forecast, or score.

Each public method builds a prompt from the structured result objects
already produced elsewhere in this codebase, calls
AnthropicNarrativeClient.generate() (app/narrative/client.py) exactly
once, and returns the model's prose untouched. Tests mock
`AnthropicNarrativeClient.generate` directly, so this layer's tests never
require a real ANTHROPIC_API_KEY or network access.
"""

from __future__ import annotations

import json

from app.calculation.models import CommissionRunResult
from app.forecasting.models import ForecastRunResult
from app.health.models import HealthReport
from app.narrative.client import AnthropicNarrativeClient
from app.scenarios.models import ScenarioSet, WhatIfResult

SYSTEM_PROMPT = (
    "You are the narrative layer of a sales compensation and forecasting system. "
    "You are given ONLY pre-computed, already-correct calculation results and their "
    "audit trails - you never see raw deal records and you never perform arithmetic "
    "yourself. Explain the numbers you're given in clear, plain English for a RevOps "
    "or sales-leadership audience. Cite the specific deal IDs, plan IDs, and tiers "
    "named in the data when they're relevant. Never invent a number, deal, or rule "
    "that is not present in the data you were given. If the data doesn't answer the "
    "question, say so instead of guessing."
)


class NarrativeLayer:
    def __init__(self, client: AnthropicNarrativeClient | None = None):
        self.client = client or AnthropicNarrativeClient()

    def explain_forecast_variance(
        self, previous: ForecastRunResult, current: ForecastRunResult
    ) -> str:
        self._require_type("previous", previous, ForecastRunResult)
        self._require_type("current", current, ForecastRunResult)
        context = {
            "previous_forecast": previous.model_dump(mode="json"),
            "current_forecast": current.model_dump(mode="json"),
        }
        prompt = (
            "The revenue forecast changed between two runs. Explain what changed and "
            "why, citing the specific deals and stage weights responsible.\n\n"
            f"{json.dumps(context, indent=2, default=str)}"
        )
        return self.client.generate(SYSTEM_PROMPT, prompt)

    def explain_commission_run(self, result: CommissionRunResult) -> str:
        self._require_type("result", result, CommissionRunResult)
        prompt = (
            "Summarize this commission run for a sales leader: total paid, which "
            "deals drove the largest payouts, and note any skipped deals and why.\n\n"
            f"{result.model_dump_json(indent=2)}"
        )
        return self.client.generate(SYSTEM_PROMPT, prompt)

    def explain_scenarios(self, scenarios: ScenarioSet) -> str:
        self._require_type("scenarios", scenarios, ScenarioSet)
        prompt = (
            "Explain the spread between the best, commit, and worst-case revenue "
            "scenarios below, and what's driving the gap between them.\n\n"
            f"{scenarios.model_dump_json(indent=2)}"
        )
        return self.client.generate(SYSTEM_PROMPT, prompt)

    def explain_what_if(self, result: WhatIfResult) -> str:
        self._require_type("result", result, WhatIfResult)
        prompt = (
            "Explain the impact of this what-if scenario on commission payouts and "
            "the revenue forecast, and why one moved and the other may not have.\n\n"
            f"{result.model_dump_json(indent=2)}"
        )
        return self.client.generate(SYSTEM_PROMPT, prompt)

    def ask(
        self,
        question: str,
        *,
        commission: CommissionRunResult | None = None,
        forecast: ForecastRunResult | None = None,
        health: HealthReport | None = None,
        scenarios: ScenarioSet | None = None,
        what_if: WhatIfResult | None = None,
    ) -> str:
        provided = {
            "commission": (commission, CommissionRunResult),
            "forecast": (forecast, ForecastRunResult),
            "health": (health, HealthReport),
            "scenarios": (scenarios, ScenarioSet),
            "what_if": (what_if, WhatIfResult),
        }
        for name, (value, expected_type) in provided.items():
            if value is not None:
                self._require_type(name, value, expected_type)

        context = {
            name: pair[0].model_dump(mode="json")
            for name, pair in provided.items()
            if pair[0] is not None
        }
        if not context:
            raise ValueError(
                "ask() requires at least one calculation-engine result as context "
                "(commission, forecast, health, scenarios, or what_if)."
            )

        prompt = (
            f"Answer this question using ONLY the calculated data below. "
            f"Question: {question}\n\nData:\n{json.dumps(context, indent=2, default=str)}"
        )
        return self.client.generate(SYSTEM_PROMPT, prompt)

    @staticmethod
    def _require_type(name: str, value: object, expected_type: type) -> None:
        """Runtime enforcement of the Layer 3 boundary.

        Type hints alone don't stop a caller from passing a raw Deal (or
        anything else) into a slot meant for a calculation-engine result
        — Python doesn't check them at runtime. This does, so the
        boundary described in this module's docstring ("never raw deal
        data") actually holds, not just in code review.
        """
        if not isinstance(value, expected_type):
            raise TypeError(
                f"{name}= must be a {expected_type.__name__} (a calculation-engine "
                f"output), got {type(value).__name__}. The narrative layer never "
                "accepts raw deal/quota/plan data."
            )
