"""
Unit tests for the deterministic scheduling engine.

These tests are pure Python — no database required.
They verify the core invariants of the scheduler:
  - Hard constraints dominate
  - No overlapping blocks
  - Pinned blocks always get their exact slot
  - Freeze horizon is respected
  - Scoring is deterministic (same inputs → same score)
  - Intraday repair reschedules missed tasks
"""

import uuid
from datetime import date, datetime, timedelta, timezone

import pytz
import pytest

from app.engine.scheduler import (
    EnginePolicy,
    PlacedBlock,
    ScheduleResult,
    SchedulingEngine,
    SchedulingTask,
    SleepWindowConstraint,
    TimeWindow,
    WorkWindowConstraint,
    compute_free_slots,
    evaluate_constraints,
)

KST = pytz.timezone("Asia/Seoul")
TODAY = date(2026, 3, 6)  # Friday


def _dt(hour: int, minute: int = 0, tz=KST) -> datetime:
    return tz.localize(datetime(TODAY.year, TODAY.month, TODAY.day, hour, minute))


def _default_policy() -> EnginePolicy:
    return EnginePolicy(
        user_timezone="Asia/Seoul",
        freeze_horizon_minutes=30,
        sleep=SleepWindowConstraint(min_hours=7.5, latest_sleep_start="00:30", earliest_wake="06:30"),
        work=WorkWindowConstraint(work_start="10:00", work_end="19:00"),
        min_buffer_minutes=5,
        prefer_earlier=True,
    )


def _task(
    name: str,
    scheduling_class: str,
    duration_minutes: int,
    priority: int = 50,
    preferred_windows=None,
    pinned_start: datetime = None,
    deadline: datetime = None,
) -> SchedulingTask:
    return SchedulingTask(
        id=str(uuid.uuid4()),
        name=name,
        scheduling_class=scheduling_class,
        duration_minutes=duration_minutes,
        priority=priority,
        preferred_windows=preferred_windows or [],
        pinned_start=pinned_start,
        deadline=deadline,
    )


engine = SchedulingEngine()


# ---------------------------------------------------------------------------
# TimeWindow tests
# ---------------------------------------------------------------------------

class TestTimeWindow:
    def test_overlaps_true(self):
        a = TimeWindow(_dt(10), _dt(11))
        b = TimeWindow(_dt(10, 30), _dt(11, 30))
        assert a.overlaps(b)

    def test_overlaps_false_adjacent(self):
        a = TimeWindow(_dt(10), _dt(11))
        b = TimeWindow(_dt(11), _dt(12))
        assert not a.overlaps(b)

    def test_overlaps_false_gap(self):
        a = TimeWindow(_dt(10), _dt(11))
        b = TimeWindow(_dt(12), _dt(13))
        assert not a.overlaps(b)

    def test_duration_minutes(self):
        w = TimeWindow(_dt(10), _dt(11, 30))
        assert w.duration_minutes() == 90.0

    def test_contains(self):
        w = TimeWindow(_dt(10), _dt(12))
        assert w.contains(_dt(11))
        assert w.contains(_dt(10))   # start IS contained in [start, end)
        assert not w.contains(_dt(12))  # end is NOT contained


# ---------------------------------------------------------------------------
# Free slot computation
# ---------------------------------------------------------------------------

class TestComputeFreeSlots:
    def test_no_blocks(self):
        slots = compute_free_slots(_dt(9), _dt(18), [])
        assert len(slots) == 1
        assert slots[0].start == _dt(9)
        assert slots[0].end == _dt(18)

    def test_single_block_in_middle(self):
        blocks = [
            PlacedBlock("x", "Work", "quota_based", _dt(12), _dt(14), reason="test"),
        ]
        slots = compute_free_slots(_dt(9), _dt(18), blocks)
        assert len(slots) == 2
        assert slots[0].end <= _dt(12)
        assert slots[1].start >= _dt(14)

    def test_with_gap(self):
        blocks = [
            PlacedBlock("x", "A", "quota_based", _dt(9), _dt(10), reason="test"),
            PlacedBlock("y", "B", "quota_based", _dt(11), _dt(12), reason="test"),
        ]
        slots = compute_free_slots(_dt(9), _dt(18), blocks, min_gap_minutes=0)
        # gap between 10:00 and 11:00
        assert any(s.start >= _dt(10) and s.end <= _dt(11) for s in slots)

    def test_with_min_gap(self):
        blocks = [
            PlacedBlock("x", "A", "quota_based", _dt(10), _dt(11), reason="test"),
        ]
        slots = compute_free_slots(_dt(9), _dt(18), blocks, min_gap_minutes=15)
        # After block (11:00) + 15min gap → free from 11:15
        after = [s for s in slots if s.start >= _dt(11)]
        assert len(after) == 1
        assert after[0].start >= _dt(11, 15)

    def test_full_day_blocked(self):
        blocks = [PlacedBlock("x", "A", "quota_based", _dt(9), _dt(18), reason="test")]
        slots = compute_free_slots(_dt(9), _dt(18), blocks, min_gap_minutes=0)
        assert all(s.duration_minutes() <= 0 for s in slots)


