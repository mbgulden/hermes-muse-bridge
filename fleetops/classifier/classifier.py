"""Failure-cause classification for Hermes kanban fleet runs.

Every failed run is classified into exactly one cause class --
infrastructure, task shape, task execution, external dependency, or
unclassified -- from its kanban record: outcome, run metadata and error
text, heartbeat evidence, the task's own block text, and reviewer
verdicts. The class decides the response under the improvement loop's
distill policy: infrastructure failures re-run as-is; task-caused
failures (task shape, task execution) are distilled, never repeated
as-is; external dependency waits on the outside input it names.

A record that cannot support a class returns unclassified. Unclassified
rows are never redistributed into other classes.

Also computes first-run value success: a task counts when its first run
completed AND its deliverable earned a reviewer-authored PASS verdict
(a completed run with no value is not a success). Verdict coverage --
of the population's done tasks, how many carry any parseable reviewer
PASS/FAIL verdict -- is returned beside the rate, never blended in.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

INFRASTRUCTURE = "infrastructure"
TASK_SHAPE = "task shape"
TASK_EXECUTION = "task execution"
EXTERNAL_DEPENDENCY = "external dependency"
UNCLASSIFIED = "unclassified"

CLASSES = (
    INFRASTRUCTURE,
    TASK_SHAPE,
    TASK_EXECUTION,
    EXTERNAL_DEPENDENCY,
    UNCLASSIFIED,
)
TASK_CAUSED = frozenset({TASK_SHAPE, TASK_EXECUTION})

FAILED_OUTCOMES = frozenset({"timed_out", "crashed", "gave_up"})

# Reviewer identities whose VERDICT lines count. The orchestrator profile
# posts the fleet's second-witness verdicts; muse posts Jimmy's. A verdict
# authored by the task's own worker never counts, whatever identity it
# claims (enforced in _latest_reviewer_verdict).
DEFAULT_REVIEWERS = frozenset({"orchestrator", "muse"})

# A final heartbeat gap longer than this before a crash points to a dead
# or unreachable worker (infrastructure) rather than a live worker
# failing at the task.
HEARTBEAT_GAP_INFRA_SECONDS = 900

_VERDICT_RE = re.compile(r"VERDICT[:\s]+(PASS|FAIL|CLEAN)\b", re.IGNORECASE)

# Infrastructure markers in a run's own record (metadata / error text).
_INFRA_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"worker (process )?(died|dead|gone|killed)",
        r"dead[- ]worker",
        r"orphaned claim",
        r"dead claim",
        r"claim expired",
        r"no heartbeat",
        r"\bendpoint\b",
        r"\bvllm\b",
        r"\btunnel\b",
        r"\btailscale\b",
        r"vm replacement",
        r"\bdispatcher\b",
        r"connection (refused|reset|closed|aborted)",
        r"gateway (down|restart|unreachable)",
        r"\boutage\b",
    )
)

_PROTOCOL_RE = re.compile(r"protocol violation", re.IGNORECASE)

# External-dependency markers. Applied ONLY to a blocked task's own text
# (its block text and latest worker comment), read first -- never as a
# first read of a run record. Patterns are deliberately narrow: the
# string "approved copy" matches none of them, and negations below
# suppress credential matches the record itself rules out.
_EXTERNAL_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"waiting (on|for)\b",
        r"awaiting\b",
        r"blocked on\b",
        r"needs? .{0,40}\bdecision\b",
        r"pending .{0,24}\b(decision|approval|reply|sign)",
        r"\bcredentials?\b",
        r"\bpassword\b",
        r"sign-?in required",
        r"login required",
        r"verification code",
        r"\b2fa\b",
        r"reply from\b",
    )
)

_EXTERNAL_NEGATIONS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"not a credentials? issue",
        r"no credentials? (issue|needed|required)",
        r"not (waiting|blocked) on",
        r"no (decision|approval|reply) (needed|required|pending)",
    )
)


# --------------------------------------------------------------------------
# Row helpers. Rows are plain dicts shaped like the kanban tables:
# tasks(id, assignee, status, ...), task_runs(id, task_id, profile, outcome,
# started_at, ended_at, metadata), task_comments(id, task_id, author, body,
# created_at).
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


def _run_text(run: Mapping[str, Any]) -> str:
    """All searchable text in a run's own record."""
    parts: List[str] = []
    for key in ("error", "error_text", "message", "status", "outcome"):
        value = run.get(key)
        if isinstance(value, str):
            parts.append(value)
    md = _metadata(run)
    for value in md.values():
        if isinstance(value, str):
            parts.append(value)
    return "\n".join(parts)


