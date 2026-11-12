#!/usr/bin/env python3
"""Local checks around native Spec Kit; no independent workflow engine."""
from __future__ import annotations
import argparse, contextlib, datetime as dt, fcntl, hashlib, json, os, re
import shlex, shutil, signal, subprocess, sys, tempfile, time, uuid
from pathlib import Path
import yaml
import notify

ROOT = Path(__file__).resolve().parents[1]
STATUSES = {'待开发', '开发中', '测试中', '已完成', '异常', '等待用户', '等待接口'}
FIELDS = ('仓库', '范围', '依赖', '类型', '独立检查', '验证命令', '状态', '下一步', '阻塞', '证据')
VOLATILE = {'状态', '下一步', '阻塞', '证据', '最近执行', '待确认标识'}
class FlowError(Exception): pass

def require(ok, message):
    if not ok: raise FlowError(message)
def now(): return dt.datetime.now(dt.timezone.utc).isoformat()
def digest(value): return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()
def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='.'+path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as f: f.write(content); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
def put(path, value): atomic(path, json.dumps(value, ensure_ascii=False, indent=2)+'\n')
def read(path): return json.loads(path.read_text())
def feature(value):
    p = (ROOT/value).resolve()
    require(p.is_relative_to(ROOT/'specs') and p != ROOT/'specs', '需求目录必须位于 specs 内')
    require((p/'spec.md').is_file(), '缺少 spec.md')
    return p

def document(path):
    s = path.read_text()
    match = re.match(r'\A---\n(.*?)\n---\n(.*)\Z', s, re.S)
    require(match is not None, str(path.name)+' 缺少 YAML 前置元数据')
    meta = yaml.safe_load(match[1]) or {}
    require(isinstance(meta, dict), '文档元数据必须是对象')
    return meta, match[2]
def write_document(path, meta, body):
    atomic(path, '---\n'+yaml.safe_dump(meta, allow_unicode=True, sort_keys=False)+'---\n'+body)
def tasks(f):
    text=(f/'tasks.md').read_text(); heads=list(re.finditer(r'^- \[([ xX])\] (T\d+) (.+)$', text, re.M))
    require(heads, 'tasks.md 必须有原生 - [ ] Txxx 任务')
    candidate_lines=re.findall(r'^- \[[^\n]*?\] T[^\n]*$',text,re.M)
    require(len(candidate_lines)==len(heads),'存在格式错误的任务 ID 或勾选，不能忽略')
    require(len({m[2] for m in heads})==len(heads), '任务 ID 重复')
    out={}
    for i,m in enumerate(heads):
        end=heads[i+1].start() if i+1<len(heads) else len(text)
        block=text[m.end():end]; fields={k:v for k,v in re.findall(r'^  - ([^：\n]+)：([^\n]*)$', block, re.M)}
        missing=set(FIELDS)-fields.keys();require(not missing, m[2]+' 缺字段 '+','.join(sorted(missing)))
        for key in ('范围','依赖','类型','验证命令','证据'):
            try: fields[key]=json.loads(fields[key])
            except ValueError: raise FlowError(m[2]+' '+key+' 必须是 JSON 数组')
            require(isinstance(fields[key],list) and all(isinstance(x,str) for x in fields[key]),m[2]+' '+key+' 必须是字符串数组')
        require(fields['状态'] in STATUSES, m[2]+' 状态无效')
        require(fields['独立检查'] in ('是','否'),m[2]+' 独立检查必须是 是/否')
        require(bool(fields['范围']),m[2]+' 需要明确相关文件/目录范围')
        fields.update(id=m[2],title=m[3],checked=m[1].lower()=='x',start=m.start(),end=end,block=block)
        require(fields['checked']==(fields['状态']=='已完成'),m[2]+' 勾选与状态冲突')
        out[m[2]]=fields
    for t in out.values(): require(all(x in out and x!=t['id'] for x in t['依赖']),t['id']+' 依赖不存在/依赖自己')
    def visit(n, trail):
        require(n not in trail,'任务依赖存在循环')
        for d in out[n]['依赖']:visit(d,trail|{n})
    for n in out:visit(n,set())
    return out

def update_task(f, task_id, **changes):
    all_tasks=tasks(f);t=all_tasks[task_id];text=(f/'tasks.md').read_text();block=text[t['start']:t['end']]
    for key,value in changes.items():
        require(key in VOLATILE,'不能通过进度更新改任务定义')
        rendered=json.dumps(value,ensure_ascii=False) if isinstance(value,list) else str(value).replace('\n',' ')
        pattern=r'(^  - '+re.escape(key)+'：)[^\n]*$'
        if re.search(pattern,block,re.M):block=re.sub(pattern,lambda m:m[1]+rendered,block,flags=re.M)
        else:block=block.rstrip()+'\n  - '+key+'：'+rendered+'\n\n'
    if '状态' in changes:
        block=re.sub(r'^- \[[ xX]\]', '- [x]' if changes['状态']=='已完成' else '- [ ]',block,count=1)
        if changes['状态'] != '等待用户':
            block = re.sub(r'^  - 待确认标识：[^\n]*\n', '', block, flags=re.M)
    atomic(f/'tasks.md',text[:t['start']]+block+text[t['end']:])

