"""Scoped support tickets, encrypted messages, and confirmed staff actions."""

from alembic import op

revision = "0012_support"
down_revision = "0011_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_orders_id_buyer", "orders", ["id", "buyer_id"])
    op.execute("""CREATE TABLE support_tickets (
        id UUID PRIMARY KEY,
        customer_id UUID NOT NULL CONSTRAINT fk_ticket_customer REFERENCES users(id),
        request_id UUID NOT NULL, kind VARCHAR(16) NOT NULL, order_id UUID,
        assignee_id UUID CONSTRAINT fk_ticket_assignee REFERENCES users(id),
        state VARCHAR(16) NOT NULL, version INTEGER NOT NULL,
        created_at TIMESTAMPTZ NOT NULL, updated_at TIMESTAMPTZ NOT NULL, closed_at TIMESTAMPTZ,
        CONSTRAINT fk_ticket_order_customer FOREIGN KEY (order_id,customer_id)
            REFERENCES orders(id,buyer_id),
        CONSTRAINT uq_ticket_request UNIQUE (customer_id,request_id),
        CONSTRAINT ck_ticket_kind CHECK (kind IN ('GENERAL','PAYMENT','DELIVERY')),
        CONSTRAINT ck_ticket_state CHECK (state IN ('OPEN','CLOSED') AND
            (state = 'CLOSED') = (closed_at IS NOT NULL)),
        CONSTRAINT ck_ticket_version CHECK (version >= 0 AND updated_at >= created_at)
    )""")
    op.execute(
        "CREATE INDEX ix_ticket_customer_updated ON support_tickets(customer_id,updated_at,id)"
    )
    op.execute(
        "CREATE INDEX ix_ticket_open_updated ON support_tickets(updated_at,id) WHERE state='OPEN'"
    )
    op.execute(
        "CREATE INDEX ix_ticket_assignee_open ON support_tickets(assignee_id,updated_at,id) "
        "WHERE state='OPEN'"
    )
    op.execute("""CREATE TABLE support_messages (
        id UUID PRIMARY KEY,
        ticket_id UUID NOT NULL CONSTRAINT fk_support_message_ticket REFERENCES support_tickets(id),
        author_id UUID NOT NULL CONSTRAINT fk_support_message_author REFERENCES users(id),
        request_id UUID NOT NULL, staff BOOLEAN NOT NULL, created_at TIMESTAMPTZ NOT NULL,
        key_version INTEGER NOT NULL, nonce BYTEA NOT NULL, ciphertext BYTEA NOT NULL,
        CONSTRAINT uq_support_message_request UNIQUE (author_id,request_id),
        CONSTRAINT uq_support_nonce UNIQUE (key_version,nonce),
        CONSTRAINT ck_support_envelope CHECK (key_version > 0 AND octet_length(nonce)=12
            AND octet_length(ciphertext) BETWEEN 17 AND 4256)
    )""")
    op.execute("CREATE INDEX ix_support_message_page ON support_messages(ticket_id,created_at,id)")
    op.execute("""CREATE TABLE support_actions (
        id UUID PRIMARY KEY,
        actor_id UUID NOT NULL CONSTRAINT fk_support_action_actor REFERENCES users(id),
        ticket_id UUID NOT NULL CONSTRAINT fk_support_action_ticket REFERENCES support_tickets(id),
        kind VARCHAR(16) NOT NULL, staff BOOLEAN NOT NULL,
        target_id UUID CONSTRAINT fk_support_action_target REFERENCES users(id),
        expected_version INTEGER NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
        status VARCHAR(16) NOT NULL DEFAULT 'pending', completed_at TIMESTAMPTZ,
        CONSTRAINT ck_support_action_kind CHECK ((kind='ASSIGN' AND staff) OR
            (kind='CLOSE' AND target_id IS NULL)),
        CONSTRAINT ck_support_action_state CHECK (status IN ('pending','applied','cancelled') AND
            (status='pending') = (completed_at IS NULL)),
        CONSTRAINT ck_support_action_version CHECK (expected_version >= 0)
    )""")
    op.execute("""CREATE FUNCTION guard_support_ticket() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN
            RAISE EXCEPTION 'support history is immutable';
        END IF;
        IF TG_OP = 'INSERT' THEN
            IF NEW.state <> 'OPEN' OR NEW.version <> 0 OR NEW.assignee_id IS NOT NULL THEN
                RAISE EXCEPTION 'invalid initial support ticket';
            END IF;
        ELSE
            IF (to_jsonb(NEW) - ARRAY['assignee_id','state','version','updated_at','closed_at'])
               IS DISTINCT FROM
               (to_jsonb(OLD) - ARRAY['assignee_id','state','version','updated_at','closed_at'])
               OR OLD.state = 'CLOSED' OR NEW.version <> OLD.version+1
               OR NEW.updated_at < OLD.updated_at THEN
                RAISE EXCEPTION 'invalid support ticket mutation';
            END IF;
            IF ROW(NEW.state,NEW.assignee_id) IS DISTINCT FROM ROW(OLD.state,OLD.assignee_id)
               AND NOT EXISTS (SELECT 1 FROM support_actions a WHERE a.ticket_id=NEW.id
                    AND a.status='applied' AND a.expected_version=OLD.version
                    AND ((a.kind='ASSIGN' AND NEW.state='OPEN'
                            AND a.target_id IS NOT DISTINCT FROM NEW.assignee_id) OR
                         (a.kind='CLOSE' AND NEW.state='CLOSED'
                            AND NEW.assignee_id IS NOT DISTINCT FROM OLD.assignee_id))) THEN
                RAISE EXCEPTION 'confirmed support action required';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER support_ticket_guard BEFORE INSERT OR UPDATE OR DELETE ON support_tickets "
        "FOR EACH ROW EXECUTE FUNCTION guard_support_ticket()"
    )
    op.execute(
        "CREATE TRIGGER support_ticket_truncate_guard BEFORE TRUNCATE ON support_tickets "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_support_ticket()"
    )
    op.execute("""CREATE FUNCTION guard_support_message() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE t support_tickets%ROWTYPE;
    BEGIN
        IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'support messages are immutable'; END IF;
        SELECT * INTO t FROM support_tickets WHERE id=NEW.ticket_id FOR UPDATE;
        IF NOT FOUND OR t.state <> 'OPEN' OR NEW.created_at < t.updated_at
           OR (NOT NEW.staff AND NEW.author_id <> t.customer_id) THEN
            RAISE EXCEPTION 'support message ownership or state changed';
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER support_message_guard BEFORE INSERT OR UPDATE OR DELETE "
        "ON support_messages "
        "FOR EACH ROW EXECUTE FUNCTION guard_support_message()"
    )
    op.execute(
        "CREATE TRIGGER support_message_truncate_guard BEFORE TRUNCATE ON support_messages "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_support_message()"
    )
    op.execute("""CREATE FUNCTION advance_support_thread() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        UPDATE support_tickets SET version=version+1, updated_at=NEW.created_at
            WHERE id=NEW.ticket_id;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER support_thread_advance AFTER INSERT ON support_messages "
        "FOR EACH ROW EXECUTE FUNCTION advance_support_thread()"
    )
    op.execute("""CREATE FUNCTION check_support_initial_message()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NOT EXISTS (SELECT 1 FROM support_messages m WHERE m.ticket_id=NEW.id
            AND m.author_id=NEW.customer_id AND NOT m.staff) THEN
            RAISE EXCEPTION 'initial customer support message required';
        END IF;
        RETURN NULL;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER support_initial_message AFTER INSERT ON support_tickets "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
        "EXECUTE FUNCTION check_support_initial_message()"
    )
    op.execute("""CREATE FUNCTION guard_support_action() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('DELETE','TRUNCATE') THEN
            RAISE EXCEPTION 'support actions are immutable';
        END IF;
        IF TG_OP='INSERT' THEN
            IF NEW.status <> 'pending' OR NEW.expires_at <= clock_timestamp() THEN
                RAISE EXCEPTION 'invalid support confirmation';
            END IF;
        ELSIF (to_jsonb(NEW)-ARRAY['status','completed_at']) IS DISTINCT FROM
              (to_jsonb(OLD)-ARRAY['status','completed_at']) OR OLD.status <> 'pending'
              OR NEW.status NOT IN ('applied','cancelled') THEN
            RAISE EXCEPTION 'invalid support confirmation transition';
        END IF;
        IF TG_OP='INSERT' OR NEW.status='applied' THEN
            IF NEW.expires_at <= clock_timestamp() OR NOT EXISTS (SELECT 1 FROM support_tickets t
                WHERE t.id=NEW.ticket_id AND t.state='OPEN' AND t.version=NEW.expected_version
                    AND (NEW.staff OR NEW.actor_id=t.customer_id)) THEN
                RAISE EXCEPTION 'support confirmation stale';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    op.execute(
        "CREATE TRIGGER support_action_guard BEFORE INSERT OR UPDATE OR DELETE ON support_actions "
        "FOR EACH ROW EXECUTE FUNCTION guard_support_action()"
    )
    op.execute(
        "CREATE TRIGGER support_action_truncate_guard BEFORE TRUNCATE ON support_actions "
        "FOR EACH STATEMENT EXECUTE FUNCTION guard_support_action()"
    )
    op.execute("""CREATE FUNCTION check_support_action_commit()
    RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF NEW.status='applied' AND NOT EXISTS (SELECT 1 FROM support_tickets t
            WHERE t.id=NEW.ticket_id AND t.version=NEW.expected_version+1
                AND ((NEW.kind='ASSIGN' AND t.state='OPEN'
                        AND t.assignee_id IS NOT DISTINCT FROM NEW.target_id)
                    OR (NEW.kind='CLOSE' AND t.state='CLOSED'))) THEN
            RAISE EXCEPTION 'support action and ticket must commit together';
        END IF;
        RETURN NULL;
    END $$""")
    op.execute(
        "CREATE CONSTRAINT TRIGGER support_action_commit AFTER UPDATE ON support_actions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_support_action_commit()"
    )


def downgrade() -> None:
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM support_tickets) THEN
            RAISE EXCEPTION 'support history prevents downgrade';
        END IF;
    END $$""")
    op.drop_table("support_actions")
    op.drop_table("support_messages")
    op.drop_table("support_tickets")
    for name in (
        "check_support_action_commit",
        "guard_support_action",
        "check_support_initial_message",
        "advance_support_thread",
        "guard_support_message",
        "guard_support_ticket",
    ):
        op.execute(f"DROP FUNCTION {name}()")
    op.drop_constraint("uq_orders_id_buyer", "orders", type_="unique")
