"""Four-character names and existing legacy identities in real Admin/Viewer UI."""
from pathlib import Path
from datetime import datetime,timezone,timedelta
import argparse,json,sqlite3,tempfile,traceback
from playwright.sync_api import sync_playwright
from runtime import Runtime,fixture
parser=argparse.ArgumentParser();parser.add_argument('--phase',default='baseline');args=parser.parse_args()
OUT=Path(__file__).parent/'names'/args.phase;OUT.mkdir(parents=True,exist_ok=True)
checks=[];runtime=None;errors=[]
def record(name,value,evidence=None):
 checks.append(dict(name=name,passed=bool(value),evidence=evidence));print(('PASS ' if value else 'FAIL ')+name,flush=True)
try:
 with tempfile.TemporaryDirectory(prefix='onair-four-name-') as tmp:
  base=Path(tmp);shared=base/'shared';shared.mkdir();sqlite3.connect(shared/'smart_duty.db').close();runtime=Runtime(base,'admin','ADMIN',shared)
  data=fixture();data['members'][0]['name']='남궁민수';data['members'][1]['name']='제갈서연'
  today=datetime.now(timezone(timedelta(hours=9))).date().isoformat()
  data['schedules']={today:[dict(name='남궁민수',role='당직'),dict(name='제갈서연',role='당직')]}
  data['absences']=[dict(id=1,name='이준호',type='연차/휴가',start=today,end=today)]
  response=runtime.send('/api/duty/save-all',dict(baseVersion=runtime.get('/api/duty/all-data').json()['dataVersion'],**data));response.raise_for_status()
  with sync_playwright() as pw:
   browser=pw.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
   for w,h in [(1920,1080),(1366,768),(1024,768)]:
    ctx,p=runtime.open(browser,width=w,height=h);p.on('pageerror',lambda e:errors.append(str(e)));p.wait_for_timeout(150)
    g=p.locator('.today-cards').evaluate('''parent=>{const r=parent.getBoundingClientRect();return {bounds:{left:r.left,right:r.right},cards:[...parent.querySelectorAll('.today-person')].map(n=>{const c=n.getBoundingClientRect();return {name:n.innerText,left:c.left,right:c.right,width:c.width,title:n.title}})}}''')
    record(str(w)+' 최대4자 당직자 칸 내부',all(c['left']>=g['bounds']['left'] and c['right']<=g['bounds']['right']+1 for c in g['cards']),g)
    p.screenshot(path=str(OUT/f'name-four-{w}.png'),animations='disabled');ctx.close()
   browser.close()
  record('브라우저 오류 없음',not errors,errors)
except Exception:errors.append(traceback.format_exc());traceback.print_exc()
finally:
 if runtime:runtime.close()
 (OUT/'evidence.json').write_text(json.dumps(dict(checks=checks,errors=errors),ensure_ascii=False,indent=2))
if errors or any(not c['passed'] for c in checks):raise SystemExit(1)
