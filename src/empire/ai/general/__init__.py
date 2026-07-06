"""The strategic-general layer: briefing renderer, order validator/compiler,
registry, the stdlib chat client, and the in-game `LlmGeneralController`.
`build_general` (re-exported from `factory`) is the one wiring point: it
reads the LLM connection config and returns either the general (enabled +
configured) or plain `PortfolioAI` — the disabled path never constructs or
touches a client."""

from empire.ai.general.factory import build_general

__all__ = ["build_general"]
