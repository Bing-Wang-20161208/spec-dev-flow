#!/usr/bin/env python3
"""Allow only reviewed framework paths in this personal repository."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ALLOWED = {
    '.gitignore', '.python-version', 'AGENTS.md', 'README.md',
    'requirements.txt', 'requirements.lock', 'LICENSE', 'THIRD_PARTY_NOTICES.md',
    'docs/configuration.md', 'docs/operations.md', 'docs/publishing.md', 'docs/workflow.md',
    '.githooks/pre-commit', '.githooks/pre-push',
    'scripts/flow.py', 'scripts/notify.py', 'scripts/repo_guard.py',
    'tests/test_flow.py', 'tests/test_notify.py', 'tests/test_repo_guard.py',
    '.specify/.gitignore', '.specify/init-options.json', '.specify/integration.json',
    '.specify/integrations/codex.manifest.json', '.specify/integrations/speckit.manifest.json',
    '.specify/memory/.constitution-template.json', '.specify/memory/constitution.md',
    '.specify/notifications.example.json', '.specify/flow.example.yml', '.specify/publish.example.json',
    '.specify/workflows/speckit/workflow.yml',
}
ALLOWED.update('.agents/skills/speckit-' + name + '/SKILL.md' for name in (
    'analyze', 'checklist', 'clarify', 'constitution', 'converge',
    'implement', 'plan', 'specify', 'tasks'))
ALLOWED.update('.specify/templates/' + name + '-template.md' for name in (
    'checklist', 'constitution', 'plan', 'spec', 'tasks'))
ALLOWED.update('.specify/scripts/bash/' + name + '.sh' for name in (
    'check-prerequisites', 'common', 'create-new-feature', 'resolve-template',
    'setup-plan', 'setup-tasks'))


def git(*args):
    return subprocess.check_output(['git', *args], stderr=subprocess.PIPE, timeout=30)


def split_paths(raw):
    return [item.decode('utf-8') for item in raw.split(b'\0') if item]


def blocked(paths):
    return sorted(set(paths) - ALLOWED)


def private_terms():
    root = Path(git('rev-parse', '--show-toplevel').decode().strip())
    path = root / '.specify/publish.local.json'
    if not path.exists():
        return []
    config = json.loads(path.read_text())
    terms = config.get('blocked_terms')
    if not isinstance(terms, list) or any(not isinstance(term, str) or not term.strip() for term in terms):
        raise ValueError('blocked_terms must be nonempty strings')
    return [term.casefold() for term in terms]


def contains_private(data, terms):
    # Inspect common encodings too: a changed text encoding must not bypass local policy.
    texts = [data.decode(encoding, errors='ignore').casefold()
             for encoding in ('utf-8', 'utf-16-le', 'utf-16-be', 'gb18030')]
    return any(term in text for term in terms for text in texts)


def content_problems(paths, revision, terms):
    if not terms:
        return []
    return [path for path in paths if path in ALLOWED and
            (contains_private(path.encode(), terms) or contains_private(git('show', revision + ':' + path), terms))]


def check_index():
    paths = split_paths(git('ls-files', '-z'))
    return sorted(set(blocked(paths) + content_problems(paths, '', private_terms())))


def check_push(lines):
    problems = set()
    terms = private_terms()
    for line in lines:
        if not line.strip():
            continue
        local_ref, local_oid, remote_ref, remote_oid = line.split()
        if set(local_oid) == {'0'}:  # deleting a ref sends no commit content
            continue
        if contains_private((local_ref + '\n' + remote_ref).encode(), terms):
            problems.add('待推送的引用名称包含本机禁止词')
        tagged = local_oid
        visited = set()
        while git('cat-file', '-t', tagged).strip() == b'tag':
            if tagged in visited:
                raise ValueError('tag cycle')
            visited.add(tagged)
            tag = git('cat-file', 'tag', tagged)
            if contains_private(tag, terms):
                problems.add('标签说明包含本机禁止词')
            tagged = tag.splitlines()[0].split()[1].decode('ascii')
        tip = git('rev-parse', '--verify', local_oid + '^{commit}').decode().strip()
        problems.update(blocked(split_paths(git('ls-tree', '-rz', '--name-only', tip))))
        arguments = ['rev-list', tip]
        if set(remote_oid) != {'0'}:
            # Missing remote objects are conservative: inspect all non-baseline ancestors.
            present = subprocess.run(['git', 'cat-file', '-e', remote_oid + '^{commit}'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
            if present:
                arguments.append('^' + remote_oid)
        for commit in git(*arguments).decode().splitlines():
            # Every new commit is checked, including files added then removed before the tip.
            # Include merges against every parent; deletions are allowed for untracking local data.
            paths = git('diff-tree', '--root', '-m', '--no-commit-id', '--name-only',
                        '--diff-filter=ACMRT', '-r', '-z', commit)
            problems.update(blocked(split_paths(paths)))
            snapshot = split_paths(git('ls-tree', '-rz', '--name-only', commit))
            problems.update(content_problems(snapshot, commit, terms))
            if contains_private(git('cat-file', 'commit', commit), terms):
                problems.add('提交元数据包含本机禁止词：' + commit[:12])
    return sorted(problems)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--index', action='store_true')
    group.add_argument('--pre-push', action='store_true')
    parser.add_argument('remote', nargs='?')
    parser.add_argument('url', nargs='?')
    args = parser.parse_args()
    problems = check_index() if args.index else check_push(sys.stdin)
    if problems:
        print('拒绝提交/推送：当前框架仓库仅允许已审核的路径。以下内容应仅保留本地：', file=sys.stderr)
        for path in problems:
            print('  ' + path, file=sys.stderr)
        print('不要绕过检查；新增纯框架文件需先审核用途并更新白名单。', file=sys.stderr)
        return 1
    print('框架路径检查通过；仍需人工/AI 检查正文不含公司业务资料。')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        print('框架范围检查未能完成，已阻止操作；请先核对 Git 现场。', file=sys.stderr)
        raise SystemExit(1)