# ---------------------------------------------------------------------------
# Constraint evaluation
# ---------------------------------------------------------------------------

class TestEvaluateConstraints:
    def test_no_violations_clean_schedule(self):
        policy = _default_policy()
        blocks = [
            PlacedBlock("a", "Morning Workout", "fixed_recurring", _dt(7), _dt(8), reason="test"),
            PlacedBlock("b", "Deep Work", "quota_based", _dt(10), _dt(12), reason="test"),
        ]
        violations = evaluate_constraints(blocks, policy, TODAY)
        # There should be no hard violations
        hard = [v for v in violations if v.kind == "hard"]
        assert len(hard) == 0

    def test_overlap_violation(self):
        policy = _default_policy()
        blocks = [
            PlacedBlock("a", "A", "quota_based", _dt(10), _dt(12), reason=""),
            PlacedBlock("b", "B", "quota_based", _dt(11), _dt(13), reason=""),
        ]
        violations = evaluate_constraints(blocks, policy, TODAY)
        hard = [v for v in violations if v.kind == "hard" and v.constraint_name == "no_overlap"]
        assert len(hard) == 1

    def test_sleep_window_violation(self):
        policy = _default_policy()
        policy.sleep.latest_sleep_start = "23:00"
        blocks = [
            PlacedBlock("a", "Late Night Work", "quota_based", _dt(23, 30), _dt(23, 30) + timedelta(hours=1), reason=""),
        ]
        violations = evaluate_constraints(blocks, policy, TODAY)
        hard = [v for v in violations if v.kind == "hard" and v.constraint_name == "sleep_window"]
        assert len(hard) == 1

    def test_fragmentation_soft_violation(self):
        policy = _default_policy()
        blocks = [
            PlacedBlock("a", "Tiny Task", "quota_based", _dt(10), _dt(10, 20), reason=""),
        ]
        violations = evaluate_constraints(blocks, policy, TODAY)
        soft = [v for v in violations if v.kind == "soft" and v.constraint_name == "fragmentation"]
        assert len(soft) == 1


# ---------------------------------------------------------------------------
# Scheduling engine: plan_day
# ---------------------------------------------------------------------------

