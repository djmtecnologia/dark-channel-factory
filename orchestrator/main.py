"""
Orchestrator Main — Dark Channel Factory
Entry point do pipeline. Agenda jobs via APScheduler e gerencia o loop de analytics.

Uso:
    python -m orchestrator.main           # Modo produção
    python -m orchestrator.main --dry-run # Modo dry-run (sem publicar)
    python -m orchestrator.main --now     # Dispara pipeline imediatamente
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from uuid import uuid4

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from rich.panel import Panel
from rich.table import Table

from orchestrator.config import CHANNELS, ChannelConfig, get_default_channel
from orchestrator.queue_manager import (
    QUEUE_ANALYTICS,
    QUEUE_SCOUT,
    get_queue_manager,
)
from shared.logger import console, get_logger, setup_logging

load_dotenv()
setup_logging()
logger = get_logger(__name__)

DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"


# ─────────────────────────────────────────────────────────────
# Funções de dispatch
# ─────────────────────────────────────────────────────────────

def trigger_pipeline(channel_id: str = "ai_tools_us", is_deep_dive: bool = False) -> None:
    """
    Dispara o pipeline completo para um canal.
    Enfileira o Scout Agent que inicia a cadeia de eventos.
    """
    from agents.scout_miner.agent import run_scout_agent

    qm = get_queue_manager()

    if not qm.ping():
        logger.error("❌ Redis unreachable! Start Redis first: docker-compose up -d redis")
        return

    job_id = uuid4()
    logger.info(f"🚀 Pipeline triggered | channel={channel_id} | job={job_id} | deep_dive={is_deep_dive}")

    if DRY_RUN:
        logger.info("[DRY RUN] Would enqueue scout job — skipping actual enqueue")
        return

    qm.enqueue(
        QUEUE_SCOUT,
        run_scout_agent,
        str(job_id),
        channel_id,
        is_deep_dive,
        job_timeout=900,  # 15 min para scout
    )
    logger.info(f"✅ Scout job enqueued: {job_id}")


def trigger_analytics_check() -> None:
    """Dispara verificação de analytics para todos os vídeos publicados."""
    from agents.manager_analyst.agent import run_analytics_monitor

    qm = get_queue_manager()
    if not qm.ping():
        logger.warning("Redis unreachable — skipping analytics check")
        return

    logger.info("📊 Triggering analytics check for all published videos...")
    qm.enqueue(
        QUEUE_ANALYTICS,
        run_analytics_monitor,
        job_timeout=600,
    )


# ─────────────────────────────────────────────────────────────
# Scheduler Setup
# ─────────────────────────────────────────────────────────────

def build_cron_trigger(days: list[str], time_utc: str) -> CronTrigger:
    """Converte lista de dias e horário UTC para CronTrigger."""
    day_map = {
        "monday": "mon", "tuesday": "tue", "wednesday": "wed",
        "thursday": "thu", "friday": "fri", "saturday": "sat", "sunday": "sun",
    }
    day_of_week = ",".join(day_map.get(d.strip().lower(), d.strip()) for d in days)
    hour, minute = time_utc.split(":")
    return CronTrigger(day_of_week=day_of_week, hour=int(hour), minute=int(minute), timezone="UTC")


def print_startup_banner(channels: list[ChannelConfig]) -> None:
    console.print(Panel.fit(
        "[bold cyan]🎬 Dark Channel Factory[/bold cyan]\n"
        "[dim]YouTube Autonomous Video Pipeline v1.0[/dim]",
        border_style="cyan",
    ))

    table = Table(title="Active Channels", border_style="dim")
    table.add_column("ID", style="cyan")
    table.add_column("Niche", style="green")
    table.add_column("Language")
    table.add_column("Voice")
    table.add_column("Schedule", style="yellow")

    for ch in channels:
        schedule = f"{','.join(ch.publish_days)} @ {ch.publish_time_utc} UTC"
        table.add_row(ch.id, ch.niche, ch.language, ch.voice, schedule)

    console.print(table)
    console.print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dark Channel Factory Orchestrator")
    parser.add_argument("--dry-run", action="store_true", help="Simula sem publicar")
    parser.add_argument("--now", action="store_true", help="Dispara pipeline imediatamente")
    parser.add_argument("--channel", default="ai_tools_us", help="ID do canal")
    parser.add_argument("--check-analytics", action="store_true", help="Executa check de analytics agora")
    args = parser.parse_args()

    if args.dry_run:
        os.environ["DRY_RUN"] = "true"
        logger.info("🔍 DRY RUN mode enabled")

    print_startup_banner(CHANNELS)

    if args.now:
        logger.info(f"⚡ Immediate pipeline trigger for channel: {args.channel}")
        trigger_pipeline(args.channel)
        return

    if args.check_analytics:
        trigger_analytics_check()
        return

    # ── Modo agendador ───────────────────────────────────────
    scheduler = BlockingScheduler(timezone="UTC")

    for channel in CHANNELS:
        # Jobs de produção de conteúdo (3x/semana)
        trigger = build_cron_trigger(channel.publish_days, channel.publish_time_utc)
        scheduler.add_job(
            func=trigger_pipeline,
            trigger=trigger,
            args=[channel.id, False],
            id=f"pipeline_{channel.id}",
            name=f"Pipeline [{channel.id}]",
            replace_existing=True,
            misfire_grace_time=3600,  # 1h de tolerância
        )
        logger.info(
            f"📅 Scheduled pipeline [{channel.id}] on "
            f"{','.join(channel.publish_days)} at {channel.publish_time_utc} UTC"
        )

    # Analytics check — a cada 48 horas
    scheduler.add_job(
        func=trigger_analytics_check,
        trigger="interval",
        hours=int(os.getenv("ANALYTICS_CHECK_INTERVAL_HOURS", "48")),
        id="analytics_monitor",
        name="Analytics Monitor (48h)",
        replace_existing=True,
    )
    logger.info("📊 Analytics monitor scheduled every 48 hours")

    console.print("[bold green]✅ Scheduler started. Press Ctrl+C to stop.[/bold green]")
    console.print(f"[dim]Dashboard: http://localhost:9181[/dim]\n")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Orchestrator stopped by user")


if __name__ == "__main__":
    main()
