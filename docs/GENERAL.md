# The LLM General (experimental)

An optional strategic layer for the smart opponent: a small local language
model reads a fog-honest briefing every few turns, maintains standing task
forces with objectives in its own words, and the deterministic AI executes
those orders. The game never depends on it — every failure quietly falls back
to the plain smart opponent for that turn.

## Enabling it

1. Serve an OpenAI-compatible endpoint with the supported model.
   The supported configuration is **Qwen3.5-4B** (Q8 GGUF) via llama.cpp:

   ```sh
   llama-server -m Qwen3.5-4B-UD-Q8_K_XL.gguf --port 8080
   ```

   Any OpenAI-compatible server works (BYO); the supported model is the one
   we test. Swap it and the general may behave unpredictably — that's on you.

2. In FARSHORE: main menu → **SETTINGS** → enable the general and set the
   base URL (e.g. `http://localhost:8080/v1`). API key only if your server
   needs one; model id blank means "whatever the server reports".

   Or edit the config file directly (`~/.config/farshore/config.yaml`,
   `%APPDATA%\farshore\config.yaml` on Windows):

   ```yaml
   llm:
     enabled: true
     base_url: http://localhost:8080/v1
     api_key: ""
     model: ""
   ```

3. Start a new game against the smart opponent. When the general is enabled,
   that seat is commanded by the model; disabled or unreachable, it is the
   identical classic opponent.

## What to expect

- **Epoch pauses.** The general thinks every 8 turns. On a modern GPU that is
  one to three minutes; on CPU it can be twenty or more — the model reasons
  at length before ordering. The turn completes normally either way.
- **Fail-soft, always.** Server down, model rambling past its token budget,
  orders that don't parse — the affected epoch is skipped, the standing
  orders (or the plain AI) carry on, and the general tries again next epoch.
  Nothing the model says can crash or hang the game.
- **Orders, not moves.** The model commands at task-force level ("capture the
  eastern city, taking the new transport"); the classic AI does all tactical
  execution. Infeasible orders are refused and reported back to the model in
  its next briefing.

## Sampling

Requests pin the model card's thinking-mode settings (temperature 1.0,
top_p 0.95, top_k 20, min_p 0, presence_penalty 1.5, thinking enabled).
Server-side defaults are overridden per request; no server tuning needed.