def spec_version(f):
    spec,body=document(f/'spec.md')
    sm={k:v for k,v in spec.items() if k not in ('review_approval','pending_decisions','release','development_status','final_verification')}
    return digest(json.dumps(sm,ensure_ascii=False,sort_keys=True)+body)

def plan_version(f, sv):
    plan, body = document(f/'plan.md')
    pm = {k: v for k, v in plan.items() if k not in ('plan_approval', 'pending_decisions')}
    # Availability is user-provided operational data; business/interface changes remain in the plan body.
    if isinstance(pm.get('api'),dict):pm['api']={k:v for k,v in pm['api'].items() if k not in ('real_ready','base_url','readiness_source')}
    definitions = tasks(f) if (f/'tasks.md').is_file() else {}
    defs = [{k: v for k, v in t.items() if k not in VOLATILE|{'checked','start','end','block'}}
            for t in definitions.values()]
    return digest(sv+json.dumps(pm,ensure_ascii=False,sort_keys=True)+body+json.dumps(defs,ensure_ascii=False,sort_keys=True))

def versions(f):
    sv = spec_version(f)
    return sv, plan_version(f, sv)

def configured_repos():
    d=yaml.safe_load((ROOT/'.specify/flow.local.yml').read_text())
    require(isinstance(d,dict) and isinstance(d.get('repositories'),dict),'缺少仓库配置')
    return {k:Path(v).resolve() for k,v in d['repositories'].items()}
def validate(f, execution=False):
    spec,_=document(f/'spec.md');plan,_=document(f/'plan.md');ts=tasks(f);repos=configured_repos()
    require(isinstance(spec.get('sources'),list) and spec.get('sources') and all(isinstance(x,str) and x.strip() for x in spec['sources']),'至少需要当前需求文档来源')
    require(isinstance(spec.get('repositories'),list) and spec.get('repositories') and all(x in repos for x in spec['repositories']),'需求仓库缺失或不在团队清单')
    ui=spec.get('ui',{}); require(ui.get('confirmed') is True and isinstance(ui.get('needed'),bool),'评审前需确认是否补 UI')
    if ui['needed']:require(all(ui.get(k) for k in ('file','page','node')),'UI 需要 MasterGo file/page/node 定位信息')
    api=plan.get('api',{});require(api.get('confirmed') is True and isinstance(api.get('needed'),bool),'实施前需确认 mock/真实接口情况')
    if api['needed']:
        require(isinstance(api.get('mock'),bool) and isinstance(api.get('real_ready'),bool),'缺少 mock/真实接口可用情况')
        require(api.get('openapi'),'缺少仓库 OpenAPI 来源')
        require(not api['real_ready'] or bool(api.get('base_url')),'真实接口可用但缺少接口地址')
        kinds={k for t in ts.values() for k in t['类型']}
        require('联调' in kinds,'涉及接口的需求需要明确联调任务')
        if api['mock']:require('mock清理' in kinds,'mock 需求需要临时 mock 清理任务')
    for t in ts.values():
        require(t['仓库'] in spec['repositories'],t['id']+' 仓库不在本需求范围')
        require(repos[t['仓库']].is_dir(),'目标仓库不存在')
        require(t['验证命令'],t['id']+' 缺少实际验证命令')
        fingerprint(repos[t['仓库']],t['范围'])
    sv,pv=versions(f)
    if execution:
        require(spec.get('review_approval',{}).get('version')==sv,'评审未确认或内容已变化')
        require(plan.get('plan_approval',{}).get('version')==pv,'执行计划未确认或范围已变化')
    return spec,plan,ts,repos,sv,pv

def file_hash(p):
    h=hashlib.sha256()
    with p.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def fingerprint(repo, scopes):
    entries={};ignored={'.git','node_modules','__pycache__','.venv','.next','dist'}
    def walk(p,ancestors):
        resolved=p.resolve();require(resolved.is_relative_to(repo),'范围内软链接越出仓库')
        if p.is_dir():
            require(resolved not in ancestors,'范围内存在目录循环链接')
            for child in sorted(p.iterdir()):
                if child.name not in ignored:yield from walk(child,ancestors|{resolved})
        else:yield p
    for scope in scopes:
        p=repo/scope;require(p.resolve().is_relative_to(repo),'代码范围越出仓库')
        for x in walk(p,set()):
            if x.is_file():entries[str(x.relative_to(repo))]=file_hash(x)
            elif not x.exists():entries[str(x.relative_to(repo))]='missing'
    return digest(json.dumps(entries,sort_keys=True))

def feature_context(f):
    repos=configured_repos();sv,pv=versions(f)
    return digest(sv+pv+json.dumps({t['id']:fingerprint(repos[t['仓库']],t['范围']) for t in tasks(f).values()},sort_keys=True))

