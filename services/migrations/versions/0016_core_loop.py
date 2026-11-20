"""0016 core loop — drafts, version creator, persisted evals with a server-held baseline, append-only audit (feature 026)

Additive only (no preflight: every new table is empty and every new column is nullable or defaulted). data-model.md:
  * agents: `description text NOT NULL DEFAULT ''`, `draft jsonb NULL` (D1), `status_before_archive agent_status NULL`
    (D4); plus the FK-target unique `uq_agents_ws_id (workspace_id, id)` the composite eval FKs point at.
  * agent_versions: `created_by text NULL` (D2; set at INSERT, never updated — the 0002 freeze trigger is unaffected).
  * eval_suites / eval_cases / eval_runs / eval_results / eval_baselines / audit_logs — every table follows the 0008
    tenant pattern (workspace FK + index, RLS ENABLE + FORCE, `<t>_ws_isolation`, grants to ac_app). Cross-table refs
    are composite `(workspace_id, …)` FKs because RI bypasses RLS (025 G8); RESTRICT unless stated.
  * `eval_baselines_run_check` trigger: a frozen baseline must be a COMPLETED run of the SAME suite (23514).
  * audit_logs (and eval_runs / eval_results): `SELECT, INSERT` only for ac_app — UPDATE/DELETE are REVOKEd (42501).

Revision ID: 0016
Revises: 0015
"""
from __future__ import annotations

from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_TABLES = ("eval_suites", "eval_cases", "eval_runs", "eval_results", "eval_baselines", "audit_logs")
_RLS = ("USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid) "
        "WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)")


