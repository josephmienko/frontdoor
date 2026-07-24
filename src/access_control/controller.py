from __future__ import annotations

import hashlib

from access_control.interfaces import CardRepository, Clock, EventStore, Relay
from access_control.models import AccessDecision, Event


class AccessController:
    """Hardware-independent access decisions and door operation."""

    def __init__(
        self,
        *,
        repository: CardRepository,
        relay: Relay,
        events: EventStore,
        clock: Clock,
        unlock_seconds: float,
    ) -> None:
        if unlock_seconds <= 0:
            raise ValueError("unlock_seconds must be positive")
        self._repository = repository
        self._relay = relay
        self._events = events
        self._clock = clock
        self._unlock_seconds = unlock_seconds

    async def handle_card(self, card_id: str) -> AccessDecision:
        authorized = await self._repository.is_authorized(card_id)
        decision = AccessDecision.GRANTED if authorized else AccessDecision.DENIED
        await self._events.append(
            Event(
                name=f"access_{decision.value}",
                timestamp=self._clock.monotonic(),
                fields={"card_token": _card_token(card_id)},
            )
        )
        if authorized:
            await self._relay.pulse(self._unlock_seconds)
        return decision

    async def handle_exit_button(self) -> None:
        # Egress deliberately does not consult the reader or card repository.
        await self._relay.pulse(self._unlock_seconds)
        await self._events.append(
            Event(name="exit_button", timestamp=self._clock.monotonic(), fields={})
        )

    async def start(self) -> None:
        await self._relay.make_safe()

    async def stop(self) -> None:
        await self._relay.make_safe()


def _card_token(card_id: str) -> str:
    return hashlib.sha256(card_id.encode()).hexdigest()[:12]
