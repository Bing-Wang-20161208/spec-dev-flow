"""Notification delivery contracts; no real network, bot, business sample or credentials."""
import base64
import contextlib
import fcntl
import hashlib
import hmac
import http.client
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import subprocess
import unittest
from unittest.mock import patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import notify
import flow
import test_flow


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        notify.init_config(self.root)
        self.config = self.root / notify.CONFIG
        self.settings = {'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/fixture',
                         'secret': 'fixture-secret', 'keyword': 'Codex'}
        self.config.write_text(json.dumps(self.settings))
        self.event = {'kind': 'attention', 'source': 'specs/fixture', 'key': 'review',
                      'version': 'v1', 'title': '示例需求', 'summary': '请确认当前范围',
                      'next': '在 Codex 回复', 'thread': 'fixture-thread'}

    def receipt(self):
        return json.loads((self.root / notify.RECEIPTS / (notify.event_id(self.event) + '.json')).read_text())

    def test_private_init_is_non_destructive(self):
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(notify.init_config(self.root)['status'], 'exists')
        self.assertEqual(json.loads(self.config.read_text()), self.settings)

    def test_empty_config_and_preview_do_not_send_or_claim_delivery(self):
        self.config.write_text(json.dumps(notify.TEMPLATE))
        with patch.object(notify, 'post') as post:
            self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'not_configured')
            self.assertEqual(notify.deliver(self.event, root=self.root, dry_run=True)['status'], 'preview')
            post.assert_not_called()
        self.assertFalse((self.root / notify.RECEIPTS).exists())

    def test_invalid_url_and_permissive_file_cannot_send_or_leak(self):
        for url in ('http://open.feishu.cn/x', 'https://evil.example/hook',
                    self.settings['webhook'] + '?secret=fixture-secret'):
            self.config.write_text(json.dumps({**self.settings, 'webhook': url}))
            with patch.object(notify, 'post') as post:
                result = notify.deliver(self.event, root=self.root)
                self.assertEqual(result['status'], 'local_error')
                self.assertNotIn('fixture-secret', json.dumps(result))
                post.assert_not_called()
        self.config.write_text(json.dumps(self.settings))
        self.config.chmod(0o644)
        self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'local_error')

    def test_signature_matches_feishu_empty_message_hmac_contract(self):
        with patch.object(notify.time, 'time', return_value=1700000000):
            value = notify.payload(self.event, self.settings)
        expected = base64.b64encode(hmac.new(b'1700000000\nfixture-secret', b'', hashlib.sha256).digest()).decode()
        self.assertEqual(value['sign'], expected)
        self.assertEqual(value['timestamp'], '1700000000')
        self.assertTrue(value['content']['text'].startswith('Codex\n'))

    def test_durable_intent_precedes_post_and_body_is_not_in_receipt(self):
        def sending(config, data):
            self.assertEqual(self.receipt()['status'], 'sending')
            return {'status': 'sent', 'code': 0}
        with patch.object(notify, 'post', side_effect=sending) as post:
            self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'sent')
            changed = {**self.event, 'summary': '改写文字不应触发重发'}
            self.assertEqual(notify.deliver(changed, root=self.root)['status'], 'suppressed')
            post.assert_called_once()
        saved = json.dumps(self.receipt(), ensure_ascii=False)
        for private in (self.settings['webhook'], self.settings['secret'], self.event['summary']):
            self.assertNotIn(private, saved)

    def test_rejected_unknown_and_crash_intent_never_auto_replay(self):
        for status in ('rejected', 'unknown', 'sending'):
            event = {**self.event, 'key': status}
            with patch.object(notify, 'post', return_value={'status': status}) as post:
                self.assertEqual(notify.deliver(event, root=self.root)['status'], status)
                result = notify.deliver(event, root=self.root)
                self.assertEqual(result['previous_status'], status)
                post.assert_called_once()

    def test_post_exception_keeps_durable_intent_and_no_auto_retry(self):
        with patch.object(notify, 'post', side_effect=RuntimeError('fixture-secret')) as post:
            result = notify.deliver(self.event, root=self.root)
            self.assertEqual(result['status'], 'local_error')
            self.assertNotIn('fixture-secret', json.dumps(result))
            self.assertEqual(self.receipt()['status'], 'sending')
            self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'suppressed')
            post.assert_called_once()

    def test_new_problem_or_version_is_a_new_event(self):
        with patch.object(notify, 'post', return_value={'status': 'sent'}) as post:
            notify.deliver(self.event, root=self.root)
            notify.deliver({**self.event, 'version': 'v2'}, root=self.root)
            notify.deliver({**self.event, 'key': 'plan'}, root=self.root)
            self.assertEqual(post.call_count, 3)

    def test_busy_sender_returns_without_post(self):
        directory = self.root / notify.RECEIPTS
        directory.mkdir()
        with (directory / (notify.event_id(self.event) + '.lock')).open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(notify, 'post') as post:
                self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'busy')
                post.assert_not_called()
                other = {**self.event, 'key': 'another-problem'}
                post.return_value = {'status': 'sent'}
                self.assertEqual(notify.deliver(other, root=self.root)['status'], 'sent')

    def test_redirect_is_disabled(self):
        self.assertIsNone(notify.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://evil.example'))

    def test_http_success_requires_explicit_business_success(self):
        for response, expected in (({'code': 0}, 'sent'), ({'StatusCode': 0}, 'sent'),
                                   ({'code': 19024}, 'rejected'), ({}, 'unknown'),
                                   ({'code': False}, 'unknown'), ([], 'unknown')):
            opener = unittest.mock.MagicMock()
            opener.open.return_value.__enter__.return_value.read.return_value = json.dumps(response).encode()
            with patch.object(notify.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(notify.post_once(self.settings, {})['status'], expected)
                self.assertEqual(opener.open.call_args.kwargs['timeout'], 5)

    def test_network_exception_and_server_error_are_unknown(self):
        for error, expected in ((TimeoutError(), 'unknown'),
                               (http.client.IncompleteRead(b'partial'), 'unknown'),
                               (urllib.error.HTTPError('fixture', 503, '', {}, None), 'unknown'),
                               (urllib.error.HTTPError('fixture', 400, '', {}, None), 'rejected')):
            opener = unittest.mock.MagicMock()
            opener.open.side_effect = error
            with patch.object(notify.urllib.request, 'build_opener', return_value=opener):
                self.assertEqual(notify.post_once(self.settings, {})['status'], expected)

    def test_total_deadline_and_worker_error_are_unknown_without_retry_or_credentials(self):
        for error in (subprocess.TimeoutExpired('worker', 8), subprocess.CalledProcessError(1, 'worker')):
            with patch.object(notify.subprocess, 'run', side_effect=error) as run:
                result = notify.post(self.settings, {'msg_type': 'text'})
                self.assertEqual(result['status'], 'unknown')
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs['timeout'], 8)
                self.assertNotIn(self.settings['webhook'], str(run.call_args.args))
                self.assertNotIn(self.settings['secret'], run.call_args.kwargs['input'])

    def test_directory_sync_failure_prevents_external_request(self):
        with patch.object(notify, 'sync_directory', side_effect=OSError('fixture')), patch.object(notify, 'post') as post:
            self.assertEqual(notify.deliver(self.event, root=self.root)['status'], 'local_error')
            post.assert_not_called()

    def test_real_stalled_delivery_worker_is_killed_at_total_deadline(self):
        worker = self.root / 'stalled_worker.py'
        pid_file = self.root / 'worker.pid'
        worker.write_text('import os,time\nfrom pathlib import Path\n'
                          + 'Path(' + repr(str(pid_file)) + ').write_text(str(os.getpid()))\n'
                          + 'time.sleep(60)\n')
        with patch.object(notify, '__file__', str(worker)):
            self.assertEqual(notify.post(self.settings, {})['status'], 'unknown')
        pid = int(pid_file.read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


class FlowNotificationTests(test_flow.FlowFixture):
    def cli(self, *arguments):
        with patch.object(sys, 'argv', ['flow.py', *arguments]), contextlib.redirect_stdout(io.StringIO()) as output:
            code = flow.main()
        return code, json.loads(output.getvalue())

    def test_task_complete_does_not_notify_parent_complete_does_after_save_outside_lock(self):
        self.approve_all()
        self.evidence()
        with patch.object(notify, 'deliver') as send:
            self.cli('complete', self.feature_arg, '--task', 'T001')
            send.assert_not_called()
        def sending(event, **kwargs):
            spec, _ = flow.document(self.f / 'spec.md')
            self.assertEqual(spec['development_status'], '已完成')
            with flow.locked():
                pass
            return {'status': 'unknown'}
        with patch.object(notify, 'deliver', side_effect=sending) as send:
            code, result = self.cli('complete', self.feature_arg, '--thread', 'fixture-thread')
            self.assertEqual(code, 0)
            self.assertEqual(result['status'], '已完成')
            self.assertEqual(result['notification']['status'], 'unknown')
            self.assertEqual(send.call_args.args[0]['thread'], 'fixture-thread')

    def test_parent_validation_failure_never_notifies(self):
        self.approve_all()
        with patch.object(notify, 'deliver') as send:
            with self.assertRaises(flow.FlowError):
                self.cli('complete', self.feature_arg)
            send.assert_not_called()

    def test_attention_works_before_plan_and_tasks(self):
        (self.f / 'plan.md').unlink()
        (self.f / 'tasks.md').unlink()
        code, result = self.cli('notify', self.feature_arg, '--key', 'review',
                                '--summary', '需评审', '--next', '确认范围', '--dry-run')
        self.assertEqual(code, 0)
        self.assertEqual(result['notification']['status'], 'preview')

    def test_completion_real_delivery_path_deduplicates_same_validated_code(self):
        self.approve_all()
        self.evidence()
        self.complete()
        notify.init_config(self.root)
        (self.root / notify.CONFIG).write_text(json.dumps({'webhook': 'https://open.feishu.cn/open-apis/bot/v2/hook/fixture'}))
        with patch.object(notify, 'post', return_value={'status': 'sent'}) as post:
            self.assertEqual(self.cli('complete', self.feature_arg)[1]['notification']['status'], 'sent')
            self.assertEqual(self.cli('complete', self.feature_arg)[1]['notification']['status'], 'suppressed')
            post.assert_called_once()


if __name__ == '__main__':
    unittest.main()
