"""Real generated form behavior, using synthetic paths and no publication/key access."""
import json
import re
import shutil
import subprocess
from html.parser import HTMLParser

from updater import setup_ui as ui, publisher


class FormStructure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.details = []
        self.stack = []
        self.fields = {}
        self.actions = {}
        self.visible_text = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'details':
            self.details.append(attrs)
            self.stack.append(len(self.details))
        if tag in ('input', 'select'):
            self.fields[attrs.get('name')] = tuple(self.stack)
        if tag == 'button' and attrs.get('data-action'):
            self.actions[attrs['data-action']] = tuple(self.stack)

    def handle_endtag(self, tag):
        if tag == 'details':
            self.stack.pop()

    def handle_data(self, text):
        if not self.stack:
            self.visible_text.append(text)


def publisher_page(monkeypatch, tmp_path, warning='키 백업을 아직 만들지 않았습니다'):
    monkeypatch.setattr(publisher, 'default_publisher_dir', lambda: tmp_path / 'publisher')
    monkeypatch.setattr(publisher, 'key_backup_status', lambda key: {'warning': warning})
    seen = {}

    def capture(form):
        seen['page'] = form.page
        form.server.server_close()

    monkeypatch.setattr(ui.LocalForm, 'run', capture)
    ui.publisher_ui(tmp_path / 'synthetic.exe')
    return seen['page']


def test_advanced_fields_are_folded_but_publication_and_required_warning_stay_visible(monkeypatch, tmp_path):
    page = publisher_page(monkeypatch, tmp_path)
    structure = FormStructure()
    structure.feed(page)
    assert len(structure.details) == 3
    assert all('open' not in d for d in structure.details)
    for name in ('repository', 'release', 'installers', 'roles', 'new_repo_ok'):
        assert structure.fields[name] == ()
    for name in ('backup_path', 'backup_pass', 'backup_pass2', 'restore_path', 'restore_pass', 'restore_replace', 'force_sequence_reset'):
        assert structure.fields[name]
    for name in ('backup', 'restore', 'pause', 'resume'):
        assert structure.actions[name]
    assert '키 백업을 아직 만들지 않았습니다' in ''.join(structure.visible_text)
    assert 'status' not in structure.actions and 'status_ok' not in structure.fields
    assert structure.actions['save_paths'] == ()


def test_every_auxiliary_action_has_its_own_inline_live_result(monkeypatch, tmp_path):
    page = publisher_page(monkeypatch, tmp_path, warning='')
    for action in ('browse_repository', 'browse_release', 'browse_installers', 'save_paths', 'backup', 'restore', 'pause', 'resume'):
        assert f'id="action-result-{action}"' in page
    assert 'class="action-result" role="status" aria-live="polite"' in page


NODE_HARNESS = r'''
const assert=require('node:assert/strict'),vm=require('node:vm');
const input=JSON.parse(process.argv[1]);
const nodes=new Map(),field={name:'release',type:'text',value:'old.zip'},password={type:'password',value:'keep-secret'};
let focused,scrollCalls=[],responses=input.responses;
const button={dataset:{action:input.action},disabled:false,focus(options){focused=this;this.focusOptions=options;}};
function node(id){if(!nodes.has(id))nodes.set(id,{id,textContent:'',className:'',hidden:true,scrollIntoView(){throw Error('auxiliary result scrolled');}});return nodes.get(id);}
const form={elements:{namedItem:name=>name==='release'?field:name==='backup_pass'?password:null},querySelectorAll:selector=>selector==='input[type=checkbox]'?[]:[button]};
const doc={getElementById:id=>id==='form'?form:node(id),activeElement:button};
const ctx=vm.createContext({document:doc,FormData:function(){return [['release',field.value]];},fetch:async()=>({json:async()=>responses.shift()}),console,
window:{scrollX:17,scrollY:430,scrollTo:(x,y)=>scrollCalls.push([x,y])}});
vm.runInContext(input.script,ctx);
(async()=>{
  await button.onclick();
  assert.equal(node('action-result-'+input.action).textContent,input.expected);
  assert.equal(node('action-result-'+input.action).hidden,false);
  assert.equal(node('result').textContent,'');
  assert.equal(button.disabled,false);
  assert.equal(focused,button);
  assert.equal(button.focusOptions.preventScroll,true);
  assert.deepEqual(scrollCalls,[[17,430]]);
  assert.equal(field.value,input.fieldValue);
  assert.equal(password.value,'keep-secret');
  assert.equal(node('action-result-'+input.action).className.includes('error'),input.error);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''


def run_action(page, action, response, expected, field_value='old.zip', error=False):
    script = re.search(r'<script>(.*?)</script>', page, re.S).group(1)
    result = subprocess.run([shutil.which('node') or 'node', '-e', NODE_HARNESS, json.dumps({
        'script': script, 'action': action, 'responses': [response], 'expected': expected,
        'fieldValue': field_value, 'error': error})], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_browse_updates_field_inline_without_losing_focus_or_scroll(monkeypatch, tmp_path):
    page = publisher_page(monkeypatch, tmp_path, warning='')
    run_action(page, 'browse_release', {'ok': True, 'result': {'message': '선택했습니다', 'values': {'release': 'new.zip', 'backup_pass': 'must-not-set'}}},
               '선택했습니다', field_value='new.zip')


def test_save_path_error_stays_by_action_with_focus_and_scroll_preserved(monkeypatch, tmp_path):
    page = publisher_page(monkeypatch, tmp_path, warning='')
    run_action(page, 'save_paths', {'ok': False, 'error': '경로는 전체 경로로 입력해 주세요'},
               '경로는 전체 경로로 입력해 주세요', error=True)


def test_cancelled_browse_keeps_existing_field_and_reports_inline(monkeypatch, tmp_path):
    page = publisher_page(monkeypatch, tmp_path, warning='')
    run_action(page, 'browse_release', {'ok': True, 'result': {'message': '선택을 취소했습니다. 기존 경로를 유지합니다.', 'values': {}}},
               '선택을 취소했습니다. 기존 경로를 유지합니다.')