def upgrade() -> None:
    # ---- changed tables --------------------------------------------------------------------------------------------
    op.execute("ALTER TABLE agents ADD COLUMN description text NOT NULL DEFAULT ''")
    op.execute("ALTER TABLE agents ADD COLUMN draft jsonb NULL")
    op.execute("ALTER TABLE agents ADD COLUMN status_before_archive agent_status NULL")
    op.execute("ALTER TABLE agents ADD CONSTRAINT uq_agents_ws_id UNIQUE (workspace_id, id)")
    op.execute("ALTER TABLE agent_versions ADD COLUMN created_by text NULL")

    # ---- eval_suites -----------------------------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_suites (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            agent_id     uuid NOT NULL,
            name         text NOT NULL CHECK (char_length(name) BETWEEN 1 AND 80),
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_eval_suites_agent_name UNIQUE (agent_id, name),
            CONSTRAINT uq_eval_suites_ws_id UNIQUE (workspace_id, id),
            CONSTRAINT fk_eval_suites_agent FOREIGN KEY (workspace_id, agent_id)
                REFERENCES agents (workspace_id, id) ON DELETE RESTRICT
        )""")

    # ---- eval_cases ------------------------------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_cases (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id    uuid NOT NULL REFERENCES workspaces(id),
            suite_id        uuid NOT NULL,
            input           jsonb NOT NULL,
            rubric          jsonb NOT NULL,
            required_fields text[] NOT NULL DEFAULT '{}',
            expected        jsonb NULL,
            content_hash    text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_eval_cases_rubric_dims CHECK (
                jsonb_typeof(rubric) = 'array' AND jsonb_array_length(rubric) BETWEEN 1 AND 8),
            CONSTRAINT fk_eval_cases_suite FOREIGN KEY (workspace_id, suite_id)
                REFERENCES eval_suites (workspace_id, id) ON DELETE CASCADE
        )""")
    op.execute("CREATE INDEX ix_eval_cases_suite ON eval_cases (suite_id)")

    # ---- eval_runs -------------------------------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_runs (
            id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id     uuid NOT NULL REFERENCES workspaces(id),
            agent_id         uuid NOT NULL,
            suite_id         uuid NOT NULL,
            version_id       uuid NOT NULL,
            status           text NOT NULL CHECK (status IN ('completed','failed')),
            verdict          text NULL CHECK (verdict IN ('pass','fail','low_confidence')),
            reason_code      text NOT NULL,
            reason           text NOT NULL,
            baseline_run_id  uuid NULL,
            baseline_origin  text NULL CHECK (baseline_origin IN ('automatic','frozen')),
            quality          numeric NULL,
            quality_delta    numeric NULL,
            cost_usd         numeric NULL,
            cost_delta_pct   numeric NULL,
            latency_ms       numeric NULL,
            latency_delta_ms numeric NULL,
            dimensions       jsonb NOT NULL DEFAULT '[]'::jsonb,
            compared_cases   int NOT NULL,
            new_cases        int NOT NULL,
            dropped_cases    int NOT NULL,
            total_cases      int NOT NULL,
            pairwise_rate    numeric NULL,
            policy           jsonb NOT NULL,
            judge_version    text NULL,
            created_by       text NOT NULL,
            started_at       timestamptz NOT NULL DEFAULT now(),
            finished_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_eval_runs_verdict_iff_completed CHECK ((status = 'failed') = (verdict IS NULL)),
            CONSTRAINT ck_eval_runs_baseline_origin CHECK ((baseline_run_id IS NULL) = (baseline_origin IS NULL)),
            CONSTRAINT uq_eval_runs_ws_id UNIQUE (workspace_id, id),
            CONSTRAINT fk_eval_runs_agent FOREIGN KEY (workspace_id, agent_id)
                REFERENCES agents (workspace_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_eval_runs_suite FOREIGN KEY (workspace_id, suite_id)
                REFERENCES eval_suites (workspace_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_eval_runs_version FOREIGN KEY (version_id, agent_id)
                REFERENCES agent_versions (id, agent_id) ON DELETE RESTRICT,
            CONSTRAINT fk_eval_runs_baseline FOREIGN KEY (workspace_id, baseline_run_id)
                REFERENCES eval_runs (workspace_id, id) ON DELETE RESTRICT
        )""")
    op.execute("CREATE INDEX ix_eval_runs_agent_finished ON eval_runs (agent_id, finished_at DESC)")
    op.execute("CREATE INDEX ix_eval_runs_auto_baseline ON eval_runs (suite_id, version_id, finished_at DESC) "
               "WHERE status = 'completed'")
    op.execute("CREATE INDEX ix_eval_runs_ws_finished ON eval_runs (workspace_id, finished_at DESC)")

    # ---- eval_results ----------------------------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE eval_results (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id         uuid NOT NULL REFERENCES workspaces(id),
            eval_run_id          uuid NOT NULL,
            case_id              uuid NOT NULL,          -- not an FK: evidence outlives a case edit/delete
            case_hash            text NOT NULL,
            run_id               uuid NULL,
            passed_deterministic boolean NOT NULL,
            quality              numeric NULL,
            rubric_scores        jsonb NOT NULL DEFAULT '{}'::jsonb,
            pairwise_winner      text NULL,
            judge_low_confidence boolean NOT NULL,
            failure_reason       text NULL,
            hard_failure         boolean NOT NULL,
            candidate_output     text NOT NULL,
            cost_usd             numeric NULL,
            cost_is_estimate     boolean NOT NULL DEFAULT false,
            latency_ms           int NULL,
            in_comparison        boolean NOT NULL,
            CONSTRAINT uq_eval_results_run_case UNIQUE (eval_run_id, case_id),
            CONSTRAINT fk_eval_results_run FOREIGN KEY (workspace_id, eval_run_id)
                REFERENCES eval_runs (workspace_id, id) ON DELETE CASCADE
        )""")

    # ---- eval_baselines (frozen pointer; absence ⇒ automatic rule) -------------------------------------------------
    op.execute("""
        CREATE TABLE eval_baselines (
            suite_id     uuid PRIMARY KEY,
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            eval_run_id  uuid NOT NULL,
            frozen_by    text NOT NULL,
            frozen_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_eval_baselines_suite FOREIGN KEY (workspace_id, suite_id)
                REFERENCES eval_suites (workspace_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_eval_baselines_run FOREIGN KEY (workspace_id, eval_run_id)
                REFERENCES eval_runs (workspace_id, id) ON DELETE RESTRICT
        )""")
    op.execute("""
        CREATE FUNCTION eval_baselines_run_check() RETURNS trigger AS $$
        DECLARE r record;
        BEGIN
            SELECT status, suite_id INTO r FROM eval_runs
             WHERE id = NEW.eval_run_id AND workspace_id = NEW.workspace_id;
            IF NOT FOUND OR r.status <> 'completed' OR r.suite_id <> NEW.suite_id THEN
                RAISE EXCEPTION 'eval_baselines_run_check: baseline must be a completed run of the same suite'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$ LANGUAGE plpgsql""")
    op.execute("CREATE TRIGGER eval_baselines_run_check BEFORE INSERT OR UPDATE ON eval_baselines "
               "FOR EACH ROW EXECUTE FUNCTION eval_baselines_run_check()")

    # ---- audit_logs (append-only) ----------------------------------------------------------------------------------
    op.execute("""
        CREATE TABLE audit_logs (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id uuid NOT NULL REFERENCES workspaces(id),
            actor        text NOT NULL,
            action       text NOT NULL,
            target_type  text NULL,
            target_id    uuid NULL,
            meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now()
        )""")
    op.execute("CREATE INDEX ix_audit_logs_ws_created ON audit_logs (workspace_id, created_at DESC)")

    # ---- tenant pattern on every new table -------------------------------------------------------------------------
    for t in _TABLES:
        op.execute(f"CREATE INDEX ix_{t}_workspace ON {t} (workspace_id)")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY {t}_ws_isolation ON {t} {_RLS}")
    for t in ("eval_suites", "eval_cases", "eval_baselines"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO ac_app")
    # eval runs/results are never updated after insert (data-model: rows are evidence). Results cascade with a run.
    # 0003 set ALTER DEFAULT PRIVILEGES … GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ac_app, so a new table is
    # born with full CRUD for ac_app: a bare GRANT narrows nothing — the extra privileges must be REVOKEd explicitly.
    for t in ("eval_runs", "eval_results", "audit_logs"):
        op.execute(f"GRANT SELECT, INSERT ON {t} TO ac_app")
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {t} FROM ac_app")


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS eval_baselines_run_check ON eval_baselines")
    op.execute("DROP FUNCTION IF EXISTS eval_baselines_run_check()")
    for t in ("audit_logs", "eval_baselines", "eval_results", "eval_runs", "eval_cases", "eval_suites"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    op.execute("ALTER TABLE agent_versions DROP COLUMN IF EXISTS created_by")
    op.execute("ALTER TABLE agents DROP CONSTRAINT IF EXISTS uq_agents_ws_id")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS status_before_archive")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS draft")
    op.execute("ALTER TABLE agents DROP COLUMN IF EXISTS description")
