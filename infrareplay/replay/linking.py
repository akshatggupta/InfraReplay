"""Pair an original event stream with a replayed one.

Event ids are regenerated on every run, so the pairing is positional: two
events correspond when they sit in the same request group, have the same
`event_type`, and are the Nth event of that type inside the group.

    original   corr_a: [request, query, result, response]
    replayed   corr_z: [request, query, result, response]
                          ^ same (group 0, http.request, #0)

Anything without a partner falls out as MISSING / NEW downstream.
"""

from infrareplay.schema import Event

_Signature = tuple[int, str, int]


def link_streams(original: list[Event], replayed: list[Event]) -> dict[str, str]:
    """Return original event_id -> replayed event_id."""

    replay_by_sig = _signatures(replayed)
    links: dict[str, str] = {}

    for signature, event_id in _signatures(original).items():
        partner = replay_by_sig.get(signature)

        if partner is None:
            continue

        links[event_id] = partner

    return links


def _signatures(events: list[Event]) -> dict[_Signature, str]:
    group_index: dict[str, int] = {}
    seen: dict[tuple[int, str], int] = {}

    signatures: dict[_Signature, str] = {}

    for event in sorted(events, key=lambda e: e.sequence):
        group = group_index.setdefault(event.correlation_id, len(group_index))
        key = (group, event.event_type.value)

        occurrence = seen.get(key, 0)
        seen[key] = occurrence + 1

        signatures[(group, event.event_type.value, occurrence)] = event.event_id

    return signatures