def git_snapshot(repo):
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,timeout=15)
    root=git('rev-parse','--show-toplevel')
    require(root.returncode==0 and Path(root.stdout.decode().strip()).resolve()==repo,'代码目录必须是独立 Git 仓库')
    head=git('rev-parse','HEAD');status=git('status','--porcelain=v1','-z','--untracked-files=all')
    require(status.returncode==0,'无法核对 git status')
    chunks=status.stdout.decode().split('\0');changes={};i=0
    while i<len(chunks):
        item=chunks[i];i+=1
        if not item:continue
        name=item[3:];path=repo/name
        if any(part in ('.git','node_modules','__pycache__','.venv','.next','dist') for part in Path(name).parts):continue
        require(path.resolve().is_relative_to(repo),'Git 变更路径越出仓库')
        changes[name]={'status':item[:2],'hash':file_hash(path) if path.is_file() else 'missing'}
        if item[0] in 'RC' or item[1] in 'RC':
            if i<len(chunks):changes[chunks[i]]={'status':'rename-source','hash':'missing'};i+=1
    return {'head':head.stdout.decode().strip() if head.returncode==0 else 'unborn','changes':changes}

def changed_outside(before,after,scopes):
    scopes=[os.path.normpath(x) for x in scopes]
    changed=[n for n in before['changes'].keys()|after['changes'].keys() if before['changes'].get(n)!=after['changes'].get(n)]
    return [n for n in changed if not any(scope=='.' or n==scope.rstrip('/') or n.startswith(scope.rstrip('/')+'/') for scope in scopes)]

def context_version(f,repo,t):
    sv,pv=versions(f);plan,_=document(f/'plan.md')
    return digest(sv+pv+fingerprint(repo,t['范围'])+json.dumps(plan.get('api',{}),sort_keys=True))

def records(f):
    result=[]
    for p in sorted((f/'evidence').glob('*/result.json')):
        try:d=read(p)
        except (ValueError,OSError) as e:raise FlowError('证据损坏，先核对：'+str(p)) from e
        d['_path']=p;result.append(d)
    return sorted(result,key=lambda x:x['started_at'])

def alive_group(pgid):
    if not pgid:return False
    proc=subprocess.run(['ps','-axo','pgid=,stat='],capture_output=True,text=True,timeout=5)
    require(proc.returncode==0,'当前执行环境不允许读取进程信息；必须先取得该能力再启动或接手任务')
    return any(parts[0]==str(pgid) and not parts[1].startswith('Z') for line in proc.stdout.splitlines() if len(parts:=line.split())>=2)
def stop_group(proc):
    if alive_group(proc.pid):os.killpg(proc.pid,signal.SIGTERM)
    deadline=time.monotonic()+2
    while alive_group(proc.pid) and time.monotonic()<deadline:time.sleep(.05)
    if alive_group(proc.pid):os.killpg(proc.pid,signal.SIGKILL)
    try:proc.wait(timeout=3)
    except subprocess.TimeoutExpired:pass
    require(not alive_group(proc.pid),'本次执行进程组未完全停止，必须人工核对')

@contextlib.contextmanager
def locked():
    p=ROOT/'.specify/flow.lock';p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('a+') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise FlowError('已有中心执行者；同一时间只接受一个写入执行')
        yield f


def codex():
    found=shutil.which('codex')
    if not found:
        p=Path('/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex')
        if p.is_file():found=str(p)
    require(found,'未找到当前 Codex CLI');return found

def discover():
    env=os.environ.copy();cfg=yaml.safe_load((ROOT/'.specify/flow.local.yml').read_text());provider=cfg.get('provider')
    args=[codex(),'debug','models']
    if provider:args+=['-c','model_provider='+json.dumps(provider)]
    last=None
    for _ in range(2):
        try:
            p=subprocess.run(args,capture_output=True,text=True,timeout=25,env=env,check=True)
            d=json.loads(p.stdout);items=d['models'];require(isinstance(items,list),'模型目录结构无效')
            return items,{'captured_at':now(),'catalog_sha256':digest(p.stdout),'provider':provider,'source':'codex debug models with active configuration'}
        except (subprocess.SubprocessError,ValueError,KeyError,FlowError) as e:last=e
    raise FlowError('当前模型目录读取失败，停止派发；'+type(last).__name__)

def history_check(f,t,problem,phase,repair,upgrade,replan):
    history=records(f)
    for other in (ROOT/'specs').iterdir():
        if other.is_dir():
            for active in records(other):
                require(not (active.get('outcome')=='pending' and alive_group(active.get('pgid'))),'旧执行进程仍活跃，禁止并发接手')
    for d in history:
        if d.get('outcome')=='pending':
            require(not alive_group(d.get('pgid')),'旧执行者仍可能写入，不能接手')
            raise FlowError('存在未正常结束的批次 '+d['attempt_id']+'；先执行 settle 核对，再接续')
    same=[d for d in history if d['problem_id']==problem]
    task_history=[d for d in history if d['task_id']==t['id']]
    require(sum(bool(d.get('upgrade')) for d in task_history)+(1 if upgrade else 0)<=1,'该任务自动模型升级已达到一次')
    require(sum(bool(d.get('replan')) for d in task_history)+(1 if replan else 0)<=1,'该任务执行中重规划已达到一次')
    require(sum(bool(d.get('repair')) for d in same)+(1 if repair else 0)<=3,'同一问题已达到三轮修复上限')
    # No progress is a semantic assessment by the parent; unknown does not silently count as progress.
    tail=[]
    for d in reversed(same):
        if d.get('progress') is True:break
        if d['outcome'] in ('failed','unknown') or (d['phase']=='implement' and d.get('progress') is not True):tail.append(d)
    require(len(tail)<2,'连续两轮没有已记录的有效进展；停止自动执行并报告用户')
    if phase=='implement' and any(d['outcome']=='failed' for d in same):require(repair,'已有同问题失败；实现必须声明为修复，不能重置计数')
    for d in history:
        require(not (d.get('outcome')=='unknown' and not d.get('settled_at')),'存在未知结果，先 settle 核对后接续')
    return history

