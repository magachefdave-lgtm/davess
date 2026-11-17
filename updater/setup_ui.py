"""Local-only first setup and administrator publication forms (no web service)."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
import webbrowser


FORM_TIMEOUT = 15 * 60       # 설정/게시 창 전체 제한 시간(초). 넘으면 창 없이 종료한다.


def _message_box(title, text, ok_cancel=False):
    """Windows 메시지 상자(복사: Ctrl+C). 확인이면 True, 취소면 False. 다른 OS 에서는 화면에 출력."""
    if os.name == 'nt':
        import ctypes
        flags = (0x1 if ok_cancel else 0x0) | 0x40
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags) != 2
    print(f'{title}\n{text}', file=sys.stderr)
    return True


def _path_input(value):
    text = str(value or '').strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    return text


def open_folder(path):
    """Windows 탐색기로 폴더를 연다(이 PC 화면에서만). 다른 OS 나 실패 시 False."""
    if os.name != 'nt':
        return False
    try:
        os.startfile(str(path))  # noqa: S606 - 사용자가 고른 로컬 폴더
        return True
    except OSError:
        return False


_PICK_KINDS = ('folder', 'zip', 'save', 'json')


def choose_publisher_path(kind, current=''):
    """Use the Windows file/folder dialog without adding a GUI dependency.

    folder: 폴더 선택, zip: Update ZIP 열기, save: 키 백업 저장 위치, json: 키 백업 파일 열기.
    """
    if os.name != 'nt':
        raise ValueError('찾아보기는 Windows에서 사용할 수 있습니다. 경로를 직접 입력해 주세요.')
    import base64
    import subprocess
    if kind not in _PICK_KINDS:
        raise ValueError('지원하지 않는 선택 종류입니다.')
    script = r"""$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Windows.Forms
