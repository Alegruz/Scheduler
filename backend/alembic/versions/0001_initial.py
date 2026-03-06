"""Initial schema: all MVP tables

Revision ID: 0001_initial
Revises: 
Create Date: 2026-03-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Enums ---
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE constraintkind AS ENUM ('hard', 'soft');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE taskstatus AS ENUM (
                'pending','scheduled','in_progress','done','skipped','missed'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE blockstatus AS ENUM (
                'proposed','committed','in_progress','done','skipped','cancelled'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE schedulingclass AS ENUM (
                'hard_real_time','fixed_recurring','deadline_driven',
                'quota_based','opportunistic','recovery'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE recurrencefrequency AS ENUM (
                'daily','weekdays','weekly','custom'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE auditeventkind AS ENUM (
                'block_created','block_moved','block_deleted','block_done','block_skipped',
                'plan_generated','plan_committed','policy_changed','constraint_violated',
                'repair_triggered','gcal_synced','ai_suggestion'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE notificationkind AS ENUM (
                'upcoming_block','block_start','block_done_prompt',
                'missed_task','plan_ready','repair_complete'
            );
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)
    op.execute("""
        DO $$ BEGIN
            CREATE TYPE syncdirection AS ENUM ('push','pull','bidirectional');
        EXCEPTION WHEN duplicate_object THEN null; END $$;
    """)

    # --- users ---
    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(320), nullable=False),
        sa.Column('hashed_password', sa.String(256), nullable=False),
        sa.Column('display_name', sa.String(128), nullable=False),
        sa.Column('timezone', sa.String(64), nullable=False, server_default='UTC'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email', name='uq_users_email'),
    )
    op.create_index('ix_users_email', 'users', ['email'], unique=True)

    # --- calendar_accounts ---
    op.create_table(
        'calendar_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('provider', sa.String(32), nullable=False, server_default='google'),
        sa.Column('external_account_email', sa.String(320), nullable=True),
        sa.Column('access_token', sa.Text(), nullable=True),
        sa.Column('refresh_token', sa.Text(), nullable=True),
        sa.Column('token_expiry', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_direction', sa.Enum('push', 'pull', 'bidirectional', name='syncdirection'), nullable=False, server_default='push'),
        sa.Column('target_calendar_id', sa.String(256), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'provider', 'external_account_email', name='uq_calendar_account'),
    )
    op.create_index('ix_calendar_accounts_user_id', 'calendar_accounts', ['user_id'])

    # --- policy_profiles ---
    op.create_table(
        'policy_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_default', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('policy_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'name', name='uq_policy_profile_name'),
    )
    op.create_index('ix_policy_profiles_user_id', 'policy_profiles', ['user_id'])

    # --- constraints ---
    op.create_table(
        'constraints',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('policy_profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('kind', sa.Enum('hard', 'soft', name='constraintkind'), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('penalty_weight', sa.Float(), nullable=False, server_default='1.0'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['policy_profile_id'], ['policy_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_constraints_policy_profile_id', 'constraints', ['policy_profile_id'])

    # --- recovery_rules ---
    op.create_table(
        'recovery_rules',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('policy_profile_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('task_category', sa.String(64), nullable=True),
        sa.Column('rule_config', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['policy_profile_id'], ['policy_profiles.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )

    # --- goals ---
    op.create_table(
        'goals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(256), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(64), nullable=True),
        sa.Column('weekly_quota_minutes', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_goals_user_id', 'goals', ['user_id'])

    # --- task_templates ---
    op.create_table(
        'task_templates',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('goal_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('name', sa.String(256), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(64), nullable=True),
        sa.Column('scheduling_class', sa.Enum(
            'hard_real_time','fixed_recurring','deadline_driven',
            'quota_based','opportunistic','recovery', name='schedulingclass'
        ), nullable=False),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('min_duration_minutes', sa.Integer(), nullable=True),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('is_recurring', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('recurrence_frequency', sa.Enum('daily','weekdays','weekly','custom', name='recurrencefrequency'), nullable=True),
        sa.Column('recurrence_rrule', sa.Text(), nullable=True),
        sa.Column('preferred_windows', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('avoid_windows', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('is_pinned', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('pinned_start_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('deadline_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['goal_id'], ['goals.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_templates_user_id', 'task_templates', ['user_id'])
    op.create_index('ix_task_templates_goal_id', 'task_templates', ['goal_id'])

    # --- task_instances ---
    op.create_table(
        'task_instances',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('template_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('name', sa.String(256), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('category', sa.String(64), nullable=True),
        sa.Column('scheduling_class', sa.Enum(
            'hard_real_time','fixed_recurring','deadline_driven',
            'quota_based','opportunistic','recovery', name='schedulingclass'
        ), nullable=False),
        sa.Column('status', sa.Enum('pending','scheduled','in_progress','done','skipped','missed', name='taskstatus'), nullable=False, server_default='pending'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='50'),
        sa.Column('duration_minutes', sa.Integer(), nullable=False),
        sa.Column('due_date', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['template_id'], ['task_templates.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_instances_user_status', 'task_instances', ['user_id', 'status'])
    op.create_index('ix_task_instances_user_due', 'task_instances', ['user_id', 'due_date'])

    # --- schedule_plans ---
    op.create_table(
        'schedule_plans',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('plan_date', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_committed', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('committed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('generation_reason', sa.String(256), nullable=True),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('score_breakdown', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_schedule_plans_user_date', 'schedule_plans', ['user_id', 'plan_date'])

    # --- time_blocks ---
    op.create_table(
        'time_blocks',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('task_instance_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('start_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('end_time', sa.DateTime(timezone=True), nullable=False),
        sa.Column('status', sa.Enum('proposed','committed','in_progress','done','skipped','cancelled', name='blockstatus'), nullable=False, server_default='proposed'),
        sa.Column('is_frozen', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_protected', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('scheduling_class', sa.Enum(
            'hard_real_time','fixed_recurring','deadline_driven',
            'quota_based','opportunistic','recovery', name='schedulingclass'
        ), nullable=False),
        sa.Column('move_reason', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['schedule_plans.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['task_instance_id'], ['task_instances.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_time_blocks_user_start', 'time_blocks', ['user_id', 'start_time'])
    op.create_index('ix_time_blocks_plan_start', 'time_blocks', ['plan_id', 'start_time'])

    # --- schedule_revisions ---
    op.create_table(
        'schedule_revisions',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('revision_number', sa.Integer(), nullable=False),
        sa.Column('author', sa.String(64), nullable=False, server_default='engine'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('diff', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['plan_id'], ['schedule_plans.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('plan_id', 'revision_number', name='uq_revision_number'),
    )
    op.create_index('ix_schedule_revisions_plan_id', 'schedule_revisions', ['plan_id'])

    # --- audit_events ---
    op.create_table(
        'audit_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.Enum(
            'block_created','block_moved','block_deleted','block_done','block_skipped',
            'plan_generated','plan_committed','policy_changed','constraint_violated',
            'repair_triggered','gcal_synced','ai_suggestion',
            name='auditeventkind'
        ), nullable=False),
        sa.Column('actor', sa.String(64), nullable=False, server_default='engine'),
        sa.Column('time_block_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('task_instance_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('plan_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('explanation', sa.Text(), nullable=False),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['time_block_id'], ['time_blocks.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['task_instance_id'], ['task_instances.id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['plan_id'], ['schedule_plans.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_audit_events_user_created', 'audit_events', ['user_id', 'created_at'])

    # --- notification_events ---
    op.create_table(
        'notification_events',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('kind', sa.Enum(
            'upcoming_block','block_start','block_done_prompt',
            'missed_task','plan_ready','repair_complete',
            name='notificationkind'
        ), nullable=False),
        sa.Column('time_block_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column('title', sa.String(256), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('is_sent', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('fcm_message_id', sa.String(256), nullable=True),
        sa.Column('user_action', sa.String(64), nullable=True),
        sa.Column('action_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['time_block_id'], ['time_blocks.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_notification_events_scheduled', 'notification_events', ['user_id', 'scheduled_at', 'is_sent'])

    # --- domain_experts ---
    op.create_table(
        'domain_experts',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('module_path', sa.String(256), nullable=False),
        sa.Column('config', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name', name='uq_domain_expert_name'),
    )

    # --- context_signals ---
    op.create_table(
        'context_signals',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('signal_type', sa.String(64), nullable=False),
        sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
        sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('source', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_context_signals_user_type_recorded', 'context_signals', ['user_id', 'signal_type', 'recorded_at'])

    # --- energy_profiles ---
    op.create_table(
        'energy_profiles',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('hourly_energy', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='[]'),
        sa.Column('preferred_sleep_start', sa.String(5), nullable=True),
        sa.Column('preferred_sleep_end', sa.String(5), nullable=True),
        sa.Column('preferred_workout_window', sa.String(32), nullable=True),
        sa.Column('work_start', sa.String(5), nullable=False, server_default='10:00'),
        sa.Column('work_end', sa.String(5), nullable=False, server_default='19:00'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', name='uq_energy_profile_user'),
    )

    # --- sync_mappings ---
    op.create_table(
        'sync_mappings',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False, server_default=sa.text('gen_random_uuid()')),
        sa.Column('calendar_account_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('time_block_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('external_event_id', sa.String(256), nullable=False),
        sa.Column('external_calendar_id', sa.String(256), nullable=False),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('sync_hash', sa.String(64), nullable=True),
        sa.Column('sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['calendar_account_id'], ['calendar_accounts.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['time_block_id'], ['time_blocks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('calendar_account_id', 'external_event_id', name='uq_sync_mapping_external'),
        sa.UniqueConstraint('calendar_account_id', 'time_block_id', name='uq_sync_mapping_block'),
    )


def downgrade() -> None:
    op.drop_table('sync_mappings')
    op.drop_table('energy_profiles')
    op.drop_table('context_signals')
    op.drop_table('domain_experts')
    op.drop_table('notification_events')
    op.drop_table('audit_events')
    op.drop_table('schedule_revisions')
    op.drop_table('time_blocks')
    op.drop_table('schedule_plans')
    op.drop_table('task_instances')
    op.drop_table('task_templates')
    op.drop_table('goals')
    op.drop_table('recovery_rules')
    op.drop_table('constraints')
    op.drop_table('policy_profiles')
    op.drop_table('calendar_accounts')
    op.drop_table('users')

    for enum_name in [
        'syncdirection', 'notificationkind', 'auditeventkind',
        'recurrencefrequency', 'schedulingclass', 'blockstatus',
        'taskstatus', 'constraintkind',
    ]:
        op.execute(f'DROP TYPE IF EXISTS {enum_name}')
