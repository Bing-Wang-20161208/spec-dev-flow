"""Actual temporary Git histories prove the personal-repository boundary."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
guard = importlib.import_module('repo_guard')


class RepositoryBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = {**os.environ, 'GIT_AUTHOR_NAME': 'Fixture', 'GIT_AUTHOR_EMAIL': 'fixture@example.invalid',
                            'GIT_COMMITTER_NAME': 'Fixture', 'GIT_COMMITTER_EMAIL': 'fixture@example.invalid'}
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.root)], check=True)
        def in_fixture(*args):
            return subprocess.check_output(['git', '-C', str(self.root), *args], stderr=subprocess.PIPE, env=self.environment)
        self.run_git = in_fixture
        self.addCleanup(patch.stopall)
        patch.object(guard, 'git', side_effect=in_fixture).start()
        # Baseline/remote probes run with subprocess.run; use a controlled cwd too.
        real_run = subprocess.run
        def run(*args, **kwargs):
            kwargs['cwd'] = self.root
            kwargs['env'] = self.environment
            return real_run(*args, **kwargs)
        patch.object(guard.subprocess, 'run', side_effect=run).start()

    def write(self, path, content='synthetic fixture\n'):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self):
        self.run_git('add', '-A')
        self.run_git('commit', '-qm', 'synthetic fixture')
        return self.run_git('rev-parse', 'HEAD').decode().strip()

    def push_line(self, tip, remote='0' * 40):
        return ['refs/heads/main ' + tip + ' refs/heads/main ' + remote]

    def test_only_framework_paths_pass(self):
        self.write('README.md')
        self.write('docs/workflow.md')
        self.write('scripts/flow.py')
        tip = self.commit()
        self.assertEqual(guard.check_index(), [])
        self.assertEqual(guard.check_push(self.push_line(tip)), [])

    def test_forced_business_and_runtime_paths_cannot_be_committed(self):
        for path in ('specs/private/spec.md', '.specify/workflows/runs/private/inputs.json',
                     '流程重构/验证记录/business.md', '.specify/flow.local.yml', 'new-business.md'):
            self.write(path)
        self.run_git('add', '-A')
        self.assertEqual(len(guard.check_index()), 5)

    def test_ignored_business_stays_on_disk_and_out_of_index_until_force_add(self):
        self.write('.gitignore', '/specs/\n')
        self.write('specs/private/spec.md')
        self.run_git('add', '-A')
        self.assertEqual(guard.check_index(), [])
        self.assertTrue((self.root / 'specs/private/spec.md').is_file())
        self.run_git('add', '-f', 'specs/private/spec.md')
        self.assertEqual(guard.check_index(), ['specs/private/spec.md'])

    def test_add_then_delete_business_still_blocks_push_history(self):
        self.write('README.md')
        remote = self.commit()
        self.write('specs/private/spec.md')
        self.commit()
        self.run_git('rm', '-q', 'specs/private/spec.md')
        tip = self.commit()
        self.assertEqual(guard.check_index(), [])
        self.assertIn('specs/private/spec.md', guard.check_push(self.push_line(tip, remote)))

    def test_untracking_data_already_in_remote_does_not_resend_it(self):
        self.write('README.md')
        self.write('.specify/flow.local.yml')
        baseline = self.commit()
        self.run_git('rm', '--cached', '.specify/flow.local.yml')
        self.write('.gitignore', '/.specify/flow.local.yml\n')
        tip = self.commit()
        self.assertEqual(guard.check_push(self.push_line(tip, baseline)), [])
        self.assertTrue((self.root / '.specify/flow.local.yml').is_file())

    def test_tags_are_dereferenced_and_ref_deletion_does_not_upload(self):
        self.write('specs/private/spec.md')
        tip = self.commit()
        self.run_git('tag', '-am', 'fixture', 'fixture-tag')
        tag = self.run_git('rev-parse', 'fixture-tag').decode().strip()
        self.assertIn('specs/private/spec.md', guard.check_push(self.push_line(tag)))
        self.assertEqual(guard.check_push(self.push_line('0' * 40, tip)), [])

    def test_private_terms_block_framework_body_commit_and_tag_messages(self):
        self.write('.specify/publish.local.json', '{"blocked_terms":["private-fixture-name"]}')
        self.write('.gitignore', '/.specify/*.local.json\n')
        self.write('README.md', 'PRIVATE-FIXTURE-NAME\n')
        self.run_git('add', '-A')
        self.assertIn('README.md', guard.check_index())
        self.write('README.md', 'framework\n')
        self.run_git('add', '-A')
        self.run_git('commit', '-qm', 'private-fixture-name')
        tip = self.run_git('rev-parse', 'HEAD').decode().strip()
        self.assertTrue(any('元数据' in item for item in guard.check_push(self.push_line(tip))))
        self.run_git('tag', '-am', 'private-fixture-name', 'framework-tag')
        tag = self.run_git('rev-parse', 'framework-tag').decode().strip()
        self.assertTrue(any('标签' in item for item in guard.check_push(self.push_line(tag))))
        self.run_git('tag', '-am', 'generic outer tag', 'outer-tag', tag)
        outer = self.run_git('rev-parse', 'outer-tag').decode().strip()
        self.assertTrue(any('标签' in item for item in guard.check_push(self.push_line(outer))))

    def test_common_text_encodings_do_not_bypass_private_terms(self):
        term = '禁止样例'
        for encoding in ('utf-8', 'utf-16', 'utf-16-le', 'utf-16-be', 'gb18030'):
            self.assertTrue(guard.contains_private(('prefix ' + term).encode(encoding), [term]))
        self.assertTrue(guard.contains_private(b'encoding GB18030\n\n' + term.encode('gb18030'), [term]))


if __name__ == '__main__':
    unittest.main()
