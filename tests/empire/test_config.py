"""`ConfigStore` tests: total load, atomic save, OS-aware default path."""

import sys
from pathlib import Path

import pytest

from empire.config import AppConfig, ConfigStore, LlmConnection


def _store(tmp_path: Path) -> ConfigStore:
    return ConfigStore(tmp_path / "config.yaml")


def test_round_trip_preserves_values_exactly(tmp_path: Path) -> None:
    config = AppConfig(
        llm=LlmConnection(
            enabled=True,
            base_url="http://localhost:8080/v1",
            api_key="sk-abc 123 = : # weird",
            model="qwen3.5-4b-instruct",
        )
    )
    _store(tmp_path).save(config)
    reloaded_store = _store(tmp_path)
    assert reloaded_store.load() == config
    assert reloaded_store.warning is None


def test_missing_file_yields_defaults_without_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.load() == AppConfig()
    assert store.warning is None
    assert not store.path.exists()  # load never creates the file


def test_empty_file_yields_defaults_without_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text("", encoding="utf-8")
    assert store.load() == AppConfig()
    assert store.warning is None


def test_garbage_yaml_yields_defaults_with_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text("{{{ not: yaml", encoding="utf-8")
    assert store.load() == AppConfig()
    assert store.warning is not None
    assert "malformed" in store.warning


def test_non_mapping_yaml_yields_defaults_with_warning(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    assert store.load() == AppConfig()
    assert store.warning is not None


def test_warning_clears_on_next_good_load(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text("{{{", encoding="utf-8")
    store.load()
    assert store.warning is not None
    store.save(AppConfig())
    assert store.load() == AppConfig()
    assert store.warning is None


def test_unknown_keys_ignored(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        "llm:\n"
        "  enabled: true\n"
        "  base_url: http://box:11434/v1\n"
        "  temperature: 0.7\n"  # unknown llm key
        "graphics:\n"  # unknown section
        "  crt_glow: true\n",
        encoding="utf-8",
    )
    config = store.load()
    assert config.llm.enabled is True
    assert config.llm.base_url == "http://box:11434/v1"
    assert config.llm.api_key == ""
    assert store.warning is None


def test_wrong_typed_values_fall_back_per_field(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.path.write_text(
        "llm:\n"
        "  enabled: 3\n"  # not a bool
        "  base_url: 42\n"  # not a string
        "  model: kept\n",
        encoding="utf-8",
    )
    config = store.load()
    assert config.llm.enabled is False
    assert config.llm.base_url == ""
    assert config.llm.model == "kept"


def test_save_creates_parent_dirs_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "nested" / "config.yaml"
    store = ConfigStore(path)
    store.save(AppConfig(llm=LlmConnection(enabled=True)))
    assert path.exists()
    assert [p.name for p in path.parent.iterdir()] == ["config.yaml"]
    assert ConfigStore(path).load().llm.enabled is True


def test_default_path_windows_appdata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    assert ConfigStore.default_path() == (
        tmp_path / "Roaming" / "farshore" / "config.yaml"
    )


def test_default_path_windows_without_appdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    assert ConfigStore.default_path() == (
        Path.home() / "AppData" / "Roaming" / "farshore" / "config.yaml"
    )


def test_default_path_xdg_config_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert ConfigStore.default_path() == (
        tmp_path / "xdg" / "farshore" / "config.yaml"
    )


def test_default_path_xdg_fallback_to_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert ConfigStore.default_path() == (
        Path.home() / ".config" / "farshore" / "config.yaml"
    )
