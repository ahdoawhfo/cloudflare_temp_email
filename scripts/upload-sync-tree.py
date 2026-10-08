#!/usr/bin/env python3
"""Upload staged Git blobs/tree before referencing them in a GitHub commit.

Only immutable objects are created here; the workflow updates main separately.
"""
import base64
import json
import os
import subprocess
import sys


def git(*args):
    return subprocess.check_output(['git', *args])


def api(resource, payload):
    return subprocess.check_output(
        ['gh', 'api', '-X', 'POST',
         f'repos/{os.environ["TARGET_REPO"]}/git/{resource}',
         '--input', '-', '--jq', '.sha'],
        input=json.dumps(payload).encode(),
    ).decode().strip()


def main():
    expected_tree = git('write-tree').decode().strip()
    base_tree = git('rev-parse', 'HEAD^{tree}').decode().strip()
    changed = git('diff', '--cached', '--no-renames', '--name-only', '-z').split(b'\0')
    staged = {}
    for record in git('ls-files', '--stage', '-z').split(b'\0'):
        if not record:
            continue
        metadata, path = record.split(b'\t', 1)
        mode, sha, stage = metadata.decode().split()
        if stage != '0':
            raise RuntimeError('Unresolved index conflicts; refusing upload')
        staged[path] = (mode, sha)

    # Validate before making any API calls (e.g. do not silently corrupt gitlinks).
    entries = []
    for path in filter(None, changed):
        name = path.decode('utf-8')
        if path not in staged:
            entries.append({'path': name, 'mode': '100644', 'type': 'blob', 'sha': None})
            continue
        mode, sha = staged[path]
        if mode not in ('100644', '100755', '120000'):
            raise RuntimeError(f'Unsupported staged mode {mode}: {name}')
        entries.append({'path': name, 'mode': mode, 'type': 'blob', 'sha': sha})

    uploaded = set()
    for entry in entries:
        sha = entry['sha']
        if sha is None or sha in uploaded:
            continue
        content = base64.b64encode(git('cat-file', 'blob', sha)).decode('ascii')
        remote_sha = api('blobs', {'content': content, 'encoding': 'base64'})
        if remote_sha != sha:
            raise RuntimeError(f'Uploaded blob SHA mismatch: {entry["path"]}')
        uploaded.add(sha)

    remote_tree = api('trees', {'base_tree': base_tree, 'tree': entries})
    if remote_tree != expected_tree:
        raise RuntimeError('Uploaded tree differs from staged merge; refusing commit')
    print(f'Uploaded {len(uploaded)} blobs and {len(entries)} tree entries', file=sys.stderr)
    print(remote_tree)


if __name__ == '__main__':
    main()
