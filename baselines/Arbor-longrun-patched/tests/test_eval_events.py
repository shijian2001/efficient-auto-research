from arbor.events import types as ev


def test_new_event_constants_present_and_stable():
    assert ev.PROTECTED_TAMPER == "eval.protected_tamper"
    assert ev.CONTAMINATION_ASSESSED == "eval.contamination_assessed"
