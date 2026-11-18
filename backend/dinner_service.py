"""Dinner polls, approved CA capabilities and receipt-owned employee responses.

The HTTP adapter owns BEGIN IMMEDIATE/commit. This module never stores a raw key
and builds public projections explicitly instead of returning database rows.
"""
from datetime import date, datetime, timedelta, timezone
import copy
import hashlib
import hmac
import json
import re
import secrets
import uuid

from .request_access_schema import AccessError, read_dataset_identity

KST = timezone(timedelta(hours=9), 'Asia/Seoul')
ANSWERS = frozenset(('possible', 'maybe', 'difficult'))


def fail(detail, status=400, code='INVALID_INPUT'):
    raise AccessError(code, detail, status)


def pack(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def text(value, label, limit, empty=False):
    if not isinstance(value, str) or len(value) > limit or (not empty and not value.strip()):
        fail(f'{label}을(를) 확인해 주세요.')
    return value.strip()


def day(value, label='날짜'):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        fail(f'{label}을 확인해 주세요.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        fail(f'{label}을 확인해 주세요.')


def moment(value):
    if not isinstance(value, str) or len(value) > 40:
        fail('마감 시각을 확인해 주세요.')
    try:
        if not re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}', value):
            raise ValueError()
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return result.replace(tzinfo=KST) if result.tzinfo is None else result.astimezone(KST)
    except ValueError:
        fail('마감 시각을 확인해 주세요.')


class DinnerService:
    def __init__(self, cur, key='', admin=False, now=None, confirm_duty=None, peer_response_hashes=None, *, master=False):
        self.cur = cur
        self.admin = bool(admin)
        self.master = bool(master)
        self.key = key if isinstance(key, str) else ''
        self.key_hash = hashlib.sha256(self.key.encode()).hexdigest() if self.key else ''
        self.now = now or datetime.now(KST)
        if self.now.tzinfo is None:
            self.now = self.now.replace(tzinfo=KST)
        self.now = self.now.astimezone(KST)
        self.identity = read_dataset_identity(cur)
        self.confirm_duty = confirm_duty
        self.peer_response_hashes = peer_response_hashes

    def _exists(self):
        return bool(self.cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dinner_polls'").fetchone())

    def _key(self):
        if not 32 <= len(self.key) <= 512 or not re.fullmatch(r'[A-Za-z0-9_\-]+', self.key):
            fail('이 PC의 개인 키가 필요합니다. 화면을 다시 열어 주세요.', 403, 'FORBIDDEN')
        return self.key_hash

    def _stamp(self):
        return self.now.isoformat(timespec='seconds')

    def _roles(self):
        # Read-only upgraded clients can encounter v39 storage before migration.
        rows = self.cur.execute('SELECT * FROM dinner_roles').fetchall()
        if self.cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dinner_appointments'").fetchone():
            rows += self.cur.execute('SELECT * FROM dinner_appointments ORDER BY id').fetchall()
        members, today = self._members(), self.now.date().isoformat()
        return [dict(id='ca', roleId=row['id'], memberName=row['member_name'], startDate=row['start_date'],
                     endDate=row['end_date'], revision=row['revision'],
                     active=bool(row['member_name'] in members and row['start_date'] <= today <= row['end_date']))
                for row in rows]

    def _role(self, role_id=None, member_name=None):
        roles = self._roles()
        if role_id is not None:
            return next((r for r in roles if r['roleId'] == role_id), None)
        candidates = [r for r in roles if r['memberName'] in self._members()
                      and (member_name is None or r['memberName'] == member_name)]
        active = next((r for r in candidates if r['active']), None)
        future = sorted((r for r in candidates if r['startDate'] > self.now.date().isoformat()),
                        key=lambda r: (r['startDate'], r['roleId']))
        if active or future:
            return active or future[0]
        if member_name is not None:
            return None
        return next((r for r in roles if r['roleId'] == 1), None) or (roles[-1] if roles else None)

    def _role_id(self, value):
        if type(value) is not int or (value != 1 and not 1900 <= value <= 9999):
            fail('CA 임명 연도를 확인해 주세요.')
        return value

    def _connection(self, role_id=None):
        if not self.key_hash:
            return None
        if role_id is None:
            role = self._role()
            role_id = role['roleId'] if role else None
        rows = self.cur.execute('SELECT * FROM dinner_connections WHERE key_hash=? ORDER BY rowid DESC',
                                (self.key_hash,)).fetchall()
        return next((dict(r) for r in rows if role_id is None or dict(r).get('role_id', 1) == role_id), None)

    def _connection_valid(self, row, role):
        return bool(row and role and role['memberName'] in self._members() and row['member_name'] == role['memberName']
                    and row.get('role_id', 1) == role['roleId']
                    and row['role_revision'] == role['revision']
                    and row['dataset_id'] == self.identity.dataset_id
                    and row['generation'] == self.identity.generation)

    def _public_connection(self, row):
        role_id = row.get('role_id', 1)
        role = self._role(role_id)
        status = row['status'] if self._connection_valid(row, role) else 'INVALIDATED'
        return dict(id=row['id'], status=status, memberName=row['member_name'],
                    roleId=role_id, active=bool(role and role['active'] and status == 'APPROVED'),
                    startDate=role['startDate'] if role else '', endDate=role['endDate'] if role else '',
                    verificationCode=row['verification_code'], deviceLabel=row['device_label'],
                    reason=row['reason'], createdAt=row['created_at'])

    def _manager(self, required=True):
        if self.master:
            return True
        row, role = self._connection(), self._role()
        permitted = bool(self._connection_valid(row, role) and role['active'] and row['status'] == 'APPROVED')
        if required and not permitted:
            fail('최고 관리자 인증 또는 임기 중인 CA의 승인된 PC 연결이 필요합니다.', 403, 'FORBIDDEN')
        return permitted

    def _handoff(self):
        today = self.now.date()
        if not self.key_hash or today.month not in (12, 1):
            return None
        year = today.year if today.month == 12 else today.year - 1
        december_end, january_start = f'{year:04d}-12-31', f'{year + 1:04d}-01-01'
        roles = self._roles()
        outgoing = next((r for r in roles if r['startDate'] <= december_end <= r['endDate']), None)
        incoming = next((r for r in roles if r['startDate'] <= january_start <= r['endDate']), None)
        if not outgoing or not incoming or outgoing['memberName'] == incoming['memberName']:
            return None
        active, reader = (outgoing, incoming) if today.month == 12 else (incoming, outgoing)
        if not active['active']:
            return None
        connection = self._connection(reader['roleId'])
        if self._connection_valid(connection, reader) and connection['status'] == 'APPROVED':
            return dict(memberName=reader['memberName'], startDate=f'{year:04d}-12-01',
                        endDate=f'{year + 1:04d}-01-31')
        return None

    def _admin(self):
        if not self.admin:
            fail('관리자만 처리할 수 있습니다.', 403, 'FORBIDDEN')

    def _members(self):
        return [r[0] for r in self.cur.execute('SELECT name FROM members ORDER BY id')]

    def _expected(self, body, revision):
        value = body.get('expectedRevision')
        if type(value) is not int:
            fail('수정 기준 정보가 없습니다. 새로고침 후 다시 시도해 주세요.', 428, 'REVISION_REQUIRED')
        if value != revision:
            fail('다른 PC에서 먼저 변경했습니다. 최신 내용을 확인해 주세요.', 409, 'REVISION_CONFLICT')

    def _operation(self, kind, body, *, principal=None):
        operation_id = body.get('operationId')
        if not isinstance(operation_id, str) or not 8 <= len(operation_id) <= 128:
            fail('처리 번호를 확인해 주세요.')
        principal = principal or ('admin' if self.admin else self._key())
        scope = self.identity.dataset_id + ':' + self.identity.generation + ':' + principal
        try:
            digest = hashlib.sha256(pack([kind, {k:v for k,v in body.items() if k != 'operationId'}]).encode()).hexdigest()
        except (ValueError, TypeError):
            fail('요청 내용을 확인해 주세요.')
        old = self.cur.execute('SELECT * FROM dinner_operations WHERE scope=? AND operation_id=?',
                               (scope, operation_id)).fetchone()
        if old:
            if not hmac.compare_digest(old['input_digest'], digest):
                fail('같은 처리 번호의 내용이 다릅니다.', 409, 'OPERATION_CONFLICT')
            return scope, operation_id, digest, json.loads(old['result_json'])
        return scope, operation_id, digest, None

    def _manager_operation(self, kind, body):
        # Recheck authority before looking up any saved poll/result response.
        self._manager()
        # Master can safely retry a pre-upgrade operator receipt under its
        # original scope. CA sessions continue to use their device principal.
        return self._operation(kind, body, principal='admin' if self.master else self._key())

    def _finish(self, op, result, action, subject):
        self.cur.execute('INSERT INTO dinner_operations VALUES(?,?,?,?,?)',
                         (op[0], op[1], op[2], pack(result), self._stamp()))
        if op[0].endswith(':admin'):
            actor = 'MASTER' if self.master else 'ADMIN'
        else:
            actor = (self._connection() or {}).get('member_name', 'EMPLOYEE')
        self.cur.execute('INSERT INTO dinner_audit VALUES(?,?,?,?,?)',
                         (str(uuid.uuid4()), action, subject, actor, self._stamp()))
        return result

    def _poll(self, poll_id):
        row = self.cur.execute('SELECT data_json FROM dinner_polls WHERE id=?', (poll_id,)).fetchone()
        if row is None:
            fail('회식 투표를 찾을 수 없습니다.', 404, 'NOT_FOUND')
        return json.loads(row[0])

    def _status(self, poll):
        return 'CLOSED' if poll['status'] == 'OPEN' and self.now >= moment(poll['deadline']) else poll['status']

    def _published(self, poll):
        # CANCELLED alone cannot prove publication: a private draft can also be
        # cancelled. For additive pre-marker records, only states reached by
        # publishing establish visibility; ambiguous cancellations stay private.
        return bool(poll.get('publishedAt')) or ('publishedAt' not in poll and
                                                poll['status'] in ('OPEN', 'CLOSED', 'CONFIRMED'))

    def _handoff_poll_visible(self, poll, handoff):
        if not self._published(poll):
            return False
        dates = [candidate['date'] for candidate in poll['candidates']]
        if poll['status'] in ('OPEN', 'CLOSED') and any(d >= self.now.date().isoformat() for d in dates):
            return True
        dates.extend(timestamp[:10] for timestamp in (poll.get('publishedAt'), poll['deadline']) if timestamp)
        return any(handoff['startDate'] <= d <= handoff['endDate'] for d in dates)

    def _save(self, poll):
        self.cur.execute('UPDATE dinner_polls SET data_json=? WHERE id=?', (pack(poll), poll['id']))

    def _rows(self, poll_id):
        return {r['member_name']: dict(r) for r in self.cur.execute('SELECT * FROM dinner_responses WHERE poll_id=?', (poll_id,))}

    def _answers(self, poll, row):
        saved = json.loads(row['answers_json'])
        return {c['id']: saved[c['id']]['value'] for c in poll['candidates']
                if c['id'] in saved and saved[c['id']]['revision'] == c['revision']}

    def _results(self, poll):
        rows = self._rows(poll['id'])
        totals = {c['id']: dict(id=c['id'], possible=0, maybe=0, difficult=0, unanswered=0) for c in poll['candidates']}
        responders, nonresponders, reconfirm = [], [], []
        for name in poll['targets']:
            row = rows.get(name)
            answers = self._answers(poll, row) if row else {}
            for cid, result in totals.items():
                result[answers.get(cid, 'unanswered')] += 1
            if row:
                complete = len(answers) == len(poll['candidates'])
                responders.append(dict(name=name, complete=complete, answers=answers, suggestion=row['suggestion']))
                if not complete:
                    reconfirm.append(name)
            else:
                nonresponders.append(name)
        return dict(candidates=list(totals.values()), responders=responders,
                    nonresponders=nonresponders, confirmationRequired=reconfirm)

    def _owns_response(self, row):
        if not self.key_hash:
            return False
        if hmac.compare_digest(row['key_hash'], self.key_hash):
            return True
        if not self.peer_response_hashes:
            return False
        peers = self.peer_response_hashes(self.identity.dataset_id, row['member_name'])
        return any(hmac.compare_digest(row['key_hash'], peer) for peer in peers)

    def _response_version(self, row):
        if not row:
            return None
        return hashlib.sha256(pack([row['answers_json'], row['suggestion']]).encode()).hexdigest()

    def _project(self, poll, name='', results=False):
        public_fields = ('id', 'title', 'description', 'deadline', 'status', 'revision',
                         'candidates', 'targets', 'selectedCandidateId', 'venue', 'notice', 'createdAt')
        out = {field: copy.deepcopy(poll[field]) for field in public_fields}
        out['status'] = self._status(poll)
        row = self._rows(poll['id']).get(name) if name in poll['targets'] else None
        valid = self._answers(poll, row) if row else {}
        needed = [c['id'] for c in poll['candidates'] if c['id'] not in valid] if row else []
        out.update(responded=bool(row), ownResponse=None, needsConfirmation=bool(needed),
                   confirmationCandidateIds=needed)
        if row and self._owns_response(row):
            out['ownResponse'] = dict(answers=valid, suggestion=row['suggestion'], version=self._response_version(row))
        if results:
            out['results'] = self._results(poll)
        if isinstance(poll.get('_dutyEffect'), dict):
            # Historical application receipt, never the private original rows.
            effect = poll['_dutyEffect']
            out['dutyEffect'] = {field: copy.deepcopy(effect[field]) for field in
                ('date', 'memo', 'noDuty', 'appliedAt', 'dataVersion', 'snapshot') if field in effect}
        return out

    def _event(self, poll, kind, names=None, notice=''):
        labels = {'PUBLISHED':'응답을 기다립니다.', 'UPDATED':'후보가 변경되어 다시 확인해 주세요.',
                  'CLOSED':'응답이 마감되었습니다.', 'EXTENDED':'응답 기한이 연장되었습니다.',
                  'CONFIRMED':'최종 일정이 확정되었습니다.', 'CANCELLED':'취소되었습니다.',
                  'REMIND':'아직 응답하지 않으셨습니다. 일정을 확인해 주세요.'}
        for name in poll['targets'] if names is None else names:
            self.cur.execute('INSERT INTO dinner_events VALUES(?,?,?,?,?,?)',
                (str(uuid.uuid4()), poll['id'], name, kind, f"{poll['title']}: {notice or labels[kind]}", self._stamp()))

    def summary(self, name):
        name = text(name, '이름', 100, empty=True)
        empty = dict(datasetKey=self.identity.dataset_id, role=None, roles=[], assignedRole=None,
                     connection=None, ownConnections={}, handoff=None, polls=[], events=[], pendingConnections=[],
                     canManagePolls=self.master, canManageRoles=self.admin)
        if not self._exists():
            return empty
        role, assigned = self._role(), self._role(member_name=name)
        connection = self._connection(assigned['roleId']) if assigned else self._connection()
        own_connections = {}
        if self.key_hash and name:
            rows = self.cur.execute('SELECT * FROM dinner_connections WHERE key_hash=? AND member_name=? ORDER BY rowid DESC',
                                    (self.key_hash, name)).fetchall()
            for row in rows:
                role_id = str(dict(row).get('role_id', 1))
                if role_id not in own_connections:
                    own_connections[role_id] = self._public_connection(dict(row))
        polls = []
        for row in self.cur.execute('SELECT data_json FROM dinner_polls ORDER BY rowid DESC').fetchall():
            poll = json.loads(row[0])
            if name in poll['targets'] and self._published(poll):
                polls.append(self._project(poll, name))
        events = [dict(id=r['id'], pollId=r['poll_id'], type=r['event_type'], text=r['message'], createdAt=r['created_at'])
                  for r in self.cur.execute('SELECT * FROM dinner_events WHERE member_name=? ORDER BY rowid DESC LIMIT 200', (name,))]
        pending = []
        if self.admin:
            pending = [self._public_connection(dict(r)) for r in self.cur.execute("SELECT * FROM dinner_connections WHERE status='PENDING' ORDER BY rowid DESC").fetchall()
                       if self._connection_valid(dict(r), self._role(dict(r).get('role_id', 1)))
                       and self._role(dict(r).get('role_id', 1))['endDate'] >= self.now.date().isoformat()]
        manager = self._manager(required=False)
        handoff = self._handoff() if name and not manager else None
        if handoff and handoff['memberName'] != name:
            handoff = None
        return dict(datasetKey=self.identity.dataset_id, role=role, roles=self._roles(), assignedRole=assigned,
                    connection=self._public_connection(connection) if connection else None,
                    ownConnections=own_connections, handoff=handoff,
                    polls=polls, events=events, pendingConnections=pending,
                    canManagePolls=manager, canManageRoles=self.admin)

    def _all_connections(self):
        rows = self.cur.execute('SELECT * FROM dinner_connections ORDER BY rowid DESC').fetchall()
        return [self._public_connection(dict(row)) for row in rows]

    def role_management(self):
        self._admin()
        return dict(role=self._role(), roles=self._roles(), members=self._members(),
                    connections=self._all_connections())

    def manage(self):
        manager = self._manager(required=False)
        handoff = None if manager else self._handoff()
        if not manager and not handoff:
            fail('최고 관리자 인증 또는 조회 권한이 있는 CA의 승인된 PC 연결이 필요합니다.', 403, 'FORBIDDEN')
        connections = self._all_connections() if self.admin else []
        polls = []
        for row in self.cur.execute('SELECT data_json FROM dinner_polls ORDER BY rowid DESC').fetchall():
            poll = json.loads(row[0])
            if manager or self._handoff_poll_visible(poll, handoff):
                polls.append(self._project(poll, results=True))
        return dict(role=self._role(), roles=self._roles(), connections=connections, handoff=handoff,
                    polls=polls, members=self._members() if manager else [],
                    canManagePolls=manager, canManageRoles=self.admin)

    def detail(self, poll_id, name=''):
        name = text(name, '이름', 100, empty=True)
        poll = self._poll(poll_id)
        manager = self._manager(required=False)
        if not manager and (not self._published(poll) or name not in poll['targets']):
            fail('이 투표의 응답 대상이 아닙니다.', 403, 'FORBIDDEN')
        return dict(poll=self._project(poll, name, results=manager))

    def set_role(self, body):
        self._admin()
        op = self._operation('role', body)
        if op[3] is not None:
            return op[3]
        role_id = self._role_id(body.get('roleId', 1))
        previous = self._role(role_id)
        self._expected(body, previous['revision'] if previous else 0)
        name = text(body.get('memberName'), 'CA 이름', 100, empty=True)
        if name:
            if name not in self._members():
                fail('현재 구성원 중 CA를 선택해 주세요.')
            start, end = day(body.get('startDate'), '임기 시작일'), day(body.get('endDate'), '임기 종료일')
            if start > end or end < self.now.date():
                fail('CA 임기를 확인해 주세요.')
            start, end = start.isoformat(), end.isoformat()
            if any(r['roleId'] != role_id and r['memberName'] and r['startDate'] <= end and start <= r['endDate']
                   for r in self._roles()):
                fail('다른 CA 임명과 임기가 겹칩니다. 시작일과 종료일을 확인해 주세요.', 409, 'ROLE_OVERLAP')
        else:
            start = end = ''
        revision = (previous['revision'] if previous else 0) + 1
        table = 'dinner_roles' if role_id == 1 else 'dinner_appointments'
        self.cur.execute(f'INSERT INTO {table} VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET member_name=excluded.member_name,start_date=excluded.start_date,end_date=excluded.end_date,revision=excluded.revision',
                         (role_id, name, start, end, revision))
        self.cur.execute("UPDATE dinner_connections SET status='INVALIDATED',reason='CA 임명이 변경되었습니다.' WHERE role_id=? AND status IN ('PENDING','APPROVED')", (role_id,))
        return self._finish(op, dict(role=self._role(role_id), roles=self._roles()), 'ROLE_CHANGED', str(role_id))

    def request_connection(self, body):
        self._key()
        name = text(body.get('memberName'), 'CA 이름', 100)
        role = self._role(self._role_id(body['roleId'])) if 'roleId' in body else self._role(member_name=name)
        if not role or role['memberName'] != name or name not in self._members() or role['endDate'] < self.now.date().isoformat():
            fail('현재 임명된 CA만 사용 연결을 요청할 수 있습니다.', 403, 'FORBIDDEN')
        op = self._operation('connection', body)
        if op[3] is not None:
            return op[3]
        existing = self._connection(role['roleId'])
        if self._connection_valid(existing, role) and existing['status'] in ('PENDING', 'APPROVED'):
            return self._finish(op, dict(connection=self._public_connection(existing)), 'CONNECTION_REOPENED', existing['id'])
        label = text(body.get('deviceLabel', ''), 'PC 표시 이름', 120, empty=True) or '이 PC'
        cid = str(uuid.uuid4())
        self.cur.execute('INSERT INTO dinner_connections(id,key_hash,member_name,role_revision,dataset_id,generation,status,verification_code,device_label,created_at,role_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                         (cid, self.key_hash, name, role['revision'], self.identity.dataset_id, self.identity.generation,
                          'PENDING', str(secrets.randbelow(1000000)).zfill(6), label, self._stamp(), role['roleId']))
        return self._finish(op, dict(connection=self._public_connection(self._connection(role['roleId']))), 'CONNECTION_REQUESTED', cid)

    def decide_connection(self, connection_id, body):
        self._admin()
        op = self._operation('decision:' + connection_id, body)
        if op[3] is not None:
            return op[3]
        row = self.cur.execute('SELECT * FROM dinner_connections WHERE id=?', (connection_id,)).fetchone()
        if row is None:
            fail('사용 연결 요청을 찾을 수 없습니다.', 404, 'NOT_FOUND')
        row = dict(row)
        role = self._role(row.get('role_id', 1))
        if not self._connection_valid(row, role) or row['status'] != 'PENDING' or role['endDate'] < self.now.date().isoformat():
            fail('이미 처리되었거나 임명이 변경된 연결 요청입니다.', 409, 'STATE_CHANGED')
        decision = body.get('decision')
        if decision not in ('APPROVE', 'REJECT'):
            fail('승인 또는 거절을 선택해 주세요.')
        reason = text(body.get('reason', ''), '처리 사유', 1000, empty=True)
        status = 'APPROVED' if decision == 'APPROVE' else 'REJECTED'
        self.cur.execute('UPDATE dinner_connections SET status=?,reason=?,decided_at=? WHERE id=?',
                         (status, reason, self._stamp(), connection_id))
        row.update(status=status, reason=reason)
        return self._finish(op, dict(connection=self._public_connection(row)), 'CONNECTION_' + status, connection_id)

    def _fields(self, body, old=None):
        poll = copy.deepcopy(old) if old else {}
        for field, label, maximum, allow_empty in [('title','제목',200,False), ('description','안내',4000,True)]:
            if field in body or old is None:
                poll[field] = text(body.get(field, ''), label, maximum, empty=allow_empty)
        if 'deadline' in body or old is None:
            deadline = moment(body.get('deadline'))
            if deadline <= self.now:
                fail('마감 시각은 현재보다 이후여야 합니다.')
            poll['deadline'] = deadline.isoformat()
        if 'targets' in body or old is None:
            targets = body.get('targets')
            if not isinstance(targets, list) or not targets or len(targets) > 5000 or not all(isinstance(n, str) for n in targets):
                fail('응답 대상을 한 명 이상 선택해 주세요.')
            if len(set(targets)) != len(targets) or not set(targets) <= set(self._members()):
                fail('현재 구성원에서 중복 없이 응답 대상을 선택해 주세요.')
            if old and old['status'] != 'DRAFT' and set(targets) != set(old['targets']):
                fail('게시 후에는 응답 대상을 변경할 수 없습니다.', 409, 'STATE_CHANGED')
            poll['targets'] = list(targets)
        if 'candidates' in body or old is None:
            candidates = body.get('candidates')
            if not isinstance(candidates, list) or not 2 <= len(candidates) <= 5:
                fail('후보는 2개에서 5개까지 지정해 주세요.')
            previous = {c['id']:c for c in old['candidates']} if old else {}
            # Keep retired candidate versions privately. Reusing a removed ID
            # must not make an old response look confirmed again (ABA change).
            versions = dict(old.get('_candidateVersions', {})) if old else {}
            versions.update({cid: item['revision'] for cid, item in previous.items()})
            rebuilt, ids, slots = [], set(), set()
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    fail('후보 날짜와 시각을 확인해 주세요.')
                cid = text(candidate.get('id') or str(uuid.uuid4()), '후보 번호', 100)
                candidate_day = day(candidate.get('date'))
                clock = candidate.get('time')
                if not isinstance(clock, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', clock):
                    fail('후보 시각을 확인해 주세요.')
                if candidate_day < self.now.date():
                    fail('후보 날짜는 오늘 이후로 지정해 주세요.')
                slot = (candidate_day.isoformat(), clock)
                if cid in ids or slot[0] in slots:
                    fail('후보 날짜는 서로 다른 날로 지정해 주세요.')
                ids.add(cid); slots.add(slot[0])
                prior = previous.get(cid)
                revision = prior['revision'] if prior else versions.get(cid, 0) + 1
                if prior and (prior['date'], prior['time']) != slot:
                    revision += 1
                rebuilt.append(dict(id=cid, date=slot[0], time=clock, revision=revision))
                versions[cid] = revision
            poll['candidates'] = rebuilt
            poll['_candidateVersions'] = versions
        self._deadline_before_candidates(poll, moment(poll['deadline']))
        return poll

    def _deadline_before_candidates(self, poll, deadline):
        if any(deadline >= moment(f"{candidate['date']}T{candidate['time']}")
               for candidate in poll['candidates']):
            fail('응답 마감은 모든 후보 날짜와 시각보다 앞서야 합니다.')

    def create_poll(self, body):
        op = self._manager_operation('create', body)
        if op[3] is not None:
            return op[3]
        poll = self._fields(body)
        poll.update(id=str(uuid.uuid4()), status='DRAFT', revision=1, selectedCandidateId=None,
                    venue='', notice='', createdAt=self._stamp(), publishedAt=None)
        self.cur.execute('INSERT INTO dinner_polls VALUES(?,?,?)', (poll['id'], pack(poll), self._stamp()))
        return self._finish(op, dict(poll=self._project(poll, results=True)), 'DRAFT_CREATED', poll['id'])

    def patch_poll(self, poll_id, body):
        op = self._manager_operation('patch:' + poll_id, body)
        if op[3] is not None:
            return op[3]
        original = self._poll(poll_id)
        self._expected(body, original['revision'])
        if self._status(original) not in ('DRAFT', 'OPEN'):
            fail('마감 또는 종료된 투표는 수정할 수 없습니다.', 409, 'STATE_CHANGED')
        poll = self._fields(body, original)
        poll['revision'] += 1
        self._save(poll)
        if original['status'] == 'OPEN' and original['candidates'] != poll['candidates']:
            self._event(poll, 'UPDATED')
        return self._finish(op, dict(poll=self._project(poll, results=True)), 'POLL_UPDATED', poll_id)

    def action(self, poll_id, body):
        op = self._manager_operation('action:' + poll_id, body)
        if op[3] is not None:
            return op[3]
        poll = self._poll(poll_id)
        self._expected(body, poll['revision'])
        action, status = body.get('action'), self._status(poll)
        event = None
        if action == 'PUBLISH' and status == 'DRAFT':
            self._fields(poll)
            poll['status'], event = 'OPEN', 'PUBLISHED'
            poll['publishedAt'] = self._stamp()
        elif action == 'CLOSE' and poll['status'] == 'OPEN':
            poll['status'], event = 'CLOSED', 'CLOSED'
        elif action == 'EXTEND' and status in ('OPEN', 'CLOSED'):
            deadline = moment(body.get('deadline'))
            if deadline <= self.now or deadline <= moment(poll['deadline']):
                fail('새 마감 시각은 기존 마감과 현재 시각보다 이후여야 합니다.')
            self._deadline_before_candidates(poll, deadline)
            poll['deadline'] = deadline.isoformat()
            poll['status'], event = 'OPEN', 'EXTENDED'
        elif action == 'CONFIRM' and status in ('OPEN', 'CLOSED'):
            selected = body.get('selectedCandidateId')
            if selected not in {c['id'] for c in poll['candidates']}:
                fail('확정할 후보를 선택해 주세요.')
            poll.update(status='CONFIRMED', selectedCandidateId=selected,
                        venue=text(body.get('venue', ''), '장소', 300, empty=True),
                        notice=text(body.get('notice', ''), '공지', 2000, empty=True))
            if self.confirm_duty is not None:
                candidate = next(c for c in poll['candidates'] if c['id'] == selected)
                poll['_dutyEffect'] = self.confirm_duty(self.cur, poll, candidate, body)
            event = 'CONFIRMED'
        elif action == 'CANCEL' and status != 'CANCELLED':
            poll['status'] = 'CANCELLED'
            poll['notice'] = text(body.get('notice', ''), '취소 안내', 2000, empty=True)
            event = 'CANCELLED' if status != 'DRAFT' else None
        elif action == 'REMIND' and status == 'OPEN':
            notice = text(body.get('notice', ''), '알림 내용', 2000, empty=True)
            results = self._results(poll)
            outstanding = set(results['nonresponders']) | set(results['confirmationRequired'])
            self._event(poll, 'REMIND', [name for name in poll['targets'] if name in outstanding], notice)
        else:
            fail('현재 투표 상태에서는 처리할 수 없습니다.', 409, 'STATE_CHANGED')
        if action != 'REMIND':
            poll['revision'] += 1
            self._save(poll)
        if event:
            self._event(poll, event)
        return self._finish(op, dict(poll=self._project(poll, results=True)), action, poll_id)

    def respond(self, poll_id, body):
        self._key()
        name = text(body.get('memberName'), '응답자', 100)
        poll = self._poll(poll_id)
        if name not in poll['targets']:
            fail('이 투표의 응답 대상이 아닙니다.', 403, 'FORBIDDEN')
        row = self._rows(poll_id).get(name)
        if row and not self._owns_response(row):
            fail('다른 PC에서 제출한 응답입니다. 처음 응답한 PC에서 수정해 주세요.', 403, 'OWNER_MISMATCH')
        op = self._operation('response:' + poll_id, body)
        if op[3] is not None:
            return op[3]
        self._expected(body, poll['revision'])
        if self._status(poll) != 'OPEN':
            fail('응답 기간이 마감되었거나 아직 게시되지 않았습니다.', 409, 'POLL_CLOSED')
        if 'expectedResponseVersion' in body and body['expectedResponseVersion'] != self._response_version(row):
            fail('다른 창에서 응답을 저장했습니다. 최신 응답을 확인한 뒤 다시 수정해 주세요.', 409, 'RESPONSE_CHANGED')
        answers = body.get('answers')
        if not isinstance(answers, dict) or set(answers) != {c['id'] for c in poll['candidates']} or not all(v in ANSWERS if isinstance(v,str) else False for v in answers.values()):
            fail('모든 후보에 가능·미정·어려움 중 하나를 선택해 주세요.')
        suggestion = text(body.get('suggestion', ''), '대안 의견', 2000, empty=True)
        stored = {c['id']:dict(value=answers[c['id']], revision=c['revision']) for c in poll['candidates']}
        self.cur.execute('INSERT INTO dinner_responses VALUES(?,?,?,?,?,?) ON CONFLICT(poll_id,member_name) DO UPDATE SET answers_json=excluded.answers_json,suggestion=excluded.suggestion,updated_at=excluded.updated_at',
                         (poll_id, name, self.key_hash, pack(stored), suggestion, self._stamp()))
        return self._finish(op, dict(poll=self._project(poll, name)), 'RESPONSE_SAVED', poll_id)
