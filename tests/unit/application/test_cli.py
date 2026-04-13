"""Unit tests for CLI interface."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ridestream.entry_points.cli import cli

SIM_PATH = "ridestream.application.ride_simulator.RideSimulator"
FACTORY_PATH = "ridestream.adapters.cloud_provider_factory.CloudProviderFactory"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


# ── Top-level ─────────────────────────────────────────────


class TestCLIGroup:
    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "RideStream v2" in result.output
        assert "simulate" in result.output
        assert "pipeline" in result.output
        assert "metrics" in result.output
        assert "config" in result.output

    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "2.0.0" in result.output

    def test_no_args_shows_usage(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, [])
        # click groups show usage and exit 0 or 2 depending on config
        assert "Usage" in result.output or "RideStream" in result.output


# ── simulate ──────────────────────────────────────────────


class TestSimulateCommand:
    @patch(FACTORY_PATH)
    @patch(SIM_PATH)
    def test_simulate_default(
        self, mock_sim_cls: MagicMock, mock_factory_cls: MagicMock, runner: CliRunner
    ) -> None:
        mock_sim = MagicMock()
        mock_sim.run.return_value = [MagicMock()] * 100
        mock_sim_cls.return_value = mock_sim
        mock_factory_cls.return_value = MagicMock()

        result = runner.invoke(cli, ["simulate"])
        assert result.exit_code == 0
        assert "100" in result.output
        mock_sim.run.assert_called_once_with(num_rides=100)

    @patch(FACTORY_PATH)
    @patch(SIM_PATH)
    def test_simulate_custom_count(
        self, mock_sim_cls: MagicMock, mock_factory_cls: MagicMock, runner: CliRunner
    ) -> None:
        mock_sim = MagicMock()
        mock_sim.run.return_value = [MagicMock()] * 10
        mock_sim_cls.return_value = mock_sim
        mock_factory_cls.return_value = MagicMock()

        result = runner.invoke(cli, ["simulate", "--num-rides", "10"])
        assert result.exit_code == 0
        mock_sim.run.assert_called_once_with(num_rides=10)

    @patch(FACTORY_PATH)
    @patch(SIM_PATH)
    def test_simulate_short_flag(
        self, mock_sim_cls: MagicMock, mock_factory_cls: MagicMock, runner: CliRunner
    ) -> None:
        mock_sim = MagicMock()
        mock_sim.run.return_value = [MagicMock()] * 5
        mock_sim_cls.return_value = mock_sim
        mock_factory_cls.return_value = MagicMock()

        result = runner.invoke(cli, ["simulate", "-n", "5"])
        assert result.exit_code == 0
        mock_sim.run.assert_called_once_with(num_rides=5)

    def test_simulate_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["simulate", "--help"])
        assert result.exit_code == 0
        assert "--num-rides" in result.output


# ── pipeline ──────────────────────────────────────────────


class TestPipelineCommand:
    def test_pipeline_default(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["pipeline"])
        assert result.exit_code == 0
        assert "all" in result.output

    def test_pipeline_silver(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["pipeline", "--task", "silver"])
        assert result.exit_code == 0
        assert "silver" in result.output

    def test_pipeline_invalid_task(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["pipeline", "--task", "invalid"])
        assert result.exit_code != 0

    def test_pipeline_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["pipeline", "--help"])
        assert result.exit_code == 0
        assert "--task" in result.output


# ── metrics ───────────────────────────────────────────────


class TestMetricsCommand:
    @patch(FACTORY_PATH)
    def test_metrics_default_days(self, mock_factory_cls: MagicMock, runner: CliRunner) -> None:
        mock_engine = MagicMock()
        mock_engine.execute.return_value = [{"ride_date": "2026-04-10", "rides_completed": 42}]
        mock_factory = MagicMock()
        mock_factory.create_query_engine.return_value = mock_engine
        mock_factory_cls.return_value = mock_factory

        result = runner.invoke(cli, ["metrics"])
        assert result.exit_code == 0
        assert "42" in result.output

    @patch(FACTORY_PATH)
    def test_metrics_empty(self, mock_factory_cls: MagicMock, runner: CliRunner) -> None:
        mock_engine = MagicMock()
        mock_engine.execute.return_value = []
        mock_factory = MagicMock()
        mock_factory.create_query_engine.return_value = mock_engine
        mock_factory_cls.return_value = mock_factory

        result = runner.invoke(cli, ["metrics", "--days", "30"])
        assert result.exit_code == 0
        assert "No metrics found" in result.output

    @patch(FACTORY_PATH)
    def test_metrics_query_error(self, mock_factory_cls: MagicMock, runner: CliRunner) -> None:
        mock_engine = MagicMock()
        mock_engine.execute.side_effect = RuntimeError("connection refused")
        mock_factory = MagicMock()
        mock_factory.create_query_engine.return_value = mock_engine
        mock_factory_cls.return_value = mock_factory

        result = runner.invoke(cli, ["metrics"])
        assert result.exit_code != 0

    def test_metrics_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["metrics", "--help"])
        assert result.exit_code == 0
        assert "--days" in result.output


# ── config ────────────────────────────────────────────────


class TestConfigCommand:
    def test_config_default(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["config"])
        assert result.exit_code == 0
        assert "local" in result.output
        assert "kafka_bootstrap_servers" in result.output

    def test_config_env_override(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["config", "--env", "production"])
        assert result.exit_code == 0
        assert "production" in result.output

    def test_config_json_format(self, runner: CliRunner) -> None:
        import json

        result = runner.invoke(cli, ["config"])
        data = json.loads(result.output)
        assert "environment" in data
        assert "kafka_bootstrap_servers" in data

    def test_config_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "--env" in result.output
