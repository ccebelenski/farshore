"""Prompt-experiment battery runner for the LLM general.

Runs a battery manifest (conditions x seeds) against an OpenAI-compatible
endpoint and writes one verbatim transcript pair (.json full-fidelity,
.md human-readable) per run. The endpoint is never configured in a
committed file: set OPENAI_BASE_URL (and OPENAI_API_KEY for hosted).

Usage:
    OPENAI_BASE_URL=http://host:8080/v1 uv run python lab/run_battery.py \
        lab/batteries/grounding_ab.toml
"""

from __future__ import annotations

import json
import os
import sys
import time
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI


@dataclass(frozen=True, slots=True)
class Sampling:
    """Sampling parameters shared by every run in a battery."""

    temperature: float
    top_p: float
    top_k: int
    presence_penalty: float
    max_tokens: int
    enable_thinking: bool

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Sampling:
        return Sampling(
            temperature=float(d["temperature"]),
            top_p=float(d["top_p"]),
            top_k=int(d["top_k"]),
            presence_penalty=float(d["presence_penalty"]),
            max_tokens=int(d["max_tokens"]),
            enable_thinking=bool(d["enable_thinking"]),
        )


@dataclass(frozen=True, slots=True)
class RunSpec:
    """One (condition, seed) cell of the battery: a fully assembled prompt."""

    condition: str
    seed: int
    prompt: str
    enable_thinking: bool

    @property
    def run_id(self) -> str:
        return f"{self.condition}-s{self.seed}"


@dataclass(frozen=True, slots=True)
class Battery:
    """A parsed battery manifest: every run it calls for, plus where results go."""

    name: str
    transcript_dir: Path
    sampling: Sampling
    runs: tuple[RunSpec, ...]

    @staticmethod
    def load(manifest_path: Path) -> Battery:
        manifest = tomllib.loads(manifest_path.read_text())
        battery = manifest["battery"]
        prompt_dir = Path(battery["prompt_dir"])
        seeds = [int(s) for s in battery["seeds"]]
        sampling = Sampling.from_dict(manifest["sampling"])
        runs = tuple(
            RunSpec(
                condition=cond["name"],
                seed=seed,
                prompt=(prompt_dir / cond["primer"]).read_text()
                + "\n"
                + (prompt_dir / cond["board"]).read_text(),
                # A condition may override the battery-wide thinking dial.
                enable_thinking=bool(
                    cond.get("enable_thinking", sampling.enable_thinking)
                ),
            )
            for cond in manifest["conditions"]
            for seed in seeds
        )
        return Battery(
            name=str(battery["name"]),
            transcript_dir=Path(battery["transcript_dir"]),
            sampling=sampling,
            runs=runs,
        )


@dataclass(frozen=True, slots=True)
class Transcript:
    """Everything observable about one completed run."""

    spec: RunSpec
    sampling: Sampling
    response: dict[str, Any]  # full model_dump of the API response
    duration_s: float

    @property
    def _message(self) -> dict[str, Any]:
        return self.response["choices"][0]["message"]

    @property
    def reasoning(self) -> str | None:
        return self._message.get("reasoning_content")

    @property
    def answer(self) -> str:
        return self._message.get("content") or ""

    @property
    def finish_reason(self) -> str:
        return str(self.response["choices"][0].get("finish_reason"))

    def write(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        base = directory / self.spec.run_id
        base.with_suffix(".json").write_text(
            json.dumps(
                {
                    "condition": self.spec.condition,
                    "seed": self.spec.seed,
                    "enable_thinking": self.spec.enable_thinking,
                    "sampling": asdict(self.sampling),
                    "duration_s": round(self.duration_s, 1),
                    "prompt": self.spec.prompt,
                    "response": self.response,
                },
                indent=2,
                default=str,
            )
        )
        base.with_suffix(".md").write_text(self._render_md())

    def _render_md(self) -> str:
        usage = self.response.get("usage") or {}
        converged = "" if self.finish_reason == "stop" else (
            f"\n> **WARNING: finish_reason={self.finish_reason} — did not converge**\n"
        )
        thinking = (
            f"## Thinking\n\n{self.reasoning}\n\n" if self.reasoning else ""
        )
        return (
            f"# {self.spec.run_id}\n\n"
            f"- model (as reported): `{self.response.get('model')}`\n"
            f"- seed: {self.spec.seed} · duration: {self.duration_s:.0f}s · "
            f"tokens: {usage.get('prompt_tokens')}+{usage.get('completion_tokens')} · "
            f"finish: {self.finish_reason}\n"
            f"{converged}\n"
            f"{thinking}"
            f"## Answer\n\n{self.answer}\n"
        )


class BatteryRunner:
    """Drives every run in a battery sequentially against one endpoint."""

    def __init__(self, battery: Battery, client: OpenAI, model: str) -> None:
        self._battery = battery
        self._client = client
        self._model = model

    def run(self) -> None:
        total = len(self._battery.runs)
        for i, spec in enumerate(self._battery.runs, 1):
            print(f"[{i}/{total}] {spec.run_id} ...", flush=True)
            transcript = self._execute(spec)
            transcript.write(self._battery.transcript_dir)
            print(
                f"[{i}/{total}] {spec.run_id} done in {transcript.duration_s:.0f}s "
                f"(finish={transcript.finish_reason})",
                flush=True,
            )

    def _execute(self, spec: RunSpec) -> Transcript:
        s = self._battery.sampling
        started = time.monotonic()
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": spec.prompt}],
            temperature=s.temperature,
            top_p=s.top_p,
            presence_penalty=s.presence_penalty,
            max_tokens=s.max_tokens,
            seed=spec.seed,
            timeout=1800,
            extra_body={
                "top_k": s.top_k,
                "chat_template_kwargs": {"enable_thinking": spec.enable_thinking},
            },
        )
        return Transcript(
            spec=spec,
            sampling=s,
            response=response.model_dump(),
            duration_s=time.monotonic() - started,
        )


class Lab:
    """Entry point: resolve endpoint + model, load the manifest, run it."""

    def main(self, argv: list[str]) -> int:
        if len(argv) != 2:
            print(__doc__, file=sys.stderr)
            return 2
        if not os.environ.get("OPENAI_BASE_URL"):
            print("OPENAI_BASE_URL is not set", file=sys.stderr)
            return 2
        client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY", "none"))
        model = self._served_model(client)
        battery = Battery.load(Path(argv[1]))
        print(f"battery {battery.name}: {len(battery.runs)} runs against {model}")
        BatteryRunner(battery, client, model).run()
        return 0

    def _served_model(self, client: OpenAI) -> str:
        """The endpoint's reported model id — logged per transcript (the BYO
        trust boundary: unverifiable, but catches the wrong-model mistake)."""
        return next(iter(client.models.list())).id


if __name__ == "__main__":
    raise SystemExit(Lab().main(sys.argv))
