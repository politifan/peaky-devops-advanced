from datetime import datetime, timezone
import json
from pathlib import Path
import time

root = Path('/sys/fs/cgroup')


def sample():
    cpu = dict(line.split() for line in (root / 'cpu.stat').read_text().splitlines())
    return {'cpu_usage_usec': int(cpu['usage_usec']), 'throttled_usec': int(cpu.get('throttled_usec', 0)),
            'nr_throttled': int(cpu.get('nr_throttled', 0)), 'memory_bytes': int((root / 'memory.current').read_text()),
            'cpu_max': (root / 'cpu.max').read_text().strip(), 'time': time.monotonic()}


before = sample()
time.sleep(5)
after = sample()
interval = after['time'] - before['time']
print(json.dumps({'utc': datetime.now(timezone.utc).isoformat(), 'interval_seconds': interval,
    'average_cpu_cores': (after['cpu_usage_usec'] - before['cpu_usage_usec']) / interval / 1000000,
    'throttled_usec_delta': after['throttled_usec'] - before['throttled_usec'],
    'throttled_periods_delta': after['nr_throttled'] - before['nr_throttled'],
    'memory_bytes': after['memory_bytes'], 'cpu_max': after['cpu_max'], 'source': 'this container cgroup v2'}))