def _names_infra(text: str) -> bool:
    return any(p.search(text) for p in _INFRA_PATTERNS)


def _names_external_wait(text: str) -> bool:
    if any(p.search(text) for p in _EXTERNAL_NEGATIONS):
        return False
    return any(p.search(text) for p in _EXTERNAL_PATTERNS)


def _heartbeat_gap_seconds(run: Mapping[str, Any]) -> Optional[float]:
    md = _metadata(run)
    gap = md.get("heartbeat_gap_seconds")
    if isinstance(gap, (int, float)):
        return float(gap)
    last_hb = md.get("last_heartbeat_at")
    ended = run.get("ended_at")
    if isinstance(last_hb, (int, float)) and isinstance(ended, (int, float)):
        return float(ended) - float(last_hb)
    return None


def _dead_worker(run: Mapping[str, Any], text: str) -> bool:
    md = _metadata(run)
    if md.get("pid_alive") is False or md.get("worker_alive") is False:
        return True
    if md.get("worker_dead") is True or md.get("dead_worker") is True:
        return True
    exit_kind = str(md.get("exit_kind") or "").lower()
    if exit_kind in {"signal", "killed", "sigterm", "sigkill", "crashed_dead"}:
        return True
    if md.get("exit_signal"):
        return True
    return bool(
        re.search(
            r"worker (process )?(died|dead|gone|killed)|dead[- ]worker|no heartbeat",
            text,
            re.IGNORECASE,
        )
    )


def _protocol_violation(run: Mapping[str, Any], text: str) -> bool:
    md = _metadata(run)
    if md.get("protocol_violation"):
        return True
    return bool(_PROTOCOL_RE.search(text))


def _sorted_runs(runs: Iterable[Mapping[str, Any]]) -> List[Mapping[str, Any]]:
    return sorted(runs, key=lambda r: (r.get("started_at") or 0, r.get("id") or 0))


def _latest_reviewer_verdict(
    comments: Sequence[Mapping[str, Any]],
    worker_identities: Set[str],
    reviewers: Set[str],
) -> Optional[str]:
    """Latest reviewer-authored verdict for a task: 'PASS', 'FAIL', or None.

    VERDICT CLEAN counts as PASS. Comments by the task's own worker
    identities never count, even if the author claims a reviewer name
    that is also the worker's own profile on this task.
    """
    verdict: Optional[str] = None
    for cm in sorted(comments, key=lambda c: (c.get("created_at") or 0, c.get("id") or 0)):
        author = (cm.get("author") or "").strip()
        if author not in reviewers or author in worker_identities:
            continue
        for m in _VERDICT_RE.finditer(cm.get("body") or ""):
            token = m.group(1).upper()
            verdict = "PASS" if token in {"PASS", "CLEAN"} else "FAIL"
    return verdict


def _fold_profile(profile: Optional[str]) -> Optional[str]:
    """Fred and orchestrator are the same profile; fold to fred."""
    if profile == "orchestrator":
        return "fred"
    return profile


def _in_population(task: Mapping[str, Any], profiles: Optional[Set[str]]) -> bool:
    if profiles is None:
        return True
    return _fold_profile(task.get("assignee")) in profiles


# --------------------------------------------------------------------------
# Classification.
# --------------------------------------------------------------------------

