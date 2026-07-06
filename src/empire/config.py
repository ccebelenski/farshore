"""`AppConfig` + `ConfigStore`: the app's persisted settings, one YAML file.

`AppConfig` is the frozen value the rest of the app reads; today it carries a
single nested section (`llm:` — the LLM general's connection seam per
`planning/08-llm-general.md`), with room to grow more sections later.
`ConfigStore` owns the file: an OS-appropriate config path, a *total* load
(missing file → defaults; malformed file → defaults plus a retained warning —
a bad config file must never crash the game), and an atomic save.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class LlmConnection:
    """Connection settings for the LLM general's chat-completions endpoint.

    One seam, two modes (planning/08 §Deployment): `base_url` is an
    OpenAI-compatible server — managed (we spawned it) or BYO (the user's).
    An empty `model` means "whatever the server reports"; the id is logged,
    never trusted. `api_key` may be blank for local servers.
    """

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    model: str = ""


@dataclass(frozen=True, slots=True)
class AppConfig:
    """The whole persisted configuration. Sections are frozen values too."""

    llm: LlmConnection = field(default_factory=LlmConnection)


class ConfigStore:
    """Loads and saves `AppConfig` as YAML at the platform config path.

    `load` never raises: any unreadable or malformed file yields defaults,
    with the reason retained on `warning` for the UI to surface. `save`
    writes atomically (temp file in the same directory + rename) and creates
    the parent directory as needed. YAML itself is imported lazily so the
    TUI's import-time hot path never pays for it.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path if path is not None else self.default_path()
        self._warning: str | None = None

    @staticmethod
    def default_path() -> Path:
        """`%APPDATA%/farshore/config.yaml` on Windows; XDG (default
        `~/.config`) `/farshore/config.yaml` everywhere else."""
        if sys.platform.startswith("win"):
            appdata = os.environ.get("APPDATA", "")
            root = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        else:
            xdg = os.environ.get("XDG_CONFIG_HOME", "")
            root = Path(xdg) if xdg else Path.home() / ".config"
        return root / "farshore" / "config.yaml"

    @property
    def path(self) -> Path:
        return self._path

    @property
    def warning(self) -> str | None:
        """Why the last `load` fell back to defaults; None when it didn't."""
        return self._warning

    # ---- load ----------------------------------------------------------------

    def load(self) -> AppConfig:
        """The stored config, or defaults. Total: never raises."""
        self._warning = None
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return AppConfig()
        except OSError as exc:
            self._warning = f"could not read {self._path}: {exc}"
            return AppConfig()

        import yaml

        try:
            payload = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            self._warning = f"malformed config {self._path}: {exc}"
            return AppConfig()
        if payload is None:  # an empty file is a valid "all defaults"
            return AppConfig()
        if not isinstance(payload, dict):
            self._warning = f"malformed config {self._path}: expected a mapping"
            return AppConfig()
        return AppConfig(llm=self._llm_from(payload.get("llm")))

    @staticmethod
    def _llm_from(section: Any) -> LlmConnection:
        """An `LlmConnection` from a parsed `llm:` section. Unknown keys are
        ignored; wrong-typed or absent values keep their defaults."""
        if not isinstance(section, dict):
            return LlmConnection()
        defaults = LlmConnection()

        def _str(key: str, fallback: str) -> str:
            value = section.get(key)
            return value if isinstance(value, str) else fallback

        enabled = section.get("enabled")
        return LlmConnection(
            enabled=enabled if isinstance(enabled, bool) else defaults.enabled,
            base_url=_str("base_url", defaults.base_url),
            api_key=_str("api_key", defaults.api_key),
            model=_str("model", defaults.model),
        )

    # ---- save ----------------------------------------------------------------

    def save(self, config: AppConfig) -> None:
        """Write `config` atomically, creating the directory if needed."""
        import yaml

        payload = {
            "llm": {
                "enabled": config.llm.enabled,
                "base_url": config.llm.base_url,
                "api_key": config.llm.api_key,
                "model": config.llm.model,
            }
        }
        text = yaml.safe_dump(payload, default_flow_style=False, sort_keys=False)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Same-directory temp + rename: readers see the old file or the new
        # one, never a torn write.
        tmp = self._path.with_name(self._path.name + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, self._path)
