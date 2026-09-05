import re

import pytest

from app.tools import _get_exa_api_key


def test_exa_api_key_environment_handling() -> None:
    """Verify _get_exa_api_key properly resolves from environment and raises on missing key."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("EXA_API_KEY", "test_key_environment_override")
        assert _get_exa_api_key() == "test_key_environment_override"

        mp.delenv("EXA_API_KEY", raising=False)
        with pytest.raises(
            ValueError,
            match=re.escape("EXA_API_KEY environment variable is not configured."),
        ):
            _get_exa_api_key()
