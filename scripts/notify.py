#!/usr/bin/env python3
"""One-way Feishu delivery. Receipts describe delivery only, never business state."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import fcntl
import hashlib
import hmac
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
TARGET = '飞书通知群'
CONFIG = '.specify/notifications.local.json'
RECEIPTS = '.specify/notifications'
TEMPLATE = {'webhook': '', 'secret': '', 'keyword': '', 'target': TARGET}


class NotificationError(Exception):
    pass


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def init_config(root=ROOT):
    path = root / CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return {'status': 'exists', 'config': str(path)}
    with os.fdopen(fd, 'w') as stream:
        json.dump(TEMPLATE, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'status': 'created', 'config': str(path)}


def load_config(root):
    path = root / CONFIG
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise NotificationError('私有配置须为普通文件且权限为 600')
    try:
        config = json.loads(path.read_text())
    except (ValueError, OSError):
        raise NotificationError('私有配置无法解析，请检查 JSON 格式') from None
    if not isinstance(config, dict) or any(not isinstance(config.get(k, ''), str) for k in TEMPLATE):
        raise NotificationError('webhook、secret、keyword 必须是字符串')
    if not config.get('webhook', '').strip():
        return None
    # No arbitrary URL, query string, redirect or environment proxy may receive credentials.
    import re
    if not re.fullmatch(r'https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9_-]+', config['webhook']):
        raise NotificationError('Webhook 必须是飞书自定义机器人 HTTPS 地址')
    return config


def event_id(event):
    identity = {k: event[k] for k in ('kind', 'source', 'key', 'version')}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def body(event, keyword=''):
    labels = {'attention': '需要你介入', 'completed': '需求开发完成', 'test': '通知接入测试'}
    text = f'【Codex｜{labels[event["kind"]]}】\n'
    text += f'事项：{event["title"]}\n{event["summary"]}\n'
    if event.get('next'):
        text += f'下一步：{event["next"]}\n'
    text += f'来源：{event["source"]}\n'
    if event.get('thread'):
        text += f'Codex 会话：{event["thread"]}\n'
    if keyword:
        text = keyword + '\n' + text
    if len(text.encode()) > 12000:
        raise NotificationError('通知过长，请改为简短摘要')
    return text


def payload(event, config):
    data = {'msg_type': 'text', 'content': {'text': body(event, config.get('keyword', ''))}}
    if config.get('secret'):
        timestamp = str(int(time.time()))
        key = (timestamp + '\n' + config['secret']).encode()
        data.update(timestamp=timestamp, sign=base64.b64encode(
            hmac.new(key, b'', hashlib.sha256).digest()).decode())
    return data


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_once(config, data):
    request = urllib.request.Request(config['webhook'], data=json.dumps(data).encode(),
                                     headers={'Content-Type': 'application/json'}, method='POST')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    # A single bounded attempt. Feishu webhook has no client idempotency token.
    try:
        with opener.open(request, timeout=5) as response:
            result = json.loads(response.read(65536))
    except urllib.error.HTTPError as exc:
        # Server errors may have happened after processing. Do not assume non-delivery.
        return {'status': 'rejected' if 300 <= exc.code < 500 else 'unknown', 'http_status': exc.code}
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError, http.client.HTTPException):
        return {'status': 'unknown', 'reason': '网络或响应异常，未自动重发'}
    if not isinstance(result, dict):
        return {'status': 'unknown', 'reason': '响应格式异常，未自动重发'}
    code = result.get('code', result.get('StatusCode'))
    if type(code) is not int:
        return {'status': 'unknown', 'reason': '缺少业务结果码，未自动重发'}
    return {'status': 'sent' if code == 0 else 'rejected', 'code': code}


def post(config, data):
    # Kill a bounded, short-lived worker even if DNS or a trickling response stalls.
    # Credentials travel through stdin, never argv or an error string.
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '_send'],
            input=json.dumps({'config': {'webhook': config['webhook']}, 'data': data}),
            capture_output=True, text=True, timeout=8, check=True)
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or value.get('status') not in ('sent', 'rejected', 'unknown'):
            raise ValueError('invalid worker result')
        return value
    except (subprocess.SubprocessError, OSError, ValueError):
        return {'status': 'unknown', 'reason': '投递超时或进程结果未知，未自动重发'}


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_receipt(path, value):
    fd, temp = tempfile.mkstemp(prefix='.receipt-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        sync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def deliver(event, *, root=ROOT, dry_run=False):
    """Never raise into business execution; never log URL, secret, response text or message body."""
    try:
        identifier = event_id(event)
        preview = body(event)
        if dry_run:
            return {'status': 'preview', 'target': TARGET, 'event_id': identifier, 'text': preview}
        config = load_config(root)
        if not config:
            return {'status': 'not_configured', 'target': TARGET}
        target = config.get('target') or TARGET
        data = payload(event, config)
        directory = root / RECEIPTS
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        sync_directory(directory.parent)
        path = directory / (identifier + '.json')
        with (directory / (identifier + '.lock')).open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return {'status': 'busy', 'reason': '同一事件正在投递；本次未重复发送'}
            if path.exists():
                previous = json.loads(path.read_text())
                return {'status': 'suppressed', 'previous_status': previous['status'], 'event_id': identifier}
            receipt = {'event_id': identifier, 'status': 'sending', 'created_at': stamp(),
                       **{k: event[k] for k in ('kind', 'source', 'key', 'version')}}
            # Durable intent before external effect. A crash here requires reconciliation, not replay.
            write_receipt(path, receipt)
            result = post(config, data)
            receipt.update(result, finished_at=stamp())
            write_receipt(path, receipt)
            return {**result, 'event_id': identifier, 'target': target}
    except Exception:
        return {'status': 'local_error', 'reason': '通知配置或本地回执异常，请核对；未自动重发'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['init', 'check', 'test'])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--thread', default='')
    args = parser.parse_args()
    if args.action == 'init':
        result = init_config()
    elif args.action == 'check':
        config = load_config(ROOT)
        result = {'configured': bool(config), 'target': (config or {}).get('target') or TARGET, 'config': str(ROOT / CONFIG),
                  'signed': bool(config and config.get('secret')), 'keyword': bool(config and config.get('keyword'))}
    else:
        result = deliver({'kind': 'test', 'source': 'notification-setup', 'key': uuid.uuid4().hex,
                          'version': '1', 'title': '单向通知接入验证',
                          'summary': '这是一条人工发起的接入测试，不代表任何业务需求完成。',
                          'next': '请核对群名、机器人与消息内容。', 'thread': args.thread}, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('status') in (None, 'created', 'exists', 'sent', 'preview') else 1


if __name__ == '__main__':
    try:
        if sys.argv[1:] == ['_send']:
            try:
                request = json.load(sys.stdin)
                outcome = post_once(request['config'], request['data'])
            except Exception:
                outcome = {'status': 'unknown', 'reason': '网络响应结果未知，未自动重发'}
            print(json.dumps(outcome, ensure_ascii=False))
            raise SystemExit(0)
        raise SystemExit(main())
    except (NotificationError, OSError, ValueError):
        print(json.dumps({'status': 'local_error', 'reason': '私有配置或本地文件异常，请核对配置格式与权限'}, ensure_ascii=False))
        raise SystemExit(1)
