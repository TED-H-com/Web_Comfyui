"""Borrowed persona calibration contracts; fake media, no dispatch/network."""
from pathlib import Path
from types import SimpleNamespace as NS
import copy, json, sys
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'webui')]
import server
from compute_parity.materials import Materials
from compute_parity.contracts import digest, ParityError, compile_request
from test_compute_parity_p1 import no_network












def test_unpaired_character_still_has_no_persona_capability():
    own = {'id': 'example-a', 'name': 'Example A', 'visualClass': 'realistic', 'kind': 'provider_character'}
    with pytest.raises(ValueError):
        server.resolve_character_capability(own, 'voice-speak', 'voice-speak', 'voice-speak.persona.v1')


