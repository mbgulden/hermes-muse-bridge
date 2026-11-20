"""Weekly measurement extract for the Hermes kanban fleet.

The scorecard is the measurement layer of the improvement loop: the
one extract the fleet dashboard and the Friday loop driver both
consume, computed the same way every week from a board snapshot. It
computes six sections:

1. First-run value success -- delegated to fleetops.classifier
   (a task counts when its first run completed AND its deliverable
   earned a reviewer-authored PASS verdict), with verdict coverage
   reported as its own named metric beside the rate, never blended
   into it.
2. The failure-cause split -- delegated to fleetops.classifier --
   for failed first runs and for all failed runs: counts and share
   per class, unclassified as its own slice, never redistributed,
   plus the task-caused share.
3. Timed-out hours share over finished runs, and weekly completions.
4. Estimate calibration: the median estimate overrun (actual run
   minutes over the body's human-equivalent estimate) per task
   class -- build / drafts / research / other -- with n per class.
5. Token accounting: recorded and estimated tokens always separate,
   never blended; replacement cost in dollars from a rate table the
   caller passes in as data; net value only where the caller
   supplies a running-cost figure.
6. Slot saturation: running runs against the concurrency cap
   (passed in as data), with a sustained-saturation flag under the
   documented definition in SATURATION_DEFINITION.

The scorecard holds no private copy of the classifier's rules: cause
classes, failed-run determination, and verdict parsing all come from
fleetops.classifier. Input is a plain snapshot (task, run, and
comment row dicts in the classifier's shapes) plus a small config
dict. Output is one JSON-serializable extract dict, plus a markdown
rendering. Pure stdlib, read-only, no board writes.
"""
from __future__ import annotations

import json
import re
import statistics
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from fleetops.classifier.classifier import (
    CLASSES,
    DEFAULT_REVIEWERS,
    TASK_CAUSED,
    classify_failures,
    classify_run,
    first_run_value_success,
)

DEFAULT_PROFILES = ("kai", "george", "ned", "fred")

# The module's only built-in rates: a clearly-labeled EXAMPLE table
# (the invented example rates from the improvement-loop skill's
# measurement definitions). They exist so the extract always names a
# basis; callers pass their own table for figures they act on. No
# real rate is hard-coded here.
EXAMPLE_RATE_TABLE = {
    "reference_model": "example-reference-model",
    "input_per_million": 2.50,
    "output_per_million": 10.00,
}

# Sustained saturation (feeds the slot-contention read): the board
# counts as sustained-saturated when, over the trailing
# SATURATION_WINDOW_HOURS, it sat at the concurrency cap for at least
# SATURATION_SHARE of the time. Concurrency is reconstructed from
# run intervals; a single moment at the cap is not saturation.
SATURATION_WINDOW_HOURS = 24
SATURATION_SHARE = 0.5
SATURATION_DEFINITION = (
    "Sustained saturation: over the trailing "
    f"{SATURATION_WINDOW_HOURS} hours, the board sat at the "
    f"concurrency cap for at least {int(SATURATION_SHARE * 100)}% of "
    "the time (concurrency reconstructed from run start/end "
    "intervals). Thresholds are config data (saturation_window_hours "
    "/ saturation_share), not constants a caller cannot move."
)

TASK_CLASSES = ("build", "drafts", "research", "other")

_ESTIMATE_RE = re.compile(
    r"Human-equivalent estimate:\s*~?\s*(\d+(?:\.\d+)?)\s*(?:min|minutes)",
    re.IGNORECASE,
)
_CLASS_LINE_RE = re.compile(r"(?:Work type|Task class)\s*:\s*([A-Za-z ]+)", re.IGNORECASE)
_DRAFTS_RE = re.compile(
    r"\b(drafts?|copy|e-?mail|letter|postcards?|newsletter|blurb)\b", re.IGNORECASE
)
_RESEARCH_RE = re.compile(
    r"\b(research|survey|scans?|investigat\w*|competitive|market)\b", re.IGNORECASE
)
_BUILD_PREFIXES = ("port-", "skillforge-", "build-", "fix-")
_BUILD_RE = re.compile(r"\b(build|implement|deploy|install)\b", re.IGNORECASE)


# --------------------------------------------------------------------------
# Row helpers (snapshot shapes are the classifier's; see its docstring).
# --------------------------------------------------------------------------

