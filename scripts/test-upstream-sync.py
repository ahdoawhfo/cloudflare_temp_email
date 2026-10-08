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
        mock.write_text('#!/bin/bash\nset -eu\nprintf "%s\\n" "$*" >> "$MOCK_LOG"\n'
                        'if [ "$3" = POST ]; then git write-tree; fi\n')
        mock.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{bin_dir}:{os.environ["PATH"]}',
                        MOCK_LOG=str(Path(self.temp.name) / 'gh.log'),
                        TARGET_REPO='test/fork', UPSTREAM_REPO='test/upstream')

    def run_sync(self, success=True):
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
