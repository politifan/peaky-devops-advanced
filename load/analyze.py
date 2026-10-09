import argparse
import csv
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('stats', type=Path)
args = parser.parse_args()
rows = list(csv.DictReader(args.stats.open(encoding='utf-8')))
rows = [row for row in rows if row['Name'] in {'GET tickets list', 'POST tickets'}]
if len(rows) != 2:
    raise SystemExit('Expected the two labelled task rows from Locust stats CSV')
output = []
for row in rows:
    count, failed = int(row['Request Count']), int(row['Failure Count'])
    output.append({'name': row['Name'], 'requests': count, 'failures': failed,
        'failure_ratio': None if count == 0 else failed / count,
        'p95_ms': float(row['95%']), 'rps': float(row['Requests/s']),
        'median_ms': float(row['Median Response Time'])})
print(json.dumps(output, ensure_ascii=False, indent=2))
