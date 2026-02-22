"""Unit tests for effort level configuration."""

import sys

sys.path.insert(0, "services/orchestrator")

from services.orchestrator.src.prompts.effort_levels import (
    EFFORT_CONFIGS,
    EffortConfig,
    EffortLevel,
    get_effort_config,
)


class TestEffortLevel:
    """Tests for EffortLevel enum."""

    def test_values(self) -> None:
        assert EffortLevel.LOW.value == "low"
        assert EffortLevel.MEDIUM.value == "medium"
        assert EffortLevel.HIGH.value == "high"

    def test_all_levels_have_config(self) -> None:
        for level in EffortLevel:
            assert level in EFFORT_CONFIGS


class TestEffortConfig:
    """Tests for EffortConfig NamedTuple."""

    def test_low_config(self) -> None:
        config = EFFORT_CONFIGS[EffortLevel.LOW]
        assert config.max_iterations == 3
        assert "directly" in config.prompt_section.lower()

    def test_medium_config(self) -> None:
        config = EFFORT_CONFIGS[EffortLevel.MEDIUM]
        assert config.max_iterations == 10
        assert "proactively" in config.prompt_section.lower()

    def test_high_config(self) -> None:
        config = EFFORT_CONFIGS[EffortLevel.HIGH]
        assert config.max_iterations == 50
        assert "deep research" in config.prompt_section.lower()


class TestGetEffortConfig:
    """Tests for get_effort_config() helper."""

    def test_low(self) -> None:
        config = get_effort_config("low")
        assert config.max_iterations == 3

    def test_medium(self) -> None:
        config = get_effort_config("medium")
        assert config.max_iterations == 10

    def test_high(self) -> None:
        config = get_effort_config("high")
        assert config.max_iterations == 50

    def test_none_defaults_to_medium(self) -> None:
        config = get_effort_config(None)
        assert config.max_iterations == 10

    def test_empty_string_defaults_to_medium(self) -> None:
        config = get_effort_config("")
        assert config.max_iterations == 10

    def test_invalid_defaults_to_medium(self) -> None:
        config = get_effort_config("invalid")
        assert config.max_iterations == 10

    def test_case_insensitive_upper(self) -> None:
        config = get_effort_config("HIGH")
        assert config.max_iterations == 50

    def test_case_insensitive_mixed(self) -> None:
        config = get_effort_config("Low")
        assert config.max_iterations == 3

    def test_returns_effort_config_type(self) -> None:
        config = get_effort_config("medium")
        assert isinstance(config, EffortConfig)
        assert isinstance(config.max_iterations, int)
        assert isinstance(config.prompt_section, str)