def parse_review(value):
    text=value.strip();fenced=re.fullmatch(r'```(?:json)?\s*(.*?)\s*```',text,re.S)
    return json.loads(fenced[1] if fenced else text)

def review_ok(verdict):
    if not isinstance(verdict,dict) or verdict.get('approved') is not True:return False
    if not isinstance(verdict.get('summary'),str) or not verdict['summary'].strip():return False
    findings=verdict.get('findings')
    if not isinstance(findings,list):return False
    for finding in findings:
        if not isinstance(finding,dict):return False
        severity=finding.get('severity')
        if not isinstance(severity,str) or severity.lower() not in {'critical','high','medium','low','info','p0','p1','p2','p3'}:return False
        if 'blocking' in finding and not isinstance(finding['blocking'],bool):return False
        if finding.get('blocking') is True or severity.lower() in {'critical','high','p0','p1'}:return False
    return True

def phase_prompt(f,t,phase,problem):
    skill='speckit-implement' if phase=='implement' else 'speckit-analyze'
    if phase=='review':
        return f'''独立代码检查，只读代码。读取 {f}/spec.md、plan.md、tasks.md 和本目录 AGENTS.md。只检查 {t['id']}：{t['title']}，相关范围 {json.dumps(t['范围'],ensure_ascii=False)}。检查需求符合性、缺陷、回归及测试覆盖。不得修改任何文件。最后只输出 JSON 对象，字段 approved(boolean)、findings(array)、summary(string)。findings 每项必须是对象，含 severity(critical/high/medium/low/info)、blocking(boolean)、message(string)，无问题则为空数组。阻塞问题存在时 approved=false。'''
    return f'''按原生技能执行当前明确限定的子任务。完整读取 {ROOT}/.agents/skills/{skill}/SKILL.md，读取当前代码目录的 AGENTS.md、命中的仓库规则，以及 {f}/spec.md、plan.md、tasks.md。只实施 {t['id']}：{t['title']}；相关范围 {json.dumps(t['范围'],ensure_ascii=False)}，问题 {problem}。其它任务只是依赖上下文，不得提前实施或勾选。不得修改需求中心文件；任务状态由主流程写回。需要原生路径检查时从 {ROOT} 执行 .specify/scripts/bash/check-prerequisites.sh --paths-only --json，避免写当前功能指针。不得以模板为由新增无关配置，不提交或推送。业务或验收含糊时停止并报告，工具同错首次后最多额外两次。实际验证命令：{json.dumps(t['验证命令'],ensure_ascii=False)}。本次 CLI 成功不自动代表任务完成。最后只报告变更、实际验证、未解决问题及下一步，不写模型中间推理。'''

