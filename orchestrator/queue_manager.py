"""
Queue Manager — Dark Channel Factory
Abstração sobre Redis Queue (RQ) para enfileirar jobs entre agentes.
"""
from __future__ import annotations

import os
from uuid import UUID

from dotenv import load_dotenv
try:
    from redis import Redis
    from rq import Queue
    from rq.job import Job
    REDIS_AVAILABLE = True
except ImportError:
    Redis = Queue = Job = None  # type: ignore
    REDIS_AVAILABLE = False

from shared.logger import get_logger

load_dotenv()
logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Nomes das Filas ────────────────────────────────────────────
QUEUE_SCOUT = "scout"
QUEUE_VIRAL_SCORE = "viral_score"    # v2: Gatekeeper pré-produção
QUEUE_COPYWRITER = "copywriter"
QUEUE_VALIDATOR = "validator"         # v2: QA do roteiro
QUEUE_VIDEO = "video"
QUEUE_DESIGNER = "designer"
QUEUE_UPLOAD = "upload"
QUEUE_SHORTS = "shorts"               # v2: YouTube Shorts + TikTok + Instagram
QUEUE_ANALYTICS = "analytics"
QUEUE_LEARNING = "learning"           # v2: Analyst & Learning


class QueueManager:
    """Gerencia conexão Redis e filas RQ."""

    def __init__(self, redis_url: str = REDIS_URL) -> None:
        if not REDIS_AVAILABLE:
            raise RuntimeError(
                "Redis/RQ not installed. Run: pip install redis rq\n"
                "Then start Redis: docker-compose up -d redis"
            )
        self.redis_conn = Redis.from_url(redis_url)
        self._queues: dict[str, Queue] = {}
        self._init_queues()

    def _init_queues(self) -> None:
        queue_names = [
            QUEUE_SCOUT,
            QUEUE_VIRAL_SCORE,
            QUEUE_COPYWRITER,
            QUEUE_VALIDATOR,
            QUEUE_VIDEO,
            QUEUE_DESIGNER,
            QUEUE_UPLOAD,
            QUEUE_SHORTS,
            QUEUE_ANALYTICS,
            QUEUE_LEARNING,
        ]
        for name in queue_names:
            self._queues[name] = Queue(
                name,
                connection=self.redis_conn,
                default_timeout=3600,  # 1h timeout por job
            )
        logger.info(f"Redis connected: {REDIS_URL} | Queues: {queue_names}")

    def enqueue(
        self,
        queue_name: str,
        func,
        *args,
        job_timeout: int = 3600,
        retry=None,
        **kwargs,
    ) -> Job:
        """Enfileira uma função em uma fila específica."""
        q = self._queues.get(queue_name)
        if not q:
            raise ValueError(f"Queue '{queue_name}' not found")

        job = q.enqueue(
            func,
            *args,
            job_timeout=job_timeout,
            retry=retry,
            **kwargs,
        )
        logger.info(f"→ Enqueued [{queue_name}] job_id={job.id}")
        return job

    def get_queue_stats(self) -> dict:
        stats = {}
        for name, q in self._queues.items():
            stats[name] = {
                "queued": len(q),
                "failed": q.failed_job_registry.count,
                "finished": q.finished_job_registry.count,
            }
        return stats

    def ping(self) -> bool:
        try:
            return self.redis_conn.ping()
        except Exception:
            return False


# Singleton global
_qm_instance: QueueManager | None = None


def get_queue_manager() -> QueueManager:
    global _qm_instance
    if _qm_instance is None:
        _qm_instance = QueueManager()
    return _qm_instance
