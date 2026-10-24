from dataclasses import asdict, fields, replace
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from telebotshop.application.ports.topups import TopUpRepository
from telebotshop.domain.errors import InvalidAction
from telebotshop.domain.models.topups import (
    Settlement,
    SettlementResult,
    SettlementSource,
    TopUpIntent,
    TopUpState,
)
from telebotshop.infrastructure.database.models.topups import (
    deposit_routes as routes,
)
from telebotshop.infrastructure.database.models.topups import (
    reconciliation_checkpoints as checkpoints,
)
from telebotshop.infrastructure.database.models.topups import (
    settlement_events as events,
)
from telebotshop.infrastructure.database.models.topups import (
    settlement_observations as observations,
)
from telebotshop.infrastructure.database.models.topups import (
    topup_requests as intents,
)
from telebotshop.infrastructure.database.models.topups import (
    topup_review_actions as actions,
)


def intent_from(row):
    if row is None:
        return None
    values = {field.name: row[field.name] for field in fields(TopUpIntent)}
    values["state"] = TopUpState(values["state"])
    return TopUpIntent(**values)


def event_from(row):
    facts = dict(row["facts"])
    facts["amount"] = Decimal(facts["amount"])
    facts["occurred_at"] = datetime.fromisoformat(facts["occurred_at"])
    return Settlement(
        **facts,
        confirmed=row["confirmed"],
        confirmations=row["confirmations"],
        required_confirmations=row["required_confirmations"],
        token_verified=row["token_verified"],
        proof_digest=row["proof_digest"],
        source=SettlementSource(row["source"]),
    )