def execute(args):
    f=feature(args.feature)
    with locked() as lease:
        alive_group(os.getpid())  # Verify process inspection before dispatch, never after spawning.
        spec,plan,ts,repos,sv,pv=validate(f,True);require(args.task in ts,'任务不存在');t=ts[args.task];repo=repos[t['仓库']]
        require(not t['checked'],'任务已完成；如需变更先更新范围并重新确认')
        require(all(ts[x]['checked'] for x in t['依赖']),'前置任务未完成')
        require(t['状态'] != '等待用户', '任务等待用户决定；记录回复并更新状态后再执行')
        problem=args.problem or t['id'];history=history_check(f,t,problem,args.phase,args.repair,args.upgrade,args.replan)
        if '联调' in t['类型'] and plan['api'].get('real_ready') is not True:
            update_task(f, t['id'], 状态='等待接口', 下一步='用户提供真实接口就绪信息和地址后接续联调',
                        阻塞='真实接口尚不可用')
            raise FlowError('真实接口尚不可用，已记录等待接口')
        prev=next((d for d in reversed(history) if d['task_id']==t['id'] and d['problem_id']==problem),None)
        if args.previous:
            prev=next((x for x in history if x['attempt_id']==args.previous),None)
            require(prev is not None and prev['task_id']==t['id'] and prev['problem_id']==problem,'接续必须保持原任务与问题身份')
            require(not alive_group(prev.get('pgid')),'旧执行进程仍活跃')
        if args.resume:
            inputs_file=ROOT/'.specify/workflows/runs'/args.resume/'inputs.json'
            require(inputs_file.is_file(),'原生 run 不存在')
            old=read(inputs_file)['inputs'];require(old['feature']==str(f.relative_to(ROOT)) and old['task_id']==t['id'] and old['problem_id']==problem and old['phase']==args.phase,'resume 不允许换需求、任务、问题或阶段')
            require(old.get('plan_version')==pv,'原生输入计划已变化，应核对后新建接续 run')
            require(old.get('code_dir')==str(repo),'原生 run 绑定了其他代码目录，不能挪用测试证据')
            state=read(inputs_file.parent/'state.json');require(state['status'] in ('paused','failed'),'仅 paused/failed 可原生 resume；running 使用 settle 后新建接续')
        model=None;effort=None;catalog=None
        if args.phase!='test':
            require(args.model and args.why,'模型执行需要当前候选及选择依据')
            items,catalog=discover();chosen=next((x for x in items if x.get('slug')==args.model and x.get('visibility')=='list'),None)
            require(chosen is not None,'所选模型不在本次当前可选目录')
            supported=[x['effort'] for x in chosen.get('supported_reasoning_levels',[])]
            if args.effort:require(args.effort in supported,'所选模型不支持该推理档位')
            model=args.model;effort=args.effort
        else:require(args.command in t['验证命令'],'测试命令必须在已确认任务中')
        require(5<=args.timeout<=3600,'单次超时范围 5–3600 秒')
        attempt=uuid.uuid4().hex[:12];e=f/'evidence'/attempt;e.mkdir(parents=True)
        before=context_version(f,repo,t);git_before=git_snapshot(repo)
        record={'attempt_id':attempt,'task_id':t['id'],'problem_id':problem,'phase':args.phase,'started_at':now(),'outcome':'pending','progress':None,'repair':args.repair,'upgrade':args.upgrade,'replan':args.replan,'previous_attempt':(prev or {}).get('attempt_id'),'resume_run':args.resume,'repo':str(repo),'scope':t['范围'],'context_version':before,'feature_context':feature_context(f),'spec_version':sv,'plan_version':pv,'execution_channel':'codex-cli','actual_model':'unknown','model':model,'reasoning_effort':effort,'model_reason':args.why,'catalog':catalog,'command':args.command if args.phase=='test' else None,'git_before':git_before,'pgid':None}
        put(e/'result.json',record)
        update_task(f,t['id'],状态='测试中' if args.phase in ('test','review') else '开发中',下一步=args.phase+' 执行中',阻塞='无',最近执行=attempt)
        native_inputs={'feature':str(f.relative_to(ROOT)),'task_id':t['id'],'problem_id':problem,'phase':args.phase,'attempt_id':attempt,'plan_version':pv,'spec_version':sv,'code_dir':str(repo),'selected_model':model or '', 'model_reason':args.why or '', 'test_command':args.command or '', 'prompt':phase_prompt(f,t,args.phase,problem), 'previous_run_id':(prev or {}).get('run_id','') or ''}
        step={'id':'work','timeout':args.timeout+30}
        if args.phase=='test':step.update(type='shell',run='{{ inputs.test_command }}')
        else:step.update(type='prompt',integration='codex',model='{{ inputs.selected_model }}',prompt='{{ inputs.prompt }}',capture=True,capture_max_bytes=120000)
        # Native shell runs at the center; use an explicit cwd with shell-quoted fixed path.
        if args.phase=='test':step['run']='cd '+shlex.quote(str(repo))+' && {{ inputs.test_command }}'
        definition={'schema_version':'1.0','workflow':{'id':'scoped-task','name':'Scoped task'},'inputs':{k:{'type':'string','default':v} for k,v in native_inputs.items()},'steps':[step]}
        flow=e/'workflow.yml';atomic(flow,yaml.safe_dump(definition,allow_unicode=True,sort_keys=False))
        specify=Path(sys.executable).with_name('specify');require(specify.is_file(),'请使用项目 .venv/bin/python 运行')
        command=[str(specify),'workflow','resume',args.resume,'--json'] if args.resume else [str(specify),'workflow','run',str(flow),'--json']
        for k,v in native_inputs.items():command+=['-i',k+'='+v]
        env=os.environ.copy();env['SPECIFY_FEATURE_DIRECTORY']=str(f.relative_to(ROOT));env['PATH']=str(Path(codex()).parent)+os.pathsep+env.get('PATH','')
        extra=['--ephemeral','--skip-git-repo-check','--sandbox','read-only' if args.phase=='review' else 'workspace-write','-C',str(repo)]
        if catalog and catalog.get('provider'):extra+=['-c','model_provider='+json.dumps(catalog['provider'])]
        if effort:extra+=['-c','model_reasoning_effort='+json.dumps(effort)]
        env['SPECKIT_INTEGRATION_CODEX_EXTRA_ARGS']=shlex.join(extra)
        protected={name:digest((f/name).read_bytes()) for name in ('spec.md','plan.md','tasks.md')}
        try:
            # The child cannot dispatch before its process identity is durable.
            ready=e/'launch-ready'
            launcher='import os,sys,time; from pathlib import Path; p=Path(sys.argv[1]); deadline=time.monotonic()+5\nwhile not p.exists() and time.monotonic()<deadline: time.sleep(.02)\nif not p.exists(): sys.exit(75)\nos.execv(sys.argv[2],sys.argv[2:])'
            proc=subprocess.Popen([sys.executable,'-c',launcher,str(ready),*command],cwd=ROOT,env=env,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,pass_fds=(lease.fileno(),))
            record['pgid']=proc.pid;put(e/'result.json',record);atomic(ready,'ready\n')
            try:stdout,stderr=proc.communicate(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                stop_group(proc);stdout,stderr=proc.communicate();raise FlowError('本次执行超时，进程组已停止；结果未知，需核对接续')
            try:native=json.loads(stdout)
            except ValueError:raise FlowError('原生执行没有返回完整结果，需核对')
            record['run_id']=native.get('run_id');require(record['run_id'],'缺少原生 run_id')
            state=read(ROOT/'.specify/workflows/runs'/record['run_id']/'state.json')
            output=state.get('step_results',{}).get('work',{}).get('output',{})
            require(not alive_group(proc.pid),'原生进程已返回但子进程仍活跃；不能判完成')
            record['git_after']=git_snapshot(repo)
            outside=changed_outside(git_before,record['git_after'],t['范围'])
            require(not outside,'执行改动越出已确认任务范围：'+','.join(outside))
            require(git_before['head']==record['git_after']['head'],'执行者改变了 Git HEAD，需核对')
            untouched=all(digest((f/name).read_bytes())==h for name,h in protected.items())
            require(untouched,'执行者修改了需求中心文档，必须核对，不能自动完成')
            passed=proc.returncode==0 and native.get('status')=='completed' and output.get('exit_code')==0
            record.update(outcome='passed' if passed else 'failed',native_status=native.get('status'),summary=str(output.get('stdout',''))[-24000:],error=None if passed else native.get('error','原生步骤未成功'))
            if args.phase=='test':record['test_output']=str(output.get('stdout',''))[-24000:]+'\n'+str(output.get('stderr',''))[-24000:]
            if args.phase=='review' and passed:
                try:verdict=parse_review(output.get('stdout',''))
                except ValueError:verdict={}
                record['review_approved']=review_ok(verdict)
                if not record['review_approved']:record.update(outcome='failed',error='独立检查未批准或未返回有效结论')
        except (Exception,KeyboardInterrupt) as exc:
            if 'proc' in locals():stop_group(proc)
            record.update(outcome='unknown',error=str(exc))
        if not record.get('run_id'):
            candidates=[]
            for ip in (ROOT/'.specify/workflows/runs').glob('*/inputs.json'):
                try:
                    if read(ip).get('inputs',{}).get('attempt_id')==attempt:candidates.append(ip.parent.name)
                except (OSError,ValueError):pass
            if len(candidates)==1:record['run_id']=candidates[0]
        record.update(finished_at=now(),after_context=context_version(f,repo,t),after_feature_context=feature_context(f))
        record['progress']=record['outcome']=='passed' and args.phase in ('test','review')
        put(e/'result.json',record)
        if record['outcome']=='passed':update_task(f,t['id'],状态='测试中',下一步='核对结果并完成任务，或继续必要验证',阻塞='无',证据=t['证据']+[str((e/'result.json').relative_to(f))])
        else:update_task(f,t['id'],状态='异常',下一步='核对记录后修复/接续',阻塞=record.get('error') or '执行失败',证据=t['证据']+[str((e/'result.json').relative_to(f))])
        return {k:v for k,v in record.items() if k not in ('test_output','summary')}

def approval(args):
    f=feature(args.feature)
    with locked():
        if args.node=='review':
            meta,_=document(f/'spec.md');repos=configured_repos()
            require(isinstance(meta.get('sources'),list) and meta['sources'],'缺少当前需求文档来源')
            require(isinstance(meta.get('repositories'),list) and meta['repositories'] and all(x in repos for x in meta['repositories']),'缺少目标仓库')
            ui=meta.get('ui',{});require(ui.get('confirmed') is True and isinstance(ui.get('needed'),bool),'需先确认是否补 UI')
            if ui['needed']:require(all(ui.get(k) for k in ('file','page','node')),'缺少 UI 容器定位')
            sv=spec_version(f);pv=None
        else:
            validate(f,False);sv,pv=versions(f)
            sm,_=document(f/'spec.md');require(sm.get('review_approval',{}).get('version')==sv,'先确认需求/UI评审')
        p=f/('spec.md' if args.node=='review' else 'plan.md');meta,body=document(p)
        require(args.quote.strip() and args.source.strip(),'确认必须有用户原话与来源')
        meta['review_approval' if args.node=='review' else 'plan_approval']={'version':sv if args.node=='review' else pv,'quote':args.quote,'source':args.source,'at':now()}
        meta.pop('pending_decisions', None)
        write_document(p,meta,body)
    return {'node':args.node,'version':sv if args.node=='review' else pv}

def completed(args):
    f=feature(args.feature)
    with locked():
        spec,plan,ts,repos,sv,pv=validate(f,True);history=records(f)
        require(not any(d['outcome']=='pending' for d in history),'有未核对执行，不能完成')
        if args.task:
            require(args.task in ts,'任务不存在');t=ts[args.task];repo=repos[t['仓库']]
            require(t['状态'] != '等待用户', '任务等待用户决定；记录回复并更新状态后再完成')
            require(all(ts[x]['checked'] for x in t['依赖']),'依赖尚未完成')
            current=context_version(f,repo,t)
            good=[d for d in history if d['task_id']==t['id'] and d['outcome']=='passed' and d['context_version']==current and d.get('after_context')==current]
            last_test=next((d for d in reversed(history) if d['task_id']==t['id'] and d['phase']=='test'),None)
            require(last_test and last_test in good,'没有对当前受测代码的真实测试通过结果')
            latest=next((d for d in reversed(history) if d['task_id']==t['id']),None)
            require(latest and latest['outcome']=='passed','最近执行仍有失败或未知结果')
            if t['独立检查']=='是':
                last_review=next((d for d in reversed(history) if d['task_id']==t['id'] and d['phase']=='review'),None)
                require(last_review and last_review in good and last_review.get('review_approved'),'缺少当前版本独立检查通过结论')
            update_task(f,t['id'],状态='已完成',下一步='无',阻塞='无')
            return {'task':t['id'],'status':'已完成'}
        require(all(t['checked'] for t in ts.values()),'仍有未完成任务')
        finals=[t for t in ts.values() if '总体验证' in t['类型']]
        require(finals,'至少一项任务需包含总体验证职责，可与小任务合并')
        for t in ts.values():
            current=context_version(f,repos[t['仓库']],t)
            require(any(d['task_id']==t['id'] and d['phase']=='test' and d['outcome']=='passed' and d.get('after_context')==current and d['context_version']==current for d in history),'已有测试证据因相关代码变化失效：'+t['id'])
        fc=feature_context(f)
        for t in finals:
            require(any(d['task_id']==t['id'] and d['phase']=='test' and d['outcome']=='passed' and d.get('feature_context')==fc and d.get('after_feature_context')==fc for d in history),'总体验证没有覆盖当前全部任务代码版本')
        spec['development_status']='已完成';spec['final_verification']={'at':now(),'tasks':list(ts),'plan_version':pv}
        _,body=document(f/'spec.md');write_document(f/'spec.md',spec,body)
        return {'feature':str(f.relative_to(ROOT)),'status':'已完成','release':spec.get('release','未上线'),
            '_notification_event':{'kind':'completed','source':str(f.relative_to(ROOT)),
                'key':'development-complete','version':digest(pv+fc),'title':f.name,
                'summary':'全部必需任务已完成，当前版本任务测试与总体验证通过。上线状态：'+str(spec.get('release','未上线')),
                'next':'在 Codex 查看实现摘要、测试证据及后续安排。'}}

def find_run(attempt):
    candidates=[]
    for ip in (ROOT/'.specify/workflows/runs').glob('*/inputs.json'):
        try:
            if read(ip).get('inputs',{}).get('attempt_id')==attempt:candidates.append(ip.parent.name)
        except (OSError,ValueError):pass
    require(len(candidates)<=1,'同一执行对应多个原生run，必须人工核对')
    return candidates[0] if candidates else None

def settle(args):
    f=feature(args.feature)
    with locked():
        p=f/'evidence'/args.attempt/'result.json';require(p.is_file(),'执行证据不存在');d=read(p)
        require(d['outcome'] in ('pending','unknown'),'仅核对未正常结束的批次')
        require(not alive_group(d.get('pgid')),'旧进程组仍活跃，禁止接手')
        require(args.evidence and args.reason,'接续需要现场核对依据与下一步')
        proof=(f/args.evidence).resolve();require(proof.is_relative_to(f) and proof.is_file(),'现场核对依据必须是本需求文件')
        if not d.get('run_id'):d['run_id']=find_run(d['attempt_id'])
        d.update(outcome='unknown',settled_at=now(),recovery_evidence=str(proof.relative_to(f)),recovery_reason=args.reason)
        put(p,d);update_task(f,d['task_id'],状态='异常',下一步=args.reason,阻塞='上次执行未正常结束，结果未知')
        return {'settled':args.attempt,'next':'使用 --previous 保持任务/问题身份，新建剩余阶段运行'}

def assess(args):
    f=feature(args.feature)
    with locked():
        p=f/'evidence'/args.attempt/'result.json';require(p.is_file(),'执行证据不存在');d=read(p)
        require(d['outcome']!='pending','先核对执行是否结束')
        proof=(f/args.evidence).resolve();require(proof.is_relative_to(f) and proof.is_file(),'需本需求内可读证据')
        require(args.reason.strip(),'需说明证据带来的实质进展')
        d.update(progress=True,progress_reason=args.reason,progress_evidence=str(proof.relative_to(f)))
        put(p,d);return {'assessed':args.attempt,'progress':True}

def attention_event(f, args, version, key):
    return {'kind': 'attention', 'source': str(f.relative_to(ROOT)),
            'key': key, 'version': version, 'title': f.name,
            'summary': args.summary.strip(), 'next': args.next.strip(), 'thread': args.thread}

def request_decision(args):
    f = feature(args.feature)
    key = args.key.strip()
    summary, next_step = args.summary.strip(), args.next.strip()
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', key), '需要稳定的问题标识（字母、数字、点、下划线或连字符）')
    require(summary and next_step, '需要问题摘要和用户要处理的事项')
    target = args.target
    with locked():
        if target in ('review', 'plan'):
            path = f/('spec.md' if target == 'review' else 'plan.md')
            version = spec_version(f)
            if target == 'plan':
                version = plan_version(f, version)
            meta, body = document(path)
            approval = meta.get('review_approval' if target == 'review' else 'plan_approval', {})
            require(approval.get('version') != version, '当前版本已经确认；先把需要重新确认的实质变更写入文档')
            pending = meta.setdefault('pending_decisions', {})
            entry = {'version': version, 'summary': summary, 'next': next_step}
            if pending.get(key) != entry:
                pending[key] = entry
                write_document(path, meta, body)
        else:
            require(re.fullmatch(r'T\d+', target), '确认位置必须是 review、plan 或任务 ID')
            require(len(summary.splitlines()) == len(next_step.splitlines()) == 1, '任务的摘要和下一步必须各为一行')
            task = tasks(f).get(target)
            require(task and not task['checked'], '任务不存在或已完成')
            version = versions(f)[1]
            if (task['状态'], task.get('待确认标识'), task['阻塞'], task['下一步']) != ('等待用户', key, summary, next_step):
                update_task(f, target, 状态='等待用户', 待确认标识=key, 阻塞=summary, 下一步=next_step)
            key = f'{target}:{key}'
        event = attention_event(f, args, version, key)
    notification = notify.deliver(event, root=ROOT)
    return {'target': target, 'status': '等待用户', 'version': version, 'notification': notification}

def notify_attention(args):
    f=feature(args.feature)
    require(args.key.strip() and args.summary.strip() and args.next.strip(), '通知需要稳定问题标识、摘要与下一步')
    # Review can happen before plan/tasks exist. Version the available authoritative material.
    with locked():
        version=versions(f)[1] if (f/'plan.md').is_file() and (f/'tasks.md').is_file() else spec_version(f)
        event = attention_event(f, args, version, args.key.strip())
    return notify.deliver(event,root=ROOT,dry_run=args.dry_run)

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    v=sub.add_parser('check');v.add_argument('feature');v.add_argument('--execution',action='store_true')
    sub.add_parser('models')
    a=sub.add_parser('approve');a.add_argument('feature');a.add_argument('node',choices=['review','plan']);a.add_argument('--quote',required=True);a.add_argument('--source',required=True)
    a=sub.add_parser('run');a.add_argument('feature');a.add_argument('task');a.add_argument('--phase',choices=['implement','test','review'],required=True);a.add_argument('--problem');a.add_argument('--model');a.add_argument('--effort');a.add_argument('--why');a.add_argument('--command');a.add_argument('--timeout',type=int,default=300);a.add_argument('--resume');a.add_argument('--previous');
    for name in ('repair','upgrade','replan'):a.add_argument('--'+name,action='store_true')
    a=sub.add_parser('complete');a.add_argument('feature');a.add_argument('--task');a.add_argument('--thread',default='')
    a=sub.add_parser('request-decision', help='写入待确认事项并发送飞书提醒')
    a.add_argument('feature');a.add_argument('target', metavar='review|plan|Txxx')
    a.add_argument('--key', required=True);a.add_argument('--summary', required=True)
    a.add_argument('--next', required=True);a.add_argument('--thread', default='')
    a=sub.add_parser('notify',help='保存原生待确认/停止记录后，显式通知用户介入')
    a.add_argument('feature');a.add_argument('--key',required=True);a.add_argument('--summary',required=True)
    a.add_argument('--next',required=True);a.add_argument('--thread',default='');a.add_argument('--dry-run',action='store_true')
    for name in ('settle','assess'):
        a=sub.add_parser(name);a.add_argument('feature');a.add_argument('attempt');a.add_argument('--evidence',required=True);a.add_argument('--reason',required=True)
    a=sub.add_parser('note');a.add_argument('feature');a.add_argument('task');a.add_argument('--status',choices=sorted(STATUSES-{'已完成', '等待用户'}),required=True,help='等待用户请用 request-decision');a.add_argument('--next',required=True);a.add_argument('--reason',default='无')
    args=p.parse_args()
    if args.action=='models':
        items,meta=discover();result={**meta,'models':[{k:m.get(k) for k in ('slug','display_name','visibility','supported_reasoning_levels','input_modalities')} for m in items if m.get('visibility')=='list']}
    elif args.action=='check':
        f=feature(args.feature);s,pl,ts,r,sv,pv=validate(f,args.execution);result={'feature':args.feature,'tasks':{k:{x:v[x] for x in ('title','状态','下一步','阻塞')} for k,v in ts.items()},'spec_version':sv,'plan_version':pv}
    elif args.action=='approve':result=approval(args)
    elif args.action=='run':result=execute(args)
    elif args.action=='complete':
        result=completed(args)
        if not args.task:
            event=result.pop('_notification_event');event['thread']=args.thread
            result['notification']=notify.deliver(event,root=ROOT)
    elif args.action=='request-decision':result=request_decision(args)
    elif args.action=='notify':result={'notification':notify_attention(args)}
    elif args.action=='settle':result=settle(args)
    elif args.action=='assess':result=assess(args)
    else:
        with locked():update_task(feature(args.feature),args.task,状态=args.status,下一步=args.next,阻塞=args.reason)
        result={'task':args.task,'status':args.status}
    print(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    return 1 if result.get('outcome') in ('failed','unknown') else 0
if __name__=='__main__':
    try:sys.exit(main())
    except (FlowError,OSError,ValueError,KeyError,yaml.YAMLError) as e:
        print(json.dumps({'blocked':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(2)
