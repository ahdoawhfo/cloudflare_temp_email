#!/usr/bin/env python3
"""Offline sync regression checks; uses temporary clones and mocked gh.

Run: python3 scripts/test-upstream-sync.py
Requires Git with merge-tree --write-tree and fetched upstream/main.
No remote writes or deployments. Uses the existing global Git identity.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / '.github/workflows/sync.yaml').read_text()
SCRIPT = textwrap.dedent(WORKFLOW.split('        run: |\n', 1)[1].split('        env:\n', 1)[0])
SCRIPT = '\n'.join(line for line in SCRIPT.splitlines()
                   if not line.strip().startswith(('git remote add upstream ', 'git fetch --no-tags ')))


MOCK_GH = r'''#!/usr/bin/env python3
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

args = sys.argv[1:]
with open(os.environ['MOCK_LOG'], 'a') as log:
    log.write(' '.join(args) + '\n')
remote = os.environ['MOCK_REMOTE']
def git(*command, input=None, env=None):
    return subprocess.check_output(['git', '--git-dir', remote, *command], input=input, env=env)
endpoint = args[args.index('-X') + 2]
if endpoint.endswith('/blobs'):
    if os.environ.get('FAIL_BLOB'):
        sys.exit('simulated blob upload failure')
    payload = json.load(sys.stdin)
    print(git('hash-object', '-w', '--stdin', input=base64.b64decode(payload['content'])).decode().strip())
elif endpoint.endswith('/trees'):
    payload = json.load(sys.stdin)
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / 'index'))
        git('read-tree', payload['base_tree'], env=env)
        records = []
        for entry in payload['tree']:
            sha = entry['sha']
            if sha is not None:
                # Fail if a local-only blob was not uploaded first.
                git('cat-file', '-e', sha + '^{blob}')
            mode = entry['mode'] if sha is not None else '0'
            records.append(f'{mode} {sha or "0" * 40}\t{entry["path"]}\0')
        git('update-index', '-z', '--index-info', input=''.join(records).encode(), env=env)
        tree = git('write-tree', env=env).decode().strip()
    print('0' * 40 if os.environ.get('BAD_TREE') else tree)
elif endpoint.endswith('/commits'):
    tree = next(arg.removeprefix('tree=') for arg in args if arg.startswith('tree='))
    # Model the original 422: a missing remote tree must reject the commit.
    git('cat-file', '-e', tree + '^{tree}')
    print(tree)
elif '/git/refs/' not in endpoint:
    sys.exit('unexpected API call')
'''


class SyncTests(unittest.TestCase):
    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.repo, text=True).strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='upstream-sync-test-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'repo'
        subprocess.run(['git', 'clone', '--shared', '--quiet', str(ROOT), str(self.repo)], check=True)
        self.git('fetch', '--quiet', str(ROOT),
                 'refs/remotes/upstream/main:refs/remotes/upstream/main')
        self.head = self.git('rev-parse', 'HEAD')
        bin_dir = Path(self.temp.name) / 'bin'
        bin_dir.mkdir()
        mock = bin_dir / 'gh'
        mock.write_text(MOCK_GH)
        mock.chmod(0o755)
        remote = Path(self.temp.name) / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '--quiet', str(remote)], check=True)
        self.git('push', '--quiet', str(remote), 'HEAD:refs/heads/main')
        (self.repo / 'scripts').mkdir(exist_ok=True)
        (self.repo / 'scripts/upload-sync-tree.py').write_text(
            (ROOT / 'scripts/upload-sync-tree.py').read_text())
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}',
                        MOCK_LOG=str(Path(self.temp.name) / 'gh.log'), MOCK_REMOTE=str(remote),
                        TARGET_REPO='test/fork', UPSTREAM_REPO='test/upstream')

    def run_sync(self, success=True):
        self.git('push', '--quiet', '--force', self.env['MOCK_REMOTE'], 'HEAD:refs/heads/main')
        result = subprocess.run(['bash', '-c', SCRIPT], cwd=self.repo, env=self.env,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        return result

    def commit_tree(self, parent, path, text):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git('add', '--', path)
        return self.git('commit-tree', self.git('write-tree'), '-p', parent, '-m', 'test fixture')

    def test_real_upstream_and_wasm(self):
        self.run_sync()
        self.assertEqual(self.git('show', ':.github/workflows/sync.yaml'),
                         self.git('show', 'HEAD:.github/workflows/sync.yaml'))
        self.assertIn('1.13.0', self.git('show', ':worker/src/constants.ts'))
        for path in ('CHANGELOG.md', 'CHANGELOG_EN.md'):
            self.assertIn('v1.13.0(main)', self.git('show', f':{path}'))
            self.assertIn('WASM', self.git('show', f':{path}'))
        # Materialize merged index only in this disposable clone.
        self.git('checkout-index', '-a', '-f')
        self.git('apply', '--check', '.github/config/mail-parser-wasm-worker.patch')
        self.assertEqual(Path(self.env['MOCK_LOG']).read_text().count('parents[]='), 2)

    def test_unchanged_upstream_heartbeat(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.head)
        self.run_sync()
        self.assertTrue(self.git('show', ':.github/config/upstream-sync-heartbeat'))
        log = Path(self.env['MOCK_LOG']).read_text()
        self.assertIn('heartbeat', log)
        self.assertEqual(log.count('parents[]='), 1)
        self.assertIn('force=false', log)

    def test_local_only_tree_reproduces_original_422(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.head)
        legacy_script = SCRIPT.replace('python3 scripts/upload-sync-tree.py', 'git write-tree')
        result = subprocess.run(['bash', '-c', legacy_script], cwd=self.repo, env=self.env,
                                text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        log = Path(self.env['MOCK_LOG']).read_text()
        self.assertIn('/git/commits', log)
        self.assertNotIn('/git/refs/', log)

    def test_upload_failure_does_not_commit(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.head)
        self.env['FAIL_BLOB'] = '1'
        self.run_sync(success=False)
        self.assertNotIn('/git/commits', Path(self.env['MOCK_LOG']).read_text())

    def test_remote_tree_mismatch_does_not_commit(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.head)
        self.env['BAD_TREE'] = '1'
        self.run_sync(success=False)
        self.assertNotIn('/git/commits', Path(self.env['MOCK_LOG']).read_text())

    def test_binary_executable_symlink_and_deletion(self):
        self.git('update-ref', 'refs/remotes/upstream/main', self.head)
        (self.repo / 'sync-binary.bin').write_bytes(bytes(range(256)))
        executable = self.repo / 'sync-executable'
        executable.write_text('#!/bin/sh\ntrue\n')
        executable.chmod(0o755)
        (self.repo / 'sync-symlink').symlink_to('sync-binary.bin')
        self.git('add', 'sync-binary.bin', 'sync-executable', 'sync-symlink')
        self.git('rm', '--cached', 'README.md')
        self.run_sync()
        tree = self.git('write-tree')
        remote_tree = subprocess.check_output(
            ['git', '--git-dir', self.env['MOCK_REMOTE'], 'ls-tree', tree], text=True)
        self.assertIn('100755', remote_tree)
        self.assertIn('120000', remote_tree)
        self.assertNotIn('\tREADME.md', remote_tree)

    def test_protected_clean_change(self):
        upstream = self.commit_tree(self.head, '.github/workflows/backend_deploy.yaml',
                                    'upstream replacement\n')
        self.git('reset', '--hard', self.head)
        self.git('update-ref', 'refs/remotes/upstream/main', upstream)
        self.run_sync()
        self.assertEqual(self.git('show', ':.github/workflows/backend_deploy.yaml'),
                         self.git('show', 'HEAD:.github/workflows/backend_deploy.yaml'))

    def test_protected_conflict(self):
        path = '.github/workflows/backend_deploy.yaml'
        ours = self.commit_tree(self.head, path, 'fork configuration\n')
        self.git('reset', '--hard', self.head)
        theirs = self.commit_tree(self.head, path, 'upstream configuration\n')
        self.git('reset', '--hard', ours)
        self.git('update-ref', 'refs/remotes/upstream/main', theirs)
        self.run_sync()
        self.assertEqual(self.git('show', f':{path}'), 'fork configuration')

    def test_unknown_conflict_stops_before_api(self):
        base = self.commit_tree(self.head, 'sync-fixture.txt', 'base\n')
        self.git('reset', '--hard', base)
        ours = self.commit_tree(base, 'sync-fixture.txt', 'ours\n')
        self.git('reset', '--hard', base)
        theirs = self.commit_tree(base, 'sync-fixture.txt', 'theirs\n')
        self.git('reset', '--hard', ours)
        self.git('update-ref', 'refs/remotes/upstream/main', theirs)
        self.run_sync(success=False)
        self.assertFalse(Path(self.env['MOCK_LOG']).exists())

    def test_unknown_modify_delete_stops(self):
        base = self.commit_tree(self.head, 'sync-fixture.txt', 'base\n')
        self.git('reset', '--hard', base)
        ours = self.commit_tree(base, 'sync-fixture.txt', 'ours\n')
        self.git('reset', '--hard', base)
        self.git('rm', 'sync-fixture.txt')
        theirs = self.git('commit-tree', self.git('write-tree'), '-p', base, '-m', 'delete fixture')
        self.git('reset', '--hard', ours)
        self.git('update-ref', 'refs/remotes/upstream/main', theirs)
        self.run_sync(success=False)
        self.assertFalse(Path(self.env['MOCK_LOG']).exists())


if __name__ == '__main__':
    unittest.main()
