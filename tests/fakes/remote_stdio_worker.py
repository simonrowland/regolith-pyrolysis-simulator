"""Small NDJSON worker used to exercise the generic stdio transport."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path


mode = sys.argv[1]


def send(value: object) -> None:
    sys.stdout.write(json.dumps(value, allow_nan=True, separators=(',', ':')) + '\n')
    sys.stdout.flush()


def spawn_descendant(parent_pid_file: str, child_pid_file: str) -> None:
    child = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(60)'],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(parent_pid_file).write_text(str(os.getpid()), encoding='utf-8')
    Path(child_pid_file).write_text(str(child.pid), encoding='utf-8')


if mode == 'startup-failure':
    sys.stderr.write('fake import or licence failure\n')
    sys.stderr.flush()
    raise SystemExit(17)
if mode == 'ready-timeout':
    time.sleep(10)

send({
    'protocol_version': 2 if mode == 'version-mismatch' else 1,
    'engine_version': 'fake-1',
    'datafile_fingerprint': 'fake-data-1',
})

count = 0
for line in sys.stdin:
    request = json.loads(line)
    count += 1
    if mode == 'crash-first':
        marker = Path(sys.argv[2])
        if not marker.exists():
            marker.write_text('crashed once', encoding='utf-8')
            os._exit(23)
    if mode == 'crash-subtree-first':
        marker = Path(sys.argv[2])
        if not marker.exists():
            spawn_descendant(sys.argv[3], sys.argv[4])
            marker.write_text('crashed once', encoding='utf-8')
            os._exit(23)
    if mode in ('subtree-timeout', 'subtree-close'):
        spawn_descendant(sys.argv[2], sys.argv[3])
    if mode == 'stderr-noise':
        sys.stderr.write('fake worker diagnostic\n' * 30000)
        sys.stderr.flush()
    if mode in ('call-timeout', 'subtree-timeout'):
        Path(sys.argv[2]).write_text(str(os.getpid()), encoding='utf-8')
        time.sleep(10)
    if mode == 'malformed':
        sys.stdout.write('{not json\n')
        sys.stdout.flush()
        break
    if mode == 'eof':
        break
    if mode == 'oversized':
        sys.stdout.write('x' * 4096 + '\n')
        sys.stdout.flush()
        break
    response = {
        'request_id': request['request_id'],
        'observation_id': request['observation_id'],
        'status': 'produced',
        'quantity': request['quantity'],
        'scalar': 2.5,
        'unit': 'test-unit',
        'engine_version': 'fake-1',
        'datafile_fingerprint': 'fake-data-1',
    }
    if mode == 'refused':
        response = {
            'request_id': request['request_id'],
            'observation_id': request['observation_id'],
            'status': 'refused',
            'code': 'unsupported_quantity',
            'detail': 'fake worker refused this quantity',
        }
    elif mode == 'wrong-id':
        response['request_id'] = 'wrong-request-id'
    elif mode == 'wrong-observation-id':
        response['observation_id'] = 'wrong-observation-id'
    elif mode == 'nan':
        response['scalar'] = float('nan')
    elif mode == 'increment':
        response['scalar'] = float(count)
    elif mode == 'array':
        response['scalar'] = [2.5]
    elif mode == 'extra-field':
        response['series'] = [2.5]
    send(response)
