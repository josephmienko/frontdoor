import pytest

from access_control.controller import AccessController
from access_control.models import AccessDecision
from access_control.simulators import (
    InMemoryCardRepository,
    InMemoryEventStore,
    SimulatedClock,
    SimulatedRelay,
)


def make_controller(
    authorized_cards: set[str] | None = None,
) -> tuple[AccessController, SimulatedRelay, InMemoryEventStore]:
    clock = SimulatedClock()
    relay = SimulatedRelay(clock)
    events = InMemoryEventStore()
    controller = AccessController(
        repository=InMemoryCardRepository(authorized_cards or set()),
        relay=relay,
        events=events,
        clock=clock,
        unlock_seconds=2.5,
    )
    return controller, relay, events


@pytest.mark.unit
@pytest.mark.asyncio
async def test_authorized_card_activates_relay_and_records_masked_event() -> None:
    controller, relay, events = make_controller({"secret-card"})

    decision = await controller.handle_card("secret-card")

    assert decision is AccessDecision.GRANTED
    assert len(relay.activations) == 1
    assert relay.activations[0].requested_duration == 2.5
    assert relay.is_safe
    assert events.events[0].name == "access_granted"
    assert events.events[0].fields["card_token"] != "secret-card"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unauthorized_card_does_not_activate_relay() -> None:
    controller, relay, events = make_controller()

    decision = await controller.handle_card("unknown")

    assert decision is AccessDecision.DENIED
    assert relay.activations == []
    assert relay.is_safe
    assert events.events[0].name == "access_denied"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_exit_button_activates_relay_without_repository_or_reader() -> None:
    controller, relay, events = make_controller()

    await controller.handle_exit_button()

    assert len(relay.activations) == 1
    assert relay.is_safe
    assert events.events[0].name == "exit_button"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_startup_and_shutdown_restore_safe_state() -> None:
    controller, relay, _ = make_controller()
    relay.simulate_unsafe_state()

    await controller.start()
    assert relay.is_safe
    relay.simulate_unsafe_state()
    await controller.stop()
    assert relay.is_safe
