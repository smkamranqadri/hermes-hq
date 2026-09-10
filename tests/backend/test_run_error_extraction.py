"""A failed run must name its OWN cause, not just the contract message.

When an agent dies before writing its completion file, the contract correctly
fails the run — but storing only "missing completion file at <path>" hides the
one fact an operator needs. `_scan_log_for_error` exists to recover the real
cause from the run log; these tests pin the two failures it actually missed in
production (runs 487 and 493, 2026-09-09/10), where the operator was handed the
bare contract message twice and had to open the logs by hand.
"""
import os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
sys.path.insert(0, ROOT)
import pytest

from core import wm_run_agent as W


def _log(tmp_path, text):
    p = tmp_path / "run.log"
    p.write_text(text, encoding="utf-8")
    return str(p)


# The two logs verbatim, smart quotes and all — that detail is load-bearing.
RUN_493 = (
    "[wm_run_agent 493] running: /opt/hermes/bin/hermes --profile librarian chat\n"
    "Session 20260910_073006_0b175e found but has no messages. Starting fresh.\n"
    "\nsession_id: 20260910_073006_0b175e\n"
    "API call failed after 3 retries: Hermes can't reach the model provider. "
    "You may be offline. Check your internet connection and try again.\n"
)
RUN_487 = (
    "[wm_run_agent 487] running: /opt/hermes/bin/hermes --profile librarian chat\n"
    "Session 20260909_120010_e3bcbb found but has no messages. Starting fresh.\n"
    "\nsession_id: 20260909_120010_e3bcbb\n"
    "I’m sorry, but I couldn’t complete the required final completion-file "
    "write in this run.\n"
)
RUN_OK = (
    "[wm_run_agent 496] running: /opt/hermes/bin/hermes --profile librarian chat\n"
    "Session 20260910_083642_5a0840 found but has no messages. Starting fresh.\n"
    "\nsession_id: 20260910_083642_5a0840\n"
    "Done. Completion recorded at: /opt/data/hermes-hq/runs/496.completion.json\n"
    "Triaged the 20 oldest untriaged inbox notes.\n"
)


def test_a_provider_outage_is_named(tmp_path):
    """Run 493. Every tier-1 pattern wanted `api error` / `connection error`
    adjacency, so "API call failed after 3 retries" slipped through and the
    operator saw only "missing completion file" for a network outage."""
    line = W._scan_log_for_error(_log(tmp_path, RUN_493))
    assert line and "API call failed after 3 retries" in line
    assert "reach the model provider" in line


def test_the_agents_own_admission_is_named(tmp_path):
    """Run 487, exit code 0. The agent SAID it had not written the file — the
    truest possible cause — and nothing matched it."""
    line = W._scan_log_for_error(_log(tmp_path, RUN_487))
    assert line and "completion-file write" in line


def test_smart_quotes_do_not_defeat_the_scan(tmp_path):
    """Agents write prose: `couldn’t` is U+2019, not an ASCII apostrophe.
    The first fix matched the sentence in isolation but still returned None on
    the real log for exactly this reason."""
    assert W._scan_log_for_error(_log(tmp_path, RUN_487)) is not None
    ascii_variant = RUN_487.replace("’", "'")
    assert W._scan_log_for_error(_log(tmp_path, ascii_variant)) is not None


def test_a_successful_run_yields_no_error(tmp_path):
    """The line 'Completion recorded at: ...' must never read as a failure —
    the admission pattern needs a NEGATED verb next to the word completion."""
    assert W._scan_log_for_error(_log(tmp_path, RUN_OK)) is None


def test_the_wrappers_own_contract_line_is_never_re_extracted(tmp_path):
    """A previous finalize writes '[wm_run_agent N] CONTRACT: ...' into the same
    log, already embedding an extracted error. Re-reading it would nest the
    message deeper on every rework."""
    text = RUN_OK + ("[wm_run_agent 496] CONTRACT: API call failed after 3 "
                     "retries -> run+task FAILED\n")
    assert W._scan_log_for_error(_log(tmp_path, text)) is None


@pytest.mark.parametrize("line", [
    "Connection refused by upstream",
    "Could not connect to the provider",
    "You may be offline",
    "request call failed",
])
def test_transport_failures_without_the_word_error(tmp_path, line):
    got = W._scan_log_for_error(_log(tmp_path, "some output\n%s\n" % line))
    assert got is not None, line
