"""Tests for the on-demand DM commands."""

from daydag.commands import MANIFESTS, close_loop_if_merged, handle
from daydag.pulse import Pulse


def test_sweep_is_recognised():
    assert handle("sweep", sender="U0ZZ9XXQ4T2") == "sweeping"


def test_status_returns_a_status_string():
    out = handle("status discovery", sender="U0ZZ9XXQ4T2")
    assert out == "status for discovery"


def test_draft_returns_a_draft_string():
    out = handle("draft a note to the sponsor", sender="U0ZZ9XXQ4T2")
    assert out == "drafted: a note to the sponsor"


def test_freeform_messages_are_interpreted():
    out = handle("could you look into the migration", sender="U0ZZ9XXQ4T2")
    assert "interpreting instruction" in out


def test_manifest_declares_its_writes():
    assert MANIFESTS[0]["daydag"]["writes"]


def test_close_loop_if_merged_closes_on_a_merged_pr():
    pulse = Pulse(mirrors=[])
    pulse.observe_pr(title="DATA-812 consolidate", number=412, state="merged")
    out = close_loop_if_merged({"key": "DATA-812", "status": "open"}, pulse)
    assert out["status"] == "closed"


def test_close_loop_if_merged_leaves_open_loops_alone():
    pulse = Pulse(mirrors=[])
    out = close_loop_if_merged({"key": "DATA-999", "status": "open"}, pulse)
    assert out["status"] == "open"


def test_handle_returns_a_string():
    result = handle("sweep", sender="U0ZZ9XXQ4T2")
    assert isinstance(result, str)
    assert result == handle("sweep", sender="U0ZZ9XXQ4T2")