class SqlTopUpRepository(TopUpRepository):
    def __init__(self, session):
        self.session = session

    async def by_action(self, buyer_id, action_id):
        row = (
            (
                await self.session.execute(
                    select(intents).where(
                        intents.c.buyer_id == buyer_id, intents.c.action_id == action_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return intent_from(row)

    async def save(self, intent):
        await self.session.execute(insert(intents).values(**asdict(intent)))

    async def get(self, intent_id):
        row = (
            (
                await self.session.execute(
                    select(intents).where(intents.c.id == intent_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return intent_from(row)

    async def own(self, buyer_id, offset):
        rows = (
            await self.session.execute(
                select(intents)
                .where(intents.c.buyer_id == buyer_id)
                .order_by(intents.c.created_at.desc(), intents.c.id.desc())
                .limit(20)
                .offset(offset)
            )
        ).mappings()
        return tuple(intent_from(row) for row in rows)

    async def route(self, buyer_id):
        # Routes are immutable; buyer locks serialize requests without route/intent lock inversion.
        return (
            (await self.session.execute(select(routes).where(routes.c.buyer_id == buyer_id)))
            .mappings()
            .one_or_none()
        )

    async def active_route_intent(self, route_id):
        row = (
            (
                await self.session.execute(
                    select(intents)
                    .where(
                        intents.c.route_id == route_id,
                        intents.c.state.not_in(["CREDITED", "REJECTED"]),
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        return intent_from(row)

    async def has_settlement(self, intent_id):
        return await self.session.scalar(
            select(select(events.c.id).where(events.c.intent_id == intent_id).exists())
        )

    async def reject_unused_quote(self, intent_id):
        await self.session.execute(
            update(intents).where(intents.c.id == intent_id).values(state="REJECTED")
        )

    async def set_route(self, buyer_id, network, token, recipient):
        existing = await self.route(buyer_id)
        if existing is not None:
            if (existing["network"], existing["token_contract"], existing["recipient"]) != (
                network,
                token,
                recipient,
            ):
                raise InvalidAction("Existing deposit route cannot be reassigned")
            return
        await self.session.execute(
            insert(routes).values(
                id=uuid4(),
                buyer_id=buyer_id,
                network=network,
                token_contract=token,
                recipient=recipient,
            )
        )

    async def observe(self, event):
        key = int.from_bytes(
            sha256((event.provider + ":" + event.transaction_id).encode()).digest()[:8],
            "big",
            signed=True,
        )
        await self.session.execute(select(func.pg_advisory_xact_lock(key)))
        row = (
            (
                await self.session.execute(
                    select(events)
                    .where(
                        events.c.provider == event.provider,
                        events.c.transaction_id == event.transaction_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        event_id = uuid4() if row is None else row["id"]
        conflict = row is not None and (row["digest"] != event.digest() or row["conflict"])
        if row is None:
            await self.session.execute(
                insert(events).values(
                    id=event_id,
                    provider=event.provider,
                    transaction_id=event.transaction_id,
                    facts=event.facts(),
                    digest=event.digest(),
                    confirmed=event.confirmed,
                    confirmations=event.confirmations,
                    required_confirmations=event.required_confirmations,
                    token_verified=event.token_verified,
                    source=event.source,
                    proof_digest=event.proof_digest,
                )
            )
        elif conflict:
            await self.session.execute(
                update(events).where(events.c.id == event_id).values(conflict=True)
            )
        else:
            event = replace(
                event,
                confirmed=event.confirmed or row["confirmed"],
                confirmations=max(event.confirmations, row["confirmations"]),
                required_confirmations=max(
                    event.required_confirmations, row["required_confirmations"]
                ),
                token_verified=event.token_verified or row["token_verified"],
            )
            await self.session.execute(
                update(events)
                .where(events.c.id == event_id)
                .values(
                    confirmed=event.confirmed,
                    confirmations=event.confirmations,
                    required_confirmations=event.required_confirmations,
                    token_verified=event.token_verified,
                    source=event.source,
                    proof_digest=event.proof_digest,
                )
            )
        observation = sha256(
            event.proof_digest
            + event.digest()
            + str((event.confirmed, event.confirmations, event.token_verified)).encode()
        ).digest()
        await self.session.execute(
            pg_insert(observations)
            .values(
                id=uuid4(),
                event_id=event_id,
                digest=observation,
                source=event.source,
                conflict=conflict,
            )
            .on_conflict_do_nothing()
        )
        return event_id, conflict, event

    async def evidence(self, event_id):
        row = (
            (
                await self.session.execute(
                    select(events).where(events.c.id == event_id).with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["conflict"]:
            return None
        return event_from(row)

    async def match(self, event):
        if event.provider == "SEPAY":
            query = select(intents).where(
                intents.c.rail == "BANK_TRANSFER", intents.c.reference == event.reference
            )
        else:
            query = (
                select(intents)
                .join(routes)
                .where(
                    intents.c.rail == "USDT_BINANCE",
                    intents.c.state.not_in(["CREDITED", "REJECTED"]),
                    routes.c.network == event.network,
                    routes.c.token_contract == event.token_contract,
                    routes.c.recipient == event.recipient,
                    routes.c.buyer_id == intents.c.buyer_id,
                    intents.c.created_at <= event.occurred_at + timedelta(seconds=2),
                    intents.c.expires_at >= event.occurred_at,
                )
            )
        rows = (
            (
                await self.session.execute(
                    query.order_by(intents.c.id).limit(2).with_for_update(of=intents)
                )
            )
            .mappings()
            .all()
        )
        return intent_from(rows[0]) if len(rows) == 1 else None

    async def event_result(self, event_id):
        row = (
            (await self.session.execute(select(events).where(events.c.id == event_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise InvalidAction("Settlement event unavailable")
        return SettlementResult(event_id, row["intent_id"], TopUpState(row["state"]), row["reason"])

    async def finish(self, event_id, intent, state, reason, journal_id=None):
        await self.session.execute(
            update(events)
            .where(events.c.id == event_id)
            .values(state=state, reason=reason, intent_id=None if intent is None else intent.id)
        )
        if intent is not None and intent.state not in {TopUpState.CREDITED, TopUpState.REJECTED}:
            values = {"state": state}
            if journal_id is not None:
                values.update(event_id=event_id, journal_id=journal_id)
            await self.session.execute(
                update(intents).where(intents.c.id == intent.id).values(**values)
            )

    async def queue(self, offset):
        rows = (
            await self.session.execute(
                select(events)
                .where(
                    (events.c.state.in_(["REVIEW_REQUIRED", "PENDING_CONFIRMATION"]))
                    | events.c.conflict.is_(True)
                    | (events.c.reason == "OPERATOR_ESCALATION")
                )
                .order_by(events.c.id)
                .limit(20)
                .offset(offset)
            )
        ).mappings()
        return tuple(
            SettlementResult(
                row["id"],
                row["intent_id"],
                TopUpState(row["state"]),
                "CONFLICT" if row["conflict"] else row["reason"],
            )
            for row in rows
        )

    async def save_review(
        self,
        action_id,
        actor_id,
        event_id,
        intent_id,
        reason,
        source,
        expires_at,
        decision="APPROVE",
    ):
        existing = await self.review(action_id)
        if existing is not None:
            if existing["decision"] != decision:
                raise InvalidAction("Review decision conflicts")
            if (
                existing["actor_id"],
                existing["event_id"],
                existing["intent_id"],
                existing["reason"],
                existing["source_digest"],
            ) != (actor_id, event_id, intent_id, reason, source):
                raise InvalidAction("Review action conflicts")
            return
        await self.session.execute(
            insert(actions).values(
                id=action_id,
                actor_id=actor_id,
                event_id=event_id,
                intent_id=intent_id,
                reason=reason,
                source_digest=source,
                expires_at=expires_at,
                decision=decision,
            )
        )

    async def review(self, action_id):
        return (
            (await self.session.execute(select(actions).where(actions.c.id == action_id)))
            .mappings()
            .one_or_none()
        )

    async def apply_review(self, action_id):
        await self.session.execute(
            update(actions).where(actions.c.id == action_id).values(applied=True)
        )

    async def checkpoint(self, provider):
        return await self.session.scalar(
            select(checkpoints.c.completed_until).where(checkpoints.c.provider == provider)
        )

    async def advance(self, provider, before):
        await self.session.execute(
            pg_insert(checkpoints)
            .values(provider=provider, completed_until=before)
            .on_conflict_do_update(
                index_elements=[checkpoints.c.provider],
                set_={"completed_until": func.greatest(checkpoints.c.completed_until, before)},
            )
        )
