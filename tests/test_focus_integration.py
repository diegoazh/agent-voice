import os

import pytest

from agent_voice import focus

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("HERDR_ENV"), reason="not running inside herdr"),
]


def test_real_detector_returns_a_bool_or_unknown_for_the_current_pane():
    detector = focus.detect_from_env(os.environ)
    if detector is None:
        pytest.skip("not Ghostty + herdr")
    assert detector.is_focused() in (True, False, None)  # read-only: lsappinfo + herdr pane get
    assert isinstance(detector.herdr_pane_focused(), bool)
