from __future__ import annotations

import threading
from collections import defaultdict

from logger import logger


class HttpfileProfiler:
    def __init__(self, job_id: str, sessionid: int):
        self.job_id = str(job_id)
        self.sessionid = int(sessionid)
        self._lock = threading.Lock()
        self._seconds: dict[str, float] = defaultdict(float)
        self._counts: dict[str, int] = defaultdict(int)

    def observe(self, stage: str, seconds: float, count: int = 1) -> None:
        if not stage:
            return
        sec = float(seconds)
        cnt = int(count)
        if sec < 0:
            sec = 0.0
        if cnt < 0:
            cnt = 0
        with self._lock:
            self._seconds[stage] += sec
            if cnt:
                self._counts[stage] += cnt

    def incr(self, stage: str, count: int = 1) -> None:
        self.observe(stage, 0.0, count=count)

    def snapshot(self) -> tuple[dict[str, float], dict[str, int]]:
        with self._lock:
            return dict(self._seconds), dict(self._counts)

    def log_summary(self, *, total_stage: str | None = None) -> None:
        seconds, counts = self.snapshot()
        total = float(seconds.get(total_stage, 0.0)) if total_stage else 0.0
        summed_stage_sec = float(sum(seconds.values()))

        logger.info(
            "[httpfile-prof] stage_summary_begin job_id=%s sessionid=%s total_stage=%s total_sec=%.3f summed_stage_sec=%.3f overlap_factor=%.3f",
            self.job_id,
            self.sessionid,
            total_stage or "n/a",
            total,
            summed_stage_sec,
            (summed_stage_sec / total) if total > 0 else -1.0,
        )
        for stage in sorted(set(seconds) | set(counts)):
            sec = float(seconds.get(stage, 0.0))
            cnt = int(counts.get(stage, 0))
            avg = (sec / cnt) if cnt > 0 else 0.0
            ratio = (sec / total * 100.0) if total > 0 else -1.0
            logger.info(
                "[httpfile-prof] stage=%s sec=%.3f cnt=%d avg_sec=%.6f ratio_pct=%.2f job_id=%s sessionid=%s",
                stage,
                sec,
                cnt,
                avg,
                ratio,
                self.job_id,
                self.sessionid,
            )
        logger.info(
            "[httpfile-prof] stage_summary_end job_id=%s sessionid=%s",
            self.job_id,
            self.sessionid,
        )
