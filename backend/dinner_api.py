"""HTTP boundary for dinner polls; capabilities never enter public state or URLs."""
from contextlib import contextmanager
from functools import lru_cache
import hmac
import sqlite3

from fastapi import Request
from fastapi.responses import JSONResponse

from . import dinner_schema as schema
from . import dinner_service as service
from .request_access_api import HostNamespace
from .request_access_schema import AccessError


class DinnerAPI:
    def __init__(self, host):
        self.h = host

    def admin(self, request, *, required=False):
        """An ADMIN process without a password is trusted only from its own PC."""
        token = request.headers.get('x-admin-token')
        password = self.h.ADMIN_PASSWORD
        if required or token:
            self.h.require_admin(token)
            if not password and not self.h.is_loopback_client(request):
                raise AccessError('FORBIDDEN', '원격 관리자 접속에는 관리자 암호가 필요합니다.', 403)
            return True
        if self.h.APP_MODE != 'ADMIN':
            return False
        if password:
            return bool(token and hmac.compare_digest(token, password))
        return self.h.is_loopback_client(request)

    @contextmanager
    def database(self, *, write=False):
        connection = None
        try:
            connection = self.h.get_db_connection(readonly=not write)
            cur = connection.cursor()
            cur.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
            if write:
                self.h.require_holiday_write_contract(cur)
                # The first upgraded process can be a VIEWER opening old data.
                self.h.ensure_change_request_schema(cur)
                schema.ensure_schema(cur)
            yield cur
            if write:
                connection.commit()
            else:
                connection.rollback()
        except sqlite3.Error:
            if connection is not None:
                connection.rollback()
            raise AccessError('STORAGE_UNAVAILABLE',
                              '공유 자료를 처리하지 못했습니다. 연결 후 다시 확인해 주세요.',
                              503, True) from None
        except Exception:
            if connection is not None:
                connection.rollback()
            raise
        finally:
            if connection is not None:
                connection.close()

    def call(self, request, method, *args, write=False, admin_required=False):
        duty_changed = False
        with self.database(write=write) as cur:
            # Recheck process mode and administrator credentials while owning the
            # transaction; domain methods separately recheck CA term and binding.
            admin = self.admin(request, required=admin_required)
            def confirm_duty(cursor, poll, candidate, body):
                nonlocal duty_changed
                result = self.h.confirm_dinner_duty(cursor, poll, candidate, body)
                duty_changed = True
                return result
            key = request.headers.get('x-onair-dinner-key', '')
            peer_response_hash = None
            if method in ('summary', 'detail', 'respond') and request.headers.get('x-onair-local-session'):
                @lru_cache(maxsize=4)
                def peer_response_hash(dataset_id, name):
                    return self.h.REQUEST_ACCESS.peer_dinner_response_hash(
                        request, dataset_id, name, key)
            domain = service.DinnerService(cur, key=key, admin=admin, confirm_duty=confirm_duty,
                                          peer_response_hash=peer_response_hash)
            result = getattr(domain, method)(*args)
            if method in ('manage', 'detail'):
                result['dutyDataVersion'] = self.h.read_data_version(cur)
        if write:
            self.h.broadcast_from_thread({'type': 'dinner_updated'})
        if duty_changed:
            self.h.broadcast_from_thread({'event': 'DATA_SYNCED',
                                          'origin': request.headers.get('x-client-id', '')})
        return result


def install(app, host):
    """Install before the root StaticFiles mount; accepts a live module globals dict."""
    existing = getattr(app.state, 'dinner_api', None)
    if existing is not None:
        return existing
    if isinstance(host, dict):
        host = HostNamespace(host)
    api = DinnerAPI(host)
    app.state.dinner_api = api

    if AccessError not in app.exception_handlers:
        @app.exception_handler(AccessError)
        async def dinner_error(request, error):
            return JSONResponse(error.as_dict(), status_code=error.status,
                                headers={'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def private_dinner_responses(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith('/api/dinner'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Referrer-Policy'] = 'no-referrer'
        return response

    @app.get('/api/dinner/summary')
    def summary(request: Request, name: str = ''):
        return api.call(request, 'summary', name)

    @app.get('/api/dinner/manage')
    def manage(request: Request):
        return api.call(request, 'manage')

    @app.get('/api/dinner/polls/{poll_id}')
    def detail(request: Request, poll_id: str, name: str = ''):
        return api.call(request, 'detail', poll_id, name)

    @app.post('/api/dinner/role')
    def role(request: Request, body: dict):
        return api.call(request, 'set_role', body, write=True, admin_required=True)

    @app.post('/api/dinner/connections')
    def connection(request: Request, body: dict):
        return api.call(request, 'request_connection', body, write=True)

    @app.post('/api/dinner/connections/{connection_id}/decision')
    def connection_decision(request: Request, connection_id: str, body: dict):
        return api.call(request, 'decide_connection', connection_id, body,
                        write=True, admin_required=True)

    @app.post('/api/dinner/polls')
    def create_poll(request: Request, body: dict):
        return api.call(request, 'create_poll', body, write=True)

    @app.patch('/api/dinner/polls/{poll_id}')
    def patch_poll(request: Request, poll_id: str, body: dict):
        return api.call(request, 'patch_poll', poll_id, body, write=True)

    @app.post('/api/dinner/polls/{poll_id}/action')
    def action(request: Request, poll_id: str, body: dict):
        return api.call(request, 'action', poll_id, body, write=True)

    @app.post('/api/dinner/polls/{poll_id}/response')
    def respond(request: Request, poll_id: str, body: dict):
        return api.call(request, 'respond', poll_id, body, write=True)

    return api
