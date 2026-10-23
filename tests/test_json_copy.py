import json
import enum
import math
import sys
from pathlib import Path

# Add the parent directory so we can import server.py
sys.path.append(str(Path(__file__).parent.parent / "webui"))

from server import _json_copy, _fast_json_copy

def test_json_copy_standard():
    d = {'a': 1, 'b': [1, 2.5, "hello", True, None]}
    assert _json_copy(d) == d
    # Ensure it's a deep copy
    copied = _json_copy(d)
    copied['b'][0] = 99
    assert d['b'][0] == 1

def test_json_copy_tuples():
    # tuples should convert to lists
    assert _json_copy((1, 2)) == [1, 2]

def test_json_copy_int_keys():
    # non-str dict keys (int/float/bool/None) coerce to str in JSON.
    # Note: True and 1 evaluate to the same hash in python so `{1: 'a', True: 'c'}` merges to `{1: 'c'}`.
    d = {1: "a", 2.5: "b", None: "d"}
    assert _json_copy(d) == {'1': "a", '2.5': "b", 'null': "d"}

def test_json_copy_enums():
    class TestEnum(enum.IntEnum):
        A = 1

    assert _json_copy(TestEnum.A) == 1

    class StrEnum(str, enum.Enum):
        B = "B"

    assert _json_copy(StrEnum.B) == "B"

def test_json_copy_nan():
    # JSON standard doesn't support NaN, but python json does.
    copied = _json_copy({'a': float('nan')})
    assert math.isnan(copied['a'])

def test_json_copy_nested_structures():
    d = {
        'a': [
            {'b': 1},
            (2, 3)
        ],
        4: "c"
    }
    assert _json_copy(d) == {
        'a': [
            {'b': 1},
            [2, 3]
        ],
        '4': "c"
    }

def test_json_copy_circular_reference():
    d = {}
    d['a'] = d
    # Circular reference raises ValueError in json.dumps, so _json_copy returns None
    assert _json_copy(d) is None

def test_json_copy_parity():
    # To be absolutely sure, we can compare with the direct json approach
    def old_json_copy(value):
        try:
            return json.loads(json.dumps(value, ensure_ascii=False))
        except (TypeError, ValueError):
            return None

    cases = [
        {'a': 1},
        [1, 2, (3, 4)],
        {1: 2},
        None,
        True,
        "string",
        42,
        3.14,
        # circular tested above
    ]

    for case in cases:
        assert _json_copy(case) == old_json_copy(case)