$kind = $env:ONAIR_PICK_KIND
if ($kind -eq 'folder') {
    $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
    $dialog.Description = '폴더를 선택하세요'
    $dialog.SelectedPath = $env:ONAIR_PICK_CURRENT
} elseif ($kind -eq 'save') {
    $dialog = New-Object System.Windows.Forms.SaveFileDialog
    $dialog.Title = '키 백업 파일을 저장할 위치 (USB 권장)'
    $dialog.Filter = '키 백업 (*.json)|*.json'
    $dialog.OverwritePrompt = $false
} else {
    $dialog = New-Object System.Windows.Forms.OpenFileDialog
    if ($kind -eq 'json') { $dialog.Title = '키 백업 파일 선택'; $dialog.Filter = '키 백업 (*.json)|*.json' }
    else { $dialog.Title = '새 Update ZIP 선택'; $dialog.Filter = 'Update ZIP (*.zip)|*.zip' }
    $dialog.CheckFileExists = $true
    $dialog.Multiselect = $false
}
if ($kind -ne 'folder' -and $env:ONAIR_PICK_CURRENT) {
    $dialog.InitialDirectory = [System.IO.Path]::GetDirectoryName($env:ONAIR_PICK_CURRENT)
    $dialog.FileName = [System.IO.Path]::GetFileName($env:ONAIR_PICK_CURRENT)
}
try {
    if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
        if ($kind -eq 'folder') { [Console]::Write($dialog.SelectedPath) }
        else { [Console]::Write($dialog.FileName) }
    }
} finally { $dialog.Dispose() }
"""
    powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = dict(os.environ, ONAIR_PICK_KIND=kind, ONAIR_PICK_CURRENT=_path_input(current))
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-STA', '-EncodedCommand',
                                 base64.b64encode(script.encode('utf-16le')).decode('ascii')],
                                env=env, capture_output=True, timeout=300, stdin=subprocess.DEVNULL,
                                creationflags=0x08000000)
        if result.returncode:
            raise ValueError('선택 창을 열지 못했습니다. 경로를 직접 입력해 주세요.')
        return result.stdout.decode('utf-8-sig').strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('선택 창을 열지 못했거나 시간이 초과되었습니다. 다시 선택하거나 경로를 직접 입력해 주세요.') from exc


def browse_result(name, kind, data):
    """찾아보기 공통 결과: 고른 값으로 칸을 채우거나, 취소면 그대로 둔다."""
    selected = choose_publisher_path(kind, data.get(name, ''))
    return {'values': {name: selected} if selected else {},
            'message': '선택했습니다.' if selected else '선택을 취소했습니다. 기존 경로를 유지합니다.'}


class _Server(ThreadingHTTPServer):
    # 게시 중에도 진행 상황·연결 확인 요청에 답하려면 요청을 동시에 처리해야 한다.
    daemon_threads = True


CLOSED_DEFAULT = '창이 닫혔습니다. 프로그램을 다시 실행하세요.'


class NeedsInput(ValueError):
    """입력이 더 필요하다는 오류. show 에 화면에서 보이게 할 칸({이름: True})을 담는다."""

    def __init__(self, message, show=None):
        super().__init__(message)
        self.show = dict(show or {})
KEEP_OPEN_EXTRA = 10 * 60      # 게시 직후 키 백업 화면은 최소 이만큼 더 열어 둔다(초)


def _field_html(field):
    kind = field.get('type')
    name = html.escape(field.get('name', ''), quote=True)
    label = html.escape(field.get('label', ''))
    hidden = ' hidden' if field.get('hidden') else ''
    help_text = (f'<span class="help">{html.escape(field["help"])}</span>' if field.get('help') else '')
    action_result = f'<div id="action-result-{name}" class="action-result" role="status" aria-live="polite" hidden></div>'
    if kind == 'end_details':
        return '</div></details>'
    if kind == 'end_group':
        return '</div>'
    if kind == 'group':
        when = html.escape('='.join(field['show_when']), quote=True)
        return f'<div class="group" data-show-when="{when}">'
    if kind == 'section':
        return f'<h2>{label}</h2>'
    if kind == 'note':
        return f'<p class="note">{label}</p>'
    if kind == 'details':
        return f'<details class="advanced"><summary>{label}</summary><div>'
    if kind == 'button':
        danger = ' danger' if field.get('danger') else ''
        return (f'<button type="button" class="act{danger}" data-action="{name}" '
                f'aria-describedby="action-result-{name}">{label}</button>{help_text}{action_result}')
    if kind == 'password':
        return f'<label>{label}<input type="password" name="{name}" autocomplete="off" spellcheck="false"></label>'
    if kind == 'checkbox':
        return (f'<label class="check" id="wrap-{name}"{hidden}><input type="checkbox" name="{name}">{label}'
                f'{help_text}</label>')
    if kind == 'select':
        chosen = field.get('value')
        options = ''.join(f'<option value="{html.escape(v, quote=True)}"{" selected" if v == chosen else ""}>'
                          f'{html.escape(t)}</option>' for v, t in field['options'])
        return f'<label>{label}<select name="{name}">{options}</select>{help_text}</label>'
    if kind == 'radio':
        chosen = field.get('value')
        items = []
        for option in field['options']:
            value, title = option[0], option[1]
            more = f'<span class="help">{html.escape(option[2])}</span>' if len(option) > 2 and option[2] else ''
            checked = ' checked' if value == chosen else ''
            items.append(f'<label class="radio"><input type="radio" name="{name}" value="{html.escape(value, quote=True)}"'
                         f'{checked}><span><b>{html.escape(title)}</b>{more}</span></label>')
        return f'<fieldset class="choice"><legend>{label}</legend>{"".join(items)}</fieldset>'
    if kind == 'copy':
        value = html.escape(str(field.get('value', '')), quote=True)
        return (f'<label>{label}<span class="path-row"><input class="mono" readonly value="{value}">'
                f'<button type="button" class="copy" data-copy="{value}">복사</button></span>{help_text}</label>')
    value = html.escape(str(field.get('value', '')), quote=True)
    placeholder = html.escape(str(field.get('placeholder', '')), quote=True)
    required = 'required' if field.get('required') else ''
    browse = field.get('browse')
    browse_name = html.escape(browse, quote=True) if browse else ''
    check = f' data-check="{html.escape(field["check"], quote=True)}"' if field.get('check') else ''
    button = (f'<button type="button" data-action="{browse_name}" aria-describedby="action-result-{browse_name}">찾아보기</button>' if browse else '')
    inline_result = f'<span id="action-result-{browse_name}" class="action-result" role="status" aria-live="polite" hidden></span>' if browse else ''
    return (f'<label>{label}<span class="path-row"><input name="{name}" value="{value}" placeholder="{placeholder}" '
            f'{required} autocomplete="off" spellcheck="false"{check}>{button}</span>{inline_result}{help_text}</label>')


_STYLE = '''
.action-result{display:block;white-space:pre-wrap;overflow-wrap:anywhere;margin-top:6px;color:#174c9d;font-size:14px;font-weight:400}.action-result[hidden]{display:none}.action-result.error{color:#b02236}.advanced{margin-top:22px;border:1px solid #dce5f5;border-radius:10px;padding:12px 16px}.advanced summary{font-weight:600;cursor:pointer}.advanced summary:focus-visible{outline:2px solid #5b8cef;outline-offset:4px}
[hidden]{display:none!important}.path-row{display:flex;gap:8px;align-items:center}.path-row input{min-width:0;flex:1}.path-row button{flex-shrink:0;margin-top:7px;padding:12px}*{box-sizing:border-box}body{margin:0;background:#f1f5fd;font:15px/1.65 "Segoe UI","Malgun Gothic",sans-serif;color:#23324c}main{max-width:700px;margin:50px auto;background:white;border:1px solid #dce5f5;border-radius:20px;padding:36px;box-shadow:0 14px 46px #19386b12}small{color:#2463ce;font-weight:700;letter-spacing:2px}h1{font-size:25px;margin:12px 0}h2{font-size:17px;margin:30px 0 0;padding-top:16px;border-top:1px solid #dce5f5}.act{margin-top:12px;display:block}.act.danger{background:#fdecee;color:#8f1d2c}p{color:#566982;white-space:pre-line}.note{margin:8px 0 0;font-size:14px}label{display:block;margin:20px 0 0;font-weight:600}.help{display:block;font-weight:400;font-size:13.5px;color:#566982;margin-top:4px}input,select{display:block;width:100%;padding:12px;margin:7px 0 0;border:1px solid #becde3;border-radius:8px;background:#fafcff;font:inherit}input.mono{font-family:Consolas,monospace;letter-spacing:1px}input:focus,select:focus{outline:2px solid #5b8cef}input[type=checkbox],input[type=radio]{display:inline;width:auto;margin:0 9px 0 0}.check{font-weight:400}fieldset.choice{border:1px solid #dce5f5;border-radius:10px;margin:20px 0 0;padding:6px 16px 14px}fieldset.choice legend{font-weight:600;padding:0 6px}label.radio{display:flex;align-items:flex-start;gap:4px;margin:12px 0 0;font-weight:400}label.radio input{margin-top:6px}.group{border-left:3px solid #dce5f5;padding-left:14px;margin-top:6px}footer{display:flex;gap:10px;margin-top:27px}button{font:inherit;border:0;padding:11px 22px;border-radius:9px;cursor:pointer;background:#e9eef8;color:#263d61}button[type=submit],button.primary{background:#235fd1;color:white}button:disabled{opacity:.6;cursor:wait}#result{white-space:pre-wrap;overflow-wrap:anywhere;margin-top:22px;color:#174c9d}#result.error{color:#b02236}.copyrow{display:flex;gap:8px;align-items:center;margin-top:10px;white-space:normal}.copyrow code{font:15px Consolas,monospace;background:#eef3fc;padding:6px 10px;border-radius:6px;letter-spacing:1px}.panel{margin-top:24px;border:2px solid #e2a400;background:#fffaf0;border-radius:14px;padding:20px 22px}.panel h2{border:0;margin:0;padding:0;color:#7a4b00}.closed{margin-top:22px;border:2px solid #b02236;background:#fdecee;color:#8f1d2c;border-radius:12px;padding:16px 18px;font-weight:700}#timer{margin-top:12px;color:#7a4b00;font-size:14px}@media(max-width:720px){main{margin:16px;padding:24px}}
'''

_SCRIPT = r'''
const token=TOKEN_VALUE, form=document.getElementById('form'), result=document.getElementById('result');
const CLOSED_TEXT=CLOSED_VALUE, HAS_PROGRESS=PROGRESS_VALUE;
let finished=false, lastData={};
function showClosed(){if(finished)return;finished=true;const c=document.getElementById('closed');if(c){c.hidden=false;c.textContent=CLOSED_TEXT;}form.hidden=true;const a=document.getElementById('after');if(a)a.hidden=true;}
async function send(path,body){let r;try{r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-OnAir-Token':token},body:JSON.stringify(body)});}catch(e){showClosed();throw Error(CLOSED_TEXT);}return r.json();}
async function sendConfirmed(path,data){let r=await send(path,data);while(r&&r.ok&&r.result&&r.result.confirm){if(!window.confirm(r.result.confirm.text))return {ok:true,result:{cancelled:true,message:'취소했습니다. 아무것도 바꾸지 않았습니다.'}};data=Object.assign({},data,{confirmed:r.result.confirm.token});r=await send(path,data);}return r;}
function collect(){const data=Object.fromEntries(new FormData(form));form.querySelectorAll('input[type=checkbox]').forEach(x=>data[x.name]=x.checked);return data;}
function busy(on){form.querySelectorAll('button').forEach(x=>x.disabled=on);}
function copyText(text,button){const done=()=>{if(button){const old=button.textContent;button.textContent='복사됨';setTimeout(()=>button.textContent=old,1500);}};
 if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(text).then(done,()=>{fallbackCopy(text);done();});}else{fallbackCopy(text);done();}}
function fallbackCopy(text){const t=document.createElement('textarea');t.value=text;document.body.appendChild(t);t.select();try{document.execCommand('copy');}catch(e){}t.remove();}
function wireCopy(root){root.querySelectorAll('[data-copy]').forEach(b=>{if(b.dataset&&b.dataset.copy!==undefined)b.onclick=()=>copyText(b.dataset.copy,b);});}
function toggleGroups(){form.querySelectorAll('[data-show-when]').forEach(g=>{if(!g.dataset||!g.dataset.showWhen)return;const [n,v]=g.dataset.showWhen.split('=');const chosen=form.querySelector('input[name="'+n+'"]:checked');g.hidden=!chosen||chosen.value!==v;});}
function applyShow(show){for(const [name,visible] of Object.entries(show||{})){const w=document.getElementById('wrap-'+name);if(w){w.hidden=!visible;if(!visible){const box=w.querySelector&&w.querySelector('input');if(box)box.checked=false;}}}}
function showResult(res,fallback){result.textContent=(res&&res.message)||fallback||'';(res&&res.copy||[]).forEach(row=>{const line=document.createElement('div');line.className='copyrow';const label=document.createElement('span');label.textContent=row.label;const code=document.createElement('code');code.textContent=row.value;const b=document.createElement('button');b.type='button';b.textContent='복사';b.onclick=()=>copyText(row.value,b);line.append(label,code,b);result.appendChild(line);if(row.hint){const h=document.createElement('div');h.className='help';h.textContent=row.hint;result.appendChild(h);}});}
wireCopy(form);
form.querySelectorAll('[data-check]').forEach(x=>{if(!x.dataset||!x.dataset.check)return;x.onchange=async()=>{try{const r=await send('/action/'+x.dataset.check,collect());if(r.ok&&r.result)applyShow(r.result.show);}catch(e){}};});
form.querySelectorAll('input[type=radio]').forEach(x=>x.onchange=toggleGroups);toggleGroups();
form.onsubmit=async e=>{e.preventDefault();const data=collect();lastData=data;busy(true);result.className='';result.textContent='처리 중입니다. 창을 닫지 말고 기다려 주세요.';
let poll=null;if(HAS_PROGRESS&&typeof setInterval==='function')poll=setInterval(async()=>{try{const p=await send('/action/progress',{});if(p.ok&&p.result&&p.result.message)result.textContent='처리 중입니다. 창을 닫지 말고 기다려 주세요.\n'+p.result.message;}catch(x){}},1500);
try{const r=await sendConfirmed('/submit',data);if(poll)clearInterval(poll);poll=null;if(!r.ok){applyShow(r.show);throw Error(r.error);}const res=r.result||{};
 if(res.cancelled){result.textContent=res.message;busy(false);return;}
 form.hidden=true;showResult(res,'완료했습니다. 이 창은 닫아도 됩니다.');
 if(res.keep_open){const a=document.getElementById('after');if(a)a.hidden=false;const path=document.getElementById('after-path');if(path&&res.backup_default)path.value=res.backup_default;}else{finished=true;}
}catch(e){if(poll)clearInterval(poll);if(e.message===CLOSED_TEXT){result.textContent='';return;}result.className='error';result.textContent=e.message;busy(false);}};
form.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{const data=collect(),active=document.activeElement,position={x:window.scrollX,y:window.scrollY},inline=document.getElementById('action-result-'+b.dataset.action);busy(true);inline.hidden=false;inline.className='action-result';inline.textContent='처리 중입니다. 잠시 기다려 주세요.';
try{const r=await sendConfirmed('/action/'+b.dataset.action,data);if(!r.ok){applyShow(r.show);throw Error(r.error);}for(const [name,value] of Object.entries(r.result.values||{})){const field=form.elements.namedItem(name);if(field&&field.type!=='password')field.value=value;}applyShow(r.result.show);inline.textContent=r.result.message||'완료했습니다.';}catch(e){inline.className='action-result error';inline.textContent=e.message;}finally{busy(false);if(active&&typeof active.focus==='function')active.focus({preventScroll:true});window.scrollTo(position.x,position.y);}});
document.getElementById('cancel').onclick=async()=>{await send('/cancel',{});finished=true;form.hidden=true;result.textContent='취소했습니다. 이 창은 닫아도 됩니다.';};
async function finish(message){try{await send('/finish',{});}catch(e){}finished=true;const a=document.getElementById('after');if(a)a.hidden=true;result.textContent=message;}
const afterOut=document.getElementById('after-result');
function bind(id,fn){const x=document.getElementById(id);if(x)x.onclick=fn;}
function afterValue(id){const x=document.getElementById(id);return x?x.value:'';}
bind('after-browse',async()=>{try{const r=await send('/action/browse_backup',{backup_path:afterValue('after-path')});if(!r.ok)throw Error(r.error);if(r.result.values&&r.result.values.backup_path)document.getElementById('after-path').value=r.result.values.backup_path;afterOut.className='action-result';afterOut.hidden=false;afterOut.textContent=r.result.message;}catch(e){afterOut.className='action-result error';afterOut.hidden=false;afterOut.textContent=e.message;}});
bind('after-backup',async()=>{afterOut.hidden=false;afterOut.className='action-result';afterOut.textContent='키 백업을 만드는 중입니다…';
 try{const r=await send('/action/backup',{backup_path:afterValue('after-path'),backup_pass:afterValue('after-pass'),backup_pass2:afterValue('after-pass2'),repository:lastData.repository||''});if(!r.ok)throw Error(r.error);
 afterOut.textContent=r.result.message;document.getElementById('after-later').hidden=true;document.getElementById('after-close').hidden=false;document.getElementById('after-backup').hidden=true;}catch(e){afterOut.className='action-result error';afterOut.textContent=e.message;}});
bind('after-later',async()=>{if(!window.confirm(LATER_VALUE))return;await finish('키 백업 없이 닫았습니다. 다음에 배포관리.cmd 를 열면 [3. 서명 키 백업]에서 꼭 만들어 주세요. 이 창은 닫아도 됩니다.');});
bind('after-close',async()=>{await finish('완료했습니다. 이 창은 닫아도 됩니다.');});
async function ping(){if(finished)return;try{const r=await fetch('/ping',{method:'POST',headers:{'Content-Type':'application/json','X-OnAir-Token':token},body:'{}'});const d=await r.json();const t=document.getElementById('timer');if(d.ok&&typeof d.remaining==='number'&&d.remaining<=180){t.hidden=false;t.textContent='이 창은 약 '+Math.max(1,Math.ceil(d.remaining/60))+'분 뒤 자동으로 닫힙니다. 그 전에 마치지 못하면 다시 실행하세요.';}}catch(e){showClosed();}}
if(typeof setInterval==='function')setInterval(ping,15000);
'''

LATER_WARNING = ('키 백업 없이 닫으시겠습니까?\n\n이 PC가 고장 나거나 Windows를 다시 설치하면 서명 키를 되살릴 수 없어, '
                 '모든 PC에 설치 ZIP을 새로 배포해야 합니다.\n지금 만드는 것을 권장합니다.')


class LocalForm:
    def __init__(self, title, fields, action, description='', actions=None, submit_label='확인 후 진행',
                 closed_message=CLOSED_DEFAULT, after_html=''):
        self.token = secrets.token_urlsafe(32)
        self.done = threading.Event()
        self.result = None
        self.timed_out = False
        self.deadline = None
        self.action = action
        # 응답을 쓰는 중인 요청 수. done 을 응답보다 먼저 켜므로, 창을 닫기 전에 이 응답들이 다 나가기를 기다린다.
        self._inflight = 0
        self._inflight_lock = threading.Condition()
        # 창을 닫지 않는 보조 동작(상태 보기·백업 등). {이름: 함수(입력)->결과}
        self.actions = dict(actions or {})
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, data, content_type='application/json; charset=utf-8'):
                raw = data.encode('utf-8') if isinstance(data, str) else json.dumps(data, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Referrer-Policy', 'no-referrer')
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                u = urlsplit(self.path)
                if u.path != '/' or parse_qs(u.query).get('token') != [owner.token]:
                    return self.respond(403, {'ok': False, 'error': '이 창을 실행기에서 다시 열어 주세요.'})
                self.respond(200, owner.page, 'text/html; charset=utf-8')

            def do_POST(self):
                with owner._inflight_lock:
                    owner._inflight += 1
                try:
                    self._post()
                finally:
                    with owner._inflight_lock:
                        owner._inflight -= 1
                        owner._inflight_lock.notify_all()

            def _post(self):
                if self.headers.get('Host') != owner.url.removeprefix('http://'):
                    return self.respond(403, {'ok': False, 'error': '허용되지 않은 주소입니다.'})
                if self.headers.get('Origin') not in (None, owner.url):
                    return self.respond(403, {'ok': False, 'error': '허용되지 않은 요청입니다.'})
                if not secrets.compare_digest(self.headers.get('X-OnAir-Token', ''), owner.token):
                    return self.respond(403, {'ok': False, 'error': '설정 창을 다시 열어 주세요.'})
                action_name = self.path.removeprefix('/action/') if self.path.startswith('/action/') else None
                if self.path not in ('/submit', '/cancel', '/ping', '/finish') and action_name not in owner.actions:
                    return self.respond(404, {'ok': False, 'error': '알 수 없는 요청입니다.'})
                try:
                    n = int(self.headers.get('Content-Length', '0'))
                    if not 0 < n <= 65536 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                        raise ValueError('잘못된 요청입니다.')
                    payload = json.loads(self.rfile.read(n))
                    if not isinstance(payload, dict):
                        raise ValueError('입력 형식을 확인해 주세요.')
                    if self.path == '/ping':
                        remaining = None if owner.deadline is None else max(0, int(owner.deadline - time.monotonic()))
                        return self.respond(200, {'ok': True, 'remaining': remaining})
                    if self.path in ('/cancel', '/finish'):
                        # 끝났다는 표시를 응답보다 먼저 켠다. 응답을 받은 쪽이 바로 done 을 확인해도 맞는 값이어야 한다.
                        # (창을 닫는 run() 은 이 응답이 다 나갈 때까지 기다린다)
                        owner.done.set()
                        return self.respond(200, {'ok': True, 'result': '닫았습니다.'})
                    if action_name is not None:
                        # 보조 동작은 결과만 보여 주고 창을 끝내지 않는다.
                        return self.respond(200, {'ok': True, 'result': owner.actions[action_name](payload)})
                    result = owner.action(payload)
                    if isinstance(result, dict) and result.get('confirm'):
                        # 확인 창에서 '확인'을 누르면 다시 제출된다. 아직 아무것도 끝나지 않았다.
                        return self.respond(200, {'ok': True, 'result': result})
                    owner.result = result
                    if isinstance(result, dict) and result.get('keep_open'):
                        # 게시는 끝났지만 키 백업 화면을 보여 주는 동안 창을 유지한다.
                        if owner.deadline is not None:
                            owner.deadline = max(owner.deadline, time.monotonic() + KEEP_OPEN_EXTRA)
                        return self.respond(200, {'ok': True, 'result': result})
                    owner.done.set()
                    self.respond(200, {'ok': True, 'result': result})
                except Exception as exc:
                    body = {'ok': False, 'error': str(exc)}
                    if getattr(exc, 'show', None):
                        body['show'] = exc.show
                    self.respond(400, body)

        self.server = _Server(('127.0.0.1', 0), Handler)
        self.server.timeout = 1
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        fields_html = ''.join(_field_html(field) for field in fields)
        script = (_SCRIPT.replace('TOKEN_VALUE', json.dumps(self.token))
                  .replace('CLOSED_VALUE', json.dumps(closed_message))
                  .replace('PROGRESS_VALUE', 'true' if 'progress' in self.actions else 'false')
                  .replace('LATER_VALUE', json.dumps(LATER_WARNING)))
        after = after_html or '<div id="after" hidden></div>'
        self.page = ('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                     '<title>WLR OnAir 설정</title><style>' + _STYLE + '</style><main><small>WLR ONAIR</small><h1>'
                     + html.escape(title) + '</h1><p>' + html.escape(description) + '</p><form id="form">' + fields_html
                     + '<footer><button type="submit">' + html.escape(submit_label)
                     + '</button><button type="button" id="cancel">취소</button></footer></form>'
                     '<div id="result" role="status" aria-live="polite"></div>' + after
                     + '<div id="timer" role="status" hidden></div><div id="closed" class="closed" role="alert" hidden></div>'
                     '</main><script>' + script + '</script></html>')

    def _wait_replies(self, limit=3.0):
        """done 을 켠 요청이 응답을 다 쓸 때까지(최대 limit 초) 기다린다. 창이 '닫혔습니다' 대신 결과를 받게 한다."""
        end = time.monotonic() + limit
        with self._inflight_lock:
            while self._inflight > 0:
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._inflight_lock.wait(left)

    def run(self, timeout=FORM_TIMEOUT):
        """창을 열고 결과를 기다린다. 취소하면 None, 제한 시간(기본 15분)이 지나도 None(timed_out=True).

        게시가 끝난 뒤 키 백업 화면을 띄운 경우(keep_open)에는 그 결과를 돌려준다.
        브라우저를 못 열면 주소를 메시지 상자로 보여 주고(복사 안내), 취소를 고를 수 있다.
        토큰이 들어 있는 주소는 로그에 남기지 않는다.
        """
        self.deadline = time.monotonic() + timeout
        worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        worker.start()
        try:
            url = f'{self.url}/?token={self.token}'
            try:
                opened = bool(webbrowser.open(url))
            except Exception:
                opened = False
            if not opened:
                keep_waiting = _message_box(
                    'WLR OnAir', '설정 화면을 자동으로 열지 못했습니다.\n\n'
                    '1) 이 창에서 Ctrl+C 를 누르면 아래 주소가 복사됩니다.\n'
                    '2) 확인을 누른 뒤 웹 브라우저(Edge 또는 Chrome) 주소창에 붙여 넣고 Enter 를 누르세요.\n'
                    '중단하려면 취소를 누르세요.\n\n'
                    f'{url}\n\n(이 주소는 이 PC에서만 열리며, 다른 사람에게 보내지 마세요.)', ok_cancel=True)
                if not keep_waiting:
                    return None
            while not self.done.is_set():
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    self.timed_out = True
                    break
                self.done.wait(min(remaining, 0.5))
            return self.result
        finally:
            self._wait_replies()
            self.server.shutdown()
            self.server.server_close()


def bootstrap_fingerprint_text(role, executable):
    """첫 설정 화면에 보여 줄 공개 키 지문 안내(설치본 옆 onair_update.json 기준)."""
    from . import core
    try:
        value = core.validate_bootstrap(core.load_json(Path(executable).parent / 'onair_update.json'), role)
    except (core.UpdateError, OSError):
        return ''
    return ('\n\n업데이트 서명 공개 키 지문: %s\n업데이트 폴더: %s\n'
            '이 지문이 관리자가 알려 준 지문(설치 ZIP의 README.txt)과 같은지 눈으로 확인하세요. '
            '다르면 진행하지 말고 관리자에게 알려 주세요.' % (core.public_key_fingerprint(value['public_key']),
                                                         value['repository']))


def bootstrap_fingerprint(role, executable):
    """설치본 옆 onair_update.json 의 (지문, 업데이트 폴더). 없거나 역할이 다르면 None."""
    from . import core
    try:
        value = core.validate_bootstrap(core.load_json(Path(executable).parent / 'onair_update.json'), role)
    except (core.UpdateError, OSError):
        return None
    return core.public_key_fingerprint(value['public_key']), value['repository']


FINGERPRINT_HINT = ('관리자가 알려 준 지문(설치 ZIP 의 README.txt)과 16자리가 모두 같은지 비교하세요. '
                    '복사 버튼으로 복사해 메신저로 받은 지문 옆에 붙여 넣으면 비교하기 쉽습니다.')


def ensure_setup(role, executable):
    from .profiles import profile_dir, prepare_profile
    config_dir = profile_dir(role) / 'config'
    ini_name = 'config_admin.ini' if role == 'ADMIN' else 'config_viewer.ini'
    if (config_dir / ini_name).is_file():
        return prepare_profile(role)
    executable = Path(executable)
    default_legacy = str(executable.parent) if any((executable.parent / n).exists() for n in (ini_name, 'config.ini')) else ''
    choices = [('legacy', '기존 공유 폴더 사용',
                '이 PC에서 예전 WLR OnAir 를 쓰고 있었습니다. 예전 프로그램 폴더를 고르면 그 설정'
                '(공유 데이터 폴더·포트·암호)을 그대로 가져옵니다.'),
               ('shared', '이 PC를 새로 연결',
                '이 PC에서는 처음 씁니다. 관리자가 알려 준 공유 데이터 폴더(smart_duty.db 가 있는 폴더)를 고르세요.')]
    if role == 'ADMIN':
        choices.append(('new', '새 데이터 만들기',
                        'WLR OnAir 를 처음 도입하는 경우에만 고르세요. 빈 당직표를 이 PC에 새로 만듭니다.'))
    fields = [
        {'type': 'radio', 'name': 'choice', 'label': '어떤 경우인가요?', 'options': choices,
         'value': 'legacy' if default_legacy else 'shared'},
        {'type': 'group', 'show_when': ('choice', 'legacy')},
        {'name': 'legacy_dir', 'label': '예전 프로그램 폴더', 'value': default_legacy, 'browse': 'browse_legacy',
         'help': '예전 WLR OnAir(관리자용 또는 조회용) 실행 파일이 들어 있던 폴더를 [찾아보기]로 고르세요.'},
        {'name': 'closed', 'label': '예전 WLR OnAir 프로그램을 종료했습니다(트레이 아이콘에서 종료).', 'type': 'checkbox'},
        {'type': 'end_group'},
        {'type': 'group', 'show_when': ('choice', 'shared')},
        {'name': 'shared_dir', 'label': '공유 데이터 폴더', 'browse': 'browse_shared',
         'help': '관리자가 알려 준 공유 폴더(당직 데이터 파일 smart_duty.db가 있는 폴더)를 [찾아보기]로 고르거나 전체 경로를 적으세요.'},
        {'type': 'end_group'},
    ]
    if role == 'ADMIN':
        fields += [{'type': 'group', 'show_when': ('choice', 'new')},
                   {'type': 'note', 'label': '다른 PC들이 함께 쓰려면 나중에 데이터 폴더를 공유 폴더로 옮겨야 합니다. '
                                             '이미 쓰고 있는 당직표가 있다면 이 항목을 고르지 마세요.'},
                   {'type': 'end_group'}]
    found = bootstrap_fingerprint(role, executable)
    if found:
        fields.insert(0, {'type': 'copy', 'label': '업데이트 서명 공개 키 지문', 'value': found[0], 'help': FINGERPRINT_HINT})

    def apply(data):
        choice = data.get('choice')
        legacy = _path_input(data.get('legacy_dir', ''))
        shared = _path_input(data.get('shared_dir', ''))
        if choice not in ('legacy', 'shared', 'new') or (choice == 'new' and role != 'ADMIN'):
            raise ValueError('위에서 어떤 경우인지 하나를 골라 주세요.')
        if choice == 'legacy':
            if not legacy:
                raise ValueError('예전 프로그램 폴더를 입력하거나 [찾아보기]로 골라 주세요.')
            if data.get('closed') is not True:
                raise ValueError('예전 WLR OnAir 프로그램을 먼저 종료하고 "프로그램을 종료했습니다"에 체크해 주세요.')
            path = prepare_profile(role, legacy_dir=Path(legacy))
        elif choice == 'shared':
            if not shared:
                raise ValueError('공유 데이터 폴더를 입력하거나 [찾아보기]로 골라 주세요.')
            path = prepare_profile(role, shared_dir=Path(shared))
        else:
            path = prepare_profile(role, allow_new=True)
        return {'config_dir': str(path), 'message': '설정을 저장했습니다. WLR OnAir를 실행합니다. 이 창은 닫아도 됩니다.'}

    form = LocalForm('관리자용 첫 설정' if role == 'ADMIN' else '조회용 첫 설정', fields, apply,
                     '한 번만 설정하면 이후 버전에서도 같은 설정과 데이터를 사용합니다.\n'
                     '아래에서 이 PC에 맞는 경우를 고르세요. 원래 당직 데이터 파일은 옮기거나 덮어쓰지 않습니다.'
                     + bootstrap_fingerprint_text(role, executable),
                     actions={'browse_legacy': lambda d: browse_result('legacy_dir', 'folder', d),
                              'browse_shared': lambda d: browse_result('shared_dir', 'folder', d)},
                     submit_label='저장하고 시작',
                     closed_message='창이 닫혔습니다. 설정을 마치지 못했습니다. WLR OnAir 를 다시 실행하세요.')
    result = form.run()
    if not result:
        if form.timed_out:
            raise RuntimeError('설정 창을 15분 안에 마치지 못해 종료했습니다. 프로그램을 다시 실행해 주세요.')
        raise RuntimeError('초기 설정을 취소했습니다.')
    return Path(result['config_dir'])


ROLE_NAMES = {'ADMIN': '관리자용', 'VIEWER': '조회용'}
ROLE_CHOICES = {'BOTH': ('ADMIN', 'VIEWER'), 'ADMIN': ('ADMIN',), 'VIEWER': ('VIEWER',)}


def _role_text(roles):
    return ' + '.join(ROLE_NAMES[r] for r in roles)


def format_repository_status(status, key_status):
    """저장소 현재 상태와 키 백업 상태를 비개발자용 문장으로 만든다(화면 표시·시험용)."""
    lines = [f"업데이트 폴더: {status['repository']}"]
    if status['new_repository']:
        lines.append('상태: 아직 게시된 적 없는 새 폴더입니다. 게시하면 여기에 새 저장소가 만들어집니다.')
    else:
        lines.append('상태: 기존 업데이트 저장소입니다.')
        lines.append(f"저장소 공개 키 지문: {status['fingerprint'] or '(없음)'}")
    if status['local_key_fingerprint']:
        lines.append(f"이 PC의 서명 키 지문: {status['local_key_fingerprint']}")
    for role, info in status['roles'].items():
        if info['error']:
            text = '확인 실패'
        elif info['published']:
            text = f"현재 버전 {info['version']} (배포 차수 {info['sequence']})"
        else:
            text = '아직 게시된 버전 없음'
        lines.append(f"{ROLE_NAMES[role]}: {text}" + ('  [업데이트 일시 정지 중]' if info['paused'] else ''))
    for warning in status['warnings']:
        lines.append(f'주의: {warning}')
    if key_status.get('warning'):
        lines.append(f"주의: {key_status['warning']}")
    elif key_status.get('backed_up'):
        lines.append(f"키 백업: 만들었음 ({key_status['backup_created']})")
    return '\n'.join(lines)


def default_backup_path(now=None):
    """키 백업 기본 저장 위치: 공유되지 않는 이 PC 사용자 폴더(개인 키 폴더와는 다른 곳)."""
    import datetime
    now = now or datetime.datetime.now()
    return Path.home() / 'WLR_OnAir_키백업' / f'WLR_OnAir_서명키백업_{now:%Y%m%d_%H%M}.json'


class PublisherActions:
    """게시 화면의 동작 모음. 화면(LocalForm)과 분리해 두어 시험할 수 있다."""

    def __init__(self, root, opener=None):
        from . import publisher
        self.publisher = publisher
        self.root = Path(root)
        self.key = self.root / publisher.KEY_FILE_NAME
        self._viewed = {}
        self._confirmations = {}
        self._progress = ''
        self._lock = threading.Lock()
        self.opener = opener if opener is not None else open_folder

    # -------------------------------------------------------------- 확인 창(위험한 동작)
    def _confirm(self, data, kind, text, **intent):
        """처음 호출이면 확인 창 내용을 돌려주고(None 아님), 확인된 재요청이면 None.

        확인 토큰은 한 번만 쓰이고, 대상·경로가 바뀌면 다시 묻는다.
        """
        key = json.dumps([kind, intent], sort_keys=True, ensure_ascii=False)
        token = data.get('confirmed')
        with self._lock:
            if isinstance(token, str) and self._confirmations.pop(token, None) == key:
                return None
            new = secrets.token_urlsafe(16)
            self._confirmations[new] = key
        return {'confirm': {'token': new, 'text': text}, 'message': '확인 창의 내용을 확인해 주세요.'}

    # -------------------------------------------------------------- 경로
    def save_paths(self, data):
        paths = {}
        for name in ('repository', 'release', 'installers'):
            text = _path_input(data.get(name, ''))
            if text:
                if len(text) >= 4096 or not Path(text).is_absolute():
                    raise ValueError('경로는 전체 경로로 입력해 주세요. 비워 둔 항목은 저장하지 않습니다.')
                paths[name] = text
        self.publisher.save_last_paths(paths, self.key, strict=True)
        return {'message': '경로를 저장했습니다. 같은 PC·Windows 계정에서 다음 실행 시 자동으로 채워집니다. 게시하지 않아도 유지됩니다.'}

    def _browse(self, name, kind, data):
        result = browse_result(name, kind, data)
        if result['values']:
            result['message'] = '선택했습니다. 다음에도 사용하려면 경로 저장을 눌러 주세요.'
        return result

    def browse_repository(self, data):
        result = self._browse('repository', 'folder', data)
        if result['values']:
            result['show'] = self._new_repository_visibility(result['values']['repository'])
        return result

    def browse_release(self, data):
        return self._browse('release', 'zip', data)

    def browse_installers(self, data):
        return self._browse('installers', 'folder', data)

    def browse_backup(self, data):
        return browse_result('backup_path', 'save', data)

    def browse_restore(self, data):
        return browse_result('restore_path', 'json', data)

    def _repository(self, data):
        text = _path_input(data.get('repository', ''))
        path = Path(text) if text else None
        if path is None or not path.is_absolute():
            raise ValueError('공유 PC의 업데이트 폴더를 \\\\로 시작하는 전체 경로로 입력해 주세요.')
        return path

    def _roles(self, data, field='roles'):
        roles = ROLE_CHOICES.get(data.get(field))
        if roles is None:
            if field == 'pause_roles':
                raise ValueError('일시 정지/해제할 대상을 먼저 골라 주세요(관리자용만 / 조회용만 / 둘 다).')
            raise ValueError('게시 대상을 확인해 주세요.')
        return roles

    @staticmethod
    def _digest(status):
        return hashlib.sha256(json.dumps(status, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()

    def _new_repository_visibility(self, repository):
        try:
            status = self.publisher.repository_status(Path(_path_input(repository)), self.key)
            return {'new_repo_ok': bool(status['new_repository'])}
        except Exception:
            return {'new_repo_ok': False}

    def check_repository(self, data):
        """업데이트 폴더 칸이 바뀌었을 때(읽기 전용): 새 저장소 확인란을 보여 줄지 정한다."""
        repo = self._repository(data)
        show = self._new_repository_visibility(str(repo))
        message = ('아직 게시된 적 없는 폴더입니다. 게시하려면 아래 "새 저장소" 확인란에 체크하세요.'
                   if show['new_repo_ok'] else '기존 업데이트 저장소이거나 확인할 수 없는 폴더입니다.')
        return {'message': message, 'show': show}

    def status(self, data):
        repo = self._repository(data)
        status = self.publisher.repository_status(repo, self.key)
        self._viewed[str(repo)] = self._digest(status)
        return {'message': format_repository_status(status, self.publisher.key_backup_status(self.key))
                + '\n\n내용이 맞으면 아래 \'위 상태를 확인했습니다\'에 체크한 뒤 게시하세요.', 'status': status,
                'show': {'new_repo_ok': bool(status['new_repository'])}}

    def progress(self, data):
        return {'message': self._progress}

    def _set_progress(self, text):
        self._progress = text

    # -------------------------------------------------------------- 게시
    def publish(self, data):
        repo = self._repository(data)
        release = Path(_path_input(data.get('release', '')))
        output = Path(_path_input(data.get('installers', '')))
        if not all(p.is_absolute() for p in (release, output)):
            raise ValueError('Update ZIP 파일과 설치 ZIP 폴더를 모두 전체 경로로 입력해 주세요.')
        roles = self._roles(data)
        status = self.publisher.repository_status(repo, self.key)       # 읽기만 한다
        if status['new_repository']:
            if data.get('new_repo_ok') is not True:
                raise NeedsInput('이 폴더에는 아직 게시된 저장소가 없습니다. 새 저장소를 만듭니다. 맞습니까? '
                                 f'맞다면 업데이트 폴더 아래에 나타난 "{self.publisher.NEW_REPOSITORY_LABEL}"에 체크하고, '
                                 '폴더를 잘못 입력했다면 경로를 고쳐 주세요.', show={'new_repo_ok': True})
            key_note = ('이 PC에는 아직 서명 키가 없으므로 새 서명 키를 만들고, 게시가 끝나면 바로 키 백업을 만들게 됩니다.'
                        if not self.key.exists() else '이 PC에 있는 서명 키로 서명합니다.')
            ask = self._confirm(data, 'new_repository',
                                '[새 업데이트 저장소 만들기]\n'
                                f'업데이트 폴더: {repo}\n게시 대상: {_role_text(roles)}\n'
                                f'결과: 이 폴더에 새 업데이트 저장소를 만들고 첫 버전을 게시합니다. {key_note}\n'
                                '직원 PC는 이 폴더에서 업데이트를 받게 됩니다. 계속할까요?',
                                repository=str(repo), roles=list(roles))
            if ask:
                return ask
        self._progress = ''
        result = self.publisher.publish_release(
            repo, release, self.key, installer_output=output, roles=roles,
            force_sequence_reset=data.get('force_sequence_reset') is True, progress=self._set_progress)
        if result.get('already_published'):
            # 저장소가 바뀌지 않았으므로 '게시 완료'라고 하지 않는다. 오류로 보여 주어 창을 열어 둔 채
            # 올바른 Update ZIP 을 다시 고를 수 있게 한다.
            raise ValueError(f"게시하지 않았습니다. 버전 {result['version']}: "
                             f'{self.publisher.ALREADY_PUBLISHED_NOTICE}\n선택한 파일: {release}')
        self.publisher.save_last_paths({'repository': str(repo), 'release': str(release),
                                        'installers': str(output)}, self.key)
        self._viewed.pop(str(repo), None)
        opened = self.opener(output)
        text = (f"버전 {result['version']} 게시를 완료했습니다. ({_role_text(roles)})\n"
                f"직원 PC용 설치 ZIP 폴더: {output}" + ('  (탐색기로 열었습니다)' if opened else '') + '\n'
                '처음 설치하는 PC에만 해당 설치 ZIP을 전달하세요(직원에게는 조회용). '
                '이미 설치한 PC는 프로그램을 다시 시작하면 새 버전을 받습니다.')
        for warning in result['warnings']:
            text += f'\n주의: {warning}'
        reply = {'message': text,
                 'copy': [{'label': '업데이트 서명 공개 키 지문', 'value': result['fingerprint'],
                           'hint': '직원 PC 첫 설정 화면에 같은 지문이 나와야 합니다. 직원에게 이 지문을 따로(메신저 등) 알려 주세요.'}]}
        key_status = self.publisher.key_backup_status(self.key)
        if result.get('key_created') or (key_status.get('key_exists') and not key_status.get('backed_up')):
            reply.update(keep_open=True, after='backup', backup_default=str(default_backup_path()))
            reply['message'] += ('\n\n' + ('이번 게시에서 이 PC에 서명 키를 새로 만들었습니다. '
                                           if result.get('key_created') else '')
                                 + '아래에서 키 백업을 지금 만들어 주세요.')
        elif key_status.get('warning'):
            reply['message'] += f"\n주의: {key_status['warning']}"
        return reply

    # -------------------------------------------------------------- 키 백업·복구
    def backup(self, data):
        if data.get('backup_pass') != data.get('backup_pass2'):
            raise ValueError('백업 암호와 암호 확인이 서로 다릅니다.')
        path = Path(_path_input(data.get('backup_path', '')))
        if not path.is_absolute():
            raise ValueError('백업 파일을 저장할 전체 경로(파일 이름까지)를 입력해 주세요. USB에 저장하는 것을 권장합니다.')
        text = _path_input(data.get('repository', ''))
        if text and Path(text).is_absolute() and self.publisher._is_within(Path(os.path.abspath(path)), Path(os.path.abspath(text))):
            raise ValueError('백업 파일을 공유 업데이트 폴더 안에 저장하지 마세요. 다른 사람이 볼 수 있습니다.')
        result = self.publisher.export_key_backup(path, data.get('backup_pass', ''), self.key)
        return {'message': f"키 백업을 만들었습니다: {result['path']}\n지문: {result['fingerprint']}\n"
                '이 파일을 USB 등 이 PC 밖에도 한 부 복사해 두고, 백업 암호는 파일과 따로 적어 두세요.'}

    def restore(self, data):
        path = Path(_path_input(data.get('restore_path', '')))
        if not path.is_absolute():
            raise ValueError('키 백업 파일의 전체 경로를 입력해 주세요.')
        text = _path_input(data.get('repository', ''))
        repo = Path(text) if text and Path(text).is_absolute() else None
        replace = data.get('restore_replace') is True
        if replace and self.key.exists():
            ask = self._confirm(data, 'replace_key',
                                '[서명 키 교체]\n'
                                f'이 PC의 서명 키 파일: {self.key}\n'
                                f'업데이트 폴더: {repo or "(입력 안 함 - 저장소와 맞는지 확인하지 않음)"}\n'
                                f'백업 파일: {path}\n'
                                '결과: 지금 키 파일은 이름만 바꿔 같은 폴더에 보관하고, 백업의 키를 이 PC의 서명 키로 씁니다. '
                                '저장소 공개 키와 다른 키로 바꾸면 이 PC에서는 그 저장소에 게시할 수 없습니다. 계속할까요?',
                                path=str(path), repository=str(repo or ''), key=str(self.key))
            if ask:
                return ask
        result = self.publisher.import_key_backup(path, data.get('restore_pass', ''), self.key, repo,
                                                  replace_existing=replace)
        return {'message': f"키를 복구했습니다. 지문: {result['fingerprint']}\n"
                '이 지문이 기존 설치 ZIP의 README.txt 에 적힌 지문, 그리고 저장소 상태의 공개 키 지문과 같은지 확인하세요.'}

    # -------------------------------------------------------------- 비상 정지
    def _pause(self, data, paused):
        roles = self._roles(data, 'pause_roles')
        repo = self._repository(data)
        if paused:
            text = ('[업데이트 일시 정지]\n'
                    f'대상: {_role_text(roles)}\n업데이트 폴더: {repo}\n'
                    '결과: 대상 PC들은 다음 실행부터 새 버전을 받지 않고 지금 설치된 버전으로 계속 실행합니다. '
                    '설치된 프로그램과 데이터는 바뀌지 않습니다. 계속할까요?')
        else:
            text = ('[일시 정지 해제]\n'
                    f'대상: {_role_text(roles)}\n업데이트 폴더: {repo}\n'
                    '결과: 대상 PC들이 다음 실행부터 지금 게시된 버전을 다시 받습니다. 계속할까요?')
        ask = self._confirm(data, 'pause' if paused else 'resume', text, repository=str(repo), roles=list(roles))
        if ask:
            return ask
        self.publisher.set_paused(repo, roles, paused)
        if paused:
            return {'message': f'{_role_text(roles)} 업데이트를 일시 정지했습니다. 대상 PC는 새 버전을 받지 않고 '
                    '설치된 버전으로 실행합니다(3.16 이상 실행기 기준). 옛 실행기는 정지를 무시하니 운영 안내서를 확인하세요.'}
        return {'message': f'{_role_text(roles)} 일시 정지를 풀었습니다. 다음 실행부터 업데이트를 다시 확인합니다.'}

    def pause(self, data):
        return self._pause(data, True)

    def resume(self, data):
        return self._pause(data, False)


_UPDATE_ZIP = re.compile(r'WLR_OnAir_v(\d+\.\d+\.\d+)_Update\.zip', re.IGNORECASE)


def find_latest_update_zip(executable):
    """실행 중인 exe 옆, 또는 빌드 결과 폴더(exe 의 상위 폴더)에서 가장 높은 버전의 Update ZIP.

    빌드 폴더 구조: <빌드폴더>/Admin/WLR_OnAir_Admin.exe 와 <빌드폴더>/WLR_OnAir_v*_Update.zip.
    같은 버전이 여럿이면 최근 파일. 찾지 못하면 None.
    """
    from .core import parse_version
    folder = Path(executable).absolute().parent
    found = []
    for directory in dict.fromkeys((folder, folder.parent)):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for item in entries:
            match = _UPDATE_ZIP.fullmatch(item.name)
            if not match:
                continue
            try:
                if item.is_symlink() or not item.is_file():
                    continue
                found.append((parse_version(match.group(1)), item.stat().st_mtime, str(item)))
            except (OSError, ValueError):
                continue
    return max(found)[2] if found else None


INSTALLERS_FOLDER = '직원PC_설치ZIP'


def default_installers_dir(built_zip, last=None):
    """설치 ZIP 기본 저장 폴더: 빌드 완료 폴더 옆(사용자가 바로 찾을 수 있는 곳).

    빌드 폴더를 찾지 못하면 지난번 폴더, 그것도 없으면 사용자 폴더 아래.
    """
    if built_zip:
        return str(Path(built_zip).parent / INSTALLERS_FOLDER)
    if last:
        return last
    return str(Path.home() / 'WLR_OnAir_설치ZIP')


def _after_publish_panel(backup_default):
    value = html.escape(str(backup_default), quote=True)
    return ('<section id="after" class="panel" hidden><h2>지금 서명 키 백업을 만드세요 (꼭 필요)</h2>'
            '<p>서명 키는 이 PC의 이 Windows 계정 안에만 있습니다. PC가 고장 나거나 Windows를 다시 설치하면 '
            '키를 잃어, 모든 PC에 설치 ZIP을 새로 배포해야 합니다. 백업 파일은 암호로 잠깁니다.\n'
            '기본 위치는 공유되지 않는 이 PC의 사용자 폴더입니다. 만든 뒤 USB 등에도 한 부 복사하세요.</p>'
            '<label>키 백업 파일을 저장할 위치<span class="path-row"><input id="after-path" value="' + value + '" '
            'autocomplete="off" spellcheck="false"><button type="button" id="after-browse">찾아보기</button></span></label>'
            '<label>백업 암호 (12자 이상, 파일과 따로 적어 두세요)<input id="after-pass" type="password" autocomplete="off"></label>'
            '<label>백업 암호 확인<input id="after-pass2" type="password" autocomplete="off"></label>'
            '<footer><button type="button" class="primary" id="after-backup">키 백업 만들기</button>'
            '<button type="button" id="after-later">나중에 하고 닫기</button>'
            '<button type="button" class="primary" id="after-close" hidden>닫기</button></footer>'
            '<div id="after-result" class="action-result" role="status" aria-live="polite" hidden></div></section>')


def publisher_ui(executable):
    from . import publisher
    root = publisher.default_publisher_dir()
    root.mkdir(parents=True, exist_ok=True)
    actions = PublisherActions(root)
    last = publisher.load_last_paths(actions.key)
    backup = publisher.key_backup_status(actions.key)
    # 지난번에 게시한 ZIP 이 아니라, 이 빌드 폴더에서 새로 만든 Update ZIP 을 기본값으로 채운다.
    built = find_latest_update_zip(executable)
    release_default = built or last.get('release', '')
    repository_default = last.get('repository', '')
    new_repo_visible = bool(repository_default) and actions._new_repository_visibility(repository_default)['new_repo_ok']
    fields = []
    if backup.get('fingerprint'):
        fields.append({'type': 'copy', 'label': '이 PC 서명 키 지문', 'value': backup['fingerprint'],
                       'help': '직원 PC 첫 설정 화면·설치 ZIP 의 README.txt 에 나오는 지문과 같아야 합니다.'})
    fields += [
        {'type': 'section', 'label': '1. 업데이트 폴더'},
        {'name': 'repository', 'browse': 'browse_repository', 'label': '공유 PC의 업데이트 폴더', 'required': True, 'value': repository_default,
         'check': 'check_repository', 'help': '직원 PC들이 업데이트를 받아 갈 공유 폴더입니다. \\\\로 시작하는 전체 경로를 적거나 [찾아보기]로 고르세요.'},
        {'type': 'button', 'name': 'check_repository', 'label': '이 폴더 상태 확인 (읽기만 함)'},
        {'name': 'new_repo_ok', 'label': publisher.NEW_REPOSITORY_LABEL, 'type': 'checkbox', 'hidden': not new_repo_visible,
         'help': '처음 게시하는 빈 폴더일 때만 보입니다. 게시 직전에 확인 창이 한 번 더 나옵니다.'},
        {'type': 'section', 'label': '2. 게시'},
        {'name': 'release', 'browse': 'browse_release', 'label': '새 Update ZIP 파일의 전체 경로', 'required': True, 'value': release_default,
         'help': '빌드가 끝나면 생기는 WLR_OnAir_v(버전)_Update.zip 파일입니다. [찾아보기]로 고르세요.'},
        {'name': 'installers', 'browse': 'browse_installers', 'label': '직원 PC용 설치 ZIP을 저장할 폴더',
         'value': default_installers_dir(built, last.get('installers')), 'required': True,
         'help': '게시가 끝나면 이 폴더를 탐색기로 열어 줍니다. 처음 설치하는 PC에만 여기의 설치 ZIP을 전달하세요.'},
        {'type': 'button', 'name': 'save_paths', 'label': '경로 저장 (게시하지 않고 저장)'},
        {'name': 'roles', 'label': '게시 대상', 'type': 'select', 'options': [('BOTH', '관리자용 + 조회용'), ('ADMIN', '관리자용만'), ('VIEWER', '조회용만')]},
        {'type': 'details', 'label': '3. 서명 키 백업'},
        {'type': 'note', 'label': '서명 키는 처음 게시할 때 이 PC에 자동으로 만들어지고, 게시 직후 백업 화면이 나옵니다. '
                                  '여기서는 언제든 백업을 한 부 더 만들 수 있습니다.'},
        {'name': 'backup_path', 'browse': 'browse_backup', 'label': '키 백업 파일을 저장할 전체 경로 (USB 권장)',
         'value': str(default_backup_path()), 'help': '파일 이름까지 적습니다. 만든 뒤 USB 등 이 PC 밖에도 한 부 복사해 두세요.'},
        {'name': 'backup_pass', 'label': '백업 암호 (12자 이상)', 'type': 'password'},
        {'name': 'backup_pass2', 'label': '백업 암호 확인', 'type': 'password'},
        {'type': 'button', 'name': 'backup', 'label': '키 백업 파일 만들기'},
        {'type': 'end_details'},
        {'type': 'details', 'label': '서명 키 복구 (PC를 바꿨거나 Windows를 다시 설치한 경우)'},
        {'name': 'restore_path', 'browse': 'browse_restore', 'label': '복구할 키 백업 파일의 전체 경로'},
        {'name': 'restore_pass', 'label': '백업 암호', 'type': 'password'},
        {'name': 'restore_replace', 'label': publisher.KEY_REPLACE_LABEL, 'type': 'checkbox'},
        {'type': 'button', 'name': 'restore', 'label': '키 복구', 'danger': True},
        {'type': 'end_details'},
        {'type': 'details', 'label': '4. 비상 정지 / 저장소 복원'},
        {'name': 'pause_roles', 'label': '일시 정지·해제할 대상', 'type': 'select', 'value': '',
         'options': [('', '— 대상을 고르세요 —'), ('VIEWER', '조회용만'), ('ADMIN', '관리자용만'), ('BOTH', '관리자용 + 조회용')]},
        {'type': 'button', 'name': 'pause', 'label': '선택한 대상의 업데이트 일시 정지', 'danger': True},
        {'type': 'button', 'name': 'resume', 'label': '선택한 대상의 일시 정지 해제', 'danger': True},
        {'name': 'force_sequence_reset', 'label': publisher.FORCE_RESET_LABEL, 'type': 'checkbox'},
        {'type': 'end_details'},
    ]
    description = ('게시 버튼을 누르면 업데이트 폴더 상태를 자동으로 검사합니다. 게시는 보통 2~5분 걸리며 진행 단계가 화면에 나옵니다.\n'
                   '직원에게는 조회용 설치 ZIP만 전달해 주세요.\n'
                   '서명 키는 이 PC의 이 Windows 계정에 보관되며 공유 폴더에는 저장되지 않습니다. 이후 게시도 같은 PC·계정에서 진행하세요.\n'
                   '이 창은 15분 동안 열려 있습니다.')
    if built:
        description += f'\n\nUpdate ZIP 칸에는 빌드 폴더에서 찾은 가장 새 파일을 채워 두었습니다: {Path(built).name}'
    if backup.get('warning'):
        description += f"\n\n[경고] {backup['warning']}"
    form = LocalForm('업데이트 배포 관리', fields, actions.publish, description,
                     actions={name: getattr(actions, name) for name in
                              ('backup', 'restore', 'pause', 'resume', 'save_paths', 'browse_repository', 'browse_release',
                               'browse_installers', 'browse_backup', 'browse_restore', 'check_repository', 'progress')},
                     submit_label='게시',
                     closed_message='창이 닫혔습니다(15분이 지났거나 프로그램이 종료됨). 배포관리.cmd 를 다시 실행하세요.',
                     after_html=_after_publish_panel(default_backup_path()))
    return form.run()
