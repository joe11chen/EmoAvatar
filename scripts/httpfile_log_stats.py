#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

KNOWN_METRICS = [
    "queue_wait_sec",
    "time_to_first_speaking_sec",
    "speaking_to_end_sec",
    "tts_empty_tail_sec",
    "asr_empty_tail_sec",
    "record_active_sec",
    "stop_record_sec",
    "move_output_sec",
    "drive_total_sec",
    "job_total_sec",
    "tts_total_sec",
    "request_to_file_sec",
]

JOB_ID_RE = re.compile(r"\bjob_id=([0-9a-fA-F]+)\b")
KV_RE = re.compile(r"\b([a-zA-Z_][a-zA-Z0-9_]*)=([^\s]+)")
NA_VALUES = {"n/a", "na", "none", "null", "nan", "-"}


@dataclass
class MetricStats:
    count: int
    avg: float
    p50: float
    p90: float
    min_v: float
    max_v: float


def parse_float(text: str) -> float | None:
    t = text.strip().strip(",")
    if t.lower() in NA_VALUES:
        return None
    try:
        value = float(t)
    except ValueError:
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return value


def percentile(values: list[float], p: float) -> float:
    if not values:
        return float("nan")
    if len(values) == 1:
        return values[0]
    xs = sorted(values)
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return xs[lo]
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def compute_stats(values: list[float]) -> MetricStats:
    return MetricStats(
        count=len(values),
        avg=sum(values) / len(values),
        p50=percentile(values, 0.50),
        p90=percentile(values, 0.90),
        min_v=min(values),
        max_v=max(values),
    )


def parse_log(log_path: Path, metrics: set[str]) -> dict[str, dict[str, float]]:
    jobs: dict[str, dict[str, float]] = {}

    with log_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if "[httpfile-prof]" not in line:
                continue
            # Skip old verbose stage logs if they exist in historical files.
            if " stage=" in line or "stage_summary_" in line:
                continue

            m = JOB_ID_RE.search(line)
            if not m:
                continue
            job_id = m.group(1)

            row = jobs.setdefault(job_id, {})
            for k, v in KV_RE.findall(line):
                if k == "job_id" or k not in metrics:
                    continue
                fv = parse_float(v)
                if fv is None:
                    continue
                row[k] = fv

    return jobs


def fmt(v: float) -> str:
    return f"{v:.3f}"


def print_metric_summary(jobs: dict[str, dict[str, float]], metrics: list[str]) -> None:
    print("=== Httpfile Metrics Summary (per job, latest value) ===")
    print(f"jobs_total: {len(jobs)}")
    with_job_total = sum(1 for m in jobs.values() if "job_total_sec" in m)
    with_request_to_file = sum(1 for m in jobs.values() if "request_to_file_sec" in m)
    print(f"jobs_with_job_total_sec: {with_job_total}")
    print(f"jobs_with_request_to_file_sec: {with_request_to_file}")
    print()

    header = (
        f"{'metric':<28} {'count':>6} {'avg':>9} {'p50':>9} {'p90':>9} {'min':>9} {'max':>9}"
    )
    print(header)
    print("-" * len(header))

    for metric in metrics:
        values = [row[metric] for row in jobs.values() if metric in row]
        if not values:
            continue
        s = compute_stats(values)
        print(
            f"{metric:<28} {s.count:>6} {fmt(s.avg):>9} {fmt(s.p50):>9} {fmt(s.p90):>9} {fmt(s.min_v):>9} {fmt(s.max_v):>9}"
        )


def print_top_jobs(jobs: dict[str, dict[str, float]], metric: str, topn: int) -> None:
    rows = [(jid, vals.get(metric)) for jid, vals in jobs.items()]
    rows = [(jid, v) for jid, v in rows if v is not None]
    rows.sort(key=lambda x: x[1], reverse=True)

    print()
    print(f"=== Top {min(topn, len(rows))} Slow Jobs by {metric} ===")
    for jid, v in rows[:topn]:
        print(f"job_id={jid} {metric}={v:.3f}")


def print_job_table(jobs: dict[str, dict[str, float]], metrics: list[str], limit: int | None) -> None:
    print()
    print("=== Per-Job Metrics ===")
    cols = ["job_id"] + metrics
    print("\t".join(cols))

    items = list(jobs.items())
    items.sort(key=lambda x: x[1].get("request_to_file_sec", x[1].get("job_total_sec", -1.0)), reverse=True)
    if limit is not None:
        items = items[:limit]

    for job_id, row in items:
        vals = [job_id]
        for m in metrics:
            v = row.get(m)
            vals.append("" if v is None else f"{v:.3f}")
        print("\t".join(vals))


def write_csv(path: Path, jobs: dict[str, dict[str, float]], metrics: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["job_id", *metrics])
        for job_id, row in sorted(jobs.items()):
            writer.writerow([job_id, *[row.get(m, "") for m in metrics]])


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Aggregate httpfile-prof metrics from livetalking logs.")
    p.add_argument("log", nargs="?", default="livetalking.log", help="Path to log file")
    p.add_argument(
        "--metrics",
        default=",".join(KNOWN_METRICS),
        help="Comma-separated metrics to aggregate",
    )
    p.add_argument("--top-metric", default="request_to_file_sec", help="Metric used for slow job ranking")
    p.add_argument("--top", type=int, default=10, help="Top-N slow jobs to show")
    p.add_argument("--show-jobs", action="store_true", help="Show per-job metric table")
    p.add_argument("--job-limit", type=int, default=None, help="Max rows for --show-jobs")
    p.add_argument("--csv", default="", help="Write per-job table to CSV")
    return p.parse_args()


def main() -> None:
    args = parse_args()
    log_path = Path(args.log)
    if not log_path.exists():
        raise SystemExit(f"log file not found: {log_path}")

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    metric_set = set(metrics)

    jobs = parse_log(log_path, metric_set)
    if not jobs:
        print("No matching [httpfile-prof] job metrics found.")
        return

    print_metric_summary(jobs, metrics)
    if args.top_metric in metric_set:
        print_top_jobs(jobs, args.top_metric, max(1, args.top))

    if args.show_jobs:
        print_job_table(jobs, metrics, args.job_limit)

    if args.csv:
        csv_path = Path(args.csv)
        write_csv(csv_path, jobs, metrics)
        print()
        print(f"CSV written: {csv_path}")


if __name__ == "__main__":
    main()