def is_failed_run(
    run: Mapping[str, Any],
    task: Mapping[str, Any],
    task_runs: Sequence[Mapping[str, Any]],
    task_comments: Sequence[Mapping[str, Any]],
    reviewers: Set[str] = DEFAULT_REVIEWERS,
) -> bool:
    """A run failed when its outcome is timed_out / crashed / gave_up, when
    its task sits blocked with this run as the latest, or when it completed
    but its deliverable earned a reviewer-authored FAIL verdict. A reclaimed
    run on a task that later recovered is not itself a failure -- the
    reclaim is an infrastructure event, counted in run statistics, not a
    task failure."""
    outcome = run.get("outcome")
    if outcome in FAILED_OUTCOMES:
        return True
    ordered = _sorted_runs(task_runs)
    is_latest = bool(ordered) and ordered[-1].get("id") == run.get("id")
    if task.get("status") == "blocked" and is_latest and outcome != "completed":
        return True
    if outcome == "completed":
        workers = {task.get("assignee"), run.get("profile")} - {None}
        return _latest_reviewer_verdict(task_comments, workers, reviewers) == "FAIL"
    return False


def classify_run(
    run: Mapping[str, Any],
    task: Mapping[str, Any],
    task_runs: Sequence[Mapping[str, Any]],
    task_comments: Sequence[Mapping[str, Any]],
    reviewers: Set[str] = DEFAULT_REVIEWERS,
) -> Optional[str]:
    """Classify one failed run into exactly one cause class.

    Returns None when the run is not a failed run (see is_failed_run).
    """
    if not is_failed_run(run, task, task_runs, task_comments, reviewers):
        return None

    outcome = run.get("outcome")
    text = _run_text(run)
    ordered = _sorted_runs(task_runs)
    is_latest = bool(ordered) and ordered[-1].get("id") == run.get("id")
    blocked_context = task.get("status") == "blocked" and is_latest

    # Blocked tasks: the task's own text is read FIRST. Only when it names
    # a wait on an outside reply, a credential, or a decision does the run
    # classify external dependency.
    if blocked_context:
        own_texts = [text]
        for cm in sorted(
            task_comments, key=lambda c: (c.get("created_at") or 0, c.get("id") or 0)
        )[-3:]:
            own_texts.append(cm.get("body") or "")
        if _names_external_wait("\n".join(own_texts)):
            return EXTERNAL_DEPENDENCY

    if outcome == "timed_out":
        # Timed-out runs on the board carry cap-error text and no
        # deliverable metadata: task shape, unless the run's own record
        # names an infrastructure cause, which wins.
        if _names_infra(text):
            return INFRASTRUCTURE
        return TASK_SHAPE

    if outcome == "crashed":
        gap = _heartbeat_gap_seconds(run)
        if _protocol_violation(run, text):
            if gap is not None and gap > HEARTBEAT_GAP_INFRA_SECONDS:
                return INFRASTRUCTURE
            return TASK_EXECUTION
        if _dead_worker(run, text):
            return INFRASTRUCTURE
        if gap is not None and gap > HEARTBEAT_GAP_INFRA_SECONDS:
            return INFRASTRUCTURE
        if _names_infra(text):
            return INFRASTRUCTURE
        return UNCLASSIFIED

    if outcome == "gave_up":
        if _names_infra(text):
            return INFRASTRUCTURE
        return TASK_EXECUTION

    if outcome == "completed":
        # is_failed_run already established a reviewer FAIL verdict.
        return TASK_EXECUTION

    if outcome == "reclaimed":
        # Reached only in a blocked context (see is_failed_run): the claim
        # or worker died, which is an infrastructure event.
        return INFRASTRUCTURE

    if blocked_context:
        if _names_infra(text):
            return INFRASTRUCTURE
        return UNCLASSIFIED

    return UNCLASSIFIED