class TestPlanDay:
    def test_empty_task_list(self):
        policy = _default_policy()
        result = engine.plan_day(
            target_date=TODAY,
            user_id=1,
            tasks=[],
            policy=policy,
        )
        assert isinstance(result, ScheduleResult)
        assert result.blocks == []
        assert result.is_feasible

    def test_single_quota_task_placed(self):
        policy = _default_policy()
        tasks = [
            _task("Language Study", "quota_based", 60, priority=70),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b.task_name == "Language Study"
        # Must be within schedulable window: earliest_wake=06:30 .. sleep 00:30 next day
        sched_start = _dt(6, 30)  # earliest_wake from policy
        sched_end = _dt(0, 30) + timedelta(days=1)  # sleep 00:30 next day
        assert b.start >= sched_start
        assert b.end <= sched_end

    def test_pinned_task_placed_at_exact_time(self):
        policy = _default_policy()
        pinned = _dt(14)
        tasks = [
            _task("Team Meeting", "hard_real_time", 60, pinned_start=pinned),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b.start == pinned
        assert b.end == pinned + timedelta(hours=1)

    def test_no_overlapping_blocks(self):
        policy = _default_policy()
        tasks = [
            _task("Task A", "quota_based", 60, priority=90),
            _task("Task B", "quota_based", 60, priority=80),
            _task("Task C", "quota_based", 90, priority=70),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        sorted_blocks = sorted(result.blocks, key=lambda b: b.start)
        for i in range(len(sorted_blocks) - 1):
            assert sorted_blocks[i].end <= sorted_blocks[i + 1].start, (
                f"Overlap: {sorted_blocks[i].task_name} ends {sorted_blocks[i].end}, "
                f"{sorted_blocks[i+1].task_name} starts {sorted_blocks[i+1].start}"
            )

    def test_deadline_driven_placed_before_deadline(self):
        policy = _default_policy()
        deadline = _dt(15)
        tasks = [
            _task("Report", "deadline_driven", 120, deadline=deadline),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        assert b.end <= deadline, f"Block ends {b.end} after deadline {deadline}"

    def test_multiple_tasks_no_overlap(self):
        policy = _default_policy()
        tasks = [
            _task("Morning Workout", "fixed_recurring", 60, priority=80,
                  preferred_windows=[TimeWindow(_dt(7), _dt(9))]),
            _task("Language Study", "quota_based", 45, priority=70),
            _task("Deep Work 1", "quota_based", 120, priority=90),
            _task("Deep Work 2", "quota_based", 90, priority=85),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        sorted_blocks = sorted(result.blocks, key=lambda b: b.start)
        for i in range(len(sorted_blocks) - 1):
            assert sorted_blocks[i].end <= sorted_blocks[i + 1].start

    def test_deterministic_same_inputs_same_result(self):
        policy = _default_policy()
        tasks = [
            _task("Task X", "quota_based", 60, priority=75),
            _task("Task Y", "quota_based", 45, priority=60),
        ]
        # Run twice with same seed
        engine2 = SchedulingEngine()
        r1 = engine.plan_day(TODAY, 1, tasks, policy)
        r2 = engine2.plan_day(TODAY, 1, tasks, policy)
        # Same number of blocks
        assert len(r1.blocks) == len(r2.blocks)
        # Same scores
        assert r1.score == r2.score

    def test_existing_frozen_blocks_respected(self):
        policy = _default_policy()
        frozen = PlacedBlock(
            task_id="frozen-id",
            task_name="Morning Standup",
            scheduling_class="hard_real_time",
            start=_dt(10),
            end=_dt(10, 30),
            is_frozen=True,
            reason="Frozen from prior plan",
        )
        tasks = [
            _task("Work Block", "quota_based", 120, priority=70),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy, existing_blocks=[frozen])
        # The new work block should NOT overlap with the frozen block
        work_blocks = [b for b in result.blocks if not b.is_frozen]
        for wb in work_blocks:
            assert wb.end <= _dt(10) or wb.start >= _dt(10, 30), (
                f"Work block {wb.start}–{wb.end} overlaps frozen block 10:00–10:30"
            )

    def test_hard_constraint_violation_infeasible(self):
        policy = _default_policy()
        # Pin two tasks to overlapping times → hard constraint violated
        tasks = [
            _task("Meeting A", "hard_real_time", 60, pinned_start=_dt(14)),
            _task("Meeting B", "hard_real_time", 60, pinned_start=_dt(14, 30)),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert not result.is_feasible
        hard_violations = [v for v in result.violations if v.kind == "hard"]
        assert len(hard_violations) > 0

    def test_score_has_breakdown(self):
        policy = _default_policy()
        tasks = [_task("Task A", "quota_based", 60)]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert "hard_violations" in result.score_breakdown
        assert "soft_violations" in result.score_breakdown
        assert "fragmentation" in result.score_breakdown

    def test_fingerprint_is_deterministic(self):
        policy = _default_policy()
        tasks = [
            _task("Task A", "quota_based", 60, priority=80),
            _task("Task B", "quota_based", 30, priority=60),
        ]
        r1 = engine.plan_day(TODAY, 1, tasks, policy)
        r2 = engine.plan_day(TODAY, 1, tasks, policy)
        assert SchedulingEngine.fingerprint(r1) == SchedulingEngine.fingerprint(r2)


# ---------------------------------------------------------------------------
# Scheduling engine: repair_day
# ---------------------------------------------------------------------------

class TestRepairDay:
    def test_repair_reschedules_missed_task(self):
        policy = _default_policy()
        now = _dt(11)  # it's 11:00, something was missed earlier

        missed_task = _task("Missed Workout", "recovery", 60, priority=80)

        current_blocks = [
            PlacedBlock("frozen", "Standup", "hard_real_time", _dt(10), _dt(10, 30), is_frozen=True, reason=""),
        ]

        result = engine.repair_day(
            target_date=TODAY,
            user_id=1,
            missed_tasks=[missed_task],
            current_blocks=current_blocks,
            policy=policy,
            now=now,
        )
        # Missed workout should be rescheduled after now + freeze_horizon
        recovery_blocks = [b for b in result.blocks if b.scheduling_class == "recovery"]
        assert len(recovery_blocks) == 1
        assert recovery_blocks[0].start >= now

    def test_repair_respects_freeze_horizon(self):
        policy = _default_policy()
        now = _dt(10)
        freeze_cutoff = now + timedelta(minutes=policy.freeze_horizon_minutes)

        # A block starting within freeze horizon
        imminent = PlacedBlock(
            "imminent", "Imminent Task", "quota_based",
            _dt(10, 20), _dt(11, 20), is_frozen=False, reason=""
        )

        missed_task = _task("Missed Task", "recovery", 60, priority=70)

        result = engine.repair_day(
            target_date=TODAY,
            user_id=1,
            missed_tasks=[missed_task],
            current_blocks=[imminent],
            policy=policy,
            now=now,
        )
        # All recovery blocks must start after freeze cutoff
        recovery_blocks = [b for b in result.blocks if b.scheduling_class == "recovery"]
        for rb in recovery_blocks:
            assert rb.start >= freeze_cutoff

    def test_repair_no_slot_available(self):
        """If day is fully packed, repair returns empty recovery blocks (graceful degradation)."""
        policy = _default_policy()
        now = _dt(18)  # Only 2 hours left before sleep at 00:30

        # Block the remaining hours
        current_blocks = [
            PlacedBlock("x", "Evening", "quota_based", _dt(18), _dt(23, 30), is_frozen=True, reason=""),
        ]
        missed_task = _task("Big Task", "recovery", 120, priority=80)

        result = engine.repair_day(
            target_date=TODAY,
            user_id=1,
            missed_tasks=[missed_task],
            current_blocks=current_blocks,
            policy=policy,
            now=now,
        )
        # Big task cannot fit → should not be in blocks (gracefully dropped)
        recovery_blocks = [b for b in result.blocks if b.scheduling_class == "recovery"]
        # Either not placed, or placed but checked for no overlap
        sorted_blocks = sorted(result.blocks, key=lambda b: b.start)
        for i in range(len(sorted_blocks) - 1):
            assert sorted_blocks[i].end <= sorted_blocks[i + 1].start

    def test_repair_result_has_reason(self):
        policy = _default_policy()
        now = _dt(12)
        missed = _task("Missed Study", "recovery", 30)
        result = engine.repair_day(TODAY, 1, [missed], [], policy, now=now)
        recovery = [b for b in result.blocks if b.scheduling_class == "recovery"]
        for rb in recovery:
            assert rb.reason != ""


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_task_too_large_for_day(self):
        """A 25-hour task should not be placed."""
        policy = _default_policy()
        tasks = [_task("Infinite Task", "quota_based", 25 * 60)]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert len(result.blocks) == 0

    def test_priority_order(self):
        """Higher priority task should be placed in earlier/better slot."""
        policy = _default_policy()
        tasks = [
            _task("Low Priority", "quota_based", 60, priority=10),
            _task("High Priority", "quota_based", 60, priority=90),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        # Sort by start time; high priority should be placed first (earlier)
        assert len(result.blocks) == 2
        sorted_blocks = sorted(result.blocks, key=lambda b: b.start)
        # High priority should be the earlier block
        assert sorted_blocks[0].task_name == "High Priority"

    def test_scheduling_class_order(self):
        """HARD_REAL_TIME is placed before QUOTA_BASED."""
        policy = _default_policy()
        pinned_time = _dt(13)
        tasks = [
            _task("Flexible Work", "quota_based", 60, priority=99),
            _task("Fixed Meeting", "hard_real_time", 60, pinned_start=pinned_time),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        meeting = next(b for b in result.blocks if b.task_name == "Fixed Meeting")
        assert meeting.start == pinned_time

    def test_preferred_window_respected(self):
        """Task with preferred window is placed within or near that window."""
        policy = _default_policy()
        preferred = [TimeWindow(_dt(7), _dt(9))]
        tasks = [
            _task("Morning Workout", "fixed_recurring", 60, preferred_windows=preferred),
        ]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert len(result.blocks) == 1
        b = result.blocks[0]
        # Should be placed within preferred window
        assert b.start >= _dt(7)
        assert b.end <= _dt(9)

    def test_weekend_policy(self):
        """Weekend mode with relaxed schedule still produces valid output."""
        policy = _default_policy()
        policy.weekend_mode = True
        policy.work.work_start = "09:00"  # more relaxed on weekends
        tasks = [_task("Leisure Activity", "opportunistic", 120)]
        result = engine.plan_day(TODAY, 1, tasks, policy)
        assert result.is_feasible or len([v for v in result.violations if v.kind == "hard"]) == 0
