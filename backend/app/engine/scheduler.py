"""
Deterministic Scheduling Engine for Life Scheduler.

Design Philosophy
-----------------
- Deterministic: given the same inputs + policies, always produces the same schedule.
- Explainable: every placement decision records a human-readable reason.
- Hard constraints dominate: violated hard constraints produce INFEASIBLE, not silent overrides.
- Low churn: freeze horizon protects imminent blocks; existing committed blocks are moved
  only when strictly necessary.
- LLMs are NEVER involved in final scheduling decisions.

Scheduling Classes (in priority order)
---------------------------------------
1. HARD_REAL_TIME   – pinned blocks (meetings, appointments). Never moved.
2. FIXED_RECURRING  – same slot every day/week (e.g. morning workout ritual). Moved only on
                       hard constraint violation.
3. DEADLINE_DRIVEN  – must complete before deadline. Scheduled greedily from deadline backward.
4. QUOTA_BASED      – N hours/week flexible. Fills remaining free slots.
5. OPPORTUNISTIC    – lowest priority, fills any gap.
6. RECOVERY         – missed task repair. Injected by intraday repair job.

Objective Function (minimise)
-----------------------------
  score = Σ hard_violations * BIG_M
        + Σ soft_penalties * weight
        + fragmentation_penalty
        + churn_penalty

  Lower is better. BIG_M = 1e9 makes hard violations dominate.

Algorithms
----------
- Nightly Planning (plan_day):
    1. Freeze blocks within freeze_horizon.
    2. Instantiate templates → TaskInstances for the target day.
    3. Place HARD_REAL_TIME blocks first.
    4. Place FIXED_RECURRING blocks.
    5. Place DEADLINE_DRIVEN blocks (EDF greedy).
    6. Fill QUOTA_BASED gaps using energy-profile-sorted windows.
    7. Fill OPPORTUNISTIC gaps.
    8. Evaluate constraints and score.
    9. Return ScheduleResult (blocks + score + violations).

- Intraday Repair (repair_day):
    1. Identify missed / delayed tasks.
    2. For each missed task, apply RecoveryRule → generate RECOVERY instances.
    3. Re-run placement for uncommitted future slots in the same day.
    4. Respect freeze horizon.
    5. Return updated ScheduleResult with repair diff.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import pytz

logger = logging.getLogger(__name__)

BIG_M = 1_000_000_000.0  # penalty weight for hard constraint violations


# ---------------------------------------------------------------------------
# Data Transfer Objects used by the engine (not tied to ORM models)
# ---------------------------------------------------------------------------

@dataclass
class TimeWindow:
    """A contiguous time window [start, end)."""
    start: datetime
    end: datetime

    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.start < other.end and other.start < self.end

    def contains(self, dt: datetime) -> bool:
        return self.start <= dt < self.end

    def __repr__(self) -> str:
        return f"[{self.start.strftime('%H:%M')}–{self.end.strftime('%H:%M')})"


@dataclass
class SchedulingTask:
    """
    Engine-internal representation of a task to be scheduled.
    Constructed from TaskInstance domain objects.
    """
    id: str                        # TaskInstance UUID (str)
    name: str
    scheduling_class: str          # SchedulingClass enum value
    duration_minutes: int
    priority: int                  # 0-100, higher = more important
    preferred_windows: list[TimeWindow] = field(default_factory=list)
    avoid_windows: list[TimeWindow] = field(default_factory=list)
    deadline: Optional[datetime] = None
    pinned_start: Optional[datetime] = None
    is_protected: bool = False
    is_frozen: bool = False
    category: str = ""
    min_duration_minutes: Optional[int] = None  # for splittable tasks


@dataclass
class PlacedBlock:
    """A task placed into a concrete time slot during planning."""
    task_id: str
    task_name: str
    scheduling_class: str
    start: datetime
    end: datetime
    is_frozen: bool = False
    is_protected: bool = False
    reason: str = ""
    score_contribution: float = 0.0

    def to_window(self) -> TimeWindow:
        return TimeWindow(self.start, self.end)


@dataclass
class ConstraintViolation:
    constraint_name: str
    kind: str  # "hard" or "soft"
    description: str
    penalty: float


@dataclass
class ScheduleResult:
    """
    Output of a planning or repair run.
    Always includes a full explanation of every placement decision.
    """
    target_date: date
    user_id: int
    blocks: list[PlacedBlock]
    violations: list[ConstraintViolation]
    score: float
    score_breakdown: dict[str, float]
    generation_reason: str
    is_feasible: bool  # False if any hard constraints violated

    def summary(self) -> str:
        lines = [
            f"ScheduleResult for {self.target_date} (user {self.user_id})",
            f"  Score: {self.score:.2f}  Feasible: {self.is_feasible}",
            f"  Blocks: {len(self.blocks)}",
        ]
        if self.violations:
            lines.append("  Violations:")
            for v in self.violations:
                lines.append(f"    [{v.kind.upper()}] {v.constraint_name}: {v.description}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Policy / Constraint definitions understood by the engine
# ---------------------------------------------------------------------------

@dataclass
class SleepWindowConstraint:
    min_hours: float = 7.5
    latest_sleep_start: str = "00:30"  # "HH:MM"
    earliest_wake: str = "06:00"       # "HH:MM"


@dataclass
class WorkWindowConstraint:
    work_start: str = "10:00"
    work_end: str = "19:00"
    allowed_overtime_before: int = 60   # minutes
    allowed_overtime_after: int = 60


@dataclass
class EnginePolicy:
    """Fully resolved policy for one planning run."""
    user_timezone: str = "UTC"
    freeze_horizon_minutes: int = 30
    sleep: SleepWindowConstraint = field(default_factory=SleepWindowConstraint)
    work: WorkWindowConstraint = field(default_factory=WorkWindowConstraint)
    min_buffer_minutes: int = 5    # gap between blocks
    prefer_earlier: bool = True    # prefer earlier slots when multiple candidates
    weekend_mode: bool = False     # relaxed constraints on weekends
    # Extra hard constraints: list of {"type": ..., ...} dicts
    extra_hard: list[dict] = field(default_factory=list)
    # Soft preference windows: list of {"type": ..., "weight": ..., ...}
    soft_preferences: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Free slot computation
# ---------------------------------------------------------------------------

def compute_free_slots(
    day_start: datetime,
    day_end: datetime,
    placed_blocks: list[PlacedBlock],
    min_gap_minutes: int = 0,
) -> list[TimeWindow]:
    """
    Return sorted list of free TimeWindows in [day_start, day_end) not occupied
    by any placed block, with at least min_gap_minutes between blocks.
    """
    occupied: list[TimeWindow] = []
    for b in placed_blocks:
        gap = timedelta(minutes=min_gap_minutes)
        occupied.append(TimeWindow(b.start - gap, b.end + gap))

    # Merge overlapping occupied windows
    occupied.sort(key=lambda w: w.start)
    merged: list[TimeWindow] = []
    for w in occupied:
        if merged and w.start <= merged[-1].end:
            merged[-1] = TimeWindow(merged[-1].start, max(merged[-1].end, w.end))
        else:
            merged.append(TimeWindow(w.start, w.end))

    free: list[TimeWindow] = []
    cursor = day_start
    for m in merged:
        if cursor < m.start:
            free.append(TimeWindow(cursor, m.start))
        cursor = max(cursor, m.end)
    if cursor < day_end:
        free.append(TimeWindow(cursor, day_end))

    return [w for w in free if w.duration_minutes() > 0]


# ---------------------------------------------------------------------------
# Constraint evaluation
# ---------------------------------------------------------------------------

def _parse_hhmm(hhmm: str, ref_date: date, tz: Any, next_day_if_early: bool = False) -> datetime:
    """
    Parse an HH:MM string relative to ref_date in the given timezone.
    If next_day_if_early=True and the hour < 12, the result is on the following
    calendar day.  This handles "sleep start at 00:30" meaning past-midnight.
    """
    h, m = map(int, hhmm.split(":"))
    target_date = ref_date + timedelta(days=1) if (next_day_if_early and h < 12) else ref_date
    naive = datetime(target_date.year, target_date.month, target_date.day, h, m)
    return tz.localize(naive)


def evaluate_constraints(
    blocks: list[PlacedBlock],
    policy: EnginePolicy,
    target_date: date,
) -> list[ConstraintViolation]:
    """
    Evaluate all policy constraints against a proposed set of placed blocks.
    Returns a list of violations (empty = fully feasible).
    """
    violations: list[ConstraintViolation] = []
    tz = pytz.timezone(policy.user_timezone)

    # ----- Hard: No overlapping blocks -----
    sorted_blocks = sorted(blocks, key=lambda b: b.start)
    for i in range(len(sorted_blocks) - 1):
        a, b = sorted_blocks[i], sorted_blocks[i + 1]
        if a.end > b.start:
            violations.append(ConstraintViolation(
                constraint_name="no_overlap",
                kind="hard",
                description=f"'{a.task_name}' ({a.end.strftime('%H:%M')}) overlaps '{b.task_name}' ({b.start.strftime('%H:%M')})",
                penalty=BIG_M,
            ))

    # ----- Hard: Blocks must be within day bounds -----
    day_start = _parse_hhmm("00:00", target_date, tz)
    next_day = day_start + timedelta(days=1)
    for b in blocks:
        if b.start < day_start or b.end > next_day + timedelta(hours=3):  # allow 3h past midnight
            violations.append(ConstraintViolation(
                constraint_name="day_bounds",
                kind="hard",
                description=f"Block '{b.task_name}' extends beyond acceptable day bounds",
                penalty=BIG_M,
            ))

    # ----- Hard: Sleep window -----
    sleep = policy.sleep
    # "00:30" means next-day 00:30 (past midnight); detect by hour < 12
    latest_sleep_start = _parse_hhmm(sleep.latest_sleep_start, target_date, tz, next_day_if_early=True)
    # Blocks should not start at or after latest_sleep_start (non-sleep blocks)
    non_sleep = [b for b in blocks if b.scheduling_class != "fixed_recurring" or "sleep" not in b.task_name.lower()]
    for b in non_sleep:
        if b.start >= latest_sleep_start:
            violations.append(ConstraintViolation(
                constraint_name="sleep_window",
                kind="hard",
                description=f"Block '{b.task_name}' starts at {b.start.strftime('%H:%M')}, past sleep start {sleep.latest_sleep_start}",
                penalty=BIG_M,
            ))

    # ----- Soft: prefer earlier placement -----
    if policy.prefer_earlier:
        work_start = _parse_hhmm(policy.work.work_start, target_date, tz)
        for b in blocks:
            if b.start > work_start and b.scheduling_class not in ("hard_real_time", "fixed_recurring"):
                delay_hours = (b.start - work_start).total_seconds() / 3600
                violations.append(ConstraintViolation(
                    constraint_name="prefer_earlier",
                    kind="soft",
                    description=f"Block '{b.task_name}' delayed {delay_hours:.1f}h past work start",
                    penalty=delay_hours * 0.5,
                ))
    for b in blocks:
        dur = (b.end - b.start).total_seconds() / 60
        if dur < 30 and b.scheduling_class == "quota_based":
            violations.append(ConstraintViolation(
                constraint_name="fragmentation",
                kind="soft",
                description=f"Block '{b.task_name}' is only {dur:.0f}min, fragmented",
                penalty=2.0,
            ))

    return violations


# ---------------------------------------------------------------------------
# Slot-finding helpers
# ---------------------------------------------------------------------------

def _find_best_slot(
    task: SchedulingTask,
    free_slots: list[TimeWindow],
    policy: EnginePolicy,
    target_date: date,
    placed: list[PlacedBlock],
) -> Optional[TimeWindow]:
    """
    Find the best free TimeWindow to place a task.
    Respects preferred_windows, avoid_windows, and prefer_earlier policy.
    Returns None if no suitable slot found.
    """
    needed = timedelta(minutes=task.duration_minutes)
    candidates: list[tuple[float, TimeWindow]] = []

    for slot in free_slots:
        if slot.duration_minutes() < task.duration_minutes:
            continue

        # Try to fit within preferred_windows
        if task.preferred_windows:
            for pw in task.preferred_windows:
                start = max(slot.start, pw.start)
                end = min(slot.end, pw.end)
                if (end - start) >= needed:
                    # Score: prefer earlier + prefer matching preferred window
                    score = _slot_score(start, task, policy, target_date, preferred=True)
                    candidates.append((score, TimeWindow(start, start + needed)))
        else:
            # No preferred windows: use the slot start (or first available minute)
            start = slot.start
            end = start + needed
            if end <= slot.end:
                # Check avoid_windows
                candidate_win = TimeWindow(start, end)
                avoided = any(candidate_win.overlaps(aw) for aw in task.avoid_windows)
                score = _slot_score(start, task, policy, target_date, preferred=False)
                if avoided:
                    score += 50.0  # penalise but don't prohibit
                candidates.append((score, candidate_win))

    if not candidates:
        return None

    # Return window with lowest score
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def _slot_score(
    start: datetime,
    task: SchedulingTask,
    policy: EnginePolicy,
    target_date: date,
    preferred: bool,
) -> float:
    """Lower score = better slot."""
    tz = pytz.timezone(policy.user_timezone)
    work_start = _parse_hhmm(policy.work.work_start, target_date, tz)

    # Base: hours after work start (prefer earlier)
    delay_hours = max(0, (start - work_start).total_seconds() / 3600)
    score = delay_hours if policy.prefer_earlier else 0.0

    # Bonus for being in a preferred window
    if preferred:
        score -= 10.0

    # Small priority factor (higher priority = lower score bias)
    score -= task.priority * 0.01

    return score


# ---------------------------------------------------------------------------
# Core Scheduling Engine
# ---------------------------------------------------------------------------

class SchedulingEngine:
    """
    Deterministic, policy-driven scheduling engine.

    Usage:
        engine = SchedulingEngine()
        result = engine.plan_day(
            target_date=date.today(),
            user_id=1,
            tasks=[...],
            policy=EnginePolicy(...),
            existing_blocks=[...],  # already-committed frozen blocks
        )
    """

    def plan_day(
        self,
        target_date: date,
        user_id: int,
        tasks: list[SchedulingTask],
        policy: EnginePolicy,
        existing_blocks: Optional[list[PlacedBlock]] = None,
        generation_reason: str = "nightly_planning",
    ) -> ScheduleResult:
        """
        Full nightly planning algorithm.

        Algorithm:
          1. Seed placed list with existing frozen/committed blocks.
          2. Sort tasks by scheduling class priority then task priority.
          3. For each task:
             a. HARD_REAL_TIME / pinned → place at pinned_start (check no overlap).
             b. FIXED_RECURRING → find first slot matching preferred_windows.
             c. DEADLINE_DRIVEN → EDF: place as late as possible before deadline.
             d. QUOTA_BASED → fill free slots sorted by energy / preference.
             e. OPPORTUNISTIC → fill any remaining gap.
             f. RECOVERY → treated like OPPORTUNISTIC but higher priority.
          4. Evaluate constraints → violations.
          5. Compute score.
          6. Return ScheduleResult.
        """
        existing_blocks = existing_blocks or []
        placed: list[PlacedBlock] = list(existing_blocks)

        tz = pytz.timezone(policy.user_timezone)
        day_start = _parse_hhmm("00:00", target_date, tz)
        # Schedulable day: earliest_wake .. latest_sleep_start (may be next day)
        # This allows pre-work activities like morning workouts
        sched_start = _parse_hhmm(policy.sleep.earliest_wake, target_date, tz)
        sched_end = _parse_hhmm(policy.sleep.latest_sleep_start, target_date, tz, next_day_if_early=True)

        CLASS_ORDER = [
            "hard_real_time",
            "fixed_recurring",
            "deadline_driven",
            "quota_based",
            "recovery",
            "opportunistic",
        ]
        priority_key = {c: i for i, c in enumerate(CLASS_ORDER)}

        def sort_key(t: SchedulingTask) -> tuple:
            return (priority_key.get(t.scheduling_class, 99), -t.priority)

        sorted_tasks = sorted(tasks, key=sort_key)
        unscheduled: list[SchedulingTask] = []

        for task in sorted_tasks:
            if task.is_frozen:
                # Already in placed (part of existing_blocks); skip
                continue

            # Recompute free slots each iteration so placements are reflected
            free_slots = compute_free_slots(
                sched_start, sched_end, placed, policy.min_buffer_minutes
            )

            if task.scheduling_class == "hard_real_time":
                block = self._place_pinned(task, placed, tz)
            elif task.scheduling_class == "fixed_recurring":
                block = self._place_preferred(task, free_slots, policy, target_date)
            elif task.scheduling_class == "deadline_driven":
                block = self._place_deadline_driven(task, free_slots, policy, target_date, tz)
            else:
                # quota_based, opportunistic, recovery
                slot = _find_best_slot(task, free_slots, policy, target_date, placed)
                if slot:
                    block = PlacedBlock(
                        task_id=task.id,
                        task_name=task.name,
                        scheduling_class=task.scheduling_class,
                        start=slot.start,
                        end=slot.end,
                        reason=f"Placed in best available slot ({slot.start.strftime('%H:%M')})",
                    )
                else:
                    block = None

            if block:
                placed.append(block)
            else:
                unscheduled.append(task)
                logger.warning(
                    "Could not schedule task '%s' (class=%s) on %s for user %d",
                    task.name, task.scheduling_class, target_date, user_id,
                )

        violations = evaluate_constraints(placed, policy, target_date)
        score, breakdown = self._compute_score(violations, placed, policy, target_date)
        is_feasible = not any(v.kind == "hard" for v in violations)

        if unscheduled:
            for t in unscheduled:
                violations.append(ConstraintViolation(
                    constraint_name="unscheduled_task",
                    kind="soft",
                    description=f"Task '{t.name}' could not be scheduled (no free slot)",
                    penalty=10.0 * (t.priority / 100.0),
                ))

        return ScheduleResult(
            target_date=target_date,
            user_id=user_id,
            blocks=placed,
            violations=violations,
            score=score,
            score_breakdown=breakdown,
            generation_reason=generation_reason,
            is_feasible=is_feasible,
        )

    # ------------------------------------------------------------------
    # Intraday repair
    # ------------------------------------------------------------------

    def repair_day(
        self,
        target_date: date,
        user_id: int,
        missed_tasks: list[SchedulingTask],
        current_blocks: list[PlacedBlock],
        policy: EnginePolicy,
        now: Optional[datetime] = None,
        generation_reason: str = "intraday_repair",
    ) -> ScheduleResult:
        """
        Intraday repair: given tasks that were missed or skipped, try to
        reschedule them in the remaining day.

        Algorithm:
          1. Identify frozen blocks (start within freeze_horizon of now).
          2. Keep frozen blocks as-is.
          3. Attempt to place missed tasks in remaining free slots.
          4. Re-evaluate constraints.
        """
        now = now or datetime.now(timezone.utc)
        tz = pytz.timezone(policy.user_timezone)
        freeze_cutoff = now + timedelta(minutes=policy.freeze_horizon_minutes)

        # Separate frozen from moveable blocks
        frozen = [b for b in current_blocks if b.start <= freeze_cutoff or b.is_frozen]
        placed: list[PlacedBlock] = list(frozen)

        sched_end = _parse_hhmm(policy.sleep.latest_sleep_start, target_date, tz, next_day_if_early=True)

        for task in sorted(missed_tasks, key=lambda t: -t.priority):
            task.scheduling_class = "recovery"
            free_slots = compute_free_slots(
                max(now, freeze_cutoff), sched_end, placed, policy.min_buffer_minutes
            )
            slot = _find_best_slot(task, free_slots, policy, target_date, placed)
            if slot:
                block = PlacedBlock(
                    task_id=task.id,
                    task_name=task.name,
                    scheduling_class="recovery",
                    start=slot.start,
                    end=slot.end,
                    reason=f"Rescheduled missed task to {slot.start.strftime('%H:%M')}",
                )
                placed.append(block)

        violations = evaluate_constraints(placed, policy, target_date)
        score, breakdown = self._compute_score(violations, placed, policy, target_date)
        is_feasible = not any(v.kind == "hard" for v in violations)

        return ScheduleResult(
            target_date=target_date,
            user_id=user_id,
            blocks=placed,
            violations=violations,
            score=score,
            score_breakdown=breakdown,
            generation_reason=generation_reason,
            is_feasible=is_feasible,
        )

    # ------------------------------------------------------------------
    # Placement helpers
    # ------------------------------------------------------------------

    def _place_pinned(
        self,
        task: SchedulingTask,
        placed: list[PlacedBlock],
        tz: Any,
    ) -> Optional[PlacedBlock]:
        if not task.pinned_start:
            logger.error("HARD_REAL_TIME task '%s' has no pinned_start — skipping", task.name)
            return None
        start = task.pinned_start
        end = start + timedelta(minutes=task.duration_minutes)
        # Check for overlaps (hard constraint violation — log but still place, report violation)
        for b in placed:
            if b.end > start and b.start < end:
                logger.warning(
                    "Pinned task '%s' overlaps existing block '%s'",
                    task.name, b.task_name,
                )
        return PlacedBlock(
            task_id=task.id,
            task_name=task.name,
            scheduling_class="hard_real_time",
            start=start,
            end=end,
            is_protected=task.is_protected,
            is_frozen=True,
            reason="Pinned to fixed time slot",
        )

    def _place_preferred(
        self,
        task: SchedulingTask,
        free_slots: list[TimeWindow],
        policy: EnginePolicy,
        target_date: date,
    ) -> Optional[PlacedBlock]:
        slot = _find_best_slot(task, free_slots, policy, target_date, [])
        if not slot:
            return None
        return PlacedBlock(
            task_id=task.id,
            task_name=task.name,
            scheduling_class="fixed_recurring",
            start=slot.start,
            end=slot.end,
            reason=f"Placed in preferred window at {slot.start.strftime('%H:%M')}",
        )

    def _place_deadline_driven(
        self,
        task: SchedulingTask,
        free_slots: list[TimeWindow],
        policy: EnginePolicy,
        target_date: date,
        tz: Any,
    ) -> Optional[PlacedBlock]:
        """
        Earliest Deadline First (EDF): place as late as possible before deadline
        so we preserve earlier slots for higher-priority tasks, but ensure we
        finish before the deadline.
        """
        if not task.deadline:
            # No deadline → treat as quota_based
            return self._place_preferred(task, free_slots, policy, target_date)

        needed = timedelta(minutes=task.duration_minutes)
        # Filter slots that finish before deadline
        viable = [
            s for s in free_slots
            if s.duration_minutes() >= task.duration_minutes
            and s.start + needed <= task.deadline
        ]
        if not viable:
            return None

        # Pick latest viable start (EDF)
        best: Optional[TimeWindow] = None
        for s in sorted(viable, key=lambda x: x.start, reverse=True):
            latest_start = min(s.end - needed, task.deadline - needed)
            if latest_start >= s.start:
                best = TimeWindow(latest_start, latest_start + needed)
                break

        if not best:
            return None

        return PlacedBlock(
            task_id=task.id,
            task_name=task.name,
            scheduling_class="deadline_driven",
            start=best.start,
            end=best.end,
            reason=f"EDF placement, deadline {task.deadline.strftime('%H:%M')}",
        )

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _compute_score(
        self,
        violations: list[ConstraintViolation],
        placed: list[PlacedBlock],
        policy: EnginePolicy,
        target_date: date,
    ) -> tuple[float, dict[str, float]]:
        breakdown: dict[str, float] = {}

        # Constraint penalties
        hard_penalty = sum(v.penalty for v in violations if v.kind == "hard")
        soft_penalty = sum(v.penalty for v in violations if v.kind == "soft")
        breakdown["hard_violations"] = hard_penalty
        breakdown["soft_violations"] = soft_penalty

        # Fragmentation: count blocks < 30 min
        frag = sum(
            1 for b in placed
            if (b.end - b.start).total_seconds() / 60 < 30
            and b.scheduling_class not in ("hard_real_time",)
        )
        breakdown["fragmentation"] = frag * 2.0

        # Churn: blocks that were moved from an existing committed plan (not tracked here at engine level)
        breakdown["churn"] = 0.0

        total = hard_penalty + soft_penalty + breakdown["fragmentation"]
        return total, breakdown

    # ------------------------------------------------------------------
    # Utility: deterministic schedule fingerprint for idempotency
    # ------------------------------------------------------------------

    @staticmethod
    def fingerprint(result: ScheduleResult) -> str:
        data = [
            {"id": b.task_id, "start": b.start.isoformat(), "end": b.end.isoformat()}
            for b in sorted(result.blocks, key=lambda x: x.start)
        ]
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
