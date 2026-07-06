"""`build_general`: the one wiring point between app config and the AI seat.

Reads `AppConfig.llm` and returns the opponent for the smart seat: the
`LlmGeneralController` (a `PortfolioAI` under doctrine epochs) when the LLM
connection is enabled and configured, else plain `PortfolioAI` — byte-
identical to the pre-general default. The disabled path never imports,
constructs, or touches a client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from empire.config import AppConfig
    from empire.contracts.controller import AIController


def build_general(config: AppConfig) -> AIController:
    """The shipped smart opponent for this config.

    Total: never raises for any config value — a bad endpoint surfaces later
    as a degraded epoch (the controller's fail-safe), not a launch failure.
    """
    from empire.ai.search.portfolio import PortfolioAI

    llm = config.llm
    if not (llm.enabled and llm.base_url):
        return PortfolioAI()
    from empire.ai.general.client import ChatClient
    from empire.ai.general.controller import LlmGeneralController

    return LlmGeneralController(client=ChatClient(llm), executor=PortfolioAI())
