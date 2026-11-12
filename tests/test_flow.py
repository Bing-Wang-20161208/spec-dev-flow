"""Isolated contract tests for the v0.2 flow guardrails.

Run with the probe venv's Python: python -m unittest discover -s tests -v.
All project files, repositories, run state and evidence live in TemporaryDirectory.
Native execution is represented by a fake Popen; no model or real repo is used.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
flow = importlib.import_module('flow')


class FlowFixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='flow-contract-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        root_patch = patch.object(flow, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)
        self.repo = self.root / 'repositories' / 'fixture'
        (self.repo / 'src').mkdir(parents=True)
        (self.repo / 'src' / 'one.py').write_text('value = 1\n')
        (self.repo / 'src' / 'two.py').write_text('value = 2\n')
        config = self.root / '.specify' / 'flow.local.yml'
        config.parent.mkdir(parents=True)
        config.write_text(yaml.safe_dump({'repositories': {'fixture': str(self.repo)}}))
        self.feature_arg = 'specs/001-contract'
        self.f = self.root / self.feature_arg
        self.f.mkdir(parents=True)
        self.write_doc('spec.md', {
            'sources': ['fixture://current-user-request'],
            'repositories': ['fixture'],
            'ui': {'confirmed': True, 'needed': False},
        }, '# 需求\n把范围内的行为修改为已确认规则。\n')
        self.write_doc('plan.md', {'api': {'confirmed': True, 'needed': False}},
                       '# 实施计划\n按任务执行并验证。\n')
        self.write_tasks(self.task())
        self.sequence = 0

    def write_doc(self, name, meta, body):
        # Build the source format independently of flow.write_document.
        (self.f / name).write_text('---\n' + yaml.safe_dump(
            meta, allow_unicode=True, sort_keys=False) + '---\n' + body)

    def edit_meta(self, name, **changes):
        meta, body = flow.document(self.f / name)
        meta.update(changes)
        self.write_doc(name, meta, body)

    def task(self, task_id='T001', *, dependencies=(), scope=('src/one.py',),
             kinds=('实现', '总体验证'), status='待开发', independent=False):
        return {'id': task_id, 'title': '验证可观察行为 ' + task_id,
                '仓库': 'fixture', '范围': list(scope), '依赖': list(dependencies),
                '类型': list(kinds), '独立检查': '是' if independent else '否',
                '验证命令': ['python -m unittest'], '状态': status,
                '下一步': '执行当前任务', '阻塞': '无', '证据': []}

    def write_tasks(self, *items):
        chunks = ['# Tasks\n\n## 实施\n']
        for item in items:
            mark = 'x' if item['状态'] == '已完成' else ' '
            lines = [f"- [{mark}] {item['id']} {item['title']}"]
            for key in flow.FIELDS:
                value = item[key]
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                lines.append(f'  - {key}：{rendered}')
            chunks.append('\n'.join(lines) + '\n')
        (self.f / 'tasks.md').write_text('\n'.join(chunks))

    def approve(self, node):
        return flow.approval(SimpleNamespace(feature=self.feature_arg, node=node,
            quote='批准当前需求及范围', source='fixture://user-approval'))

    def approve_all(self):
        self.approve('review')
        self.approve('plan')
        flow.validate(self.f, execution=True)

    def complete(self, task='T001'):
        return flow.completed(SimpleNamespace(feature=self.feature_arg, task=task))

    def evidence(self, *, task='T001', problem='P-shared', phase='test',
                 outcome='passed', progress=True, **overrides):
        self.sequence += 1
        task_def = flow.tasks(self.f)[task]
        current = flow.context_version(self.f, self.repo, task_def)
        feature_current = flow.feature_context(self.f)
        record = {
            'attempt_id': f'attempt-{self.sequence:03d}',
            'run_id': f'native-run-{self.sequence:03d}',
            'task_id': task, 'problem_id': problem, 'phase': phase,
            'started_at': f'2026-11-10T00:00:{self.sequence:02d}+00:00',
            'outcome': outcome, 'progress': progress,
            'repair': False, 'upgrade': False, 'replan': False, 'pgid': None,
            'context_version': current, 'after_context': current,
            'feature_context': feature_current, 'after_feature_context': feature_current,
        }
        record.update(overrides)
        destination = self.f / 'evidence' / record['attempt_id'] / 'result.json'
        destination.parent.mkdir(parents=True)
        destination.write_text(json.dumps(record, ensure_ascii=False))
        return record

    def history(self, *, problem='P-shared', task='T001', phase='implement',
                repair=False, upgrade=False, replan=False):
        # Process liveness is a separate native integration concern.
        with patch.object(flow, 'alive_group', return_value=False):
            return flow.history_check(self.f, flow.tasks(self.f)[task], problem,
                                      phase, repair, upgrade, replan)

    def run_fake_test(self, *, stdout=None, output=None, returncode=0,
                      native_status='completed', resume=None, phase='test'):
        """Use the real execute bookkeeping with a deterministic native boundary."""
        self.approve_all()
        run_dir = self.root / '.specify/workflows/runs/fake-native-run'
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / 'state.json').write_text(json.dumps({
            'status': native_status,
            'step_results': {'work': {'output': output if output is not None else {
                'exit_code': 0, 'stdout': 'fixture test passed', 'stderr': ''}}},
        }))
        proc = Mock(pid=99999999, returncode=returncode)
        proc.communicate.return_value = (
            stdout if stdout is not None else json.dumps({
                'run_id': 'fake-native-run', 'status': native_status}), '')
        args = SimpleNamespace(feature=self.feature_arg, task='T001', phase=phase,
            problem='P-shared', repair=False, upgrade=False, replan=False,
            previous=None, resume=resume, model='fixture-current-model' if phase!='test' else None, why='fixture current catalog' if phase!='test' else None, effort=None,
            command='python -m unittest', timeout=5)
        with patch.object(flow.subprocess, 'Popen', return_value=proc), \
             patch.object(flow, 'git_snapshot', return_value={'head': 'test', 'changes': {}}), \
             patch.object(flow, 'alive_group', return_value=False), \
             patch.object(flow, 'stop_group'), \
             patch.object(flow, 'codex', return_value='/fixture-only/bin/codex'), \
             patch.object(flow, 'discover', return_value=([{'slug':'fixture-current-model','visibility':'list','supported_reasoning_levels':[]}],{'provider':'fixture'})):
            return flow.execute(args)


class TaskFormatTests(FlowFixture):
    def test_valid_native_tasks_keep_dependency_and_completed_state(self):
        self.write_tasks(self.task(status='已完成'),
                         self.task('T002', dependencies=['T001'], scope=['src/two.py']))
        parsed = flow.tasks(self.f)
        self.assertEqual(set(parsed), {'T001', 'T002'})
        self.assertTrue(parsed['T001']['checked'])
        self.assertEqual(parsed['T002']['依赖'], ['T001'])
        flow.validate(self.f)

    def test_duplicate_ids_are_rejected(self):
        self.write_tasks(self.task(), self.task())
        with self.assertRaises(flow.FlowError):
            flow.tasks(self.f)

    def test_missing_field_is_rejected(self):
        path = self.f / 'tasks.md'
        path.write_text(path.read_text().replace('  - 验证命令：["python -m unittest"]\n', ''))
        with self.assertRaises(flow.FlowError):
            flow.tasks(self.f)

    def test_array_fields_reject_wrong_shape_and_non_strings(self):
        for field in ('范围', '依赖', '类型', '验证命令', '证据'):
            for bad in ('not-json', '{}', '[1]', 'null'):
                with self.subTest(field=field, value=bad):
                    self.write_tasks(self.task())
                    path = self.f / 'tasks.md'
                    lines = path.read_text().splitlines()
                    path.write_text('\n'.join(
                        f'  - {field}：{bad}' if line.startswith(f'  - {field}：') else line
                        for line in lines) + '\n')
                    with self.assertRaises(flow.FlowError):
                        flow.tasks(self.f)

    def test_checkbox_and_status_must_agree(self):
        for status, replacement in (('待开发', '- [x] T001'), ('已完成', '- [ ] T001')):
            with self.subTest(status=status):
                self.write_tasks(self.task(status=status))
                path = self.f / 'tasks.md'
                text = path.read_text()
                path.write_text(text.replace('- [x] T001' if status == '已完成' else '- [ ] T001', replacement))
                with self.assertRaises(flow.FlowError):
                    flow.tasks(self.f)

    def test_invalid_status_and_independent_check_are_rejected(self):
        for field, bad in (('状态', '差不多完成'), ('独立检查', 'maybe')):
            with self.subTest(field=field):
                task = self.task()
                task[field] = bad
                self.write_tasks(task)
                with self.assertRaises(flow.FlowError):
                    flow.tasks(self.f)

    def test_dependencies_reject_missing_self_and_cycles(self):
        cases = [
            [self.task(dependencies=['T404'])],
            [self.task(dependencies=['T001'])],
            [self.task(dependencies=['T002']), self.task('T002', dependencies=['T001'])],
            [self.task(dependencies=['T002']), self.task('T002', dependencies=['T003']),
             self.task('T003', dependencies=['T001'])],
        ]
        for items in cases:
            with self.subTest(edges=[(t['id'], t['依赖']) for t in items]):
                self.write_tasks(*items)
                with self.assertRaises(flow.FlowError):
                    flow.tasks(self.f)

    def test_malformed_task_is_not_silently_dropped_beside_valid_task(self):
        # Losing one bad heading must not make an incomplete plan look valid.
        self.write_tasks(self.task(), self.task('T002'))
        path = self.f / 'tasks.md'
        path.write_text(path.read_text().replace('- [ ] T002 ', '- [ ] Tbad '))
        with self.assertRaises(flow.FlowError):
            flow.validate(self.f)

    def test_unconfigured_repo_and_scope_escape_are_rejected(self):
        for field, value in (('仓库', 'real-project'), ('范围', ['../outside.py'])):
            with self.subTest(field=field):
                task = self.task()
                task[field] = value
                self.write_tasks(task)
                with self.assertRaises(flow.FlowError):
                    flow.validate(self.f)


class ApprovalTests(FlowFixture):
    def test_review_can_be_confirmed_before_plan_or_tasks_exist(self):
        (self.f / 'plan.md').unlink()
        (self.f / 'tasks.md').unlink()
        result = self.approve('review')
        spec, _ = flow.document(self.f / 'spec.md')
        self.assertEqual(spec['review_approval']['version'], flow.spec_version(self.f))
        self.assertEqual(result['version'], spec['review_approval']['version'])
        self.assertFalse((self.f / 'plan.md').exists())
        self.assertFalse((self.f / 'tasks.md').exists())

    def test_plan_requires_prior_review(self):
        with self.assertRaises(flow.FlowError):
            self.approve('plan')
        self.approve_all()

    def test_progress_updates_preserve_approval_and_round_trip(self):
        self.approve_all()
        original = flow.versions(self.f)
        for status in ('开发中', '测试中', '异常', '等待用户', '等待接口', '已完成'):
            with self.subTest(status=status):
                flow.update_task(self.f, 'T001', 状态=status, 下一步='继续核对',
                                 阻塞='已有依据', 证据=['evidence/local.json'], 最近执行='run-fixture')
                parsed = flow.tasks(self.f)['T001']
                self.assertEqual(parsed['状态'], status)
                self.assertEqual(parsed['checked'], status == '已完成')
                self.assertEqual(parsed['证据'], ['evidence/local.json'])
                self.assertEqual(flow.versions(self.f), original)
                flow.validate(self.f, execution=True)

    def test_cli_note_changes_status_without_invalidating_plan(self):
        self.approve_all()
        original = flow.versions(self.f)
        argv = ['flow.py', 'note', self.feature_arg, 'T001', '--status', '开发中',
                '--next', '编写实现', '--reason', '无']
        with patch.object(sys, 'argv', argv), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(flow.main(), 0)
        self.assertEqual(flow.tasks(self.f)['T001']['状态'], '开发中')
        self.assertEqual(flow.versions(self.f), original)
        flow.validate(self.f, execution=True)

    def test_scope_change_invalidates_plan_but_review_can_be_reused(self):
        self.approve_all()
        previous = flow.versions(self.f)
        self.write_tasks(self.task(scope=['src/one.py', 'src/two.py']))
        current = flow.versions(self.f)
        self.assertEqual(current[0], previous[0])
        self.assertNotEqual(current[1], previous[1])
        with self.assertRaises(flow.FlowError):
            flow.validate(self.f, execution=True)
        self.approve('plan')
        flow.validate(self.f, execution=True)

    def test_spec_content_change_invalidates_review_and_plan(self):
        self.approve_all()
        with (self.f / 'spec.md').open('a') as handle:
            handle.write('\n新增验收：必须覆盖额外边界。\n')
        with self.assertRaises(flow.FlowError):
            flow.validate(self.f, execution=True)
        with self.assertRaises(flow.FlowError):
            self.approve('plan')


class CompletionTests(FlowFixture):
    def test_current_passing_test_can_complete_task_and_parent(self):
        self.approve_all()
        self.evidence()
        self.complete()
        self.assertTrue(flow.tasks(self.f)['T001']['checked'])
        self.complete(task=None)
        spec, _ = flow.document(self.f / 'spec.md')
        self.assertEqual(spec['development_status'], '已完成')
        self.assertEqual(spec['final_verification']['tasks'], ['T001'])
        flow.validate(self.f, execution=True)

    def test_code_change_makes_prior_passing_test_stale(self):
        self.approve_all()
        self.evidence()
        (self.repo / 'src/one.py').write_text('value = 99\n')
        with self.assertRaises(flow.FlowError):
            self.complete()
        self.assertFalse(flow.tasks(self.f)['T001']['checked'])
        self.evidence()
        self.complete()

    def test_test_that_changed_code_is_not_current_passing_evidence(self):
        self.approve_all()
        self.evidence(after_context='different-code-after-test')
        with self.assertRaises(flow.FlowError):
            self.complete()

    def test_implementation_success_is_not_test_success(self):
        self.approve_all()
        self.evidence(phase='implement')
        with self.assertRaises(flow.FlowError):
            self.complete()

    def test_later_failed_or_unknown_execution_blocks_old_success(self):
        self.approve_all()
        self.evidence()
        latest = self.evidence(outcome='failed', progress=False)
        path = self.f / 'evidence' / latest['attempt_id'] / 'result.json'
        for outcome in ('failed', 'unknown', 'pending'):
            with self.subTest(outcome=outcome):
                latest['outcome'] = outcome
                path.write_text(json.dumps(latest))
                with self.assertRaises(flow.FlowError):
                    self.complete()
                self.assertFalse(flow.tasks(self.f)['T001']['checked'])

    def test_independent_review_requires_current_explicit_approval(self):
        self.write_tasks(self.task(independent=True))
        self.approve_all()
        self.evidence()
        self.evidence(phase='review', review_approved=False)
        with self.assertRaises(flow.FlowError):
            self.complete()
        self.evidence(phase='review', review_approved=True)
        self.complete()

    def test_parent_requires_all_tasks_and_final_verification_role(self):
        self.write_tasks(self.task(kinds=['实现']))
        self.approve_all()
        self.evidence()
        with self.assertRaises(flow.FlowError):
            self.complete(task=None)
        self.complete()
        with self.assertRaises(flow.FlowError):
            self.complete(task=None)

    def test_parent_requires_final_test_against_current_whole_feature(self):
        # T001 is the final verifier. Its own file stays untouched when T002 changes.
        self.write_tasks(self.task(), self.task('T002', scope=['src/two.py'], kinds=['实现']))
        self.approve_all()
        self.evidence(task='T001')
        self.complete('T001')
        (self.repo / 'src/two.py').write_text('value = 200\n')
        self.evidence(task='T002')
        self.complete('T002')
        # Every individual task has a current passing test; only aggregate proof is stale.
        with self.assertRaises(flow.FlowError):
            self.complete(task=None)
        self.evidence(task='T001')
        self.complete(task=None)
        self.assertEqual(flow.document(self.f / 'spec.md')[0]['development_status'], '已完成')

    def test_parent_rechecks_code_even_if_tasks_are_checked(self):
        self.approve_all()
        self.evidence()
        self.complete()
        (self.repo / 'src/one.py').write_text('value = 123\n')
        with self.assertRaises(flow.FlowError):
            self.complete(task=None)

    def test_settle_and_progress_assessment_do_not_turn_unknown_into_pass(self):
        self.approve_all()
        record = self.evidence(outcome='unknown', progress=None)
        (self.f / 'recovery.md').write_text('进程已结束；现场修改已核对，尚未验证。\n')
        args = SimpleNamespace(feature=self.feature_arg, attempt=record['attempt_id'],
                               evidence='recovery.md', reason='保存修改后重新测试')
        with patch.object(flow, 'alive_group', return_value=False):
            flow.settle(args)
        flow.assess(args)
        saved = flow.records(self.f)[0]
        self.assertEqual(saved['outcome'], 'unknown')
        self.assertTrue(saved['progress'])
        self.assertIn('settled_at', saved)
        with self.assertRaises(flow.FlowError):
            self.complete()


class HistoryLimitTests(FlowFixture):
    def test_repair_budget_accumulates_across_distinct_native_runs(self):
        for _ in range(2):
            self.evidence(phase='implement', outcome='failed', repair=True, progress=True)
        self.history(repair=True)  # Third repair is allowed.
        self.evidence(phase='implement', outcome='failed', repair=True, progress=True)
        with self.assertRaises(flow.FlowError):
            self.history(repair=True)  # A new run cannot buy a fourth repair.
        # A genuinely different problem has its own repair budget.
        self.history(problem='P-independent', repair=True)

    def test_failed_problem_cannot_bypass_repair_accounting_with_fresh_run(self):
        self.evidence(phase='implement', outcome='failed', progress=True)
        with self.assertRaises(flow.FlowError):
            self.history(repair=False)
        self.history(repair=True)

    def test_upgrade_and_replan_each_have_one_task_wide_budget(self):
        self.history(upgrade=True, replan=True)
        self.evidence(phase='implement', upgrade=True, replan=True)
        for flag in ('upgrade', 'replan'):
            for problem in ('P-shared', 'P-renamed'):
                with self.subTest(flag=flag, problem=problem):
                    with self.assertRaises(flow.FlowError):
                        self.history(problem=problem, **{flag: True})
        self.history(problem='P-renamed')

    def test_no_progress_limit_spans_runs_and_unknown_requires_settlement(self):
        self.evidence(phase='implement', outcome='failed', progress=False)
        self.history(repair=True)
        self.evidence(phase='implement', outcome='unknown', progress=None,
                      settled_at='2026-11-10T01:00:00+00:00')
        with self.assertRaises(flow.FlowError):
            self.history(repair=True)
        self.history(problem='P-independent')

    def test_cli_success_without_semantic_progress_counts_as_no_progress(self):
        for _ in range(2):
            self.evidence(phase='implement', outcome='passed', progress=None)
        with self.assertRaises(flow.FlowError):
            self.history()

    def test_recorded_progress_breaks_consecutive_stall_but_not_total_budget(self):
        self.evidence(phase='implement', outcome='failed', progress=False, repair=True)
        self.evidence(phase='implement', outcome='failed', progress=True, repair=True)
        self.evidence(phase='implement', outcome='failed', progress=False, repair=True)
        self.history(phase='test')  # Only one consecutive stalled attempt.
        with self.assertRaises(flow.FlowError):
            self.history(repair=True)  # Total budget remains exhausted.

    def test_unsettled_unknown_blocks_dispatch_even_for_different_problem(self):
        self.evidence(outcome='unknown', progress=None)
        with self.assertRaises(flow.FlowError):
            self.history(problem='P-independent', phase='test')


class NativeResultContractTests(FlowFixture):
    def test_explicit_success_creates_usable_test_evidence(self):
        result = self.run_fake_test()
        self.assertEqual(result['outcome'], 'passed')
        self.assertEqual(flow.records(self.f)[0]['run_id'], 'fake-native-run')
        self.complete()

    def test_missing_exit_code_cannot_count_as_success(self):
        result = self.run_fake_test(output={'stdout': 'looks successful'})
        self.assertNotEqual(result['outcome'], 'passed')
        with self.assertRaises(flow.FlowError):
            self.complete()

    def test_incomplete_native_json_remains_unknown(self):
        result = self.run_fake_test(stdout='{"run_id":')
        self.assertEqual(result['outcome'], 'unknown')
        self.assertFalse(flow.tasks(self.f)['T001']['checked'])
        with self.assertRaises(flow.FlowError):
            self.complete()

    def test_zero_shell_exit_does_not_override_native_failure(self):
        result = self.run_fake_test(native_status='failed')
        self.assertNotEqual(result['outcome'], 'passed')
        with self.assertRaises(flow.FlowError):
            self.complete()

    def test_unstructured_blocking_review_cannot_complete_through_execute(self):
        self.write_tasks(self.task(independent='是'))
        self.approve_all()
        self.evidence(phase='test',outcome='passed',progress=True)
        result=self.run_fake_test(phase='review',output={'exit_code':0,'stdout':json.dumps({'approved':True,'findings':['P1: blocking defect'],'summary':'blocking issue'})})
        self.assertEqual(result['outcome'],'failed')
        self.assertFalse(result['review_approved'])
        with self.assertRaises(flow.FlowError):self.complete()

    def test_nonzero_launcher_exit_does_not_override_success_payload(self):
        result = self.run_fake_test(returncode=1)
        self.assertNotEqual(result['outcome'], 'passed')
        with self.assertRaises(flow.FlowError):
            self.complete()


class ImplementationPromptTests(FlowFixture):
    def test_prompt_leaves_internal_agent_orchestration_unspecified(self):
        self.write_tasks(self.task())
        prompt = flow.phase_prompt(
            self.f, flow.tasks(self.f)['T001'], 'implement', 'T001'
        )
        self.assertIn('只实施 T001', prompt)
        for phrase in ('自行委派', '切模型', '一层子 Agent', '最多两个'):
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, prompt)


class ReviewSchemaTests(unittest.TestCase):
    def test_string_finding_cannot_hide_a_blocking_issue(self):
        self.assertFalse(flow.review_ok({'approved':True,'findings':['P1: blocking defect'],'summary':'blocking issue'}))

    def test_unknown_or_contradictory_findings_cannot_pass(self):
        for value in ({}, {'severity':'unrecognized'}, {'severity':'high','blocking':False}, {'severity':'low','blocking':True}, {'severity':'low','blocking':'false'}):
            with self.subTest(value=value):self.assertFalse(flow.review_ok({'approved':True,'findings':[value],'summary':'review'}))

    def test_real_fenced_review_output_and_empty_findings_are_valid(self):
        value=flow.parse_review('```json\n{"approved":true,"findings":[],"summary":"checked"}\n```')
        self.assertTrue(flow.review_ok(value))

class IndependentReviewTests(FlowFixture):
    def test_dot_relative_scope_allows_the_same_file_that_validate_accepted(self):
        self.write_tasks(self.task(scope=['./src/one.py']))
        flow.validate(self.f)
        before = {'head': 'test', 'changes': {}}
        after = {'head': 'test', 'changes': {
            'src/one.py': {'status': ' M', 'hash': 'new-content'}}}
        self.assertEqual(flow.changed_outside(before, after, ['./src/one.py']), [])

    def test_outside_modification_is_detected_without_blame_for_existing_dirt(self):
        before = {'head': 'test', 'changes': {
            'unrelated.py': {'status': ' M', 'hash': 'preexisting'}}}
        inside_only = {'head': 'test', 'changes': {
            **before['changes'], 'src/one.py': {'status': ' M', 'hash': 'new'}}}
        self.assertEqual(flow.changed_outside(before, inside_only, ['src/one.py']), [])
        outside_changed = {'head': 'test', 'changes': {
            **inside_only['changes'], 'unrelated.py': {'status': ' M', 'hash': 'changed'}}}
        self.assertEqual(flow.changed_outside(before, outside_changed, ['src/one.py']),
                         ['unrelated.py'])

    def test_changed_source_in_directory_symlink_invalidates_passing_test(self):
        linked_source = self.repo / 'linked-source'
        linked_source.mkdir()
        (linked_source / 'value.py').write_text('value = 1\n')
        (self.repo / 'src' / 'linked').symlink_to(linked_source, target_is_directory=True)
        self.write_tasks(self.task(scope=['src']))
        # Both rejecting linked directories up front and hashing their contents
        # are safe policies; silently certifying stale contents is not.
        with self.assertRaises(flow.FlowError):
            self.approve_all()
            self.evidence()
            (linked_source / 'value.py').write_text('value = 2\n')
            self.complete()

    def test_scope_must_reject_directory_symlink_outside_repository(self):
        outside = self.root / 'outside-source'
        outside.mkdir()
        (outside / 'value.py').write_text('value = 1\n')
        (self.repo / 'src' / 'linked').symlink_to(outside, target_is_directory=True)
        self.write_tasks(self.task(scope=['src']))
        with self.assertRaises(flow.FlowError):
            flow.validate(self.f)

    def write_resume(self, *, code_dir=None, **input_changes):
        self.approve_all()
        run = self.root / '.specify/workflows/runs/previous-native-run'
        run.mkdir(parents=True, exist_ok=True)
        inputs = {'feature': self.feature_arg, 'task_id': 'T001',
                  'problem_id': 'P-shared', 'phase': 'test',
                  'code_dir': str(self.repo) if code_dir is None else code_dir,
                  'plan_version': flow.versions(self.f)[1]}
        inputs.update(input_changes)
        (run / 'inputs.json').write_text(json.dumps({'inputs': inputs}))
        (run / 'state.json').write_text(json.dumps({'status': 'failed'}))
        return run.name

    def test_resume_revalidates_current_approval_before_launch(self):
        run = self.write_resume()
        with (self.f / 'spec.md').open('a') as handle:
            handle.write('\n新增验收要求。\n')
        args = SimpleNamespace(feature=self.feature_arg, task='T001', phase='test',
            problem='P-shared', repair=False, upgrade=False, replan=False,
            previous=None, resume=run, model=None, why=None, effort=None,
            command='python -m unittest', timeout=5)
        with patch.object(flow.subprocess, 'Popen') as dispatch, \
             patch.object(flow, 'alive_group', return_value=False):
            with self.assertRaises(flow.FlowError):
                flow.execute(args)
            dispatch.assert_not_called()

    def test_resume_cannot_move_test_evidence_to_another_repository(self):
        # Native workflow.yml embeds the original repo in its shell `cd`.
        # A remapped alias must not let the old run certify the new repo's files.
        old_repo = self.root / 'repositories' / 'old-fixture'
        old_repo.mkdir()
        run = self.write_resume(code_dir=str(old_repo))
        with self.assertRaises(flow.FlowError):
            self.run_fake_test(resume=run)

    def test_resume_rechecks_native_identity_and_plan_version(self):
        for changes in ({'feature': 'specs/another-feature'}, {'task_id': 'T999'},
                        {'problem_id': 'P-another'}, {'phase': 'implement'},
                        {'plan_version': 'previous-plan-version'}):
            with self.subTest(changes=changes):
                run = self.write_resume(**changes)
                with self.assertRaises(flow.FlowError):
                    self.run_fake_test(resume=run)

    def test_resume_does_not_reset_the_no_progress_budget(self):
        run = self.write_resume()
        for _ in range(2):
            self.evidence(phase='implement', outcome='passed', progress=None)
        with self.assertRaises(flow.FlowError):
            self.run_fake_test(resume=run)

    def test_same_process_cannot_take_a_second_global_writer_lease(self):
        with flow.locked():
            with self.assertRaises(flow.FlowError):
                with flow.locked():
                    self.fail('Two writers acquired the global lease')
        # The lock must be released after the writer leaves the critical section.
        with flow.locked():
            pass


if __name__ == '__main__':
    unittest.main()
