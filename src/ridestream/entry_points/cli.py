"""RideStream v2 CLI.

Entry point for simulator, pipeline control, metrics queries, and config display.
Uses CloudProviderFactory for adapter selection — never imports adapters directly.
"""

from __future__ import annotations

import json
import sys

import click

from ridestream.config import Config, Environment


@click.group()
@click.version_option(version="2.0.0", prog_name="ridestream")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """RideStream v2 — Real-time ride event processing pipeline."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config()


# ── simulate ──────────────────────────────────────────────


@cli.command()
@click.option("--num-rides", "-n", default=100, type=int, help="Number of rides to simulate.")
@click.option("--duration", "-d", default=0, type=int, help="Duration in seconds (0=instant).")
@click.pass_context
def simulate(ctx: click.Context, num_rides: int, duration: int) -> None:  # noqa: ARG001
    """Generate and publish simulated ride events to Kafka."""
    config: Config = ctx.obj["config"]
    click.echo(f"Simulating {num_rides} rides (env={config.environment})...")

    from ridestream.adapters.cloud_provider_factory import CloudProviderFactory
    from ridestream.application.ride_simulator import RideSimulator

    factory = CloudProviderFactory(config)
    publisher = factory.create_event_publisher()

    simulator = RideSimulator(event_publisher=publisher, config=config)
    rides = simulator.run(num_rides=num_rides)

    click.echo(f"Generated {len(rides)} rides successfully.")


# ── pipeline ──────────────────────────────────────────────


@cli.command()
@click.option("--task", "-t", default="all", help="Pipeline task to run (all, silver, gold, test).")
@click.pass_context
def pipeline(ctx: click.Context, task: str) -> None:
    """Trigger a pipeline task (dbt run/test)."""
    config: Config = ctx.obj["config"]
    click.echo(f"Running pipeline task: {task} (env={config.environment})")

    valid_tasks = {"all", "silver", "gold", "test", "compile"}
    if task not in valid_tasks:
        click.echo(
            f"Error: unknown task '{task}'. Valid: {', '.join(sorted(valid_tasks))}", err=True
        )
        sys.exit(1)

    click.echo(f"Pipeline task '{task}' queued. Use Airflow UI or CLI to monitor.")


# ── metrics ───────────────────────────────────────────────


@cli.command()
@click.option("--days", "-d", default=7, type=int, help="Number of days of metrics to show.")
@click.pass_context
def metrics(ctx: click.Context, days: int) -> None:
    """Query Gold layer metrics for the last N days."""
    config: Config = ctx.obj["config"]
    click.echo(f"Querying metrics for last {days} days (env={config.environment})...")

    from ridestream.adapters.cloud_provider_factory import CloudProviderFactory

    factory = CloudProviderFactory(config)
    engine = factory.create_query_engine()

    try:
        # days is validated as int by click — safe for interpolation
        query = (
            f"SELECT * FROM hive.ridestream_dev.rides_summary "  # nosec B608
            f"WHERE ride_date >= CURRENT_DATE - INTERVAL '{days}' DAY "
            f"ORDER BY ride_date DESC LIMIT 50"
        )
        results = engine.execute(query)
        if results:
            click.echo(json.dumps(results, indent=2, default=str))
        else:
            click.echo("No metrics found for the specified period.")
    except Exception as e:
        click.echo(f"Query failed: {e}", err=True)
        sys.exit(1)


# ── config ────────────────────────────────────────────────


@cli.command("config")
@click.option("--env", type=click.Choice(["local", "staging", "production"]), default=None)
@click.pass_context
def show_config(ctx: click.Context, env: str | None) -> None:
    """Display current configuration."""
    config = Config(environment=Environment(env)) if env else ctx.obj["config"]

    data = {
        "environment": config.environment.value,
        "kafka_bootstrap_servers": config.kafka_bootstrap_servers,
        "minio_endpoint": config.minio_endpoint,
        "trino_host": config.trino_host,
        "trino_port": config.trino_port,
        "dbt_target": config.dbt_target,
        "log_level": config.log_level.value,
    }
    click.echo(json.dumps(data, indent=2))


# ── entry point ───────────────────────────────────────────


def main() -> None:
    """Main CLI entry point."""
    cli()


if __name__ == "__main__":
    main()