def classify_failures(
    tasks: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    profiles: Optional[Iterable[str]] = None,
    reviewers: Set[str] = DEFAULT_REVIEWERS,
) -> Dict[str, Any]:
    """Classify every failed run, and separately every failed FIRST run.

    profiles filters by task assignee (orchestrator folds into fred).
    Returns {'all_runs': split, 'first_runs': split} where each split is
    {'total_failed': n, 'counts': {class: n}, 'task_caused_share': pct}.
    The five class counts always sum exactly to total_failed.
    """
    profile_set = {_fold_profile(p) for p in profiles} if profiles is not None else None
    task_by_id = {t.get("id"): t for t in tasks}
    runs_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in runs:
        runs_by_task.setdefault(r.get("task_id"), []).append(r)
    comments_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for cm in comments:
        comments_by_task.setdefault(cm.get("task_id"), []).append(cm)

    def empty_split() -> Dict[str, Any]:
        return {
            "total_failed": 0,
            "counts": {cls: 0 for cls in CLASSES},
            "task_caused_share": None,
        }

    all_split = empty_split()
    first_split = empty_split()

    for task in tasks:
        if not _in_population(task, profile_set):
            continue
        tid = task.get("id")
        task_runs = runs_by_task.get(tid, [])
        task_comments = comments_by_task.get(tid, [])
        ordered = _sorted_runs(task_runs)
        for idx, run in enumerate(ordered):
            cls = classify_run(run, task, task_runs, task_comments, reviewers)
            if cls is None:
                continue
            all_split["counts"][cls] += 1
            all_split["total_failed"] += 1
            if idx == 0:
                first_split["counts"][cls] += 1
                first_split["total_failed"] += 1

    for split in (all_split, first_split):
        total = split["total_failed"]
        if total:
            caused = sum(split["counts"][c] for c in TASK_CAUSED)
            split["task_caused_share"] = round(100.0 * caused / total, 1)

    return {"all_runs": all_split, "first_runs": first_split}


def first_run_value_success(
    tasks: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    comments: Sequence[Mapping[str, Any]],
    profiles: Optional[Iterable[str]] = None,
    reviewers: Set[str] = DEFAULT_REVIEWERS,
) -> Dict[str, Any]:
    """First-run value success for a task population.

    A task counts in the numerator when its first run completed AND its
    deliverable earned a reviewer-authored PASS verdict. The denominator
    is the population's tasks whose first run completed AND that carry
    any parseable reviewer PASS/FAIL verdict -- tasks with no verdict
    count in neither. Verdict coverage (done tasks carrying any parseable
    reviewer verdict, over all done tasks in the population) is returned
    beside the rate, never blended into it.
    """
    profile_set = {_fold_profile(p) for p in profiles} if profiles is not None else None
    runs_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for r in runs:
        runs_by_task.setdefault(r.get("task_id"), []).append(r)
    comments_by_task: Dict[Any, List[Mapping[str, Any]]] = {}
    for cm in comments:
        comments_by_task.setdefault(cm.get("task_id"), []).append(cm)

    done_tasks = 0
    verdict_tasks = 0
    numerator = 0
    denominator = 0

    for task in tasks:
        if not _in_population(task, profile_set):
            continue
        tid = task.get("id")
        task_runs = _sorted_runs(runs_by_task.get(tid, []))
        task_comments = comments_by_task.get(tid, [])
        workers = {task.get("assignee")} - {None}
        if task_runs:
            workers.add(task_runs[0].get("profile"))
        workers.discard(None)
        verdict = _latest_reviewer_verdict(task_comments, workers, reviewers)

        if task.get("status") == "done":
            done_tasks += 1
            if verdict is not None:
                verdict_tasks += 1

        if task_runs and task_runs[0].get("outcome") == "completed" and verdict is not None:
            denominator += 1
            if verdict == "PASS":
                numerator += 1

    return {
        "rate": round(100.0 * numerator / denominator, 1) if denominator else None,
        "numerator": numerator,
        "denominator": denominator,
        "verdict_coverage": round(100.0 * verdict_tasks / done_tasks, 1)
        if done_tasks
        else None,
        "done_tasks": done_tasks,
        "verdict_tasks": verdict_tasks,
    }