def _metadata(run: Mapping[str, Any]) -> Dict[str, Any]:
    md = run.get("metadata")
    if isinstance(md, dict):
        return md
    if isinstance(md, str) and md.strip():
        try:
            parsed = json.loads(md)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _fold_profile(profile: Optional[str]) -> Optional[str]:
    """Fred and orchestrator are the same profile; fold to fred."""
    if profile == "orchestrator":
        return "fred"
    return profile


def _sorted_runs(runs: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(runs, key=lambda r: (r.get("started_at") or 0, r.get("id") or 0))


def _hours(run: Mapping[str, Any]) -> float:
    if run.get("ended_at") and run.get("started_at"):
        return (run["ended_at"] - run["started_at"]) / 3600.0
    return 0.0


def _pct(num: float, den: float) -> Optional[float]:
    return round(100.0 * num / den, 1) if den else None


# --------------------------------------------------------------------------
# Task class for estimate calibration. The board carries no class
# field, so the class is derived deterministically and documented
# here and in the README: an explicit Work type / Task class line in
# the body wins; else title signals in the order drafts, research,
# build; else other.
# --------------------------------------------------------------------------

def task_class(task: Mapping[str, Any]) -> str:
    body = str(task.get("body") or "")
    m = _CLASS_LINE_RE.search(body)
    if m:
        declared = m.group(1).strip().lower()
        if declared.startswith("build"):
            return "build"
        if declared.startswith("draft"):
            return "drafts"
        if declared.startswith("research"):
            return "research"
        return "other"
    title = str(task.get("title") or "")
    lowered = title.lower()
    if _DRAFTS_RE.search(title):
        return "drafts"
    if _RESEARCH_RE.search(title):
        return "research"
    if lowered.startswith(_BUILD_PREFIXES) or _BUILD_RE.search(title):
        return "build"
    return "other"


# --------------------------------------------------------------------------
# Section builders. Each takes the population-filtered rows.
# --------------------------------------------------------------------------

def _value_block(
    tasks: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    reviewers: Set[str],
) -> Dict[str, Any]:
    value = first_run_value_success(tasks, runs, comments, profiles=None, reviewers=reviewers)
    return {
        "rate": value["rate"],
        "numerator": value["numerator"],
        "denominator": value["denominator"],
        "covered_tasks": value["denominator"],
        "done_tasks": value["done_tasks"],
        "total_done": value["done_tasks"],
        "verdict_tasks": value["verdict_tasks"],
        "verdict_coverage": value["verdict_coverage"],
    }


def _with_shares(split: Mapping[str, Any]) -> Dict[str, Any]:
    total = split["total_failed"]
    counts = {cls: split["counts"].get(cls, 0) for cls in CLASSES}
    return {
        "total_failed": total,
        "counts": counts,
        "shares": {cls: _pct(counts[cls], total) for cls in CLASSES},
        "task_caused_share": split["task_caused_share"],
    }


def _weekly_first_run_tasks(
    tasks: Sequence[Mapping[str, Any]],
    runs_by_task: Mapping[Any, List[Mapping[str, Any]]],
    cut: float,
) -> List[Mapping[str, Any]]:
    """Tasks whose FIRST run (over their full history) started in-window."""
    out = []
    for task in tasks:
        ordered = _sorted_runs(runs_by_task.get(task.get("id"), []))
        if ordered and ordered[0].get("started_at") and ordered[0]["started_at"] >= cut:
            out.append(task)
    return out


def _timeout_block(runs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    finished = [r for r in runs if r.get("ended_at")]
    total_h = sum(_hours(r) for r in finished)
    timed_out_h = sum(_hours(r) for r in finished if r.get("outcome") == "timed_out")
    return {
        "finished_runs": len(finished),
        "total_hours": round(total_h, 2),
        "timed_out_hours": round(timed_out_h, 2),
        "share_pct": _pct(timed_out_h, total_h),
    }


def _estimate_block(
    tasks: Sequence[Mapping[str, Any]],
    runs_by_task: Mapping[Any, List[Mapping[str, Any]]],
) -> Dict[str, Any]:
    factors: Dict[str, List[float]] = {cls: [] for cls in TASK_CLASSES}
    with_estimate = 0
    for task in tasks:
        m = _ESTIMATE_RE.search(str(task.get("body") or ""))
        if not m:
            continue
        with_estimate += 1
        if task.get("status") != "done":
            continue
        est_min = float(m.group(1))
        if not est_min:
            continue
        actual_min = (
            sum(_hours(r) for r in runs_by_task.get(task.get("id"), []) if r.get("ended_at"))
            * 60.0
        )
        factors[task_class(task)].append(actual_min / est_min)
    all_factors = sorted(f for values in factors.values() for f in values)
    classes = {
        cls: {
            "n": len(values),
            "median_overrun": round(statistics.median(values), 2) if values else None,
        }
        for cls, values in factors.items()
    }
    return {
        "classes": classes,
        "overall": {
            "n": len(all_factors),
            "median_overrun": round(statistics.median(all_factors), 2)
            if all_factors
            else None,
        },
        "tasks_with_estimate": with_estimate,
    }


def _token_block(
    runs: Sequence[Mapping[str, Any]],
    medians: Mapping[str, float],
    overall_median: Optional[float],
    recorded_ratio_in: float,
    rate_table: Mapping[str, Any],
    electricity_usd: Optional[float],
) -> Dict[str, Any]:
    """Token accounting over one run set. `medians` / `overall_median`
    / `recorded_ratio_in` come from the all-time recorded population,
    so a window's estimates rest on the full history (documented in
    the README). Recorded and estimated are always separate."""
    finished = [r for r in runs if r.get("ended_at")]
    rec_in = rec_out = 0
    recorded_runs = 0
    est_in = est_out = 0.0
    for run in finished:
        md = _metadata(run)
        total = md.get("total_tokens")
        if total:
            recorded_runs += 1
            rec_in += md.get("input_tokens") or 0
            rec_out += md.get("output_tokens") or 0
        else:
            est_total = medians.get(run.get("profile")) or overall_median or 0
            est_in += est_total * recorded_ratio_in
            est_out += est_total * (1.0 - recorded_ratio_in)
    est_in_i, est_out_i = round(est_in), round(est_out)
    total_in, total_out = rec_in + est_in_i, rec_out + est_out_i

    def cost(tokens_in: float, tokens_out: float) -> float:
        return round(
            tokens_in / 1e6 * float(rate_table["input_per_million"])
            + tokens_out / 1e6 * float(rate_table["output_per_million"]),
            2,
        )

    cost_recorded = cost(rec_in, rec_out)
    cost_estimated = cost(est_in_i, est_out_i)
    cost_total = round(cost_recorded + cost_estimated, 2)
    return {
        "finished_runs": len(finished),
        "recorded_runs": recorded_runs,
        "recorded_run_share": _pct(recorded_runs, len(finished)),
        "recorded_input_tokens": rec_in,
        "recorded_output_tokens": rec_out,
        "estimated_input_tokens": est_in_i,
        "estimated_output_tokens": est_out_i,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "recorded_token_share": _pct(rec_in + rec_out, total_in + total_out),
        "replacement_cost_usd": {
            "recorded": cost_recorded,
            "estimated": cost_estimated,
            "total": cost_total,
        },
        "electricity_usd": electricity_usd,
        "net_value_usd": round(cost_total - electricity_usd, 2)
        if electricity_usd is not None
        else None,
    }


def _token_context(
    runs: Sequence[Mapping[str, Any]],
) -> tuple[Dict[str, float], Optional[float], float]:
    """Per-profile median total tokens over recorded completed runs,
    the overall median, and the recorded input share of tokens."""
    by_profile: Dict[str, List[float]] = {}
    rec_in = rec_out = 0
    for run in runs:
        md = _metadata(run)
        total = md.get("total_tokens")
        if total:
            rec_in += md.get("input_tokens") or 0
            rec_out += md.get("output_tokens") or 0
            if run.get("outcome") == "completed":
                by_profile.setdefault(run.get("profile") or "unknown", []).append(total)
    medians = {p: float(statistics.median(v)) for p, v in by_profile.items() if v}
    flat = [t for values in by_profile.values() for t in values]
    overall = float(statistics.median(flat)) if flat else None
    ratio_in = rec_in / (rec_in + rec_out) if (rec_in + rec_out) else 0.0
    return medians, overall, ratio_in


def _saturation_block(
    all_runs: Sequence[Mapping[str, Any]],
    now: float,
    cap: Optional[int],
    window_hours: float,
    share_threshold: float,
) -> Dict[str, Any]:
    running = [
        r
        for r in all_runs
        if r.get("ended_at") is None and r.get("started_at") and r["started_at"] <= now
    ]
    block: Dict[str, Any] = {
        "running_runs": len(running),
        "concurrency_cap": cap,
        "at_cap_now": None,
        "trailing_window_hours": window_hours,
        "at_cap_share": None,
        "sustained_saturation": None,
        "sustained_threshold_share": share_threshold,
        "definition": SATURATION_DEFINITION,
    }
    if not cap:
        return block
    block["at_cap_now"] = len(running) >= cap
    start = now - window_hours * 3600.0
    events: List[tuple[float, int]] = []
    for run in all_runs:
        s, e = run.get("started_at"), run.get("ended_at")
        if not s:
            continue
        s_c = max(float(s), start)
        e_c = min(float(e), now) if e else now
        if e_c > s_c:
            events.append((s_c, 1))
            events.append((e_c, -1))
    events.sort(key=lambda ev: (ev[0], -ev[1]))
    at_cap_seconds = 0.0
    concurrency = 0
    prev = start
    for when, delta in events:
        if concurrency >= cap:
            at_cap_seconds += when - prev
        concurrency += delta
        prev = when
    if concurrency >= cap:
        at_cap_seconds += now - prev
    share = at_cap_seconds / (window_hours * 3600.0) if window_hours else 0.0
    block["at_cap_share"] = round(100.0 * share, 1)
    block["sustained_saturation"] = share >= share_threshold
    return block


# --------------------------------------------------------------------------
# The extract.
# --------------------------------------------------------------------------

def build_scorecard(
    snapshot: Mapping[str, Any],
    config: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Build the weekly measurement extract from a board snapshot.

    snapshot is {"tasks": [...], "runs": [...], "comments": [...]} in
    the classifier's row shapes. config keys (all optional):
    profiles, now, window_days, concurrency_cap, rate_table,
    electricity_usd, electricity_usd_all_time, reviewers,
    saturation_window_hours, saturation_share. See the module
    docstring and README for the definitions.
    """
    cfg = dict(config or {})
    profiles = cfg.get("profiles", DEFAULT_PROFILES)
    profile_set = (
        {_fold_profile(p) for p in profiles} if profiles is not None else None
    )
    now = float(cfg.get("now") or time.time())
    window_days = float(cfg.get("window_days", 7))
    if window_days <= 0:
        raise ValueError(f"window_days must be > 0, got {window_days}")
    cap = cfg.get("concurrency_cap")
    if cap is not None and int(cap) < 1:
        raise ValueError(f"concurrency_cap must be >= 1, got {cap}")
    rate_table = dict(cfg.get("rate_table") or EXAMPLE_RATE_TABLE)
    for key in ("input_per_million", "output_per_million"):
        if float(rate_table.get(key, 0)) < 0:
            raise ValueError(f"rate_table[{key!r}] must be >= 0")
    reviewers = cfg.get("reviewers", DEFAULT_REVIEWERS)
    cut = now - window_days * 86400.0

    tasks_all = list(snapshot.get("tasks") or [])
    runs_all = list(snapshot.get("runs") or [])
    comments_all = list(snapshot.get("comments") or [])

    def in_population(task: Mapping[str, Any]) -> bool:
        return profile_set is None or _fold_profile(task.get("assignee")) in profile_set

    tasks = [t for t in tasks_all if in_population(t)]
    task_ids = {t.get("id") for t in tasks}
    runs = [r for r in runs_all if r.get("task_id") in task_ids]
    comments = [c for c in comments_all if c.get("task_id") in task_ids]

    runs_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in runs:
        runs_by_task.setdefault(r.get("task_id"), []).append(r)
    comments_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for c in comments:
        comments_by_task.setdefault(c.get("task_id"), []).append(c)

    # 1 + 2: value success and cause split, via the classifier.
    value_all = _value_block(tasks, runs, comments, reviewers)
    failures_all = classify_failures(tasks, runs, comments, profiles=None, reviewers=reviewers)

    window_tasks = _weekly_first_run_tasks(tasks, runs_by_task, cut)
    window_task_ids = {t.get("id") for t in window_tasks}
    window_runs_ft = [r for r in runs if r.get("task_id") in window_task_ids]
    window_comments_ft = [c for c in comments if c.get("task_id") in window_task_ids]
    value_window = _value_block(window_tasks, window_runs_ft, window_comments_ft, reviewers)
    failures_window_first = classify_failures(
        window_tasks, window_runs_ft, window_comments_ft, profiles=None, reviewers=reviewers
    )["first_runs"]

    # Weekly all-runs split: classify each in-window run with its
    # task's FULL run/comment context, so first/latest determination
    # matches the all-time read exactly.
    task_by_id = {t.get("id"): t for t in tasks}
    window_counts = {cls: 0 for cls in CLASSES}
    window_total = 0
    for run in runs:
        if not run.get("started_at") or run["started_at"] < cut:
            continue
        cls = classify_run(
            run,
            task_by_id[run.get("task_id")],
            runs_by_task.get(run.get("task_id"), []),
            comments_by_task.get(run.get("task_id"), []),
            reviewers=reviewers,
        )
        if cls is not None:
            window_counts[cls] += 1
            window_total += 1
    window_all_split = {
        "total_failed": window_total,
        "counts": window_counts,
        "shares": {cls: _pct(window_counts[cls], window_total) for cls in CLASSES},
        "task_caused_share": _pct(
            sum(window_counts[c] for c in TASK_CAUSED), window_total
        ),
    }

    # 3: timed-out hours + completions.
    window_runs = [r for r in runs if r.get("started_at") and r["started_at"] >= cut]
    completions_window = sum(
        1
        for t in tasks
        if t.get("status") == "done" and t.get("completed_at") and t["completed_at"] >= cut
    )

    # 5: tokens (context from the all-time recorded population).
    medians, overall_median, ratio_in = _token_context(runs)
    tokens_all = _token_block(
        runs, medians, overall_median, ratio_in, rate_table,
        cfg.get("electricity_usd_all_time"),
    )
    tokens_window = _token_block(
        window_runs, medians, overall_median, ratio_in, rate_table,
        cfg.get("electricity_usd"),
    )

    return {
        "generated_at": now,
        "window": {"days": window_days, "cut": cut, "now": now},
        "population_profiles": sorted(profile_set) if profile_set is not None else None,
        "first_run_value_success": {"all_time": value_all, "window": value_window},
        "verdict_coverage": {
            "rate": value_all["verdict_coverage"],
            "verdict_tasks": value_all["verdict_tasks"],
            "done_tasks": value_all["done_tasks"],
        },
        "failure_causes": {
            "all_time": {
                "first_runs": _with_shares(failures_all["first_runs"]),
                "all_runs": _with_shares(failures_all["all_runs"]),
            },
            "window": {
                "first_runs": _with_shares(failures_window_first),
                "all_runs": window_all_split,
            },
        },
        "timed_out": {
            "window": _timeout_block(window_runs),
            "all_time": _timeout_block(runs),
        },
        "completions": {
            "window_tasks_done": completions_window,
            "window_completed_runs": sum(
                1 for r in window_runs if r.get("outcome") == "completed"
            ),
        },
        "estimate_calibration": _estimate_block(tasks, runs_by_task),
        "tokens": {
            "rate_table": rate_table,
            "all_time": tokens_all,
            "window": tokens_window,
        },
        "saturation": _saturation_block(
            runs_all,
            now,
            int(cap) if cap is not None else None,
            float(cfg.get("saturation_window_hours", SATURATION_WINDOW_HOURS)),
            float(cfg.get("saturation_share", SATURATION_SHARE)),
        ),
    }


# --------------------------------------------------------------------------
# Markdown rendering.
# --------------------------------------------------------------------------

def _split_lines(title: str, split: Mapping[str, Any]) -> List[str]:
    lines = [
        f"**{title}** — {split['total_failed']} failed",
    ]
    for cls in CLASSES:
        share = split["shares"][cls]
        share_text = f"{share}%" if share is not None else "—"
        lines.append(f"- {cls}: {split['counts'][cls]} ({share_text})")
    caused = split["task_caused_share"]
    lines.append(
        f"- task-caused share: {caused}%" if caused is not None else "- task-caused share: —"
    )
    return lines


def render_markdown(extract: Mapping[str, Any]) -> str:
    """Render an extract dict as a markdown scorecard."""
    value = extract["first_run_value_success"]["all_time"]
    value_w = extract["first_run_value_success"]["window"]
    causes = extract["failure_causes"]
    timed_out = extract["timed_out"]["window"]
    estimates = extract["estimate_calibration"]
    tokens = extract["tokens"]
    tok = tokens["all_time"]
    tok_w = tokens["window"]
    sat = extract["saturation"]
    rate = tokens["rate_table"]

    def rate_text(block: Mapping[str, Any]) -> str:
        if block["rate"] is None:
            return "n/a (no verdict-covered tasks)"
        return (
            f"{block['rate']}% ({block['numerator']}/{block['denominator']} covered "
            f"tasks; verdict coverage {block['verdict_coverage']}% of "
            f"{block['done_tasks']} done)"
        )

    lines = [
        "# Fleet scorecard",
        "",
        f"Window: last {extract['window']['days']:g} days. Population: "
        + (
            ", ".join(extract["population_profiles"])
            if extract["population_profiles"]
            else "all tasks"
        ),
        "",
        "## First-run value success",
        "",
        f"- All-time: {rate_text(value)}",
        f"- Window: {rate_text(value_w)}",
        f"- Verdict coverage: {extract['verdict_coverage']['rate']}% "
        f"({extract['verdict_coverage']['verdict_tasks']} of "
        f"{extract['verdict_coverage']['done_tasks']} done tasks)",
        "",
        "## Failure causes (all-time)",
        "",
        *_split_lines("Failed first runs", causes["all_time"]["first_runs"]),
        "",
        *_split_lines("All failed runs", causes["all_time"]["all_runs"]),
        "",
        "## Failure causes (window)",
        "",
        *_split_lines("Failed first runs", causes["window"]["first_runs"]),
        "",
        *_split_lines("All failed runs", causes["window"]["all_runs"]),
        "",
        "## Timed-out hours (window)",
        "",
        f"- Timed-out hours: {timed_out['timed_out_hours']} of "
        f"{timed_out['total_hours']} finished-run hours "
        f"({timed_out['share_pct'] if timed_out['share_pct'] is not None else '—'}%)",
        f"- Completions in window: {extract['completions']['window_tasks_done']} tasks done, "
        f"{extract['completions']['window_completed_runs']} completed runs",
        "",
        "## Estimate calibration (median overrun, done tasks with estimates)",
        "",
    ]
    for cls in TASK_CLASSES:
        entry = estimates["classes"][cls]
        median = entry["median_overrun"]
        lines.append(
            f"- {cls}: {median}× (n={entry['n']})" if median is not None else f"- {cls}: — (n=0)"
        )
    overall = estimates["overall"]
    lines.append(
        f"- overall: {overall['median_overrun']}× (n={overall['n']})"
        if overall["median_overrun"] is not None
        else "- overall: — (n=0)"
    )
    lines += [
        "",
        "## Tokens and replacement cost",
        "",
        f"Rates: {rate.get('reference_model')} — ${rate['input_per_million']}/M input, "
        f"${rate['output_per_million']}/M output (replacement cost, not money spent).",
        "",
        f"- All-time recorded: {tok['recorded_input_tokens']:,} in / "
        f"{tok['recorded_output_tokens']:,} out "
        f"({tok['recorded_run_share']}% of finished runs recorded)",
        f"- All-time estimated: {tok['estimated_input_tokens']:,} in / "
        f"{tok['estimated_output_tokens']:,} out (kept separate, never blended)",
        f"- All-time replacement cost: ${tok['replacement_cost_usd']['total']:,.2f} "
        f"(recorded ${tok['replacement_cost_usd']['recorded']:,.2f} + estimated "
        f"${tok['replacement_cost_usd']['estimated']:,.2f})",
        f"- Window replacement cost: ${tok_w['replacement_cost_usd']['total']:,.2f}; "
        f"net value (window): "
        + (f"${tok_w['net_value_usd']:,.2f}" if tok_w["net_value_usd"] is not None else "n/a (no running-cost figure supplied)"),
        "",
        "## Slot saturation",
        "",
        f"- Running now: {sat['running_runs']} runs; cap: "
        + (str(sat["concurrency_cap"]) if sat["concurrency_cap"] is not None else "not configured"),
        f"- At cap now: {sat['at_cap_now']}; at-cap share over the trailing "
        f"{sat['trailing_window_hours']:g}h: {sat['at_cap_share']}%; "
        f"sustained saturation: {sat['sustained_saturation']}",
        "",
    ]
    return "\n".join(lines)
