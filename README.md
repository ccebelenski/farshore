# FARSHORE — Terra Incognita

A turn-based strategy wargame for the terminal.

The world starts unknown. Your cities produce armies, ships, and aircraft;
your scouts push back the fog; and somewhere across the water, an enemy is
doing the same. Explore, expand, build a navy, land on the far shore, and
take every city on the map.

> Inspired by Walter Bright's *EMPIRE: Wargame of the Century*.
> FARSHORE is an independent, from-scratch implementation — it contains no
> original Empire code and is not affiliated with or endorsed by Walter
> Bright.

## Install & run

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync                 # install into a local .venv
uv run farshore         # launch the game menu
```

From the menu: pick your map size and opponent, reroll the seed if you like,
and START. F2 saves in-game; F3 loads.

Other entry points:

```sh
uv run farshore play-tui --profile STANDARD --seed 42 --opponent portfolio
uv run farshore viewer --profile SMALL      # watch two AIs fight
```

## How to play

You command every unit each turn; the AI moves when you end yours (`e`, or
auto-end once every unit has orders). Fog of war is total — you see what
your units and cities see, and remember what they've seen.

**The essentials:**

| Key | Action |
|-----|--------|
| arrows / numpad | move the selected unit (or the cursor) |
| `u` | select the unit under the cursor |
| `n` / `Tab` | next unit / peek next |
| `e` | end turn |
| `p` | set a city's production |
| `?` | full key reference |

**Standing orders** (a unit on an order keeps going until something
important happens — then it wakes and asks you):

| Key | Order |
|-----|-------|
| `v` | explore — reveal unknown territory, coastline first |
| `g` | go-to — walk to a destination |
| `t` | patrol route (ships) — shuttle between two points |
| `b` | return to base (fighters) — fly home and land |
| `d` | heading — keep walking one direction |
| `.` | sentry &nbsp;·&nbsp; `w` wake |

Units on orders never start a fight on their own: they stop one step short
of the enemy and hand you the decision.

**Winning:** capture every city. Lose all of yours and it's over.

The full rules — units, combat, fog, production, the optional
fortified-cities preset — are in [`docs/RULES.md`](docs/RULES.md).

## Opponents

- **horde** — the classic greedy swarm. Fast, relentless, predictable.
- **portfolio** — the smart one: a search-based commander that scouts,
  masses force, builds fleets, and lands on your shore.

## Development

```sh
make install   # dev environment
make check     # lint, typecheck, import-rules, tests
```

The test suite (600+ tests) and the AI arena harnesses ship with the source.

## License

MIT — see [LICENSE](LICENSE). © 2026 Chris Cebelenski.
