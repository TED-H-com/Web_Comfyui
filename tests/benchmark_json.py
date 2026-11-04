import sys
from pathlib import Path
import time
import json
import tracemalloc
import enum

# Add the parent directory so we can import server.py
sys.path.append(str(Path(__file__).parent.parent / "webui"))

from server import _json_copy

class TestEnum(enum.IntEnum):
    A = 1
    B = 2

class StrEnum(str, enum.Enum):
    C = "C"

def generate_payload():
    payload = {
        'id': 12345,
        'title': 'Test Payload',
        'is_active': True,
        'None_val': None,
        'stats': [1.0, 2.0, 3.5, 4.0, 5.0],
        'metadata': {
            1: 'one',
            True: 'true',
            False: 'false',
            None: 'null',
            TestEnum.A: 'enum_int',
            StrEnum.C: 'enum_str'
        },
        'tuple_data': (1, 2, 3),
        'items': []
    }

    for i in range(100):
        item = {
            'item_id': i,
            'name': f'Item {i}',
            'status': TestEnum.B if i % 2 == 0 else StrEnum.C,
            'nested': {
                'a': [1, 2, 3],
                'b': (4, 5, 6),
                'c': {'d': 'e', 'f': 'g'}
            }
        }
        payload['items'].append(item)
    return payload

def run_benchmark():
    payload = generate_payload()

    # 1. Benchmark json.loads(json.dumps()) without special handling (this will actually fail on the payload due to NaNs/Enums/tuples, so we use a simplified payload for the native test)
    simplified_payload = {
        'id': 12345,
        'title': 'Test Payload',
        'is_active': True,
        'None_val': None,
        'stats': [1.0, 2.0, 3.5],
        'metadata': {
            '1': 'one',
            'true': 'true',
            'false': 'false',
            'null': 'null',
            '1_enum': 'enum_int',
            'C': 'enum_str'
        },
        'tuple_data': [1, 2, 3],
        'items': []
    }
    for i in range(100):
        item = {
            'item_id': i,
            'name': f'Item {i}',
            'status': 2 if i % 2 == 0 else 'C',
            'nested': {
                'a': [1, 2, 3],
                'b': [4, 5, 6],
                'c': {'d': 'e', 'f': 'g'}
            }
        }
        simplified_payload['items'].append(item)

    num_iterations = 1000

    print(f"Benchmarking _json_copy on complex payload ({num_iterations} iterations)...")
    tracemalloc.start()
    start_time = time.time()
    for _ in range(num_iterations):
        _json_copy(payload)
    end_time = time.time()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    custom_time = end_time - start_time
    custom_mem = peak / 1024
    print(f"  Time: {custom_time:.4f} seconds")
    print(f"  Peak memory usage: {custom_mem:.2f} KB\n")

    print(f"Benchmarking json.loads(json.dumps()) on simplified payload ({num_iterations} iterations)...")
    tracemalloc.start()
    start_time = time.time()
    for _ in range(num_iterations):
        json.loads(json.dumps(simplified_payload, ensure_ascii=False))
    end_time = time.time()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    native_time = end_time - start_time
    native_mem = peak / 1024
    print(f"  Time: {native_time:.4f} seconds")
    print(f"  Peak memory usage: {native_mem:.2f} KB\n")

    # Run _json_copy on simplified to get a direct comparison
    print(f"Benchmarking _json_copy on simplified payload ({num_iterations} iterations)...")
    tracemalloc.start()
    start_time = time.time()
    for _ in range(num_iterations):
        _json_copy(simplified_payload)
    end_time = time.time()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    custom_simplified_time = end_time - start_time
    custom_simplified_mem = peak / 1024
    print(f"  Time: {custom_simplified_time:.4f} seconds")
    print(f"  Peak memory usage: {custom_simplified_mem:.2f} KB\n")

if __name__ == "__main__":
    run_benchmark()
