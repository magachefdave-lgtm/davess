"""WLR OnAir 3.18.49 — readable source recovered from supplied 3.14.5 executables.

Backend behavior is restored from Python 3.13 bytecode. Original comments and
repository history are not recoverable. See RECOVERY_REPORT.md and tests.
"""
import os
import sys

if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w')

import json
import copy
import hashlib
import uuid
from contextvars import ContextVar
import re
import csv
import io
import getpass
import shutil
import secrets
import sqlite3
import platform
import logging
from logging.handlers import RotatingFileHandler
import threading
import webbrowser
import configparser
import tempfile
import time
import errno
import stat
import ipaddress
import socket
import anyio.from_thread
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime, timezone, timedelta
from collections import Counter
from pathlib import Path
from urllib.parse import quote, urlsplit
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Header, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from backend import request_access_schema
from backend import request_access_api
from backend import dinner_schema, dinner_api, master_auth

APP_VERSION = '3.18.49'
NL = chr(10)
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
    APP_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

FRONTEND_DIST = os.path.join(BASE_DIR, 'frontend', 'dist')
# runpy injects the compiled launcher's BUILD_ROLE before loading this module.
# An older/unknown frozen launcher can only open a viewer.
if getattr(sys, 'frozen', False):
    DISTRIBUTION_ROLE = globals().get('BUILD_ROLE', 'VIEWER')
    if DISTRIBUTION_ROLE not in ('ADMIN', 'VIEWER', 'MASTER'):
        DISTRIBUTION_ROLE = 'VIEWER'
else:
    DISTRIBUTION_ROLE = os.environ.get('SMART_DUTY_MODE', 'ADMIN').upper()
    if DISTRIBUTION_ROLE not in ('ADMIN', 'VIEWER', 'MASTER'):
        DISTRIBUTION_ROLE = 'ADMIN'
APP_MODE = 'VIEWER' if DISTRIBUTION_ROLE == 'VIEWER' else 'ADMIN'
CONFIG_NAME = f'config_{DISTRIBUTION_ROLE.lower()}.ini'
# Executables change versions; configuration/data/logs remain in a stable profile.
MANAGED_CONFIG = 'WLR_CONFIG_DIR' in os.environ
CONFIG_DIR = os.environ.get('WLR_CONFIG_DIR', APP_DIR)
if not CONFIG_DIR or not os.path.isabs(CONFIG_DIR) or not os.path.isdir(CONFIG_DIR):
    raise RuntimeError('WLR_CONFIG_DIR에는 접근 가능한 절대 폴더 경로를 지정해야 합니다.')
CONFIG_PATH = os.path.join(CONFIG_DIR, CONFIG_NAME)
LEGACY_CONFIG_PATH = os.path.join(CONFIG_DIR, 'config.ini')
if MANAGED_CONFIG and not os.path.isfile(CONFIG_PATH):
    raise RuntimeError(f'프로필 설정이 없습니다. 초기 설정에서 기존 데이터 위치를 지정하세요: {CONFIG_PATH}')
UPDATE_INSTANCE_ID = os.environ.get('WLR_UPDATE_INSTANCE_ID', '')
HEALTH_FILE = os.environ.get('WLR_HEALTH_FILE', '')
if bool(UPDATE_INSTANCE_ID) != bool(HEALTH_FILE):
    raise RuntimeError('업데이트 시작 확인에는 인스턴스 ID와 상태 파일이 함께 필요합니다.')
if HEALTH_FILE and (not os.path.isabs(HEALTH_FILE) or not os.path.isdir(os.path.dirname(HEALTH_FILE)) or os.path.islink(HEALTH_FILE)):
    raise RuntimeError('WLR_HEALTH_FILE에는 접근 가능한 절대 파일 경로를 지정해야 합니다.')
DEFAULT_PORT = {'ADMIN': '8000', 'VIEWER': '8001', 'MASTER': '8002'}[DISTRIBUTION_ROLE]
# v3.16.0 관리자용·조회용을 같은 폴더에 두면 한 로그 파일을 두 프로세스가 열어 Windows 에서
#   회전이 실패했다. 모드별로 파일을 나눈다.
LOG_PATH = os.path.join(CONFIG_DIR, f'smart_duty_{DISTRIBUTION_ROLE.lower()}.log')
logger = logging.getLogger('smartduty')
logger.setLevel(logging.INFO)
try:
    _fh = RotatingFileHandler(LOG_PATH, maxBytes=2097152, backupCount=3, encoding='utf-8')
    _fh.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(_fh)
except Exception:
    pass

CONFIG_DEFAULTS = {
    'shared_folder_path': '', 'server_port': DEFAULT_PORT,
    'db_timeout_seconds': '30.0', 'bind_host': '127.0.0.1',
    'admin_password': '', 'open_browser': 'true', 'tray_icon': 'true',
}
BACKUP_KEEP = 30
SNAPSHOT_KEEP = 10   # 스키마 변경 직전·복원 직전 스냅샷 보관 개수
BACKUP_STATE = {'ok': None, 'detail': '', 'verified': '', 'mirror': '', 'mirrorDetail': ''}
DAILY_BACKUP_RE = re.compile(r'^smart_duty_\d{4}-\d{2}-\d{2}\.db$')
MANUAL_FULL_BACKUP_RE = re.compile(r'^smart_duty_full_(\d{8})_(\d{6})_\d{6}_[0-9a-f]{16}\.db$')
SNAPSHOT_RE = re.compile(r'^(premigration|prerestore|auto)_[0-9A-Za-z_.-]+\.db$')
# v3.18.0 '방금 작업 되돌리기' 용 자동 사본. 자동 배정 확정·신청 승인·백업 파일 불러오기·일괄 작업 직전에 만든다.
AUTO_SNAPSHOT_KEEP = 20
AUTO_SNAPSHOT_KINDS = {
    'assign': '자동 배정 직전', 'approve': '신청 승인 직전', 'import': '백업 파일 불러오기 직전',
    'bulk': '일괄 작업 직전', 'member': '사원 삭제 직전', 'manual': '직접 만든 사본', 'holiday': '공휴일 보완 직전',
    'dinner': '석식간담회 확정 직전',
}
AUTO_SNAPSHOT_RE = re.compile(r'^auto_([a-z]+)_(\d{8})-(\d{6})(?:-\d{3})?\.db$')
SNAPSHOT_KIND_LABELS = {'daily': '하루 첫 저장 전 자동 백업', 'premigration': '구조 변경 직전', 'prerestore': '복원 직전'}
REQUEST_TYPE_LABELS = {'SWAP': '맞교환', 'SUBSTITUTE': '대체 근무', 'ABSENCE': '휴가·부재'}



# Configuration, SQLite persistence and validation recovered from the 3.14.5 binary.


def log_info(msg):
    try:
        logger.info(msg)
    except Exception:
        pass


def log_error(msg, exc=None):
    try:
        logger.error(msg, exc_info=exc is not None)
    except Exception:
        pass


def load_config():
    config = configparser.ConfigParser(interpolation=None)
    existed = os.path.exists(CONFIG_PATH)

    def read_ini(path):
        try:
            config.read(path, encoding='utf-8-sig')
        except (UnicodeDecodeError, configparser.MissingSectionHeaderError):
            config.read(path, encoding='cp949')

    try:
        if existed:
            read_ini(CONFIG_PATH)
        elif DISTRIBUTION_ROLE != 'MASTER' and os.path.exists(LEGACY_CONFIG_PATH):
            read_ini(LEGACY_CONFIG_PATH)
            log_info(f'기존 config.ini 설정을 {CONFIG_NAME} 로 이전합니다.')
    except Exception as e:
        if MANAGED_CONFIG:
            raise RuntimeError(f'프로필 설정을 읽을 수 없습니다: {CONFIG_PATH}') from e
        log_error(f'설정 파일을 읽지 못했습니다({CONFIG_PATH}). 기본값으로 진행합니다: {e}')
        config = configparser.ConfigParser(interpolation=None)
    if 'NETWORK' not in config:
        config['NETWORK'] = {}
    missing = [k for k in CONFIG_DEFAULTS if not config.has_option('NETWORK', k)]
    for k in missing:
        config.set('NETWORK', k, CONFIG_DEFAULTS[k])
    port_fixed = False
    if not existed and APP_MODE == 'VIEWER' and config.get('NETWORK', 'server_port', fallback='') == '8000':
        config.set('NETWORK', 'server_port', '8001')
        port_fixed = True
    if not existed:
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                config.write(f)
            log_info(f'설정 파일을 생성했습니다: {CONFIG_PATH}')
            if DISTRIBUTION_ROLE != 'MASTER' and os.path.exists(LEGACY_CONFIG_PATH):
                try:
                    os.rename(LEGACY_CONFIG_PATH, LEGACY_CONFIG_PATH + '.migrated')
                    log_info('기존 config.ini 를 config.ini.migrated 로 변경했습니다. (더 이상 사용하지 않습니다)')
                except Exception as e:
                    log_error(f'기존 config.ini 이름을 바꾸지 못했습니다(무시하고 진행): {e}')
        except Exception as e:
            log_error(f'설정 파일을 저장하지 못했습니다: {e}')
    elif missing:
        log_info(f"설정 파일에 없는 항목은 기본값을 사용합니다: {', '.join(missing)}")
    if port_fixed:
        log_info('조회 모드 기본 포트를 8001 로 조정했습니다.')
    return config


def _cfg(key, fallback=''):
    try:
        return config_data.get('NETWORK', key, fallback=fallback).strip()
    except Exception:
        return fallback


def probe_storage_writable():
    global STORAGE_WRITABLE, STORAGE_WRITE_DETAIL
    probe = os.path.join(STORAGE_DIR, '.wlr_write_test.tmp')
    try:
        with open(probe, 'w', encoding='utf-8') as fp:
            fp.write('ok')
        os.remove(probe)
        STORAGE_WRITABLE = True
        STORAGE_WRITE_DETAIL = ''
    except Exception as e:
        STORAGE_WRITABLE = False
        STORAGE_WRITE_DETAIL = str(e)
        log_error(f'데이터 폴더에 쓸 수 없습니다({STORAGE_DIR}): {e}')
    return STORAGE_WRITABLE


def build_readonly_uri(path):
    '\n읽기 전용 접속용 URI를 만든다.\n\n네트워크 공유 폴더 경로에 주의해야 한다.\nSQLite 는 URI 의 호스트 자리에 빈 값이나 localhost 만 허용하므로,\n공유 경로는 반드시 슬래시 4개 형태여야 한다.\n    (X) file://192.168.0.10/공유/db      → invalid uri authority 오류\n    (O) file:////192.168.0.10/공유/db\n'
    ap = os.path.abspath(path)
    unc_prefix = chr(92) * 2
    if ap.startswith(unc_prefix):
        rest = ap[2:].replace(chr(92), '/')
        return 'file:////' + quote(rest) + '?mode=ro'
    return Path(ap).as_uri() + '?mode=ro'


def open_readonly(path):
    '\n읽기 전용으로 연결한다.\n  1차: URI 방식(파일 자체를 읽기 전용으로 염)\n  2차: 실패하면 일반 연결 + PRAGMA query_only (쓰기 차단은 동일하게 보장)\n공유 폴더 환경에서 1차가 막히는 경우가 있어 2차를 함께 둔다.\n'
    try:
        conn = sqlite3.connect(build_readonly_uri(path), uri=True, timeout=DB_TIMEOUT)
        conn.execute('SELECT 1')
        return conn
    except Exception as e:
        log_error(f'읽기 전용 URI 연결 실패({e}). 일반 연결 + query_only 로 전환합니다.')
        # mode=rw forbids creating a blank database if the shared file disappears.
        conn = sqlite3.connect(build_readonly_uri(path).replace('?mode=ro', '?mode=rw'),
                               uri=True, timeout=DB_TIMEOUT)
        conn.execute('PRAGMA query_only = ON;')
        conn.execute(f'PRAGMA busy_timeout = {busy_timeout_ms()};')
        return conn


# BEGIN HOLIDAY STORAGE
# Holiday supplement persistence. Integrate these definitions in backend/main.py.
# Existing imports only: json, hashlib, re, sqlite3, date, HTTPException.
# All writes participate in the caller's transaction; no helper commits.

HS_STORAGE_TABLES = ('holiday_supplements', 'holiday_supplement_bases', 'holiday_supplement_operations')
HS_STORAGE_GUARDED_TABLES = (
    'members', 'schedules', 'day_meta', 'absences', 'absence_types',
    'custom_holidays', 'system_rules', 'app_settings', 'change_history',
    'change_requests', 'holiday_supplements', 'holiday_supplement_bases',
    'holiday_supplement_operations',
)
HS_STORAGE_COLUMNS = {
    'holiday_supplements': {'supplement_id', 'date_key', 'year', 'allocation_seq',
        'state', 'credit_state', 'basis_revision', 'pool_key', 'schema_version', 'payload_json'},
    'holiday_supplement_bases': {'year', 'pool_key', 'revision', 'schema_version', 'payload_json'},
    'holiday_supplement_operations': {'operation_id', 'payload_hash', 'action',
        'result_json', 'supplement_id', 'created_at', 'schema_version'},
}
HS_STORAGE_EVENT_FIELDS = {
    'supplement_id', 'date_key', 'year', 'allocation_seq', 'state', 'credit_state',
    'basis_revision', 'pool_key', 'order_snapshot', 'order_fingerprint',
    'annual_basis_hash', 'selected_ordinal', 'end_ordinal', 'original_female_slot',
    'female_credit', 'current_slot_binding', 'original_holiday', 'skipped_candidates', 'created_at',
}
HS_STORAGE_EVENT_OPTIONAL = {'cancelled_at', 'updated_at', 'review_code', 'review_reason'}
HS_STORAGE_BASIS_FIELDS = {
    'year', 'pool_key', 'revision', 'sequence_snapshot', 'prior_year_credit_hash',
    'ordinary_reservation_hash', 'ordinary_tail_position', 'anchor_before_year',
    'effective_boundary', 'policy', 'adopted_at',
}
HS_STORAGE_BASIS_HASH_FIELDS = (
    'year', 'pool_key', 'revision', 'sequence_snapshot', 'prior_year_credit_hash',
    'ordinary_reservation_hash', 'ordinary_tail_position', 'anchor_before_year',
)
HS_STORAGE_PUBLIC_EVENT_FIELDS = HS_STORAGE_EVENT_FIELDS - {'original_holiday', 'skipped_candidates'}
HS_STORAGE_INDEXES = {
    'holiday_supplements_live_date': "CREATE UNIQUE INDEX holiday_supplements_live_date ON holiday_supplements(date_key) WHERE state IN ('ACTIVE','NEEDS_REVIEW')",
    'holiday_supplements_pool': 'CREATE INDEX holiday_supplements_pool ON holiday_supplements(year,pool_key,allocation_seq)',
}


def hs_storage_fail(message, status=400):
    raise HTTPException(status_code=status, detail='공휴일 보완 자료를 확인해 주세요: ' + message)


def hs_storage_object(value, required, optional=()):
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - set(optional):
        hs_storage_fail('지원하지 않거나 불완전한 자료 형식입니다.')


def hs_storage_int(value, minimum=0, maximum=2147483647):
    if type(value) is not int or not minimum <= value <= maximum:
        hs_storage_fail('순번·연도·버전은 유효한 정수여야 합니다.')
    return value


def hs_storage_text(value, allow_empty=False, limit=10000):
    if not isinstance(value, str) or (not allow_empty and not value.strip()) or len(value) > limit:
        hs_storage_fail('문자 자료의 형식이 올바르지 않습니다.')
    return value


def hs_storage_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        hs_storage_fail('날짜 형식이 올바르지 않습니다.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        hs_storage_fail('실제 달력에 없는 날짜입니다.')


def hs_storage_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        hs_storage_fail('기준 해시의 형식이 올바르지 않습니다.')


def hs_storage_year(value):
    hs_storage_int(value, 1, 9999)
    if not 2023 <= value <= 2029:
        hs_storage_fail('공휴일 자료를 지원하는 2023–2029년만 보완할 수 있습니다.')


def hs_storage_names(value, allow_empty=False):
    if not isinstance(value, list) or (not value and not allow_empty):
        hs_storage_fail('고정 순번 목록이 올바르지 않습니다.')
    for name in value:
        hs_storage_text(name, limit=200)


def hs_storage_slot(value):
    hs_storage_object(value, {'name', 'role'}, {'isSubstitute', 'zeroScore'})
    hs_storage_text(value['name'], limit=200)
    hs_storage_text(value['role'], allow_empty=True, limit=200)
    value.setdefault('isSubstitute', False)
    value.setdefault('zeroScore', False)
    if type(value['isSubstitute']) is not bool or type(value['zeroScore']) is not bool:
        hs_storage_fail('근무 속성은 참/거짓이어야 합니다.')


def hs_storage_json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        hs_storage_fail('JSON 자료를 저장할 수 없습니다.')


def hs_storage_digest(value):
    return hashlib.sha256(hs_storage_json(value).encode('utf-8')).hexdigest()


def hs_storage_load_json(value):
    try:
        result = json.loads(value)
        hs_storage_json(result)
        return result
    except (ValueError, TypeError, RecursionError):
        hs_storage_fail('저장된 JSON 자료가 손상되었습니다.', 503)


def hs_storage_validate_operation(value):
    hs_storage_object(value, {'operation_id', 'payload_hash', 'action', 'result', 'supplement_id', 'created_at'})
    operation_id = value['operation_id']
    if not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', operation_id):
        hs_storage_fail('작업 식별자가 올바르지 않습니다.')
    hs_storage_hash(value['payload_hash'])
    if value['action'] not in ('ADD', 'REMOVE', 'LABEL_ONLY', 'NOOP', 'RECONCILE'):
        hs_storage_fail('지원하지 않는 보완 작업입니다.')
    if value['supplement_id'] is not None:
        hs_storage_text(value['supplement_id'], limit=200)
    hs_storage_text(value['created_at'], allow_empty=True, limit=100)
    result = value['result']
    hs_storage_object(result, {'status', 'operationId', 'dataVersion', 'supplementId', 'changedDates', 'snapshot'},
                      {'snapshotLabel'})
    if result['status'] != 'success' or result['operationId'] != operation_id or result['supplementId'] != value['supplement_id']:
        hs_storage_fail('작업 영수증의 식별자 또는 성공 상태가 맞지 않습니다.')
    hs_storage_int(result['dataVersion'])
    if not isinstance(result['changedDates'], list):
        hs_storage_fail('작업 영수증의 변경 날짜가 올바르지 않습니다.')
    for day in result['changedDates']:
        hs_storage_date(day)
    if len(set(result['changedDates'])) != len(result['changedDates']):
        hs_storage_fail('작업 영수증의 변경 날짜가 중복되었습니다.')
    hs_storage_text(result['snapshot'], allow_empty=True, limit=500)
    if 'snapshotLabel' in result:
        hs_storage_text(result['snapshotLabel'], allow_empty=True, limit=500)
    return value


def validate_holiday_supplements(data):
    """Validate a complete private after-state; public all-data is not an import format."""
    hs_storage_object(data, {'schema', 'events', 'bases', 'requiredContract'}, {'operations'})
    if type(data['schema']) is not int or data['schema'] != 1:
        hs_storage_fail('지원하지 않는 보완 자료 버전입니다.')
    hs_storage_int(data['requiredContract'], maximum=1)
    if not isinstance(data['events'], list) or not isinstance(data['bases'], list) or not isinstance(data.get('operations', []), list):
        hs_storage_fail('보완·기준·작업 목록이 올바르지 않습니다.')
    normalized = json.loads(hs_storage_json(data))
    normalized.setdefault('operations', [])
    if (normalized['events'] or normalized['bases']) and normalized['requiredContract'] != 1:
        hs_storage_fail('보완 자료가 있으면 호환 쓰기 계약이 필요합니다.')
    bases = {}
    for basis in normalized['bases']:
        hs_storage_object(basis, HS_STORAGE_BASIS_FIELDS, {'state', 'review_code'})
        # These are derived from live schedules, never authority supplied by an import.
        basis.pop('state', None)
        basis.pop('review_code', None)
        hs_storage_year(basis['year'])
        hs_storage_int(basis['revision'], 1)
        if basis['pool_key'] not in ('holiday|M', 'weekend|M'):
            hs_storage_fail('남성 순번 묶음이 올바르지 않습니다.')
        hs_storage_names(basis['sequence_snapshot'])
        hs_storage_hash(basis['prior_year_credit_hash'])
        hs_storage_hash(basis['ordinary_reservation_hash'])
        hs_storage_int(basis['ordinary_tail_position'], maximum=len(basis['sequence_snapshot']) - 1)
        hs_storage_int(basis['anchor_before_year'], maximum=len(basis['sequence_snapshot']) - 1)
        if basis['effective_boundary'] != '%04d-01-01' % basis['year']:
            hs_storage_fail('연간 기준 경계가 해당 연도의 1월 1일과 다릅니다.')
        if basis['policy'] not in ('ANNUAL_TAIL', 'PRESERVE_EXISTING'):
            hs_storage_fail('지원하지 않는 연간 기준 정책입니다.')
        hs_storage_text(basis['adopted_at'], allow_empty=True, limit=100)
        key = (basis['year'], basis['pool_key'], basis['revision'])
        if key in bases:
            hs_storage_fail('같은 연간 기준 버전이 중복되었습니다.')
        bases[key] = basis
    ids, active_dates, allocations = set(), set(), set()
    for event in normalized['events']:
        hs_storage_object(event, HS_STORAGE_EVENT_FIELDS, HS_STORAGE_EVENT_OPTIONAL)
        hs_storage_text(event['supplement_id'], limit=200)
        day = hs_storage_date(event['date_key'])
        hs_storage_year(event['year'])
        hs_storage_int(event['allocation_seq'], 1)
        hs_storage_int(event['basis_revision'], 1)
        hs_storage_int(event['selected_ordinal'])
        hs_storage_int(event['end_ordinal'], 1)
        if event['year'] != day.year or event['end_ordinal'] != event['selected_ordinal'] + 1:
            hs_storage_fail('대상 연도 또는 소비 순번의 끝 위치가 맞지 않습니다.')
        if event['state'] not in ('ACTIVE', 'CANCELLED', 'NEEDS_REVIEW') or event['credit_state'] not in ('CONSUMED', 'RELEASED'):
            hs_storage_fail('보완 또는 순번 소비 상태가 올바르지 않습니다.')
        if event['state'] != 'CANCELLED' and event['credit_state'] != 'CONSUMED':
            hs_storage_fail('활성 보완의 순번 소비를 반환할 수 없습니다.')
        if event['pool_key'] not in ('holiday|M', 'weekend|M'):
            hs_storage_fail('남성 순번 묶음이 올바르지 않습니다.')
        basis = bases.get((event['year'], event['pool_key'], event['basis_revision']))
        if not basis or event['order_snapshot'] != basis['sequence_snapshot']:
            hs_storage_fail('보완의 역사적 연간 기준 또는 순번 목록을 찾을 수 없습니다.')
        hs_storage_hash(event['order_fingerprint'])
        hs_storage_hash(event['annual_basis_hash'])
        if event['order_fingerprint'] != hs_storage_digest(event['order_snapshot']):
            hs_storage_fail('보완의 순번 목록과 저장된 지문이 일치하지 않습니다.')
        historical_basis = {field: basis[field] for field in HS_STORAGE_BASIS_HASH_FIELDS}
        if event['annual_basis_hash'] != hs_storage_digest(historical_basis):
            hs_storage_fail('보완의 역사적 연간 기준과 저장된 지문이 일치하지 않습니다.')
        hs_storage_slot(event['original_female_slot'])
        credit = event['female_credit']
        hs_storage_object(credit, {'pool_key', 'before', 'after', 'consumes', 'order_snapshot'})
        if credit['pool_key'] != 'weekday|F' or type(credit['consumes']) is not bool:
            hs_storage_fail('여성의 원래 평일 순번 소비가 올바르지 않습니다.')
        hs_storage_names(credit['order_snapshot'], allow_empty=not credit['consumes'])
        for field in ('before', 'after'):
            hs_storage_int(credit[field], maximum=max(0, len(credit['order_snapshot']) - 1))
        order = credit['order_snapshot']
        original = event['original_female_slot']
        positions = [i for i, name in enumerate(order) if name == original['name']]
        if credit['consumes']:
            if not positions:
                hs_storage_fail('여성 순번을 소비한 원래 근무자가 역사적 순번 목록에 없습니다.')
            position = min(positions, key=lambda i: (i - credit['before']) % len(order))
            if credit['after'] != (position + 1) % len(order):
                hs_storage_fail('여성의 원래 평일 근무와 소비 후 순번이 일치하지 않습니다.')
        else:
            if credit['before'] != credit['after']:
                hs_storage_fail('미소비 여성 순번의 위치가 바뀌었습니다.')
            if positions and (not original['isSubstitute'] or order[credit['before']] == original['name']):
                hs_storage_fail('원래 자신의 순번을 소비한 여성 근무를 미소비로 바꿀 수 없습니다.')
        if not isinstance(event['current_slot_binding'], list):
            hs_storage_fail('현재 근무 연결은 목록이어야 합니다.')
        for slot in event['current_slot_binding']:
            hs_storage_slot(slot)
        holiday = event['original_holiday']
        if holiday is not None:
            hs_storage_object(holiday, {'name', 'isMajor', 'isRemoved'})
            hs_storage_text(holiday['name'], allow_empty=True)
            if type(holiday['isMajor']) is not bool or type(holiday['isRemoved']) is not bool:
                hs_storage_fail('원래 공휴일 속성이 올바르지 않습니다.')
        if not isinstance(event['skipped_candidates'], list) or any(not isinstance(item, dict) for item in event['skipped_candidates']):
            hs_storage_fail('제외된 후보 기록은 객체 목록이어야 합니다.')
        hs_storage_text(event['created_at'], allow_empty=True, limit=100)
        for field in HS_STORAGE_EVENT_OPTIONAL & set(event):
            hs_storage_text(event[field], allow_empty=True)
        allocation = (event['year'], event['pool_key'], event['allocation_seq'])
        if event['supplement_id'] in ids or allocation in allocations:
            hs_storage_fail('보완 식별자 또는 확정 순번이 중복되었습니다.')
        ids.add(event['supplement_id'])
        allocations.add(allocation)
        if event['state'] != 'CANCELLED':
            if event['date_key'] in active_dates:
                hs_storage_fail('같은 날짜의 활성 보완이 중복되었습니다.')
            active_dates.add(event['date_key'])
    operations = {}
    for operation in normalized['operations']:
        hs_storage_validate_operation(operation)
        prior = operations.get(operation['operation_id'])
        if prior is not None and prior != operation:
            hs_storage_fail('같은 작업 식별자의 영수증이 서로 다릅니다.', 409)
        operations[operation['operation_id']] = operation
    normalized['operations'] = list(operations.values())
    return normalized


def hs_storage_schema_name(schema):
    if not isinstance(schema, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', schema):
        hs_storage_fail('DB 영역 이름이 올바르지 않습니다.')
    return '"' + schema + '"'


def hs_storage_table_names(cur, schema='main'):
    prefix = hs_storage_schema_name(schema)
    return {row[0] for row in cur.execute(f"SELECT name FROM {prefix}.sqlite_master WHERE type='table'").fetchall()}


def hs_storage_required_contract(cur, schema='main'):
    prefix = hs_storage_schema_name(schema)
    if 'app_settings' not in hs_storage_table_names(cur, schema):
        return 0
    row = cur.execute(f"SELECT value FROM {prefix}.app_settings WHERE key='required_write_contract'").fetchone()
    if row is None:
        return 0
    if row[0] not in ('0', '1'):
        hs_storage_fail('이 DB의 쓰기 계약 버전은 지원하지 않습니다.', 503)
    return int(row[0])


def hs_storage_check_schema(cur, schema='main'):
    prefix = hs_storage_schema_name(schema)
    names = hs_storage_table_names(cur, schema)
    present = names & set(HS_STORAGE_TABLES)
    if not present:
        if hs_storage_required_contract(cur, schema):
            hs_storage_fail('호환 계약이 활성화된 DB에 보완 테이블이 없습니다.', 503)
        return False
    if present != set(HS_STORAGE_TABLES):
        hs_storage_fail('보완 테이블의 일부가 없으므로 자료를 읽을 수 없습니다.', 503)
    for table, columns in HS_STORAGE_COLUMNS.items():
        actual = {r[1] for r in cur.execute(f'PRAGMA {prefix}.table_info("{table}")').fetchall()}
        if actual != columns:
            hs_storage_fail('지원하지 않거나 손상된 보완 테이블 구조입니다.', 503)
    return True


def hs_storage_trigger_definitions():
    definitions = {}
    for table in HS_STORAGE_GUARDED_TABLES:
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            name = 'wlr_hs_contract_' + table + '_' + action.lower()
            definitions[name] = (f'CREATE TRIGGER "{name}" BEFORE {action} ON "{table}" '
                "WHEN wlr_schedule_contract() IS NOT 1 BEGIN SELECT RAISE(ABORT, 'WLR_SCHEDULE_CONTRACT_REQUIRED'); END")
    return definitions


def verify_holiday_contract(cur):
    """Fail closed on an activated DB with missing/altered guards; never repairs."""
    required = hs_storage_required_contract(cur)
    actual = {r[0]: r[1] for r in cur.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'wlr_hs_contract_%'").fetchall()}
    if not required:
        if actual:
            hs_storage_fail('보완 쓰기 보호와 계약 설정이 일치하지 않습니다. 관리자 복구가 필요합니다.', 503)
        present = hs_storage_table_names(cur) & {'holiday_supplements', 'holiday_supplement_bases'}
        for table in present:
            if cur.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone():
                hs_storage_fail('보완 자료의 호환 쓰기 보호가 비활성 상태입니다.', 503)
        return 0
    hs_storage_check_schema(cur)
    expected = hs_storage_trigger_definitions()
    if set(actual) != set(expected) or any(actual[name].strip() != sql.strip() for name, sql in expected.items()):
        hs_storage_fail('보완 쓰기 보호가 없거나 변경되었습니다. 최신 관리자에서 안전 사본을 확인하고 복구해 주세요.', 503)
    indexes = {r[0]: r[1] for r in cur.execute("SELECT name,sql FROM sqlite_master WHERE type='index'").fetchall()}
    if any(indexes.get(name) != sql for name, sql in HS_STORAGE_INDEXES.items()):
        hs_storage_fail('보완 보호 인덱스가 없거나 변경되었습니다. 관리자 복구가 필요합니다.', 503)
    return required


def ensure_holiday_supplement_schema(cur):
    """ADMIN migration only; caller first takes the existing premigration snapshot."""
    if not cur.connection.in_transaction:
        cur.execute('BEGIN IMMEDIATE')
    present = hs_storage_check_schema(cur)
    verify_holiday_contract(cur)
    if not present:
        cur.execute("CREATE TABLE holiday_supplements (supplement_id TEXT PRIMARY KEY, date_key TEXT NOT NULL, year INTEGER NOT NULL, allocation_seq INTEGER NOT NULL, state TEXT NOT NULL CHECK(state IN ('ACTIVE','CANCELLED','NEEDS_REVIEW')), credit_state TEXT NOT NULL CHECK(credit_state IN ('CONSUMED','RELEASED')), basis_revision INTEGER NOT NULL, pool_key TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL)")
        cur.execute('CREATE TABLE holiday_supplement_bases (year INTEGER NOT NULL, pool_key TEXT NOT NULL, revision INTEGER NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1, payload_json TEXT NOT NULL, PRIMARY KEY(year,pool_key,revision))')
        cur.execute('CREATE TABLE holiday_supplement_operations (operation_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, action TEXT NOT NULL, result_json TEXT NOT NULL, supplement_id TEXT, created_at TEXT NOT NULL, schema_version INTEGER NOT NULL DEFAULT 1)')
    indexes = {r[0]: r[1] for r in cur.execute("SELECT name,sql FROM sqlite_master WHERE type='index'").fetchall()}
    for name, sql in HS_STORAGE_INDEXES.items():
        if name not in indexes:
            if hs_storage_required_contract(cur):
                hs_storage_fail('보완 보호 인덱스가 없습니다. 관리자 복구가 필요합니다.', 503)
            cur.execute(sql)
        elif indexes[name] != sql:
            hs_storage_fail('보완 보호 인덱스가 변경되었습니다.', 503)


def activate_holiday_contract(cur):
    """First activation is atomic with the first supplement in the caller's transaction."""
    if not cur.connection.in_transaction:
        cur.execute('BEGIN IMMEDIATE')
    required = verify_holiday_contract(cur)
    if required:
        return required
    if not hs_storage_check_schema(cur) or not set(HS_STORAGE_GUARDED_TABLES) <= hs_storage_table_names(cur):
        hs_storage_fail('보완 쓰기 보호를 설치할 업무 테이블이 없습니다.', 503)
    try:
        compatible = cur.execute('SELECT wlr_schedule_contract() IS 1').fetchone()[0]
    except sqlite3.Error:
        hs_storage_fail('호환 쓰기 연결을 사용할 수 없습니다. 최신 프로그램을 사용해 주세요.', 503)
    if not compatible:
        hs_storage_fail('호환 쓰기 연결을 사용할 수 없습니다. 최신 프로그램을 사용해 주세요.', 503)
    for sql in hs_storage_trigger_definitions().values():
        cur.execute(sql)
    cur.execute("INSERT INTO app_settings(key,value) VALUES ('required_write_contract','1') ON CONFLICT(key) DO UPDATE SET value='1'")
    return verify_holiday_contract(cur)


def hs_storage_public_projection(data):
    return {
        'schema': 1, 'requiredContract': data['requiredContract'],
        'events': [{key: value for key, value in item.items() if key in HS_STORAGE_PUBLIC_EVENT_FIELDS}
                   for item in data['events']],
        'bases': [{key: value for key, value in item.items() if key in HS_STORAGE_BASIS_FIELDS or key in ('state', 'review_code')}
                  for item in data['bases']],
    }


def read_holiday_supplements(cur, private=False, schema='main'):
    """Read-only, including legacy DBs; private results are for server/admin backup only."""
    prefix = hs_storage_schema_name(schema)
    data = {'schema': 1, 'events': [], 'bases': [], 'operations': [],
            'requiredContract': hs_storage_required_contract(cur, schema)}
    if hs_storage_check_schema(cur, schema):
        for table, target, fields, order in (
            ('holiday_supplement_bases', 'bases', ('year', 'pool_key', 'revision'), 'year,pool_key,revision'),
            ('holiday_supplements', 'events', ('supplement_id', 'date_key', 'year', 'allocation_seq', 'state', 'credit_state', 'basis_revision', 'pool_key'), 'year,pool_key,allocation_seq,supplement_id'),
        ):
            columns = ','.join(fields) + ',schema_version,payload_json'
            for row in cur.execute(f'SELECT {columns} FROM {prefix}."{table}" ORDER BY {order}').fetchall():
                if row[len(fields)] != 1:
                    hs_storage_fail('지원하지 않는 저장 자료 버전입니다.', 503)
                payload = hs_storage_load_json(row[len(fields) + 1])
                if not isinstance(payload, dict) or any(payload.get(key) != row[i] for i, key in enumerate(fields)):
                    hs_storage_fail('검색용 열과 보완 원본 자료가 일치하지 않습니다.', 503)
                data[target].append(payload)
        for row in cur.execute(f'SELECT operation_id,payload_hash,action,result_json,supplement_id,created_at,schema_version FROM {prefix}.holiday_supplement_operations ORDER BY created_at,operation_id').fetchall():
            if row[6] != 1:
                hs_storage_fail('지원하지 않는 작업 영수증 버전입니다.', 503)
            data['operations'].append({'operation_id': row[0], 'payload_hash': row[1], 'action': row[2],
                'result': hs_storage_load_json(row[3]), 'supplement_id': row[4], 'created_at': row[5]})
        try:
            data = validate_holiday_supplements(data)
        except HTTPException as error:
            hs_storage_fail('저장 자료의 검증에 실패했습니다. ' + str(error.detail), 503)
    return data if private else hs_storage_public_projection(data)


def hs_storage_operation_merge_plan(cur, operations):
    if not isinstance(operations, list):
        hs_storage_fail('작업 영수증 목록이 올바르지 않습니다.')
    existing = {item['operation_id']: item for item in read_holiday_supplements(cur, private=True)['operations']}
    additions = []
    for operation in operations:
        hs_storage_validate_operation(operation)
        prior = existing.get(operation['operation_id'])
        if prior is not None:
            if prior != operation:
                hs_storage_fail('동일한 작업 식별자가 다른 요청 또는 결과에 사용되었습니다.', 409)
        else:
            existing[operation['operation_id']] = operation
            additions.append(operation)
    return additions


def hs_storage_insert_operations(cur, operations):
    for item in operations:
        cur.execute('INSERT INTO holiday_supplement_operations (operation_id,payload_hash,action,result_json,supplement_id,created_at,schema_version) VALUES (?,?,?,?,?,?,1)',
                    (item['operation_id'], item['payload_hash'], item['action'], hs_storage_json(item['result']), item['supplement_id'], item['created_at']))


def merge_holiday_supplement_operations(cur, operations):
    if not cur.connection.in_transaction:
        cur.execute('BEGIN IMMEDIATE')
    verify_holiday_contract(cur)
    additions = hs_storage_operation_merge_plan(cur, operations)
    if additions:
        hs_storage_insert_operations(cur, additions)
    return len(additions)


def write_holiday_supplement_operation(cur, operation):
    return merge_holiday_supplement_operations(cur, [operation])


def read_holiday_supplement_operation(cur, operation_id):
    return next((item for item in read_holiday_supplements(cur, private=True)['operations'] if item['operation_id'] == operation_id), None)


def write_holiday_supplements(cur, data):
    """Private full state only. Never call with ordinary save-all's client metadata."""
    normalized = validate_holiday_supplements(data)
    if not cur.connection.in_transaction:
        cur.execute('BEGIN IMMEDIATE')
    if not hs_storage_check_schema(cur):
        hs_storage_fail('관리자에서 보완 자료 구조를 먼저 준비해 주세요.', 503)
    verify_holiday_contract(cur)
    additions = hs_storage_operation_merge_plan(cur, normalized['operations'])
    if normalized['requiredContract'] or hs_storage_required_contract(cur):
        activate_holiday_contract(cur)
    cur.execute('DELETE FROM holiday_supplements')
    cur.execute('DELETE FROM holiday_supplement_bases')
    for basis in normalized['bases']:
        cur.execute('INSERT INTO holiday_supplement_bases(year,pool_key,revision,schema_version,payload_json) VALUES (?,?,?,1,?)',
                    (basis['year'], basis['pool_key'], basis['revision'], hs_storage_json(basis)))
    fields = ('supplement_id', 'date_key', 'year', 'allocation_seq', 'state', 'credit_state', 'basis_revision', 'pool_key')
    for event in normalized['events']:
        cur.execute('INSERT INTO holiday_supplements(supplement_id,date_key,year,allocation_seq,state,credit_state,basis_revision,pool_key,schema_version,payload_json) VALUES (?,?,?,?,?,?,?,?,1,?)',
                    tuple(event[field] for field in fields) + (hs_storage_json(event),))
    hs_storage_insert_operations(cur, additions)
# END HOLIDAY STORAGE


# BEGIN HOLIDAY CALCULATION
# Pure holiday supplement decisions. All imports are stdlib; no DB/clock/global state.
import copy as hs_copy
import datetime as hs_datetime
import hashlib as hs_hashlib
import json as hs_json
import re as hs_re

hs_BASE_HOLIDAYS = {'2023-01-01': {'name': '신정'}, '2023-01-21': {'name': '설연휴', 'isMajor': True}, '2023-01-22': {'name': '설날', 'isMajor': True}, '2023-01-23': {'name': '설연휴', 'isMajor': True}, '2023-01-24': {'name': '대체공휴일'}, '2023-03-01': {'name': '삼일절'}, '2023-05-05': {'name': '어린이날'}, '2023-05-27': {'name': '부처님오신날'}, '2023-05-29': {'name': '대체공휴일'}, '2023-06-06': {'name': '현충일'}, '2023-08-15': {'name': '광복절'}, '2023-09-28': {'name': '추석연휴', 'isMajor': True}, '2023-09-29': {'name': '추석', 'isMajor': True}, '2023-09-30': {'name': '추석연휴', 'isMajor': True}, '2023-10-02': {'name': '임시공휴일'}, '2023-10-03': {'name': '개천절'}, '2023-10-09': {'name': '한글날'}, '2023-12-25': {'name': '성탄절'}, '2024-01-01': {'name': '신정'}, '2024-02-09': {'name': '설연휴', 'isMajor': True}, '2024-02-10': {'name': '설날', 'isMajor': True}, '2024-02-11': {'name': '설연휴', 'isMajor': True}, '2024-02-12': {'name': '대체공휴일'}, '2024-03-01': {'name': '삼일절'}, '2024-04-10': {'name': '국회의원선거'}, '2024-05-05': {'name': '어린이날'}, '2024-05-06': {'name': '대체공휴일'}, '2024-05-15': {'name': '부처님오신날'}, '2024-06-06': {'name': '현충일'}, '2024-08-15': {'name': '광복절'}, '2024-09-16': {'name': '추석연휴', 'isMajor': True}, '2024-09-17': {'name': '추석', 'isMajor': True}, '2024-09-18': {'name': '추석연휴', 'isMajor': True}, '2024-10-01': {'name': '국군의날'}, '2024-10-03': {'name': '개천절'}, '2024-10-09': {'name': '한글날'}, '2024-12-25': {'name': '성탄절'}, '2025-01-01': {'name': '신정'}, '2025-01-27': {'name': '임시공휴일'}, '2025-01-28': {'name': '설연휴', 'isMajor': True}, '2025-01-29': {'name': '설날', 'isMajor': True}, '2025-01-30': {'name': '설연휴', 'isMajor': True}, '2025-03-01': {'name': '삼일절'}, '2025-03-03': {'name': '대체공휴일'}, '2025-05-05': {'name': '어린이날·부처님오신날'}, '2025-05-06': {'name': '대체공휴일'}, '2025-06-03': {'name': '대통령선거'}, '2025-06-06': {'name': '현충일'}, '2025-08-15': {'name': '광복절'}, '2025-10-03': {'name': '개천절'}, '2025-10-05': {'name': '추석연휴', 'isMajor': True}, '2025-10-06': {'name': '추석', 'isMajor': True}, '2025-10-07': {'name': '추석연휴', 'isMajor': True}, '2025-10-08': {'name': '대체공휴일'}, '2025-10-09': {'name': '한글날'}, '2025-12-25': {'name': '성탄절'}, '2026-01-01': {'name': '신정'}, '2026-02-16': {'name': '설연휴', 'isMajor': True}, '2026-02-17': {'name': '설날', 'isMajor': True}, '2026-02-18': {'name': '설연휴', 'isMajor': True}, '2026-03-01': {'name': '삼일절'}, '2026-03-02': {'name': '대체공휴일'}, '2026-05-01': {'name': '노동절'}, '2026-05-05': {'name': '어린이날'}, '2026-05-24': {'name': '부처님오신날'}, '2026-05-25': {'name': '대체공휴일'}, '2026-06-03': {'name': '전국동시지방선거'}, '2026-06-06': {'name': '현충일'}, '2026-07-17': {'name': '제헌절'}, '2026-08-15': {'name': '광복절'}, '2026-08-17': {'name': '대체공휴일'}, '2026-09-24': {'name': '추석연휴', 'isMajor': True}, '2026-09-25': {'name': '추석', 'isMajor': True}, '2026-09-26': {'name': '추석연휴', 'isMajor': True}, '2026-10-03': {'name': '개천절'}, '2026-10-05': {'name': '대체공휴일'}, '2026-10-09': {'name': '한글날'}, '2026-12-25': {'name': '성탄절'}, '2027-01-01': {'name': '신정'}, '2027-02-06': {'name': '설연휴', 'isMajor': True}, '2027-02-07': {'name': '설날', 'isMajor': True}, '2027-02-08': {'name': '설연휴', 'isMajor': True}, '2027-02-09': {'name': '대체공휴일'}, '2027-03-01': {'name': '삼일절'}, '2027-05-01': {'name': '노동절'}, '2027-05-03': {'name': '대체공휴일'}, '2027-05-05': {'name': '어린이날'}, '2027-05-13': {'name': '부처님오신날'}, '2027-06-06': {'name': '현충일'}, '2027-07-17': {'name': '제헌절'}, '2027-07-19': {'name': '대체공휴일'}, '2027-08-15': {'name': '광복절'}, '2027-08-16': {'name': '대체공휴일'}, '2027-09-14': {'name': '추석연휴', 'isMajor': True}, '2027-09-15': {'name': '추석', 'isMajor': True}, '2027-09-16': {'name': '추석연휴', 'isMajor': True}, '2027-10-03': {'name': '개천절'}, '2027-10-04': {'name': '대체공휴일'}, '2027-10-09': {'name': '한글날'}, '2027-10-11': {'name': '대체공휴일'}, '2027-12-25': {'name': '성탄절'}, '2027-12-27': {'name': '대체공휴일'}, '2028-01-01': {'name': '신정'}, '2028-01-26': {'name': '설연휴', 'isMajor': True}, '2028-01-27': {'name': '설날', 'isMajor': True}, '2028-01-28': {'name': '설연휴', 'isMajor': True}, '2028-03-01': {'name': '삼일절'}, '2028-04-12': {'name': '국회의원선거'}, '2028-05-01': {'name': '노동절'}, '2028-05-02': {'name': '부처님오신날'}, '2028-05-05': {'name': '어린이날'}, '2028-06-06': {'name': '현충일'}, '2028-07-17': {'name': '제헌절'}, '2028-08-15': {'name': '광복절'}, '2028-10-02': {'name': '추석연휴', 'isMajor': True}, '2028-10-03': {'name': '추석·개천절', 'isMajor': True}, '2028-10-04': {'name': '추석연휴', 'isMajor': True}, '2028-10-05': {'name': '대체공휴일'}, '2028-10-09': {'name': '한글날'}, '2028-12-25': {'name': '성탄절'}, '2029-01-01': {'name': '신정'}, '2029-02-12': {'name': '설연휴', 'isMajor': True}, '2029-02-13': {'name': '설날', 'isMajor': True}, '2029-02-14': {'name': '설연휴', 'isMajor': True}, '2029-03-01': {'name': '삼일절'}, '2029-05-01': {'name': '노동절'}, '2029-05-05': {'name': '어린이날'}, '2029-05-07': {'name': '대체공휴일'}, '2029-05-20': {'name': '부처님오신날'}, '2029-05-21': {'name': '대체공휴일'}, '2029-06-06': {'name': '현충일'}, '2029-07-17': {'name': '제헌절'}, '2029-08-15': {'name': '광복절'}, '2029-09-21': {'name': '추석연휴', 'isMajor': True}, '2029-09-22': {'name': '추석', 'isMajor': True}, '2029-09-23': {'name': '추석연휴', 'isMajor': True}, '2029-09-24': {'name': '대체공휴일'}, '2029-10-03': {'name': '개천절'}, '2029-10-09': {'name': '한글날'}, '2029-12-25': {'name': '성탄절'}}

hs_BASE_MAJOR_LINKS = {'2023-01-24': '2023-01-21', '2024-02-12': '2024-02-09', '2025-10-08': '2025-10-05', '2027-02-09': '2027-02-06', '2028-10-05': '2028-10-02', '2029-09-24': '2029-09-21'}


def hs_fingerprint(value):
    return hs_hashlib.sha256(hs_json.dumps(value, ensure_ascii=False, sort_keys=True,
                                          separators=(',', ':')).encode('utf-8')).hexdigest()


def hs_date(value):
    if not isinstance(value, str) or not hs_re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Invalid date')
    return hs_datetime.date.fromisoformat(value)


def hs_shift(key, days):
    return (hs_date(key) + hs_datetime.timedelta(days=days)).isoformat()


def hs_year_dates(year):
    first = hs_datetime.date(year, 1, 1)
    for offset in range((hs_datetime.date(year+1, 1, 1)-first).days):
        yield (first + hs_datetime.timedelta(days=offset)).isoformat()


def hs_holidays(state):
    holidays = hs_copy.deepcopy(hs_BASE_HOLIDAYS)
    for key, value in (state.get('customHolidays') or {}).items():
        if value.get('isRemoved'):
            holidays.pop(key, None)
        else:
            holidays[key] = hs_copy.deepcopy(value)
    return holidays


def hs_major_start(holidays, key):
    for _ in range(10):
        previous = hs_shift(key, -1)
        if not holidays.get(previous, {}).get('isMajor'):
            break
        key = previous
    return key


def hs_major(holidays, key):
    holiday = holidays.get(key)
    if not holiday:
        return None
    if holiday.get('isMajor'):
        return hs_major_start(holidays, key)
    if '대체공휴일' in hs_BASE_HOLIDAYS.get(key, {}).get('name', ''):
        start = hs_BASE_MAJOR_LINKS.get(key)
        if not start:
            return None
        for day in [hs_shift(start, 2), hs_shift(start, 1), start]:
            if holidays.get(day, {}).get('isMajor'):
                return hs_major_start(holidays, day)
        return None
    if '대체공휴일' not in holiday.get('name', ''):
        return None
    previous = hs_shift(key, -1)
    for _ in range(3):
        info = holidays.get(previous)
        if info and info.get('isMajor'):
            return hs_major_start(holidays, previous)
        if info or hs_date(previous).weekday() < 5:
            return None
        previous = hs_shift(previous, -1)
    return None


def hs_major_main(holidays, key):
    holiday = holidays.get(key) or {}
    return bool(holiday.get('isMajor') and hs_re.match(r'^(?:설날|추석)(?:·|$)', holiday.get('name', '')))


def hs_category(state, key, gender='M', holidays=None):
    holidays = hs_holidays(state) if holidays is None else holidays
    if hs_major(holidays, key):
        return 'major'
    if hs_date(key).weekday() >= 5:
        return 'weekend'
    if key in holidays:
        orders = (state.get('rules') or {}).get('fixedOrders') or {}
        shared = not orders.get('holiday'+gender) if gender else not (orders.get('holidayM') or orders.get('holidayF'))
        return 'weekend' if shared else 'holiday'
    return 'weekday'


def hs_order(state, key):
    orders = (state.get('rules') or {}).get('fixedOrders') or {}
    category, gender = key.split('|')
    if key == 'weekday|F':
        return list(orders.get('weekday') or [])
    if category == 'weekday':
        return []
    order = list(orders.get(category+gender) or [])
    if category == 'holiday' and not order:
        order = list(orders.get('weekend'+gender) or [])
    if category == 'weekend' and order and len(order) % 2 == 0:
        return order + [order[i ^ 1] for i in range(len(order))]
    return order


def hs_gender(state, worker):
    for member in state.get('members') or []:
        if member.get('name') == worker.get('name'):
            return member.get('gender')
    match = hs_re.search(r'\((남|여)\)', str(worker.get('role') or ''))
    return ('M' if match[1] == '남' else 'F') if match else None


def hs_absence(state, name, key):
    for absence in state.get('absences') or []:
        if absence.get('name') != name:
            continue
        for span in absence.get('segments') or [absence]:
            if str(span.get('start', span.get('start_date', ''))) <= key <= str(span.get('end', span.get('end_date', ''))):
                return absence
    return None


def hs_worker_cursor(state, key, date_key, worker, current):
    """The existing JS replay rule, including skips, substitutes and paired occurrences."""
    order = hs_order(state, key)
    if not order:
        return current, False
    size = len(order)
    previous = hs_shift(date_key, -1)
    previous_name = None
    if not (state.get('noDuty') or {}).get(previous):
        for row in (state.get('schedules') or {}).get(previous, []):
            if hs_gender(state, row) == key.split('|')[1]:
                previous_name = row.get('name')
    for offset in range(size):
        candidate = order[(current+offset) % size]
        if candidate == worker.get('name'):
            return (current+offset+1) % size, True
        guarded = key.startswith('weekend|') and hs_date(date_key).weekday() == 6 and candidate == previous_name
        if not hs_absence(state, candidate, date_key) and not guarded:
            break
    if not worker.get('isSubstitute') and worker.get('name') in order:
        positions = [i for i, name in enumerate(order) if name == worker['name']]
        if key.startswith('weekend|') and len(positions) > 1 and hs_date(date_key).weekday() >= 5:
            parity = 1 if hs_date(date_key).weekday() == 6 else 0
            matching = [i for i in positions if i % 2 == parity]
            positions = matching or positions
        return (min(positions, key=lambda pos: (pos-current) % size)+1) % size, True
    roster = {member.get('name') for member in state.get('members') or []}
    if worker.get('name') not in roster and not worker.get('isSubstitute'):
        return (current+1) % size, True
    return current, False


def hs_events(state):
    return (state.get('holidaySupplements') or {}).get('events') or []


def hs_bases(state):
    return (state.get('holidaySupplements') or {}).get('bases') or []


def hs_active_events(state):
    return {event['date_key']: event for event in hs_events(state)
            if event.get('state') in ('ACTIVE', 'NEEDS_REVIEW')}


def hs_latest_basis(state, year, pool):
    rows = [basis for basis in hs_bases(state) if basis.get('year') == year and basis.get('pool_key') == pool]
    return max(rows, key=lambda basis: basis.get('revision', 0)) if rows else None


def hs_apply_year_tail(state, indexes, year):
    for pool in ('holiday|M', 'weekend|M'):
        consumed = [event for event in hs_events(state) if event.get('year') == year
                    and event.get('pool_key') == pool and event.get('credit_state') == 'CONSUMED']
        if not consumed:
            continue
        event = max(consumed, key=lambda item: item.get('end_ordinal', 0))
        basis = next((row for row in hs_bases(state) if row.get('year') == year and row.get('pool_key') == pool
                      and row.get('revision') == event.get('basis_revision')), None)
        # This occurrence belongs to the saved sequence. A caller must validate
        # inherited bases before using the resulting cursor for a new allocation.
        order = (basis or {}).get('sequence_snapshot') or event.get('order_snapshot') or []
        if basis and order:
            indexes[pool] = (basis['ordinary_tail_position']+event['end_ordinal']) % len(order)


def hs_rotation_indexes(state, before_key):
    """Replay ordinary dates, virtual female credits, and completed year male tails."""
    indexes = {key: 0 for key in ('weekday|F','weekend|M','weekend|F','holiday|M','holiday|F','major|M','major|F')}
    if (state.get('rules') or {}).get('algoType') != 'FIXED_ORDER':
        return indexes
    holidays = hs_holidays(state)
    active = hs_active_events(state)
    keys = sorted(key for key in set(state.get('schedules') or {}) | set(active) if key < before_key)
    years = sorted({int(before_key[:4])} | {int(key[:4]) for key in keys} | {event['year'] for event in hs_events(state) if event['year'] < int(before_key[:4])})
    seen_major = set()
    for year in years:
        for pool in ('holiday|M','weekend|M'):
            basis = hs_latest_basis(state, year, pool)
            if basis and basis.get('policy') == 'PRESERVE_EXISTING' and hs_order(state, pool):
                indexes[pool] = basis['anchor_before_year'] % len(hs_order(state, pool))
        for key in keys:
            if int(key[:4]) != year:
                continue
            if key in active:
                credit = active[key].get('female_credit') or {}
                if credit.get('consumes') and hs_order(state, 'weekday|F'):
                    indexes['weekday|F'] = credit['after'] % len(hs_order(state, 'weekday|F'))
                continue
            if (state.get('noDuty') or {}).get(key) or hs_major_main(holidays, key):
                continue
            for worker in (state.get('schedules') or {}).get(key, []):
                gender = hs_gender(state, worker)
                if gender not in ('M','F'):
                    continue
                category = hs_category(state, key, gender, holidays)
                pool = category+'|'+gender
                if pool not in indexes:
                    continue
                if category == 'major':
                    marker = (hs_major(holidays,key), gender)
                    if marker in seen_major:
                        continue
                    seen_major.add(marker)
                indexes[pool], _ = hs_worker_cursor(state,pool,key,worker,indexes[pool])
        if year < int(before_key[:4]):
            hs_apply_year_tail(state, indexes, year)
    return indexes


def hs_ordinary_records(state, year, pool):
    holidays = hs_holidays(state)
    active = hs_active_events(state)
    records, missing = [], []
    for key in hs_year_dates(year):
        if key in active or (state.get('noDuty') or {}).get(key) or hs_major_main(holidays,key):
            continue
        if hs_category(state,key,'M',holidays)+'|M' != pool:
            continue
        workers = [row for row in (state.get('schedules') or {}).get(key,[]) if hs_gender(state,row) == 'M']
        # Credits depend on substitute availability, never on actual statistics points.
        unavailable = sorted({name for name in hs_order(state,pool) if hs_absence(state,name,key)})
        records.append({'date_key':key,'workers':[{'name':row.get('name'), 'isSubstitute':bool(row.get('isSubstitute'))} for row in workers],
                        'unavailable':unavailable})
        # A retired regular worker's historical slot still consumes one turn in
        # the legacy engine; current candidate eligibility is checked separately.
        if len(workers) != 1:
            missing.append(key)
    return records, missing


def hs_prior_hash(state, year, pool):
    earliest = min([year] + [int(key[:4]) for key in state.get('schedules') or {}] + [event['year'] for event in hs_events(state)])
    history = []
    for previous_year in range(earliest, year):
        records, _ = hs_ordinary_records(state, previous_year, pool)
        # Empty required dates are not prior reservations, but existing records retain exact chronology.
        history.extend(row for row in records if row['workers'])
    credits = [{field:event.get(field) for field in ('year','pool_key','allocation_seq','basis_revision','selected_ordinal','end_ordinal','order_fingerprint')}
               for event in hs_events(state) if event.get('year',year)>=earliest and event.get('year',year)<year
               and event.get('pool_key')==pool and event.get('credit_state')=='CONSUMED']
    anchors = [{field:basis.get(field) for field in ('year','revision','anchor_before_year','ordinary_tail_position','sequence_snapshot')}
               for basis in hs_bases(state) if basis.get('year',year)<year and basis.get('pool_key')==pool
               and basis.get('policy')=='PRESERVE_EXISTING']
    return hs_fingerprint({'ordinary':history,'credits':credits,'anchors':anchors})


def hs_compute_basis(state, year, pool, anchor=None):
    order = hs_order(state,pool)
    records, missing = hs_ordinary_records(state,year,pool)
    before = hs_rotation_indexes(state, f'{year:04d}-01-01').get(pool,0)
    latest = hs_latest_basis(state,year,pool)
    if anchor is not None:
        before = anchor
    elif latest and latest.get('policy')=='PRESERVE_EXISTING':
        before = latest['anchor_before_year']
    cursor = before
    for row in records:
        for worker in row['workers']:
            cursor, _ = hs_worker_cursor(state,pool,row['date_key'],worker,cursor)
    return {'year':year,'pool_key':pool,'revision':(latest or {}).get('revision',0)+1,
            'sequence_snapshot':order,'prior_year_credit_hash':hs_prior_hash(state,year,pool),
            'ordinary_reservation_hash':hs_fingerprint(records),'ordinary_tail_position':cursor,
            'anchor_before_year':before,'effective_boundary':f'{year:04d}-01-01',
            'policy':'ANNUAL_TAIL','adopted_at':''}, records, missing


def hs_basis_review(state, basis):
    current, _, missing = hs_compute_basis(state,basis['year'],basis['pool_key'])
    changed_sequence = current['sequence_snapshot'] != basis.get('sequence_snapshot')
    changed_reservations = current['ordinary_reservation_hash'] != basis.get('ordinary_reservation_hash')
    changed_prior = current['prior_year_credit_hash'] != basis.get('prior_year_credit_hash')
    consumed = any(event.get('year')==basis['year'] and event.get('pool_key')==basis['pool_key']
                   and event.get('credit_state')=='CONSUMED' for event in hs_events(state))
    code = ('BASIS_REBASE_REQUIRED' if consumed else 'BASIS_REVIEW_REQUIRED') if changed_sequence or changed_reservations else (
        'BASIS_RECONCILE_REQUIRED' if changed_prior else None)
    return {'state':'NEEDS_REVIEW' if code else 'CURRENT','review_code':code,
            'sequence_changed':changed_sequence,'ordinary_reservations_changed':changed_reservations,
            'prior_year_changed':changed_prior,'has_consumed_credits':consumed,
            'incomplete_dates':missing}


def hs_inherited_sources(state, year, pool):
    """Earlier consumed occurrences whose current interpretation is unresolved."""
    current_pool='holiday|M' if ((state.get('rules') or {}).get('fixedOrders') or {}).get('holidayM') else 'weekend|M'
    sources={}
    for event in hs_events(state):
        source_year=event.get('year',year);source_pool=event.get('pool_key')
        if source_year>=year or event.get('credit_state')!='CONSUMED' or pool not in (source_pool,current_pool):
            continue
        basis=hs_latest_basis(state,source_year,source_pool)
        review=hs_basis_review(state,basis) if basis else {'review_code':'BASIS_REBASE_REQUIRED'}
        code=review.get('review_code')
        if (source_pool!=current_pool or hs_order(state,source_pool)!=event.get('order_snapshot')
                or event.get('state')=='NEEDS_REVIEW'):
            code='BASIS_REBASE_REQUIRED'
        if code:
            marker=(source_year,source_pool)
            # A retained cancelled occurrence needs exactly the same protection.
            if marker not in sources or code=='BASIS_REBASE_REQUIRED':
                sources[marker]={'year':source_year,'pool_key':source_pool,'review_code':code}
    return [sources[key] for key in sorted(sources)]


def hs_has_adopted_anchor(state, year, pool):
    basis=hs_latest_basis(state,year,pool)
    order=hs_order(state,pool)
    if not basis or basis.get('policy')!='PRESERVE_EXISTING' or not order:
        return False
    anchor=basis.get('anchor_before_year')
    return (type(anchor) is int and 0<=anchor<len(order) and basis.get('sequence_snapshot')==order
            and basis.get('effective_boundary')==f'{year:04d}-01-01'
            and not hs_basis_review(state,basis).get('review_code'))


def hs_position(state,pool,position):
    order=hs_order(state,pool)
    if not order:
        return None
    base=((state.get('rules') or {}).get('fixedOrders') or {}).get('weekend'+pool[-1]) or []
    paired=pool.startswith('weekend|') and bool(base) and len(base)%2==0 and len(order)==2*len(base)
    position%=len(order)
    return {'name':order[position],'position':position,'cycle':2 if paired and position>=len(base) else 1,
            'ordinal':position%len(base)+1 if paired else position+1,'paired':paired}


def hs_issue(code,message,**extras):
    return {'code':code,'message':message,**extras}


def hs_effective_kind(state,key,holidays=None):
    holidays=hs_holidays(state) if holidays is None else holidays
    if hs_major(holidays,key):
        return 'major'
    if key in holidays or hs_date(key).weekday()>=5:
        return 'holiday'
    return 'weekday'


def hs_pending_impacts(state, after_state, key):
    impacts,blockers,warnings=[],[],[]
    for request in state.get('requests') or []:
        if request.get('status','PENDING') not in ('PENDING','pending'):
            continue
        kind=request.get('request_type',request.get('type',''))
        start=request.get('target_date') or request.get('start_date') or ''
        end=request.get('absence_end_date') or request.get('end_date') or start
        swap=request.get('swap_date') or ''
        original=request.get('amendment_original')
        if isinstance(original,str):
            try:
                original=hs_json.loads(original)
            except (ValueError,TypeError):
                original=None
        original=original if isinstance(original,dict) else {}
        original_relevant=any(str(span.get('start') or '')<=key<=str(span.get('end') or '')
                              for span in original.get('segments') or [original])
        if not (key==swap or start<=key<=end or original_relevant):
            continue
        item=hs_issue('PENDING_REQUEST_IMPACT','대기 신청의 날짜 구분과 승인 영향을 다시 확인해야 합니다.',requestId=request.get('id'))
        impacts.append(item)
        if kind=='SWAP' and start and swap and hs_effective_kind(after_state,start)!=hs_effective_kind(after_state,swap):
            blockers.append(hs_issue('PENDING_SWAP_CATEGORY_CONFLICT','대기 맞교환의 한쪽 날짜가 휴일로 바뀝니다. 해당 신청을 먼저 정리해 주세요.',requestId=request.get('id')))
        else:
            warnings.append(hs_issue('PENDING_REQUEST_REVIEW','관련 대기 신청은 확정 부재로 취급하지 않으며 승인 전에 영향을 다시 확인합니다.',requestId=request.get('id')))
        if kind in ('ABSENCE','ABSENCE_AMEND','ABSENCE_CANCEL'):
            impacts[-1]['message']='대기 휴가·부재의 구간과 일수는 현재 공휴일 기준으로 다시 확인합니다. 이미 승인된 부재는 그대로 유지합니다.'
            absence_type=str(request.get('absence_type') or request.get('substitute_name') or '')
            if kind!='ABSENCE_CANCEL' and ('연차' in absence_type or '휴가' in absence_type) and start and end:
                try:
                    begin=hs_date(start);finish=hs_date(end)
                    span=[(begin+hs_datetime.timedelta(days=i)).isoformat() for i in range(max(0,(finish-begin).days+1))]
                    before_holidays=hs_holidays(state);after_holidays=hs_holidays(after_state)
                    before_days=sum(hs_date(day).weekday()<5 and day not in before_holidays for day in span)
                    after_days=sum(hs_date(day).weekday()<5 and day not in after_holidays for day in span)
                    impacts[-1]['beforeDays']=before_days;impacts[-1]['afterDays']=after_days
                    if before_days>0 and after_days==0:
                        blockers.append(hs_issue('PENDING_ABSENCE_ZERO_DAYS','대기 휴가의 유효한 평일이 0일이 됩니다. 해당 신청을 먼저 확인해 주세요.',requestId=request.get('id')))
                except ValueError:
                    warnings.append(hs_issue('PENDING_REQUEST_INVALID_RANGE','관련 대기 신청의 구간을 확인할 수 없습니다. 신청 내역에서 확인해 주세요.',requestId=request.get('id')))
    return impacts,blockers,warnings


def hs_calculate(state,draft,today):
    """Pure review/apply decision. Private complete maps are written only by the caller."""
    state=hs_copy.deepcopy(state)
    draft=hs_copy.deepcopy(draft or {})
    events=hs_copy.deepcopy(hs_events(state));bases=hs_copy.deepcopy(hs_bases(state))
    schedules=hs_copy.deepcopy(state.get('schedules') or {});custom=hs_copy.deepcopy(state.get('customHolidays') or {})
    key=draft.get('date_key');intent=draft.get('holidayIntent')
    holidays=hs_holidays(state)
    result={'schema':1,'effect':'BLOCKED','eligible':False,'blockers':[],'warnings':[],
            'baseVersion':state.get('dataVersion',0),'before':{'date_key':key,'holiday':holidays.get(key),'workers':schedules.get(key,[])},
            'after':{'date_key':key,'holiday':holidays.get(key),'workers':schedules.get(key,[])},
            'candidate':None,'rotationBasis':None,'impacts':[],'affectedDates':[],
            'preservedDatesCount':len(schedules),'requiredContract':1,
            '_events':events,'_bases':bases,'_customHolidays':custom,'_schedules':schedules}

    def block(code,message):
        result['blockers'].append(hs_issue(code,message))
        return result

    def finish(effect,changed=False):
        result['effect']=effect;result['eligible']=True
        result['affectedDates']=[key] if changed and key else []
        result['preservedDatesCount']=len(state.get('schedules') or {})-(1 if changed and key in (state.get('schedules') or {}) else 0)
        return result

    if intent=='RECONCILE':
        if (state.get('rules') or {}).get('algoType')!='FIXED_ORDER':
            return block('FIXED_ORDER_REQUIRED','연간 시작점 재확인은 고정 순환제에서 지원합니다. 현재 배정 설정은 변경하지 않습니다.')
        year=draft.get('year');pool=draft.get('pool_key')
        if type(year) is not int or year<2023 or year>2029 or pool not in ('holiday|M','weekend|M'):
            return block('INVALID_BASIS','재확인할 연도와 순번 묶음을 확인해 주세요.')
        if draft.get('reconcilePolicy') not in (None,'PRESERVE_EXISTING'):
            return block('INVALID_RECONCILE_POLICY','기존 예약 유지 방식만 지원합니다.')
        basis=hs_latest_basis(state,year,pool)
        fresh=basis is None
        consumed=[event for event in events if event.get('year')==year and event.get('credit_state')=='CONSUMED']
        if any(event.get('pool_key')!=pool for event in consumed) or (fresh and consumed):
            return block('BASIS_REBASE_REQUIRED','대상 연도에 이미 소비한 보완 순번이 있습니다. 다른 묶음이나 새 기준으로 그 소비 위치를 자동 이동할 수 없습니다.')
        inherited=hs_inherited_sources(state,year,pool)
        if fresh:
            basis,_,missing=hs_compute_basis(state,year,pool)
            basis['revision']=0
            review={'state':'NEEDS_REVIEW','review_code':'BASIS_RECONCILE_REQUIRED','has_consumed_credits':False,'incomplete_dates':missing}
        else:
            review=hs_basis_review(state,basis)
        result['rotationBasis']={**hs_copy.deepcopy(basis),**review,'inherited_sources':inherited}
        if ((fresh and draft.get('expected_basis_revision')!=0)
                or (not fresh and draft.get('expected_basis_revision') not in (None,basis['revision']))):
            return block('BASIS_REVISION_CHANGED','연간 기준이 바뀌었습니다. 다시 미리보기해 주세요.')
        if review['review_code']=='BASIS_REBASE_REQUIRED':
            return block('BASIS_REBASE_REQUIRED','같은 해 순번 목록 또는 정규 예약이 변경되어 기존 소비 위치를 새 기준으로 옮길 수 없습니다. 기존 근무는 보존합니다. 발생 회차 재매핑은 이번 버전에서 지원하지 않습니다.')
        anchor=draft.get('anchor')
        anchor=anchor.get('position') if isinstance(anchor,dict) else anchor
        order=hs_order(state,pool)
        if fresh and anchor is None:
            return block('ANCHOR_REQUIRED','이 연도에는 저장된 시작점이 없습니다. 회차와 위치를 직접 선택해 미리보기해 주세요. 이전 연도의 미해결 소비 기록은 그대로 보존합니다.')
        if anchor is None:
            anchor=basis['anchor_before_year']
        if type(anchor) is not int or not order or anchor<0 or anchor>=len(order):
            return block('INVALID_ANCHOR','펼친 순번의 유효한 회차와 위치를 선택해 주세요.')
        current,_,missing=hs_compute_basis(state,year,pool,anchor)
        if missing:
            return block('ANNUAL_RESERVATIONS_INCOMPLETE','대상 연도 순번 묶음에 미배정 날짜가 있어 전체 예약을 확정할 수 없습니다.')
        if review['has_consumed_credits'] and current['ordinary_tail_position']!=basis['ordinary_tail_position']:
            return block('BASIS_REBASE_REQUIRED','선택한 시작점으로 연간 후미가 달라져 기존 보완 소비 위치를 그대로 재사용할 수 없습니다. 발생 회차 재매핑은 이번 버전에서 지원하지 않습니다.')
        if (not fresh and (not inherited or hs_has_adopted_anchor(state,year,pool))
                and not review['review_code'] and anchor==basis['anchor_before_year']):
            return finish('NOOP')
        current['policy']='PRESERVE_EXISTING';bases.append(current)
        result['rotationBasis']={**current,'state':'CURRENT','review_code':None,'inherited_sources':inherited,
                                 'nextPosition':hs_position(state,pool,current['ordinary_tail_position'])}
        result['impacts'].append(hs_issue('CONTINUITY_RESET','선택한 시작점은 대상 연도 1월 1일에 적용합니다. 기존 날짜별 근무와 이미 확정한 보완 순번은 유지합니다.'))
        if inherited:
            result['warnings'].append(hs_issue('PRIOR_BASIS_PRESERVED','이전 연도의 미해결 소비 기록을 새 순번으로 옮기지 않습니다. 이번 선택은 대상 연도의 1월 1일 시작점에만 적용합니다.'))
        return finish('RECONCILE')

    try:
        target=hs_date(key)
    except (ValueError,TypeError):
        return block('INVALID_DATE','올바른 날짜를 선택해 주세요.')
    if intent not in ('SET_CUSTOM','DELETE_CUSTOM','REMOVE_BASE','RESTORE_BASE'):
        return block('INVALID_INTENT','지원하지 않는 공휴일 변경입니다.')
    desired=hs_copy.deepcopy(custom)
    if intent=='SET_CUSTOM':
        name=str(draft.get('name') or '').strip()
        if not name or len(name)>120:
            return block('INVALID_NAME','공휴일 이름을 1~120자로 입력해 주세요.')
        if draft.get('is_major',False):
            return block('MAJOR_BLOCK','명절은 기존 명절 팀 배정에서 처리해 주세요.')
        desired[key]={'name':name,'isMajor':False,'isRemoved':False}
    elif intent in ('DELETE_CUSTOM','RESTORE_BASE'):
        if intent=='RESTORE_BASE' and key not in hs_BASE_HOLIDAYS:
            return block('BASE_HOLIDAY_NOT_FOUND','복구할 기본 공휴일이 없습니다.')
        desired.pop(key,None)
    elif intent=='REMOVE_BASE':
        if key not in hs_BASE_HOLIDAYS:
            return block('BASE_HOLIDAY_NOT_FOUND','해제할 기본 공휴일이 없습니다.')
        desired[key]={**hs_copy.deepcopy(hs_BASE_HOLIDAYS[key]),'isMajor':bool(hs_BASE_HOLIDAYS[key].get('isMajor')),'isRemoved':True}
    normalized=lambda rows:{day:{'name':row.get('name',''),'isMajor':bool(row.get('isMajor')),'isRemoved':bool(row.get('isRemoved'))} for day,row in rows.items()}
    if normalized(desired)==normalized(custom):
        return finish('NOOP')
    if target.year<2023 or target.year>2029:
        return block('UNSUPPORTED_YEAR','기본 공휴일 자료는 2023~2029년을 지원합니다. 연간 달력 기준을 먼저 확인해 주세요.')
    if target<=today:
        return block('NOT_FUTURE_DATE','당일 또는 과거 날짜는 자동 보완할 수 없습니다. 기존 관리 경로에서 확인해 주세요.')
    if key[:7] in (state.get('closedMonths') or []):
        return block('CLOSED_MONTH','마감한 달은 변경할 수 없습니다.')
    if (state.get('noDuty') or {}).get(key):
        return block('NO_DUTY','당직 없는 날·패밀리데이는 자동 보완할 수 없습니다. 휴무 표시는 그대로 유지합니다.')
    after_state=hs_copy.deepcopy(state);after_state['customHolidays']=desired
    after_holidays=hs_holidays(after_state)
    result['after']['holiday']=hs_copy.deepcopy(after_holidays.get(key))
    if hs_major(holidays,key) or hs_major(after_holidays,key):
        return block('MAJOR_BLOCK','명절 연휴 또는 연결된 대체공휴일입니다. 기존 명절 팀 배정에서 처리해 주세요.')
    # A label can change the adjacent substitute's effective block as well as its own.
    for offset in range(1,4):
        nearby=hs_shift(key,offset)
        if hs_major(holidays,nearby)!=hs_major(after_holidays,nearby):
            return block('MAJOR_BLOCK','이 이름 변경은 연결된 명절 팀의 날짜 구분에 영향을 줍니다. 명절 관리에서 확인해 주세요.')
    impacts,blockers,warnings=hs_pending_impacts(state,after_state,key)
    result['impacts']+=impacts;result['blockers']+=blockers;result['warnings']+=warnings
    if blockers:
        return result
    before_kind=hs_effective_kind(state,key,holidays);after_kind=hs_effective_kind(after_state,key,after_holidays)
    active=hs_active_events(state).get(key)
    if before_kind==after_kind:
        result['_customHolidays']=desired
        return finish('LABEL_ONLY',True)
    if before_kind=='holiday' and after_kind=='weekday':
        if not active:
            return block('NO_OWNED_SUPPLEMENT','보완 기록이 없는 기존 공휴일 근무는 자동으로 제거할 수 없습니다. 기존 근무표 관리에서 확인해 주세요.')
        if active.get('state')!='ACTIVE' or schedules.get(key,[])!=active.get('current_slot_binding',[]):
            return block('SLOT_BINDING_CHANGED','보완 이후 현재 근무가 변경되었습니다. 승인된 변경을 원본으로 덮지 않고 현재 담당자를 먼저 확인해야 합니다.')
        original=active['original_female_slot']
        expected_female={**original,'role':'공휴일(여)'}
        if expected_female not in schedules.get(key,[]) or len(schedules.get(key,[]))!=2:
            return block('SLOT_BINDING_CHANGED','유지 여성 또는 추가 남성의 현재 배정이 원래 보완 결과와 달라 자동 해제할 수 없습니다.')
        # A binding update after approval does not grant ownership over a replacement person.
        original_candidate=active['order_snapshot'][(next(row for row in bases if row['year']==active['year'] and row['pool_key']==active['pool_key'] and row['revision']==active['basis_revision'])['ordinary_tail_position']+active['selected_ordinal'])%len(active['order_snapshot'])]
        expected_male={'name':original_candidate,'role':'공휴일(남)','isSubstitute':False,'zeroScore':False}
        if expected_male not in schedules.get(key,[]):
            return block('SLOT_BINDING_CHANGED','추가 남성이 이후 변경되어 자동 삭제할 수 없습니다. 현재 배정을 확인해 주세요.')
        result['_customHolidays']=desired;schedules[key]=[hs_copy.deepcopy(original)]
        result['after']['workers']=hs_copy.deepcopy(schedules[key])
        for event in events:
            if event['supplement_id']==active['supplement_id']:
                event['state']='CANCELLED';event['current_slot_binding']=hs_copy.deepcopy(schedules[key])
        pool=active['pool_key'];year=active['year']
        dependency=any(int(day[:4])>year and any(hs_gender(state,row)=='M' for row in rows)
                       and hs_category(state,day,'M')+'|M'==pool
                       for day,rows in (state.get('schedules') or {}).items() if not (state.get('noDuty') or {}).get(day))
        dependency=dependency or any(event.get('year',0)>year and event.get('pool_key')==pool and event.get('credit_state')=='CONSUMED' for event in events)
        if not dependency:
            for event in sorted((event for event in events if event.get('year')==year and event.get('pool_key')==pool and event.get('credit_state')=='CONSUMED'),key=lambda item:item['allocation_seq'],reverse=True):
                if event.get('state')!='CANCELLED':
                    break
                event['credit_state']='RELEASED'
        else:
            result['warnings'].append(hs_issue('CREDIT_RETAINED','후속 연도의 같은 순번 예약이 있어 취소한 남성의 연말 소비는 유지합니다.'))
        return finish('REMOVE',True)
    if (state.get('rules') or {}).get('algoType')!='FIXED_ORDER':
        return block('FIXED_ORDER_REQUIRED','추가 공휴일 보완은 고정 순환제에서 지원합니다. 현재 균등 배정 설정은 변경하지 않습니다.')
    workers=schedules.get(key,[])
    roster={member.get('name'):member.get('gender') for member in state.get('members') or []}
    if len(workers)!=1 or roster.get(workers[0].get('name'))!='F' or hs_re.search(r'\(남\)',str(workers[0].get('role') or '')):
        return block('INVALID_FEMALE_SLOT','현재 명단에 있는 여성 1명만 배정된 정상 평일이어야 합니다. 빈자리·중복·성별·기존 남성 배정을 먼저 확인해 주세요.')
    original=hs_copy.deepcopy(workers[0])
    if hs_absence(state,original['name'],key):
        return block('FEMALE_ABSENT','유지할 여성에게 승인된 부재가 있습니다. 자동 대체하지 않고 기존 배정을 먼저 확인해 주세요.')
    pool='holiday|M' if ((state.get('rules') or {}).get('fixedOrders') or {}).get('holidayM') else 'weekend|M'
    year=target.year;order=hs_order(state,pool)
    if not order:
        return block('EMPTY_ROTATION','남성 공휴일·공유 주말 고정 순번이 비어 있습니다.')
    if any(event.get('year')==year and event.get('pool_key')!=pool and event.get('credit_state')=='CONSUMED' for event in events):
        return block('BASIS_REBASE_REQUIRED','공휴일 전용 순번과 주말 공유 순번 사이의 설정이 바뀌었습니다. 기존 소비를 다른 묶음으로 자동 이동할 수 없습니다. 발생 회차 재매핑은 이번 버전에서 지원하지 않습니다.')
    for event in hs_active_events(state).values():
        credit=event.get('female_credit') or {}
        if event.get('year')==year and credit.get('order_snapshot',[])!=hs_order(state,'weekday|F'):
            return block('BASIS_REBASE_REQUIRED','기존 여성 평일 순번 목록이 바뀌어 저장된 소비 위치를 그대로 재사용할 수 없습니다. 기존 배정을 유지합니다.')
    basis,records,missing=hs_compute_basis(state,year,pool)
    latest=hs_latest_basis(state,year,pool)
    review=hs_basis_review(state,latest) if latest else {'state':'CURRENT','review_code':None}
    result['rotationBasis']={**hs_copy.deepcopy(latest or basis),**review,
                             'annual_reservation_count':len(records),'missingDates':missing}
    if review['review_code']=='BASIS_REBASE_REQUIRED':
        return block('BASIS_REBASE_REQUIRED','같은 해 순번 목록 또는 정규 예약이 바뀌어 기존 소비 회차를 새 기준으로 옮길 수 없습니다. 발생 회차 재매핑은 이번 버전에서 지원하지 않습니다.')
    inherited=hs_inherited_sources(state,year,pool)
    if inherited and not hs_has_adopted_anchor(state,year,pool):
        result['rotationBasis'].update({'revision':latest['revision'] if latest else 0,
                                       'state':'NEEDS_REVIEW','review_code':'BASIS_RECONCILE_REQUIRED',
                                       'inherited_sources':inherited})
        return block('BASIS_RECONCILE_REQUIRED','이전 연도의 소비 기준이 현재 순번과 일치하지 않습니다. 이전 기록은 보존하며, 이 연도의 1월 1일 시작점을 명시적으로 재확인한 뒤 보완할 수 있습니다.')
    if review['review_code']=='BASIS_RECONCILE_REQUIRED':
        return block('BASIS_RECONCILE_REQUIRED','이전 연도 소비가 바뀌었습니다. 기존 예약 유지 방식으로 이 연도 시작점을 먼저 재확인해 주세요.')
    if missing:
        return block('ANNUAL_RESERVATIONS_INCOMPLETE','대상 연도 같은 순번 묶음에 미배정 날짜가 있어 전체 연간 예약을 확인할 수 없습니다.')
    if latest and not review['review_code']:
        basis=latest
    consumed=[event for event in events if event.get('year')==year and event.get('pool_key')==pool and event.get('credit_state')=='CONSUMED']
    start=max([0]+[event['end_ordinal'] for event in consumed]);skipped=[];chosen=None
    for offset in range(len(order)):
        ordinal=start+offset;position=(basis['ordinary_tail_position']+ordinal)%len(order);name=order[position]
        code=None
        if roster.get(name)!='M':
            code='INVALID_ROSTER_MEMBER';message='현재 남성 명단에 없는 순번입니다.'
        elif hs_absence(state,name,key):
            code='APPROVED_ABSENCE';message='대상일에 승인된 부재가 있습니다.'
        if code:
            skipped.append({**hs_position(state,pool,position),'code':code,'message':message})
            continue
        chosen={**hs_position(state,pool,position),'pool_key':pool,'selected_ordinal':ordinal,'end_ordinal':ordinal+1}
        break
    result['rotationBasis']['skipped_candidates']=skipped
    if chosen is None:
        return block('NO_ELIGIBLE_CANDIDATE','고정 순번에 대상일 근무 가능한 남성이 없습니다. 명단 첫 사람으로 임의 배정하지 않습니다.')
    before_cursor=hs_rotation_indexes(state,key)['weekday|F']
    after_cursor,consumes=hs_worker_cursor(state,'weekday|F',key,original,before_cursor)
    male={'name':chosen['name'],'role':'공휴일(남)','isSubstitute':False,'zeroScore':False}
    female={**original,'role':'공휴일(여)'}
    after_workers=[male,female]
    if not latest or basis['revision']!=latest['revision']:
        bases.append(hs_copy.deepcopy(basis))
    # Snapshot already-published future years BEFORE this addition changes their prior credit.
    future_years=sorted({int(day[:4]) for day,rows in schedules.items() if int(day[:4])>year and rows
                        and hs_category(state,day,'M')+'|M'==pool})
    for future_year in future_years:
        if 2023<=future_year<=2029 and not hs_latest_basis(state,future_year,pool):
            future_basis,_,_=hs_compute_basis(state,future_year,pool);bases.append(future_basis)
    if future_years:
        result['warnings'].append(hs_issue('FUTURE_YEAR_RECONCILE_REQUIRED','이미 배정된 후속 연도는 유지합니다. 해당 연도 같은 순번의 새 보완·재생성 전에 시작점 재확인이 필요합니다.',years=future_years))
    allocation=max([0]+[event.get('allocation_seq',0) for event in events if event.get('year')==year and event.get('pool_key')==pool])+1
    event={'supplement_id':str(draft.get('_operationId') or 'preview-'+hs_fingerprint([key,allocation,state.get('dataVersion',0)])[:24]),
           'date_key':key,'year':year,'allocation_seq':allocation,'state':'ACTIVE','credit_state':'CONSUMED',
           'basis_revision':basis['revision'],'pool_key':pool,'order_snapshot':list(order),'order_fingerprint':hs_fingerprint(order),
           'annual_basis_hash':hs_fingerprint({field:basis[field] for field in ('year','pool_key','revision','sequence_snapshot','prior_year_credit_hash','ordinary_reservation_hash','ordinary_tail_position','anchor_before_year')}),
           'selected_ordinal':chosen['selected_ordinal'],'end_ordinal':chosen['end_ordinal'],'original_female_slot':original,
           'female_credit':{'pool_key':'weekday|F','before':before_cursor,'after':after_cursor,'consumes':consumes,'order_snapshot':hs_order(state,'weekday|F')},
           'current_slot_binding':hs_copy.deepcopy(after_workers),'original_holiday':hs_copy.deepcopy(custom.get(key)),
           'skipped_candidates':hs_copy.deepcopy(skipped),'created_at':''}
    events.append(event);schedules[key]=after_workers;result['_customHolidays']=desired
    result['after']['workers']=hs_copy.deepcopy(after_workers);result['candidate']=chosen
    last=next((row for row in reversed(records) if row['workers']),None)
    last_position=hs_position(state,pool,(basis['ordinary_tail_position']-1)%len(order))
    result['rotationBasis']={**hs_copy.deepcopy(basis),'state':'CURRENT','review_code':None,
        'annual_reservation_count':len(records),'lastReservation':({**last_position,'date_key':last['date_key'],'name':last['workers'][-1]['name']} if last else None),
        'nextPosition':hs_position(state,pool,basis['ordinary_tail_position']+chosen['end_ordinal']),
        'selected_ordinal':chosen['selected_ordinal'],'end_ordinal':chosen['end_ordinal'],'skipped_candidates':skipped,
        'countsByName':{name:sum(1 for day,rows in (state.get('schedules') or {}).items() if day.startswith(str(year)+'-') and not (state.get('noDuty') or {}).get(day)
                                and hs_category(state,day,'M')+'|M'==pool for row in rows if row.get('name')==name) for name in dict.fromkeys(order)}}
    # Visible renderStats counts a weekday as 1 point; its holiday zero-score
    # exception is conditional on the current rule (default true in dutyRule).
    zero_applies=bool(original.get('zeroScore') and (state.get('rules') or {}).get('substituteScoreZero',True))
    result['impacts'].append(hs_issue('STATISTICS_CHANGE','여성의 0점 표시는 보존하고 실제 공휴일 점수에는 현재 대체 0점 규칙을 적용합니다. 여성 평일 순번은 기존 소비를 보존합니다.',
        changes=[{'name':original['name'],'beforeCategory':'weekday','afterCategory':'holiday','beforePoints':1,'afterPoints':0 if zero_applies else 2},
                 {'name':chosen['name'],'beforeCategory':None,'afterCategory':'holiday','beforePoints':0,'afterPoints':2}]))
    for adjacent in (hs_shift(key,-1),hs_shift(key,1)):
        if any(worker.get('name')==chosen['name'] for worker in (state.get('schedules') or {}).get(adjacent,[])) and not (state.get('noDuty') or {}).get(adjacent):
            result['warnings'].append(hs_issue('CONSECUTIVE_DUTY','추천 남성의 인접 날짜 근무가 있습니다. 기존 연속 근무 확인 규칙에 따라 검토해 주세요.',date_key=adjacent))
    return finish('ADD',True)
# END HOLIDAY CALCULATION


def get_db_connection(readonly=None):
    '모든 DB 접근은 이 함수를 거친다. (row_factory 누락으로 인한 결재 실패 방지)'
    if SHARED_PATH:
        require_existing_shared_database()
    ro = APP_MODE == 'VIEWER' if readonly is None else readonly
    if ro:
        if not os.path.exists(DB_PATH):
            log_error(f'당직 데이터 파일 없음: {DB_PATH}')
            raise HTTPException(status_code=503, detail='공유 폴더에서 당직 데이터 파일을 찾을 수 없습니다. 공유 폴더에 연결되어 있는지 확인하세요. 연결되면 자동으로 다시 불러옵니다.')
        try:
            conn = open_readonly(DB_PATH)
        except sqlite3.OperationalError as e:
            log_error(f'당직 데이터 파일 열기 실패: {DB_PATH}')
            raise HTTPException(status_code=503, detail=friendly_db_error(e, '당직 데이터 파일을 열'))
    else:
        if SHARED_PATH:
            conn = sqlite3.connect(build_readonly_uri(DB_PATH).replace('?mode=ro', '?mode=rw'),
                                   uri=True, timeout=DB_TIMEOUT)
        else:
            conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT)
        conn.execute(f'PRAGMA busy_timeout = {busy_timeout_ms()};')
        # v3.16.0 [위험] TRUNCATE 는 WAL 과 달리 파일에 남지 않는 '연결별' 설정이다.
        #   예전에는 init_db 연결에서만 걸어, 실제 저장·결재 연결은 기본값(DELETE)으로 돌았다.
        #   공유 폴더에서 저널 파일을 매번 만들고 지우지 않도록 쓰기 연결마다 건다.
        try:
            conn.execute('PRAGMA journal_mode=TRUNCATE;')
        except sqlite3.Error as e:
            log_error(f'journal_mode 설정 실패(운영에는 영향 없음): {e}')
    conn.row_factory = sqlite3.Row
    conn.create_function('wlr_schedule_contract', 0, lambda: 1)
    request_access_schema.register_connection(conn)
    return conn


def busy_timeout_ms():
    # v3.16.0 db_timeout_seconds 를 실제로 반영한다. 예전에는 30000 고정이라 설정을 바꿔도 효과가 없었다.
    try:
        return max(100, int(float(DB_TIMEOUT) * 1000))
    except Exception:
        return 30000


def josa(word, pair='을/를'):
    """v3.18.0 받침에 맞춰 조사를 붙인다. josa('신청자', '을/를') -> '신청자를'. 끝의 괄호는 읽지 않는다."""
    word = '' if word is None else str(word)
    with_final, without_final = pair.split('/')
    core = re.sub(r'(\s*[(\[][^()\[\]]*[)\]])+\s*$', '', word).rstrip(" '\"")
    ch = core[-1:] if core else ''
    final = ''
    if ch and '가' <= ch <= '힣':
        jong = (ord(ch) - 0xAC00) % 28
        final = '' if jong == 0 else ('ㄹ' if jong == 8 else 'x')
    elif ch in '1780':
        final = 'ㄹ' if ch in '178' else 'x'
    elif ch in '36':
        final = 'x'
    if with_final == '으로':
        return word + ('으로' if final and final != 'ㄹ' else '로')
    return word + (with_final if final else without_final)


def month_label(month):
    """'2026-10' -> '2026년 10월' (화면 문장용)."""
    m = re.match(r'^(\d{4})-(\d{2})$', str(month or ''))
    return f'{int(m.group(1))}년 {int(m.group(2))}월' if m else str(month or '')


def friendly_db_error(e, action='처리'):
    """v3.16.0 원시 예외 대신 비개발자가 다음 행동을 알 수 있는 문장.
    v3.18.0 원문(영어 예외)은 화면에 붙이지 않고 로그에만 남긴다. 관리자는 [자가 진단]의 최근 오류에서 본다."""
    text = str(e)
    low = text.lower()
    try:
        log_error(f'{action} 실패(원문): {text}')
    except Exception:
        pass
    if 'locked' in low or 'busy' in low:
        guide = '다른 PC가 저장 중입니다. 잠시 후 다시 시도해 주세요.'
    elif 'readonly' in low or 'read-only' in low or 'permission' in low or 'access is denied' in low:
        guide = '이 PC에 당직 데이터 파일 쓰기 권한이 없습니다. 공유 폴더 권한을 관리자에게 확인해 주세요.'
    elif 'disk i/o' in low or 'network' in low or 'i/o error' in low:
        guide = '공유 폴더 연결이 불안정합니다. 네트워크 연결을 확인하고 다시 시도해 주세요.'
    elif 'unable to open' in low or 'no such file' in low:
        guide = '당직 데이터 파일을 열 수 없습니다. 공유 폴더에 연결되어 있는지 확인해 주세요.'
    elif 'malformed' in low or 'not a database' in low or 'corrupt' in low:
        guide = '당직 데이터 파일이 손상된 것 같습니다. 관리자가 [더보기 → 자동 백업으로 되돌리기]를 확인해 주세요.'
    elif 'disk is full' in low or 'no space' in low:
        guide = '저장 공간이 부족합니다. 공유 폴더의 여유 공간을 확인해 주세요.'
    else:
        guide = '잠시 후 다시 시도하고, 계속되면 [연결 정보 → 자가 진단 열기 → 진단 리포트 저장]으로 만든 파일을 관리자에게 전달해 주세요.'
    return f'{action}하지 못했습니다. {guide}'


def read_data_version(cur):
    '현재 데이터 버전을 읽는다. (예전 DB에는 없을 수 있으므로 0으로 간주)'
    try:
        has = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone()[0]
        if not has:
            return 0
        row = cur.execute("SELECT value FROM app_settings WHERE key = 'data_version'").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def bump_data_version(cur, source=''):
    '저장이 성공할 때마다 버전을 1 올린다. (동시 저장 충돌 감지·자동 새로고침용)'
    new_v = read_data_version(cur) + 1
    cur.execute("INSERT INTO app_settings (key, value) VALUES ('data_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(new_v),))
    stamp_last_write(cur, source)
    return new_v


def stamp_last_write(cur, source=''):
    # v3.18.0 source: 이 저장이 무엇 때문에 생겼는지('request:<id>:<status>' 등). 조회용 화면이
    #   결재 한 건 때문에 생긴 '당직표 갱신' 알림을 결과 알림 하나로 합치는 데 쓴다. 일반 저장은 빈 값.
    try:
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('last_write_source', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(source or '')[:100],))
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('last_write_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (APP_VERSION,))
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('last_write_host', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (HOST_LABEL,))
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('last_write_at', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (datetime.now().strftime('%Y-%m-%d %H:%M:%S'),))
        row = cur.execute("SELECT value FROM app_settings WHERE key = 'max_write_version'").fetchone()
        prev_max = (row[0] if row else '') or ''
        if not prev_max or version_tuple(APP_VERSION) > version_tuple(prev_max):
            cur.execute("INSERT INTO app_settings (key, value) VALUES ('max_write_version', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (APP_VERSION,))
    except Exception as e:
        log_error(f'마지막 저장 정보 기록 실패(운영에는 영향 없음): {e}')


def read_last_write(cur):
    '마지막으로 저장한 프로그램 정보를 읽는다. (없으면 빈 값)'
    out = {'version': '', 'host': '', 'at': '', 'max': '', 'source': ''}
    try:
        has = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='app_settings'").fetchone()[0]
        if not has:
            return out
        for key, field in (('last_write_version', 'version'), ('last_write_host', 'host'), ('last_write_at', 'at'), ('max_write_version', 'max'), ('last_write_source', 'source')):
            row = cur.execute('SELECT value FROM app_settings WHERE key = ?', (key,)).fetchone()
            if row:
                out[field] = row[0] or ''
    except Exception:
        pass
    return out


def version_tuple(text):
    "'3.10.0' > '3.9.0' 이 되도록 숫자 세 자리로 비교한다. (문자열 비교 사고 방지)"
    parts = []
    for chunk in str(text or '0').split('.')[:3]:
        digits = ''.join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def backup_dir_path():
    return os.path.join(STORAGE_DIR, 'backups')


def _verify_sqlite_file(path):
    """백업 파일이 실제로 열리고 quick_check 를 통과하는지. 실패 사유 문자열, 정상이면 ''."""
    try:
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return '파일이 비어 있거나 없습니다'
        uri = Path(os.path.abspath(path)).as_uri() + '?mode=ro'
        if os.path.abspath(path).startswith(chr(92) * 2):
            uri = build_readonly_uri(path)
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            result = conn.execute('PRAGMA quick_check').fetchone()[0]
            if result != 'ok':
                return f'무결성 검사 실패: {result}'
            conn.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
        finally:
            conn.close()
        return ''
    except Exception as e:
        return f'열 수 없습니다: {e}'


def _snapshot_to(target):
    """현재 DB 를 target 으로 복사한다. 임시 파일에 받고 검사를 통과한 뒤에만 이름을 바꾼다.
    중간에 끊겨 반쯤 쓰인 파일이 '오늘 백업' 행세를 하지 못하게 하기 위해서다."""
    folder = os.path.dirname(target)
    os.makedirs(folder, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.backup-', suffix='.tmp', dir=folder)
    os.close(descriptor)
    try:
        src = get_db_connection(readonly=True)
        try:
            dst = sqlite3.connect(temporary)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        problem = _verify_sqlite_file(temporary)
        if problem:
            raise RuntimeError(f'백업 검증 실패: {problem}')
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            try:
                os.remove(temporary)
            except Exception:
                pass


def _prune(folder, pattern, keep, by_mtime=False, protect=None):
    """오래된 파일부터 지운다. 일일 백업은 이름(날짜)이 곧 순서지만, 직전 사본(prerestore_v{N}·
    premigration_{버전})은 이름순이 시간순이 아니다(v100 < v99, 3.10.0 < 3.9.0).
    v3.17.13 그런 파일은 수정 시각으로 정렬하고, 방금 만든 사본(protect)은 어떤 경우에도 지우지 않는다."""
    names = [f for f in os.listdir(folder) if pattern.match(f)]
    if by_mtime:
        def order(name):
            try:
                return (os.path.getmtime(os.path.join(folder, name)), name)
            except OSError:
                return (0.0, name)
        files = sorted(names, key=order)
    else:
        files = sorted(names)
    keep_name = os.path.basename(protect) if protect else ''
    for old in files[:-keep] if keep else files:
        if old == keep_name:
            continue
        try:
            os.remove(os.path.join(folder, old))
        except Exception:
            pass


def _mirror_backup(path):
    """v3.16.0 [C-4 완화] 백업이 DB 와 같은 공유 폴더에만 있으면 폴더 하나를 잃을 때 함께 사라진다.
    config 의 backup_mirror_dir 에 같은 파일을 한 벌 더 둔다(선택 사항)."""
    mirror = _cfg('backup_mirror_dir', '')
    if not mirror:
        BACKUP_STATE['mirror'] = ''
        return
    BACKUP_STATE['mirror'] = mirror
    try:
        if not os.path.isabs(mirror):
            raise RuntimeError('backup_mirror_dir 는 절대 경로여야 합니다')
        os.makedirs(mirror, exist_ok=True)
        target = os.path.join(mirror, os.path.basename(path))
        if os.path.exists(target) and not _verify_sqlite_file(target):
            BACKUP_STATE['mirrorDetail'] = ''
            return
        descriptor, temporary = tempfile.mkstemp(prefix='.mirror-', suffix='.tmp', dir=mirror)
        os.close(descriptor)
        try:
            shutil.copyfile(path, temporary)
            problem = _verify_sqlite_file(temporary)
            if problem:
                raise RuntimeError(problem)
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        _prune(mirror, DAILY_BACKUP_RE, BACKUP_KEEP)
        BACKUP_STATE['mirrorDetail'] = ''
        log_info(f'백업 사본 저장: {target}')
    except Exception as e:
        BACKUP_STATE['mirrorDetail'] = str(e)
        log_error(f'백업 사본 저장 실패(운영에는 영향 없음): {e}')


def ensure_daily_backup():
    '''
하루 한 번, 변경 전 상태를 backups 폴더에 스냅샷으로 남긴다. (관리자 전용)
프로그램 시작 시와 저장·결재 직전에 호출되므로, 사람이 잊어도 매일 첫 변경 전
상태가 자동으로 보관된다. 실패해도 운영은 계속된다.

v3.16.0 [위험] 예전에는 오늘 날짜 파일이 '있기만 하면' 정상으로 보고했다. 중간에 끊겨
0바이트나 반쪽 파일이 남아도 그날은 다시 백업하지 않았다. 이제 파일을 그날 한 번 검사하고,
깨져 있으면 다시 만든다. 새 백업은 임시 파일에서 검사를 통과한 뒤에만 이름을 얻는다.
'''
    if APP_MODE != 'ADMIN':
        return
    # v3.17.13 저장·결재 핸들러가 스레드에서 동시에 돌 수 있어, 같은 PC 안에서는 하루 백업을 한 번에 하나만 만든다.
    with DAILY_BACKUP_LOCK:
        _ensure_daily_backup_locked()


DAILY_BACKUP_LOCK = threading.Lock()


@contextmanager
def _daily_backup_file_lock(folder):
    """All PCs lock the same stable file; never unlink it while another PC may wait."""
    os.makedirs(folder, exist_ok=True)
    flags = os.O_CREAT | os.O_RDWR | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(os.path.join(folder, '.daily-backup.lock'), flags, 0o600)
    locked = False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise OSError('백업 잠금 파일이 올바르지 않습니다.')
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'\0')
        else:
            import fcntl
        deadline = time.monotonic() + max(1.0, DB_TIMEOUT)
        while True:
            try:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        yield
    finally:
        try:
            if locked:
                if os.name == 'nt':
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _ensure_daily_backup_locked():
    try:
        with _daily_backup_file_lock(backup_dir_path()):
            _ensure_daily_backup_file_locked()
    except Exception as e:
        BACKUP_STATE['ok'] = False
        BACKUP_STATE['detail'] = str(e)
        log_error(f'자동 백업 실패(운영에는 영향 없음): {e}')


def _ensure_daily_backup_file_locked():
    if not os.path.exists(DB_PATH):
        return
    today = datetime.now().strftime('%Y-%m-%d')
    folder = backup_dir_path()
    target = os.path.join(folder, f'smart_duty_{today}.db')
    if os.path.exists(target):
        problem = _verify_sqlite_file(target)
        if not problem:
            BACKUP_STATE.update(ok=True, detail='', verified=today)
            _mirror_backup(target)
            return
        log_error(f'오늘 백업 파일이 손상되어 다시 만듭니다: {target} ({problem})')
        broken = target + f'.broken-{datetime.now().strftime("%H%M%S")}'
        try:
            os.replace(target, broken)
        except Exception:
            pass
    _snapshot_to(target)
    log_info(f'자동 백업 저장: {target}')
    BACKUP_STATE.update(ok=True, detail='', verified=today)
    _prune(folder, DAILY_BACKUP_RE, BACKUP_KEEP)
    _mirror_backup(target)

def take_named_snapshot(kind, label=''):
    """스키마 변경 직전(premigration)·복원 직전(prerestore) 스냅샷. 일일 백업과 별도로 보관한다.
    실패하면 예외를 그대로 올린다 — 이 스냅샷이 없으면 되돌릴 근거가 없으므로 호출 측이 멈춰야 한다."""
    assert kind in ('premigration', 'prerestore')
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    safe = re.sub(r'[^0-9A-Za-z_.-]', '_', label or APP_VERSION)
    folder = backup_dir_path()
    target = os.path.join(folder, f'{kind}_{safe}_{stamp}.db')
    _snapshot_to(target)
    _prune(folder, re.compile(rf'^{kind}_[0-9A-Za-z_.-]+\.db$'), SNAPSHOT_KEEP, by_mtime=True, protect=target)
    log_info(f'{kind} 스냅샷 저장: {target}')
    return target


def take_auto_snapshot(kind):
    """v3.18.0 되돌리기용 자동 사본 backups/auto_<kind>_<YYYYmmdd-HHMMSS-mmm>.db.
    검증을 통과한 파일만 이름을 얻고(_snapshot_to), 자동 사본끼리 수정 시각 기준 최근 AUTO_SNAPSHOT_KEEP 개만 남긴다.
    실패하면 예외를 올린다 — 호출 측이 멈출지(불러오기·일괄 작업) 계속할지(승인) 정한다."""
    if kind not in AUTO_SNAPSHOT_KINDS:
        raise ValueError(f'알 수 없는 자동 사본 종류: {kind}')
    now = datetime.now()
    folder = backup_dir_path()
    base = f"auto_{kind}_{now.strftime('%Y%m%d-%H%M%S')}-{now.microsecond // 1000:03d}"
    target = os.path.join(folder, base + '.db')
    serial = 0
    while os.path.exists(target):
        serial += 1
        target = os.path.join(folder, f'{base[:-3]}{(now.microsecond // 1000 + serial) % 1000:03d}.db')
    _snapshot_to(target)
    _prune(folder, re.compile(r'^auto_[a-z]+_[0-9-]+\.db$'), AUTO_SNAPSHOT_KEEP, by_mtime=True, protect=target)
    log_info(f'자동 사본 저장({AUTO_SNAPSHOT_KINDS[kind]}): {target}')
    return target


def snapshot_label(name, modified=None):
    """백업 파일을 사람이 읽는 이름으로. 예: '자동 배정 직전 (14:32)', 다른 날이면 '10/13 14:32'."""
    match = AUTO_SNAPSHOT_RE.match(name or '')
    manual = MANUAL_FULL_BACKUP_RE.fullmatch(name or '')
    if manual:
        kind_label = '회식 포함 전체 수동 백업'
        try:
            when = datetime.strptime(manual.group(1) + manual.group(2), '%Y%m%d%H%M%S')
        except ValueError:
            when = modified
    elif match:
        kind_label = AUTO_SNAPSHOT_KINDS.get(match.group(1), '자동 사본')
        try:
            when = datetime.strptime(match.group(2) + match.group(3), '%Y%m%d%H%M%S')
        except ValueError:
            when = modified
    else:
        kind = 'daily' if DAILY_BACKUP_RE.match(name or '') else (name or '').split('_', 1)[0]
        kind_label = SNAPSHOT_KIND_LABELS.get(kind, kind)
        when = modified
        if when is None and name:
            try:
                when = datetime.fromtimestamp(os.path.getmtime(os.path.join(backup_dir_path(), os.path.basename(name))))
            except OSError:
                when = None
    if not when:
        return kind_label
    stamp = when.strftime('%H:%M') if when.date() == datetime.now().date() else f'{when.month}/{when.day} {when:%H:%M}'
    return f'{kind_label} ({stamp})'


# 'testclient' 는 시험용 가짜 주소다. 실제 접속 주소는 IP 라 이 이름과 겹치지 않는다.
LOOPBACK_HOSTS = {'127.0.0.1', '::1', 'localhost', 'testclient'}


def require_diag_access(token: Optional[str], request: Optional[Request] = None):
    "\nv3.9.1 [D] 진단·경로 확인 전용 관문.\n\n이 응답에는 DB 경로·공유 폴더 경로·PC 이름과 로그의 최근 오류 5줄이 들어 있고,\ncheck-path 는 임의 폴더의 존재와 쓰기 가능 여부까지 알려 준다.\nadmin_password 를 설정했다는 것은 서버를 사내망에 열었다는 뜻이므로(bind_host != 127.0.0.1)\n그때는 반드시 토큰을 요구한다.\n\nrequire_admin 을 쓰지 않는 이유: 조회용 PC 도 '왜 내 화면이 다른가'를 자기 PC 에서\n진단할 수 있어야 한다. 모드가 아니라 '암호를 걸었는지'로만 판단한다.\n"
    if ADMIN_PASSWORD and token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail='관리자 암호가 필요합니다.')
    # v3.16.0 [C-3] 암호 없이 사내망에 열린 경우(bind_host 가 127.0.0.1 이 아님)에는 진단·경로 확인을
    #   그 PC 자신만 쓸 수 있게 한다. 예전에는 사내망 누구나 임의 폴더의 존재·쓰기 가능 여부를 탐색할 수 있었다.
    if not ADMIN_PASSWORD and BIND_HOST not in ('127.0.0.1', 'localhost') and request is not None:
        client = (request.client.host if request.client else '') or ''
        if client not in LOOPBACK_HOSTS:
            raise HTTPException(status_code=403, detail='이 화면은 프로그램을 실행한 PC에서만 볼 수 있습니다. 다른 PC에서 보려면 관리자 암호를 설정해 주세요.')


def require_admin(token: Optional[str]):
    '쓰기 작업 공통 관문: 모드 확인 + (설정된 경우) 암호 확인.'
    if APP_MODE != 'ADMIN':
        raise HTTPException(status_code=403, detail='조회용에서는 당직표를 바로 고칠 수 없습니다. [당직 변경 신청]을 이용하세요.')
    if ADMIN_PASSWORD and token != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail='관리자 암호가 필요합니다.')


def ensure_change_request_schema(cur):
    '구형 DB에 Viewer가 먼저 신청해도 휴가·부재 필드를 저장할 수 있게 한다.'
    cur.execute("CREATE TABLE IF NOT EXISTS change_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, requester_name TEXT NOT NULL, target_date TEXT NOT NULL, request_type TEXT NOT NULL, swap_date TEXT, swap_worker TEXT, substitute_name TEXT, is_zero_score INTEGER DEFAULT 1, reason TEXT, status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL, processed_at TEXT, absence_type TEXT, absence_end_date TEXT, receipt_token TEXT)")
    cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
    added = []
    if 'absence_type' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN absence_type TEXT')
        added.append('absence_type')
    if 'absence_end_date' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN absence_end_date TEXT')
        added.append('absence_end_date')
    if 'receipt_token' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN receipt_token TEXT')
        added.append('receipt_token')
    if 'reject_reason' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN reject_reason TEXT')
        added.append('reject_reason')
    # v3.16.0 [C-2] 신청이 어느 PC·어느 Windows 계정에서 들어왔는지. 사칭을 막지는 못하지만
    #   '누가 올렸는가' 를 나중에 확인할 근거를 남긴다.
    for col in ('requester_host', 'requester_account'):
        if col not in cols:
            cur.execute(f"ALTER TABLE change_requests ADD COLUMN {col} TEXT DEFAULT ''")
            added.append(col)
    if 'absence_details' not in cols:
        cur.execute("ALTER TABLE change_requests ADD COLUMN absence_details TEXT DEFAULT ''")
        added.append('absence_details')
    if 'revision' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN revision INTEGER NOT NULL DEFAULT 0')
        added.append('revision')
    if 'approved_history_id' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN approved_history_id INTEGER')
        added.append('approved_history_id')
    if 'amends_request_id' not in cols:
        cur.execute('ALTER TABLE change_requests ADD COLUMN amends_request_id INTEGER')
        added.append('amends_request_id')
    if 'amendment_original' not in cols:
        cur.execute("ALTER TABLE change_requests ADD COLUMN amendment_original TEXT DEFAULT ''")
        added.append('amendment_original')
    cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_change_requests_pending_amendment "
                "ON change_requests(amends_request_id) WHERE amends_request_id IS NOT NULL AND status = 'PENDING'")
    # 구버전 관리자는 모르는 신청 종류를 거부한다. 구버전 Viewer의 EDIT로 이 보호가 풀리지 않게 한다.
    cur.execute('DROP TRIGGER IF EXISTS guard_absence_amendment_kind')
    cur.execute("CREATE TRIGGER guard_absence_amendment_kind BEFORE UPDATE OF request_type ON change_requests "
                "WHEN OLD.amends_request_id IS NOT NULL AND NEW.request_type IS NOT OLD.request_type "
                "BEGIN SELECT RAISE(ABORT, '휴가 변경·취소 신청은 최신 OnAir에서 처리해 주세요.'); END")
    has_index = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index' AND name='idx_change_requests_receipt_token'").fetchone()[0]
    if not has_index:
        # v3.17.13 옛 DB 에는 토큰이 '' 인 행이 여럿 있을 수 있다. '' 는 '토큰 없음' 이므로 NULL 로 바꾼 뒤
        #   색인을 만든다. 그러지 않으면 고유 색인 생성이 실패해 신청 접수·결재가 모두 막힌다.
        cur.execute("UPDATE change_requests SET receipt_token = NULL WHERE receipt_token = ''")
        cur.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_change_requests_receipt_token ON change_requests(receipt_token) WHERE receipt_token IS NOT NULL')
    request_access_schema.ensure_request_access_schema(cur)
    return added


HISTORY_EXTRA_COLUMNS = ('actor', 'host')
HISTORY_JSON_COLUMNS = ('before_json', 'after_json')
EDUCATION_FIELDS = ('title', 'location', 'provider', 'preparation', 'content', 'notes')


def ensure_absence_schema(cur):
    cols = {r[1] for r in cur.execute('PRAGMA table_info(absences)').fetchall()}
    if 'education_json' not in cols:
        cur.execute("ALTER TABLE absences ADD COLUMN education_json TEXT DEFAULT ''")
        return ['education_json']
    return []


def normalize_education(value):
    """교육 정보는 모두 선택 문자열이며 신청 사유와 별도로 저장한다."""
    if value is None:
        return None
    if not isinstance(value, dict) or any(not isinstance(value.get(key, ''), str) for key in EDUCATION_FIELDS):
        raise HTTPException(status_code=400, detail='교육 정보의 각 항목은 문자열로 입력해 주세요.')
    return {key: value.get(key, '') for key in EDUCATION_FIELDS}


def normalize_absence_id(value):
    """구형 JSON의 정수 문자열도 동일 ID로 조회하되 SQLite가 받을 수 있는 값만 허용한다."""
    if value is None:
        return None
    if isinstance(value, str) and re.fullmatch(r'[+-]?[0-9]+', value.strip()):
        try:
            value = int(value)
        except ValueError:
            pass
    if type(value) is not int or not -(2 ** 63) <= value < 2 ** 63:
        raise HTTPException(status_code=400, detail='부재 기록 형식이 올바르지 않습니다. 화면을 새로고침(F5)한 뒤 다시 저장해 주세요.')
    return value


def optional_json(value):
    if not value:
        return None
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, (dict, list)) else None
    except (TypeError, ValueError):
        return None


def read_absences(cur):
    cols = {r[1] for r in cur.execute('PRAGMA table_info(absences)').fetchall()}
    extra = 'education_json' if 'education_json' in cols else "'' AS education_json"
    rows = cur.execute(f'SELECT id, name, type, start_date, end_date, {extra} FROM absences ORDER BY start_date DESC, id').fetchall()
    result = []
    for row in rows:
        item = {'id': row['id'], 'name': row['name'], 'type': row['type'], 'start': row['start_date'], 'end': row['end_date']}
        education = optional_json(row['education_json'])
        if isinstance(education, dict):
            item['education'] = education
        result.append(item)
    return result


def domain_snapshot(cur):
    schedules = {}
    rows = cur.execute('SELECT date_key, worker_name, role, is_substitute, zero_score FROM schedules ORDER BY date_key, slot_index').fetchall()
    for row in rows:
        schedules.setdefault(row['date_key'], []).append({
            # v3.17.13 옛 DB 의 role NULL 은 '' 로 남긴다. 전후 기록에 null 이 들어가면 JSON 백업 복구가 거절했다.
            'name': row['worker_name'], 'role': row['role'] if row['role'] is not None else '',
            'isSubstitute': bool(row['is_substitute']), 'zeroScore': bool(row['zero_score'])})
    return {'schedules': schedules, 'absences': read_absences(cur), 'holidaySupplements': hs_metadata_snapshot(cur)}


def domain_changes(before, after):
    """서버가 실제로 바꾼 날짜와 부재 기록만 전후 비교에 남긴다."""
    old, new = {}, {}
    dates = sorted(set(before['schedules']) | set(after['schedules']))
    changed_dates = [key for key in dates if before['schedules'].get(key, []) != after['schedules'].get(key, [])]
    if changed_dates:
        old['schedules'] = {key: before['schedules'].get(key, []) for key in changed_dates}
        new['schedules'] = {key: after['schedules'].get(key, []) for key in changed_dates}
    old_records = {item['id']: item for item in before['absences']}
    new_records = {item['id']: item for item in after['absences']}
    changed_ids = {key for key in set(old_records) | set(new_records) if old_records.get(key) != new_records.get(key)}
    if changed_ids:
        old['absences'] = [item for item in before['absences'] if item['id'] in changed_ids]
        new['absences'] = [item for item in after['absences'] if item['id'] in changed_ids]
    if before.get('holidaySupplements') != after.get('holidaySupplements'):
        old['holidaySupplements'] = before['holidaySupplements']
        new['holidaySupplements'] = after['holidaySupplements']
    return (old, new) if old else (None, None)


def imported_history_snapshots(record):
    """백업 전후값은 검증 후 그대로 보존한다. 현재 복원 작업의 비교로 대체하지 않는다."""
    before, after = record.get('before'), record.get('after')
    if before is None and after is None:
        return None, None  # 전후 비교가 없던 구형 이력

    def invalid():
        raise HTTPException(status_code=400, detail='가져온 이력의 전후 기록 형식이 올바르지 않습니다.')

    for snapshot in (before, after):
        if not isinstance(snapshot, dict) or not snapshot or set(snapshot) - {'schedules', 'absences', 'customHolidays', 'holidaySupplements'}:
            invalid()
        if 'holidaySupplements' in snapshot:
            validate_holiday_supplements(snapshot['holidaySupplements'])
        if 'customHolidays' in snapshot:
            if not isinstance(snapshot['customHolidays'], dict):
                invalid()
            for day, row in snapshot['customHolidays'].items():
                strict_iso_date(day, '이력 공휴일 날짜')
                if row is not None and (not isinstance(row, dict) or set(row) - {'name','isMajor','isRemoved'} or not isinstance(row.get('name'), str) or any(key in row and type(row[key]) is not bool for key in ('isMajor','isRemoved'))):
                    invalid()
        if 'schedules' in snapshot:
            if not isinstance(snapshot['schedules'], dict):
                invalid()
            for day, workers in snapshot['schedules'].items():
                if not isinstance(day, str) or strict_iso_date(day, '이력 날짜') != day or not isinstance(workers, list):
                    invalid()
                for worker in workers:
                    if not isinstance(worker, dict) or not isinstance(worker.get('name'), str):
                        invalid()
                    if set(worker) - {'name', 'role', 'isSubstitute', 'zeroScore'}:
                        invalid()
                    if 'role' in worker and not isinstance(worker['role'], str):
                        invalid()
                    if any(key in worker and type(worker[key]) is not bool for key in ('isSubstitute', 'zeroScore')):
                        invalid()
        if 'absences' in snapshot:
            if not isinstance(snapshot['absences'], list):
                invalid()
            seen = set()
            for item in snapshot['absences']:
                if not isinstance(item, dict) or type(item.get('id')) is not int:
                    invalid()
                record_id = normalize_absence_id(item['id'])
                if record_id in seen or set(item) - {'id', 'name', 'type', 'start', 'end', 'education'}:
                    invalid()
                seen.add(record_id)
                if any(not isinstance(item.get(key), str) for key in ('name', 'type', 'start', 'end')):
                    invalid()
                if any(strict_iso_date(item[key], '이력 부재일') != item[key] for key in ('start', 'end')) or item['start'] > item['end']:
                    invalid()
                if item.get('education') is not None:
                    normalize_education(item['education'])
                    if set(item['education']) - set(EDUCATION_FIELDS):
                        invalid()
    if set(before) != set(after):
        invalid()
    if 'schedules' in before and set(before['schedules']) != set(after['schedules']):
        invalid()
    return before, after


def ensure_history_schema(cur):
    """v3.16.0 [B-2] 변경 이력에 '어느 PC 의 어느 Windows 계정이' 를 남긴다. 관리자 암호가 공용이라
    사람을 식별할 수단이 없었다. 이 두 칸은 서버가 채우며 화면이 보낸 값으로 바뀌지 않는다."""
    cols = {r[1] for r in cur.execute('PRAGMA table_info(change_history)').fetchall()}
    added = []
    for col in HISTORY_EXTRA_COLUMNS + HISTORY_JSON_COLUMNS:
        if col not in cols:
            cur.execute(f"ALTER TABLE change_history ADD COLUMN {col} TEXT DEFAULT ''")
            added.append(col)
    return added


def ensure_client_status_schema(cur):
    cur.execute('CREATE TABLE IF NOT EXISTS client_status (host TEXT NOT NULL, role TEXT NOT NULL, '
                'app_version TEXT, account TEXT, managed INTEGER DEFAULT 0, first_seen TEXT, last_seen TEXT, '
                "detail TEXT DEFAULT '', PRIMARY KEY (host, role))")


def insert_history(cur, timestamp, kind, date_key, details, before=None, after=None):
    """change_history 에 쓰는 유일한 경로. 서버 기준 PC 이름·계정을 붙인다(구조가 옛것이면 생략)."""
    cols = {r[1] for r in cur.execute('PRAGMA table_info(change_history)').fetchall()}
    names = ['timestamp', 'type', 'date_key', 'details']
    values = [timestamp, kind, date_key, details]
    for key, value in (('actor', ACTOR_LABEL), ('host', HOST_LABEL), ('before_json', before), ('after_json', after)):
        if key in cols:
            names.append(key)
            values.append(json.dumps(value, ensure_ascii=False) if key in HISTORY_JSON_COLUMNS and value is not None else
                          ('' if key in HISTORY_JSON_COLUMNS else value))
    cur.execute(f"INSERT INTO change_history ({', '.join(names)}) VALUES ({', '.join('?' for _ in names)})", values)


MONTH_RE = re.compile(r'^\d{4}-(0[1-9]|1[0-2])$')


def read_closed_months(cur):
    """v3.16.0 [B-3] 마감한 달 목록. 비어 있으면(기본) 아무것도 막지 않는다."""
    try:
        row = cur.execute("SELECT value FROM app_settings WHERE key = 'closed_months'").fetchone()
        if not row or not row[0]:
            return set()
        data = json.loads(row[0])
        return {m for m in data if isinstance(m, str) and MONTH_RE.match(m)}
    except Exception:
        return set()


def closed_month_of(date_key, closed):
    month = (date_key or '')[:7]
    return month if month in closed else ''


def closed_months_in_range(start, end, closed):
    return sorted(m for m in closed if start[:7] <= m <= end[:7])


def _norm_workers(items):
    return [(n or '', r or '당직', bool(sub), bool(z)) for n, r, sub, z in items]


def closed_month_conflicts(cur, dto, closed):
    """저장하려는 내용이 마감된 달의 당직·휴무·메모를 바꾸는지. 바뀌는 날짜 목록."""
    if not closed:
        return []
    changed = set()
    in_closed = lambda k: (k or '')[:7] in closed
    if dto.schedules is not None:
        before = {}
        for r in cur.execute('SELECT date_key, worker_name, role, is_substitute, zero_score FROM schedules ORDER BY date_key, slot_index'):
            if in_closed(r[0]):
                before.setdefault(r[0], []).append((r[1], r[2], r[3], r[4]))
        after = {}
        for dk, wlist in (dto.schedules or {}).items():
            if in_closed(dk) and wlist:
                after[dk] = [(w.get('name'), w.get('role', '당직'), w.get('isSubstitute'), w.get('zeroScore')) for w in wlist]
        for dk in set(before) | set(after):
            if _norm_workers(before.get(dk, [])) != _norm_workers(after.get(dk, [])):
                changed.add(dk)
    if dto.noDuty is not None or dto.comments is not None:
        meta = {r[0]: (r[1] or '', r[2], r[3] or '') for r in cur.execute(
            "SELECT date_key, comment, is_no_duty, COALESCE(no_duty_reason, '') FROM day_meta") if in_closed(r[0])}
        if dto.noDuty is not None:
            b = {k: v[2] for k, v in meta.items() if v[1]}
            a = {k: (v if isinstance(v, str) else '') for k, v in (dto.noDuty or {}).items() if v and in_closed(k)}
            changed |= {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
        if dto.comments is not None:
            b = {k: v[0] for k, v in meta.items() if v[0]}
            a = {k: t for k, t in (dto.comments or {}).items() if t and in_closed(k)}
            changed |= {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    # v3.17.13 [위험] 예전에는 휴가·부재와 지정 휴일을 보지 않아, 신청으로는 423 인 일(마감 달 부재 추가)을
    #   저장으로는 할 수 있었다. 기간이 마감 달에 걸친 부재의 추가·변경·삭제, 마감 달 날짜의 휴일 변경을 막는다.
    if dto.absences is not None:
        def closed_key(item):
            start, end = str(item.get('start') or ''), str(item.get('end') or '')
            months = closed_months_in_range(start, end, closed) if start and end and start <= end else []
            if not months:
                return None
            first_day = start if start[:7] == months[0] else months[0] + '-01'
            return (str(item.get('name') or ''), str(item.get('type') or ''), start, end), first_day
        old_items, new_items = Counter(), Counter()
        first_days = {}
        for source, bucket in ((read_absences(cur), old_items), (dto.absences or [], new_items)):
            for item in source:
                if not isinstance(item, dict):
                    continue
                found = closed_key(item)
                if found:
                    bucket[found[0]] += 1
                    first_days[found[0]] = found[1]
        for key in (old_items - new_items) + (new_items - old_items):
            changed.add(first_days[key])
    if dto.customHolidays is not None:
        hol_cols = {r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)').fetchall()}
        removed_col = 'is_removed' if 'is_removed' in hol_cols else '0'
        b = {r[0]: (r[1] or '', bool(r[2]), bool(r[3])) for r in cur.execute(
            f'SELECT date_key, name, is_major, {removed_col} FROM custom_holidays') if in_closed(r[0])}
        a = {k: (str(v.get('name') or ''), bool(v.get('isMajor')), bool(v.get('isRemoved')))
             for k, v in (dto.customHolidays or {}).items() if in_closed(k) and isinstance(v, dict)}
        changed |= {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
    return sorted(changed)


def validate_save_payload(dto, cur=None):
    """v3.17.13 [위험] 저장은 받아 두고 JSON 백업 복구(historyBulk 검사)는 거절하는 값이 있어, 잘못된 한 행
    때문에 백업 복구 전체가 실패했다. 같은 규칙(실제 달력 날짜, 시작일 <= 종료일, 역할은 문자열)을
    저장 단계에서 먼저 확인한다. 화면이 옛 DB 값(role NULL)을 그대로 돌려보내는 경우는 거절하지 않고
    빈 문자열로 바꾼다(_norm_workers·마감 비교에서 NULL 과 같은 값으로 취급된다).
    이 검사 전에 이미 DB 에 들어 있던 값(같은 날짜 키, 똑같은 부재 기록)을 화면이 그대로 돌려보내는 것은 막지 않는다.
    옛 기록 하나 때문에 평소 저장이 모두 막히지 않게 하기 위해서다(고치거나 새로 넣는 값만 검사)."""
    known_days, known_absences = set(), set()
    if cur is not None:
        if dto.schedules is not None:
            known_days = {r[0] for r in cur.execute('SELECT DISTINCT date_key FROM schedules').fetchall()}
        if dto.absences is not None:
            known_absences = {(a['name'], a['type'], a['start'], a['end']) for a in read_absences(cur)}
    if dto.schedules is not None:
        for day, workers in dto.schedules.items():
            if not isinstance(workers, list) or any(not isinstance(w, dict) for w in workers):
                raise HTTPException(status_code=400, detail=f'{day} 당직표 형식이 올바르지 않습니다. 화면을 새로고침한 뒤 다시 저장해 주세요.')
            if day not in known_days:
                try:
                    strict_iso_date(day if isinstance(day, str) else '', '당직 날짜')
                except HTTPException:
                    raise HTTPException(status_code=400, detail=f'당직 날짜({day})가 올바른 날짜가 아닙니다. 화면을 새로고침(F5)한 뒤 다시 저장해 주세요.')
            for worker in workers:
                if 'role' in worker:
                    if worker['role'] is None:
                        worker['role'] = ''
                    elif not isinstance(worker['role'], str):
                        raise HTTPException(status_code=400, detail=f'{day} 당직 역할은 문자열이어야 합니다.')
    if dto.absences is not None:
        for item in dto.absences:
            if not isinstance(item, dict):
                raise HTTPException(status_code=400, detail='부재 기록 형식이 올바르지 않습니다.')
            if (item.get('name'), item.get('type'), item.get('start'), item.get('end')) in known_absences:
                continue
            who = f"'{item.get('name')}' " if isinstance(item.get('name'), str) else ''
            start = item.get('start') if isinstance(item.get('start'), str) else ''
            end = item.get('end') if isinstance(item.get('end'), str) else ''
            for value, label in ((start, '시작일'), (end, '종료일')):
                try:
                    valid = strict_iso_date(value, label) == value
                except HTTPException:
                    valid = False
                if not valid:
                    raise HTTPException(status_code=400, detail=f'{who}휴가·부재 {label}({value or "비어 있음"})은 YYYY-MM-DD 형식의 실제 날짜여야 합니다.')
            if start > end:
                raise HTTPException(status_code=400, detail=f'{who}휴가·부재 종료일({end})이 시작일({start})보다 빠릅니다.')
    if dto.customHolidays is not None:
        if any(not isinstance(v, dict) for v in dto.customHolidays.values()):
            raise HTTPException(status_code=400, detail='지정 휴일 형식이 올바르지 않습니다.')


# v3.17.13 화면이 이력(historyLog)을 보내지 않아도, 무엇을 바꿨는지 서버가 한 줄 남긴다.
HISTORY_COLLECTION_LABELS = (
    ('members', '사원 명단'), ('rules', '배정 규칙'), ('customHolidays', '지정 휴일'),
    ('comments', '날짜 메모'), ('noDuty', '휴무일'), ('globalMemo', '공지'), ('absenceTypes', '부재 구분'),
)


def collection_state(cur, keys):
    """저장 전후 비교용. 당직표·부재는 domain_snapshot 이 따로 다룬다."""
    out = {}
    if 'members' in keys:
        out['members'] = [tuple(r) for r in cur.execute('SELECT id, name, gender FROM members ORDER BY id')]
    if 'rules' in keys:
        row = cur.execute("SELECT rule_json FROM system_rules WHERE rule_key = 'DEFAULT'").fetchone()
        out['rules'] = row[0] if row else None
    if 'customHolidays' in keys:
        cols = {r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)').fetchall()}
        removed = 'is_removed' if 'is_removed' in cols else '0'
        out['customHolidays'] = [tuple(r) for r in cur.execute(
            f'SELECT date_key, name, is_major, {removed} FROM custom_holidays ORDER BY date_key')]
    if 'comments' in keys:
        out['comments'] = [tuple(r) for r in cur.execute(
            "SELECT date_key, comment FROM day_meta WHERE comment IS NOT NULL AND comment <> '' ORDER BY date_key")]
    if 'noDuty' in keys:
        out['noDuty'] = [tuple(r) for r in cur.execute(
            "SELECT date_key, COALESCE(no_duty_reason, '') FROM day_meta WHERE is_no_duty ORDER BY date_key")]
    if 'globalMemo' in keys:
        row = cur.execute("SELECT value FROM app_settings WHERE key = 'global_memo'").fetchone()
        out['globalMemo'] = (row[0] if row else '') or ''
    if 'absenceTypes' in keys:
        out['absenceTypes'] = [r[0] for r in cur.execute('SELECT type_name FROM absence_types ORDER BY id')]
    return out


def tolerant_history_snapshots(record):
    """v3.17.13 가져온 이력의 전후 기록이 형식에 맞지 않으면 그 전후 기록만 버리고 이력 행은 남긴다.
    (옛 버전이 남긴 role NULL 같은 값 하나 때문에 JSON 백업 복구 전체가 실패하지 않게)"""
    try:
        before, after = imported_history_snapshots(record)
        return before, after, False
    except HTTPException:
        return None, None, True


def pending_schema_migrations():
    """이번 실행에서 init_db 가 바꿀 구조 목록. 파일이 없거나 읽을 수 없으면 빈 목록(새 DB 는 백업할 것이 없다)."""
    if APP_MODE != 'ADMIN' or not os.path.isfile(DB_PATH):
        return []
    try:
        conn = get_db_connection(readonly=True)
    except Exception:
        return []
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'members' not in tables:
            return []
        wanted = {
            **{name: [] for name in request_access_schema.PROTECTED_TABLES},
            **{name: [] for name in dinner_schema.TABLES},
            'holiday_supplements': [], 'holiday_supplement_bases': [], 'holiday_supplement_operations': [],
            'custom_holidays': ['is_removed'], 'schedules': ['zero_score'], 'day_meta': ['no_duty_reason'],
            'change_requests': ['absence_type', 'absence_end_date', 'receipt_token', 'reject_reason',
                                'requester_host', 'requester_account', 'absence_details', 'revision', 'approved_history_id',
                                'amends_request_id', 'amendment_original', 'request_uid'],
            'absences': ['education_json'],
            'change_history': list(HISTORY_EXTRA_COLUMNS + HISTORY_JSON_COLUMNS),
        }
        out = []
        for table, cols in wanted.items():
            if table not in tables:
                out.append(f'{table} 표 추가')
                continue
            have = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
            out += [f'{table}.{c}' for c in cols if c not in have]
        return out
    except Exception:
        return []
    finally:
        conn.close()


def apply_schema(cur):
    """표 만들기·열 추가·기본값 채우기. init_db 와 복원(옛 구조 백업을 임시 사본에서 맞출 때)이 함께 쓴다."""
    cur.execute("CREATE TABLE IF NOT EXISTS members (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, gender TEXT NOT NULL, role TEXT DEFAULT '사원')")
    cur.execute('CREATE TABLE IF NOT EXISTS schedules (date_key TEXT NOT NULL, slot_index INTEGER NOT NULL, worker_name TEXT NOT NULL, role TEXT, is_substitute INTEGER DEFAULT 0, zero_score INTEGER DEFAULT 0, PRIMARY KEY (date_key, slot_index))')
    cur.execute("CREATE TABLE IF NOT EXISTS day_meta (date_key TEXT PRIMARY KEY, comment TEXT, is_no_duty INTEGER DEFAULT 0, no_duty_reason TEXT DEFAULT '')")
    cur.execute('CREATE TABLE IF NOT EXISTS absences (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, type TEXT NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL)')
    cur.execute('CREATE TABLE IF NOT EXISTS absence_types (id INTEGER PRIMARY KEY AUTOINCREMENT, type_name TEXT UNIQUE NOT NULL)')
    cur.execute('CREATE TABLE IF NOT EXISTS custom_holidays (date_key TEXT PRIMARY KEY, name TEXT NOT NULL, is_major INTEGER DEFAULT 0, is_removed INTEGER DEFAULT 0)')
    cur.execute('CREATE TABLE IF NOT EXISTS system_rules (rule_key TEXT PRIMARY KEY, rule_json TEXT NOT NULL)')
    cur.execute('CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)')
    cur.execute('CREATE TABLE IF NOT EXISTS change_history (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, type TEXT NOT NULL, date_key TEXT NOT NULL, details TEXT NOT NULL)')
    cur.execute("CREATE TABLE IF NOT EXISTS change_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, requester_name TEXT NOT NULL, target_date TEXT NOT NULL, request_type TEXT NOT NULL, swap_date TEXT, swap_worker TEXT, substitute_name TEXT, is_zero_score INTEGER DEFAULT 1, reason TEXT, status TEXT DEFAULT 'PENDING', created_at TEXT NOT NULL, processed_at TEXT, absence_type TEXT, absence_end_date TEXT, receipt_token TEXT)")
    hol_cols = [r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)').fetchall()]
    if 'is_removed' not in hol_cols:
        cur.execute('ALTER TABLE custom_holidays ADD COLUMN is_removed INTEGER DEFAULT 0')
        log_info('custom_holidays.is_removed 컬럼을 추가했습니다.')
    sch_cols = [r[1] for r in cur.execute('PRAGMA table_info(schedules)').fetchall()]
    if 'zero_score' not in sch_cols:
        cur.execute('ALTER TABLE schedules ADD COLUMN zero_score INTEGER DEFAULT 0')
        cur.execute('UPDATE schedules SET zero_score = is_substitute')
        log_info('schedules.zero_score 컬럼을 추가하고 기존 값을 이전했습니다.')
    meta_cols = [r[1] for r in cur.execute('PRAGMA table_info(day_meta)').fetchall()]
    if 'no_duty_reason' not in meta_cols:
        cur.execute("ALTER TABLE day_meta ADD COLUMN no_duty_reason TEXT DEFAULT ''")
        log_info('day_meta.no_duty_reason 컬럼을 추가했습니다.')
    for col in ensure_change_request_schema(cur):
        log_info(f'change_requests.{col} 컬럼을 추가했습니다.')
    for col in ensure_absence_schema(cur):
        log_info(f'absences.{col} 컬럼을 추가했습니다.')
    for col in ensure_history_schema(cur):
        log_info(f'change_history.{col} 컬럼을 추가했습니다.')
    ensure_client_status_schema(cur)
    cur.execute('SELECT COUNT(*) FROM members')
    if cur.fetchone()[0] == 0:
        default_m = [('김철수', 'M'), ('박민수', 'M'), ('강동원', 'M'), ('윤서준', 'M'), ('임재범', 'M'), ('이영희', 'F'), ('최지은', 'F'), ('정하나', 'F'), ('송혜교', 'F'), ('한지민', 'F'), ('김나영', 'F'), ('박소연', 'F'), ('오유진', 'F')]
        cur.executemany('INSERT INTO members (name, gender) VALUES (?, ?)', default_m)
    cur.execute('SELECT COUNT(*) FROM absence_types')
    if cur.fetchone()[0] == 0:
        default_t = [('연차/휴가',), ('모성보호',), ('파견',), ('장기출장',), ('병가/경조사',), ('공가',)]
        cur.executemany('INSERT INTO absence_types (type_name) VALUES (?)', default_t)
    cur.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('data_version', '0')")
    ensure_holiday_supplement_schema(cur)
    dinner_schema.ensure_schema(cur)


def init_db():
    if APP_MODE != 'ADMIN':
        log_info('조회 모드이므로 DB 초기화를 건너뜁니다.')
        return
    # v3.16.0 [위험] 예전에는 하루 첫 백업이 init_db 뒤에 돌아, ALTER TABLE 과
    #   'UPDATE schedules SET zero_score = is_substitute' 같은 자료 변경 직전 상태가 남지 않았다.
    #   바꿀 것이 있으면 바꾸기 전에 스냅샷을 먼저 받고, 받지 못하면 바꾸지 않는다.
    pending_migrations = pending_schema_migrations()
    if pending_migrations:
        try:
            take_named_snapshot('premigration', f'{APP_VERSION}')
        except Exception as e:
            log_error(f'스키마 변경 직전 스냅샷을 만들지 못해 변경을 중단합니다: {e}', e)
            raise RuntimeError('데이터 구조를 바꾸기 전에 안전 사본을 만들지 못했습니다. '
                               '공유 폴더의 backups 폴더 쓰기 권한과 여유 공간을 확인해 주세요.') from e
        log_info('스키마 변경 예정: ' + ', '.join(pending_migrations))
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        verify_holiday_contract(cur)
        apply_schema(cur)
        conn.commit()
        log_info('DB 초기화 완료.')
    except Exception as e:
        log_error(f'DB 초기화 실패: {e}', e)
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        if MANAGED_CONFIG:
            raise RuntimeError('당직 데이터 파일을 준비하지 못했습니다. 원본 데이터는 바꾸지 않았습니다. 관리자에게 알려 주세요.') from e
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


class ConnectionManager:
    def __init__(self):
        self.active_connections = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active_connections:
            self.active_connections.remove(ws)

    async def broadcast(self, msg: dict):
        for c in list(self.active_connections):
            try:
                await c.send_json(msg)
            except Exception:
                self.disconnect(c)


def broadcast_from_thread(msg: dict):
    """v3.17.13 DB 잠금 대기·SMB 복사처럼 막히는 일을 하는 핸들러는 일반 def 로 두어 FastAPI 가 스레드에서
    돌린다(예전 async 핸들러는 그동안 이 PC 서버 전체—화면 응답·웹소켓—를 멈췄다).
    웹소켓 방송은 이벤트 루프에서 해야 하므로 그 스레드에서 루프로 넘긴다. 실패해도 저장 결과에는 영향이 없다."""
    if not manager.active_connections:
        return
    try:
        anyio.from_thread.run(manager.broadcast, msg)
    except Exception as e:
        log_info(f'실시간 알림 전송을 건너뜁니다(운영에는 영향 없음): {e}')


class ChangeRequestSubmitDTO(BaseModel):
    requester_name: str
    target_date: str
    request_type: str
    swap_date: Optional[str] = ''
    swap_worker: Optional[str] = ''
    substitute_name: Optional[str] = ''
    is_zero_score: Optional[bool] = True
    reason: Optional[str] = ''
    absence_type: Optional[str] = ''
    absence_end_date: Optional[str] = ''
    absence_details: Optional[dict] = None


class RequestManageDTO(BaseModel):
    receipt_token: str
    action: str
    request: Optional[ChangeRequestSubmitDTO] = None
    reason: Optional[str] = ''


class RequestActionDTO(BaseModel):
    request_id: int
    action: str
    reject_reason: Optional[str] = None
    zero_score: Optional[bool] = None   # v3.16.0 대체 근무 승인 시 관리자가 0점 여부를 정한다(없으면 신청 값)
    request_revision: Optional[int] = None
    impact_digest: Optional[str] = None


class SyncFullDataDTO(BaseModel):
    holidaySupplements: Optional[dict] = None
    members: Optional[list] = None
    schedules: Optional[dict] = None
    comments: Optional[dict] = None
    noDuty: Optional[dict] = None
    absences: Optional[list] = None
    absenceTypes: Optional[list] = None
    customHolidays: Optional[dict] = None
    rules: Optional[dict] = None
    historyLog: Optional[dict] = None
    historyBulk: Optional[list] = None
    globalMemo: Optional[str] = None
    baseVersion: Optional[int] = None
    # v3.18.0 값이 있으면 저장 직전(쓰기 잠금을 쥔 채) 되돌리기용 자동 사본을 만든다. 사본이 없으면 저장하지 않는다.
    snapshotKind: Optional[str] = None


def strict_iso_date(value, field_label):
    'YYYY-MM-DD 형식과 실제 달력 날짜를 모두 검증한다.'
    text = (value or '').strip()
    try:
        parsed = datetime.strptime(text, '%Y-%m-%d')
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f'{josa(field_label, "이/가")} 올바른 날짜가 아닙니다. 달력에서 다시 골라 주세요.')
    if parsed.strftime('%Y-%m-%d') != text:
        raise HTTPException(status_code=400, detail=f'{josa(field_label, "이/가")} 올바른 날짜가 아닙니다. 달력에서 다시 골라 주세요.')
    return text


# v3.17.13 [위험] 인증 없는 신청 API 가 글자 수를 제한하지 않아 수 MB 사유 한 건으로 공유 DB 가 부풀 수 있었다.
#   화면 입력칸의 maxlength 와 같은 값이다(사유 500, 교육 안내 짧은 칸 500·내용/메모 2000).
REQUEST_TEXT_LIMITS = (('requester_name', '신청자 이름', 50), ('swap_worker', '맞교환 대상자 이름', 50),
                       ('substitute_name', '대체 근무자 이름', 50), ('absence_type', '휴가·부재 구분', 50),
                       ('reason', '신청 사유', 500), ('request_type', '신청 유형', 20),
                       ('target_date', '대상일', 20), ('swap_date', '맞교환일', 20), ('absence_end_date', '종료일', 20))
EDUCATION_TEXT_LIMITS = {'title': 500, 'location': 500, 'provider': 500, 'preparation': 500, 'content': 2000, 'notes': 2000}
EDUCATION_LABELS = {'title': '교육명', 'location': '장소', 'provider': '주관', 'preparation': '준비물', 'content': '내용', 'notes': '메모'}


def check_request_text_limits(dto):
    for field, label, limit in REQUEST_TEXT_LIMITS:
        value = getattr(dto, field, '')
        if isinstance(value, str) and len(value.strip()) > limit:
            raise HTTPException(status_code=400, detail=f'{josa(label, "은/는")} {limit}자 이내로 입력해 주세요. (현재 {len(value.strip())}자)')
    details = getattr(dto, 'absence_details', None)
    if isinstance(details, dict):
        for key, limit in EDUCATION_TEXT_LIMITS.items():
            value = details.get(key)
            if isinstance(value, str) and len(value) > limit:
                raise HTTPException(status_code=400, detail=f'교육 안내의 {josa(EDUCATION_LABELS[key], "은/는")} {limit}자 이내로 입력해 주세요. (현재 {len(value)}자)')


def require_member(cur, name, field_label='신청자'):
    clean_name = (name or '').strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail=f'{josa(field_label, "을/를")} 선택해 주세요.')
    cur.execute('SELECT 1 FROM members WHERE name = ? LIMIT 1', (clean_name,))
    if not cur.fetchone():
        raise HTTPException(status_code=400, detail=f"{field_label} {clean_name} 님을 사원 명단에서 찾을 수 없습니다.")
    return clean_name


def require_absence_type(cur, absence_type):
    clean_type = (absence_type or '').strip()
    if not clean_type:
        raise HTTPException(status_code=400, detail='휴가·부재 구분을 선택해 주세요.')
    cur.execute('SELECT 1 FROM absence_types WHERE type_name = ? LIMIT 1', (clean_type,))
    if not cur.fetchone():
        raise HTTPException(status_code=400, detail=f'등록되지 않은 휴가·부재 구분입니다: {clean_type}')
    return clean_type



# Initialisation order preserves the executable's configuration/storage behavior.
config_data = load_config()
SHARED_PATH = _cfg('shared_folder_path', '')
BIND_HOST = _cfg('bind_host', '127.0.0.1') or '127.0.0.1'
ADMIN_PASSWORD = _cfg('admin_password', '')
OPEN_BROWSER = (_cfg('open_browser', 'true').lower() not in ('false', '0', 'no') and
                os.environ.get('WLR_MANUAL_RESTART') != '1')
TRAY_ENABLED = _cfg('tray_icon', 'true').lower() not in ('false', '0', 'no')
try:
    SERVER_PORT = int(_cfg('server_port', DEFAULT_PORT))
except ValueError:
    log_error(f'server_port 값이 올바르지 않아 기본값 {DEFAULT_PORT} 을 사용합니다.')
    SERVER_PORT = int(DEFAULT_PORT)
try:
    DB_TIMEOUT = float(_cfg('db_timeout_seconds', '30.0'))
except ValueError:
    DB_TIMEOUT = 30.0
STORAGE_WARNING = ''
if MANAGED_CONFIG and not SHARED_PATH:
    raise RuntimeError('프로필의 데이터 경로가 비어 있습니다. 초기 설정에서 기존 데이터 위치를 지정하세요.')
if MANAGED_CONFIG and not os.path.isabs(SHARED_PATH):
    raise RuntimeError('프로필의 shared_folder_path는 절대 경로여야 합니다.')
STORAGE_DIR = SHARED_PATH if SHARED_PATH else CONFIG_DIR
DB_PATH = os.path.join(STORAGE_DIR, 'smart_duty.db')


def require_existing_shared_database():
    # Fail closed at startup and every connection. Never fall back to a local DB.
    if not os.path.isdir(STORAGE_DIR) or not os.path.isfile(DB_PATH):
        log_error(f'공유 폴더의 당직 데이터 파일에 접근할 수 없음: {DB_PATH}')
        raise HTTPException(status_code=503, detail='공유 폴더에 연결할 수 없습니다. 네트워크와 공유 폴더 연결을 확인하세요. 연결되면 자동으로 다시 불러옵니다.')


if SHARED_PATH:
    require_existing_shared_database()
try:
    HOST_LABEL = (os.environ.get('COMPUTERNAME') or platform.node() or '알 수 없음').strip()
except Exception:
    HOST_LABEL = '알 수 없음'
try:
    ACTOR_LABEL = (os.environ.get('USERNAME') or getpass.getuser() or '알 수 없음').strip()[:64]
except Exception:
    ACTOR_LABEL = '알 수 없음'
STORAGE_WRITABLE = None
STORAGE_WRITE_DETAIL = ''
if BIND_HOST not in ('127.0.0.1', 'localhost') and not ADMIN_PASSWORD and APP_MODE == 'ADMIN':
    log_error('경고: 관리자 서버가 외부에 열려 있는데 admin_password 가 비어 있습니다. 설정 파일에 암호를 지정하거나 bind_host 를 127.0.0.1 로 되돌리세요.')
log_info(f"=== WLR OnAir {APP_VERSION} 시작 | 모드={APP_MODE} | 포트={SERVER_PORT} | 바인딩={BIND_HOST} | 암호보호={'ON' if ADMIN_PASSWORD else 'OFF'} | DB={DB_PATH} ===")
def startup_health_payload():
    host = '127.0.0.1' if BIND_HOST in ('0.0.0.0', 'localhost') else '::1' if BIND_HOST == '::' else BIND_HOST
    return {'pid': os.getpid(), 'instance_id': UPDATE_INSTANCE_ID,
            'version': APP_VERSION, 'mode': DISTRIBUTION_ROLE,
            'port': globals().get('RUN_PORT', SERVER_PORT), 'host': host}


def write_startup_health():
    if not HEALTH_FILE and not MANAGED_CONFIG:
        return
    # One startup check only: polling the health endpoint does not repeat DB scans.
    connection = get_db_connection(readonly=True)
    try:
        required_columns = {
            'members': {'id', 'name', 'gender', 'role'},
            'schedules': {'date_key', 'slot_index', 'worker_name', 'role', 'is_substitute', 'zero_score'},
            'day_meta': {'date_key', 'comment', 'is_no_duty', 'no_duty_reason'},
            'absences': {'id', 'name', 'type', 'start_date', 'end_date'},
            'absence_types': {'id', 'type_name'},
            'custom_holidays': {'date_key', 'name', 'is_major', 'is_removed'},
            'system_rules': {'rule_key', 'rule_json'},
            'app_settings': {'key', 'value'},
            'change_history': {'id', 'timestamp', 'type', 'date_key', 'details'},
            'change_requests': {'id', 'requester_name', 'target_date', 'request_type',
                                'swap_date', 'swap_worker', 'substitute_name', 'is_zero_score',
                                'reason', 'status', 'created_at', 'processed_at', 'absence_type',
                                'absence_end_date', 'receipt_token', 'reject_reason'},
        }
        for table, required in required_columns.items():
            actual = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            missing = required - actual
            if missing:
                raise RuntimeError(f'당직 데이터 파일의 구조를 확인할 수 없습니다: {table} 필수 항목 누락 ({", ".join(sorted(missing))})')
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('당직 데이터 파일 점검(무결성 검사)에 실패했습니다.')
    finally:
        connection.close()
    if not HEALTH_FILE:
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.health-', suffix='.tmp', dir=os.path.dirname(HEALTH_FILE))
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(startup_health_payload(), stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, HEALTH_FILE)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


CLIENT_HEARTBEAT = {'at': 0.0}
CLIENT_HEARTBEAT_SECONDS = 600


def record_client_heartbeat(force=False):
    """v3.16.0 [D-2] 이 PC 가 어떤 버전으로 돌고 있는지 공유 DB 에 1행 남긴다(10분에 한 번).
    data_version 은 올리지 않는다 — 다른 PC 화면을 새로고침시키지 않기 위해서다.
    쓰기 권한이 없거나 DB 가 없으면 조용히 넘어간다(운영에 영향 없음)."""
    import time
    now = time.monotonic()
    if not force and now - CLIENT_HEARTBEAT['at'] < CLIENT_HEARTBEAT_SECONDS:
        return
    CLIENT_HEARTBEAT['at'] = now
    if not os.path.isfile(DB_PATH):
        return
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        verify_holiday_contract(cur)
        ensure_client_status_schema(cur)
        stamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        detail = 'update-managed' if UPDATE_INSTANCE_ID else ''
        cur.execute('INSERT INTO client_status (host, role, app_version, account, managed, first_seen, last_seen, detail) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(host, role) DO UPDATE SET '
                    'app_version = excluded.app_version, account = excluded.account, managed = excluded.managed, '
                    'last_seen = excluded.last_seen, detail = excluded.detail',
                    (HOST_LABEL, APP_MODE, APP_VERSION, ACTOR_LABEL, 1 if MANAGED_CONFIG else 0, stamp, stamp, detail))
        conn.commit()
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_info(f'PC 현황 기록을 건너뜁니다(운영에는 영향 없음): {e}')
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@asynccontextmanager
async def application_lifespan(application):
    write_startup_health()
    record_client_heartbeat(force=True)
    try:
        yield
    finally:
        MASTER.clear()


app = FastAPI(title=f'WLR OnAir ({APP_MODE})', version=APP_VERSION,
              lifespan=application_lifespan)
_origins = []
for _p in range(SERVER_PORT, SERVER_PORT + 21):
    _origins.append(f'http://localhost:{_p}')
    _origins.append(f'http://127.0.0.1:{_p}')
app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True,
                   allow_methods=['*'], allow_headers=['*'])


# v3.17.13 [위험] 교차 사이트·DNS 리바인딩 방어.
#   이 서버는 각 PC 의 브라우저가 http://localhost:포트 로 쓰고, bind_host 를 0.0.0.0 으로 연 경우에만
#   사내망의 다른 PC 가 http://<IP>:포트 또는 http://<PC 이름>:포트 로 접속한다.
#   - Host 검사: 공격자 도메인을 이 PC 의 주소로 바꿔치기(DNS 리바인딩)하면 브라우저는 그 도메인 이름을 Host 로
#     보낸다. 루프백 이름, 모든 IP 주소, 이 PC 의 이름(COMPUTERNAME·hostname·%USERDNSDOMAIN% 을 붙인 FQDN), bind_host,
#     설정 파일 allowed_hosts(쉼표로 구분, '*' 는 검사 끔)에 적은 이름만 받는다.
#     DNS 별칭(CNAME)으로 접속하는 곳이 있으면 allowed_hosts 에 그 이름을 적으면 된다.
#   - Origin 검사: 다른 웹사이트가 이 PC 의 서버로 저장·신청을 보내거나 웹소켓을 여는 것을 막는다.
#     Origin 헤더가 있으면(브라우저가 보낸 요청) 같은 주소(Host 와 같은 출처)이거나 이 PC 의 localhost 주소여야 한다.
#     Origin 이 없는 요청(브라우저 밖의 프로그램·같은 출처 GET)은 그대로 받는다.
LOCAL_ORIGINS = set(_origins) | {f'http://[::1]:{_p}' for _p in range(SERVER_PORT, SERVER_PORT + 21)}


def _host_name_only(value):
    value = (value or '').strip()
    if value.startswith('['):
        end = value.find(']')
        return value[1:end] if end > 0 else value
    if value.count(':') == 1:
        return value.split(':', 1)[0]
    return value


def _is_ip_literal(name):
    try:
        ipaddress.ip_address(name.split('%', 1)[0])
        return True
    except ValueError:
        return False


def build_allowed_host_names():
    names = {'localhost', 'testserver'}   # testserver: 시험용 가짜 주소(testclient 와 같은 취지)
    candidates = [HOST_LABEL, BIND_HOST, platform.node()]
    try:
        hostname = socket.gethostname()
    except Exception:
        hostname = ''
    candidates.append(hostname)
    # socket.getfqdn() 은 DNS 역조회로 시작을 몇 초씩 막을 수 있어 쓰지 않는다. 도메인 PC 는 환경 변수로 FQDN 을 만든다.
    domain = os.environ.get('USERDNSDOMAIN', '').strip()
    if domain:
        candidates += [f'{base}.{domain}' for base in (hostname, HOST_LABEL) if base]
    candidates += re.split(r'[,\s]+', _cfg('allowed_hosts', ''))
    for name in candidates:
        clean = (name or '').strip().lower().rstrip('.')
        if clean and clean not in ('0.0.0.0', '::'):
            names.add(clean)
            if clean != '*' and '.' in clean and not _is_ip_literal(clean):
                names.add(clean.split('.', 1)[0])   # FQDN 의 첫 이름(사내 DNS 접미사 없이 쓰는 경우)
    return names


ALLOWED_HOST_NAMES = build_allowed_host_names()


def host_header_allowed(host_header):
    if host_header is None:
        return True   # 브라우저는 항상 Host 를 보낸다. 없는 요청은 리바인딩 공격이 아니다.
    name = _host_name_only(host_header).lower().rstrip('.')
    if not name:
        return False
    return '*' in ALLOWED_HOST_NAMES or name in ALLOWED_HOST_NAMES or _is_ip_literal(name)


def origin_allowed(origin, host_header):
    if origin is None:
        return True
    text = origin.strip().lower()
    if text in LOCAL_ORIGINS:
        return True
    try:
        parsed = urlsplit(text)
    except ValueError:
        return False
    return parsed.scheme in ('http', 'https') and bool(host_header) and parsed.netloc == host_header.strip().lower()


class HostOriginGuard:
    """Host·Origin 검사(위 설명). 순수 ASGI 라 HTTP 와 웹소켓에 모두 걸린다."""

    def __init__(self, application):
        self.app = application

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        headers = {}
        for key, value in scope.get('headers') or []:
            headers.setdefault(key.decode('latin-1').lower(), value.decode('latin-1'))
        host = headers.get('host')
        problem = ''
        if not host_header_allowed(host):
            problem = 'host'
        elif scope['type'] == 'websocket' or scope.get('method', 'GET') not in ('GET', 'HEAD', 'OPTIONS'):
            if not origin_allowed(headers.get('origin'), host):
                problem = 'origin'
        if not problem:
            return await self.app(scope, receive, send)
        log_info(f"요청 거절({problem}): Host={host!r} Origin={headers.get('origin')!r} 경로={scope.get('path')}")
        if scope['type'] == 'websocket':
            await receive()   # websocket.connect
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if problem == 'host':
            status, detail = 400, (f'이 주소({host})로는 접속할 수 없습니다. 이 PC의 이름이나 IP 주소로 접속하세요. '
                                   '다른 이름(별칭)으로 접속해야 한다면 관리자가 [연결 정보 → 자주 바꾸는 설정]의 \'허용할 접속 이름\'에 그 이름을 넣고 프로그램을 다시 시작해야 합니다.')
        else:
            status, detail = 403, '다른 웹사이트에서 보낸 요청이라 처리하지 않았습니다. WLR OnAir 화면에서 다시 시도해 주세요.'
        body = json.dumps({'detail': detail}, ensure_ascii=False).encode('utf-8')
        await send({'type': 'http.response.start', 'status': status,
                    'headers': [(b'content-type', b'application/json; charset=utf-8'),
                                (b'content-length', str(len(body)).encode('ascii'))]})
        await send({'type': 'http.response.body', 'body': body})


app.add_middleware(HostOriginGuard)
# v3.16.0 백업을 먼저 받는다. 구조 변경은 init_db 안에서 별도 스냅샷을 다시 받은 뒤에만 한다.
probe_storage_writable()
ensure_daily_backup()
init_db()
manager = ConnectionManager()



@app.get('/api/system/health')
def get_system_health():
    return startup_health_payload()


def is_loopback_client(request: Optional[Request]):
    client = (request.client.host if request is not None and request.client else '') or ''
    if client in LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(client)
    except ValueError:
        return False
    mapped = getattr(address, 'ipv4_mapped', None)
    return address.is_loopback or bool(mapped and mapped.is_loopback)


def redact_paths(text):
    """원격 호출자에게 보내는 문장에서 이 PC 의 데이터 경로를 가린다."""
    out = str(text or '')
    for secret in sorted({DB_PATH, STORAGE_DIR, SHARED_PATH, CONFIG_DIR} - {''}, key=len, reverse=True):
        out = out.replace(secret, '(경로 숨김)')
    return out


@app.get('/api/system/ping')
async def system_ping():
    """v3.18.0 화면의 연결 상태 확인용. 데이터 파일·공유 폴더는 건드리지 않는다(몇 초마다 불린다)."""
    return {'ok': True, 'mode': APP_MODE, 'version': APP_VERSION}


@app.post('/api/system/admin-auth', status_code=204)
def authenticate_admin(x_admin_token: Optional[str] = Header(default=None)):
    """Check the header button's credentials without loading or modifying data."""
    require_admin(x_admin_token)
    return Response(status_code=204, headers={'Cache-Control': 'no-store'})


@app.get('/api/system/info')
def get_system_info(request: Request, x_admin_token: Optional[str] = Header(default=None)):
    record_client_heartbeat()
    conn = None
    cnt = 0
    ver = 0
    db_ok = True
    detail = ''
    last_write = {'version': '', 'host': '', 'at': '', 'max': ''}
    fresh_db = False
    reveal = is_loopback_client(request) or bool(ADMIN_PASSWORD and x_admin_token == ADMIN_PASSWORD)
    recovery_notifications = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM change_requests WHERE status = 'PENDING'")
        cnt = cur.fetchone()[0]
        if APP_MODE == 'ADMIN' and reveal and _cfg('request_access_enabled', 'true').lower() not in ('false', '0', 'no'):
            # A failed recovery lookup must not hide otherwise healthy business data.
            try:
                recovery_notifications = request_access_schema.notification_summary(cur, int(time.time()))
            except (sqlite3.Error, request_access_schema.AccessError):
                pass
        ver = read_data_version(cur)
        last_write = read_last_write(cur)
        fresh_db = (ver == 0 and not SHARED_PATH and APP_MODE == 'ADMIN')
    except HTTPException as e:
        db_ok = False
        detail = e.detail
    except Exception as e:
        db_ok = False
        detail = friendly_db_error(e, '당직 데이터 파일을 열')
        log_error('당직 데이터 파일 확인 실패', e)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    # v3.17.13 [위험] 인증 없는 이 응답에 DB 경로·공유 폴더·PC 이름이 들어 있어, 사내망의 아무 PC 나
    #   경로를 알아낼 수 있었다. 이 PC 자신(루프백)이나 관리자 암호를 아는 호출자에게만 보낸다.
    #   화면은 값이 비면 '-' 로 보이고 다른 동작에는 쓰지 않는다.
    reveal = is_loopback_client(request) or bool(ADMIN_PASSWORD and x_admin_token == ADMIN_PASSWORD)
    if not reveal:
        last_write = {**last_write, 'host': ''}
        detail = redact_paths(detail)
    return {
        # Historical launcher supervisors check this field to find duplicate roles.
        'mode': DISTRIBUTION_ROLE,
        'effectiveMode': APP_MODE,
        'version': APP_VERSION,
        'distributionRole': DISTRIBUTION_ROLE,
        'pendingCount': cnt,
        'requestRecovery': recovery_notifications,
        'dataVersion': ver,
        'dbOk': db_ok,
        'dbPath': DB_PATH if reveal else '',
        'storageWarning': STORAGE_WARNING if reveal else redact_paths(STORAGE_WARNING),
        'authRequired': bool(ADMIN_PASSWORD),
        'detail': detail,
        'storageDir': STORAGE_DIR if reveal else '',
        'sharedConfigured': bool(SHARED_PATH),
        'storageWritable': STORAGE_WRITABLE,
        'storageWriteDetail': STORAGE_WRITE_DETAIL if reveal else redact_paths(STORAGE_WRITE_DETAIL),
        'freshLocalDb': fresh_db,
        'hostLabel': HOST_LABEL if reveal else '',
        'lastWrite': last_write,
        'backupOk': BACKUP_STATE.get('ok'),
        'backupDetail': BACKUP_STATE.get('detail', '') if reveal else redact_paths(BACKUP_STATE.get('detail', '')),
        # v3.16.0 [C-3] 암호 없이 사내망에 열려 있으면 화면 위에 경고를 띄운다.
        'exposedWithoutPassword': bool(BIND_HOST not in ('127.0.0.1', 'localhost') and not ADMIN_PASSWORD),
    }


@app.post('/api/system/recheck-storage')
def recheck_storage(request: Request, x_admin_token: Optional[str] = Header(default=None)):
    """[04] 사용자가 권한을 고친 뒤 다시 검사할 때. 폴링이 아니라 명시적 호출 전용."""
    require_diag_access(x_admin_token, request)
    ok = probe_storage_writable()
    return {'storageWritable': ok, 'storageWriteDetail': STORAGE_WRITE_DETAIL, 'storageDir': STORAGE_DIR}


class CheckPathDTO(BaseModel):
    path: Optional[str] = ''


@app.post('/api/system/check-path')
def check_path(dto: CheckPathDTO, request: Request, x_admin_token: Optional[str] = Header(default=None)):
    """
[18] 설치용 연결 테스트. 경로를 받아 '폴더 있음 / 쓰기 가능 / 당직표 파일 있음'만 확인한다.
설정을 저장하지는 않는다 — 인증 없는 설정 쓰기 경로를 만들지 않기 위해 읽기 전용으로 둔다.
v3.9.1 [D] 임의 폴더의 존재·쓰기 가능 여부를 알려 주는 동작이라 암호 설정 시 토큰을 요구한다.
v3.17.13 [위험] 예전에는 GET 이라 다른 웹사이트의 <img src=...> 한 줄로도 이 PC 가 임의 UNC 경로에 접속하고
  시험 파일을 썼다(부수 효과가 있는 GET). JSON 본문의 POST 로만 받는다.
"""
    require_diag_access(x_admin_token, request)
    target = (dto.path or '').strip()
    if not target:
        raise HTTPException(status_code=400, detail='확인할 폴더 경로를 입력하세요.')
    out = {'path': target, 'exists': False, 'writable': False, 'dbExists': False,
           'dbSize': 0, 'dbModified': '', 'detail': ''}
    try:
        if not os.path.isdir(target):
            out['detail'] = '폴더를 찾을 수 없습니다. 경로를 다시 확인하세요.'
            return out
        out['exists'] = True
        probe = os.path.join(target, '.wlr_path_test.tmp')
        try:
            with open(probe, 'w', encoding='utf-8') as fp:
                fp.write('ok')
            os.remove(probe)
            out['writable'] = True
        except Exception as e:
            log_error(f'폴더 쓰기 확인 실패: {target}', e)
            out['detail'] = '폴더는 있지만 이 PC에서 쓸 수 없습니다. 공유 폴더 권한을 확인해 주세요.'
        db_file = os.path.join(target, 'smart_duty.db')
        if os.path.isfile(db_file):
            out['dbExists'] = True
            out['dbSize'] = os.path.getsize(db_file)
            out['dbModified'] = datetime.fromtimestamp(
                os.path.getmtime(db_file)).strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        log_error(f'폴더 확인 실패: {target}', e)
        out['detail'] = '폴더를 확인하지 못했습니다. 경로와 네트워크 연결을 확인한 뒤 다시 시도해 주세요.'
    return out


class UpdateStatusActionDTO(BaseModel):
    action: str = ''
    settings: Optional[dict] = None
    offer_id: Optional[str] = None
    instance_id: Optional[str] = None


def _operator_profiles():
    """updater.profiles (exe 안에 함께 들어 있다). 소스 실행이면 소스 폴더를 경로에 더한다."""
    try:
        from updater import profiles
    except ImportError:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.append(root)
        from updater import profiles
    return profiles


# Count writes through their full ASGI lifetime, including response background work.
# New writes are rejected only during the short, bounded shutdown drain.
class UpdateWriteGate:
    def __init__(self):
        self.active = 0
        self.draining = False

    async def drain(self, timeout=15):
        import asyncio
        self.draining = True
        deadline = time.monotonic() + timeout
        while self.active and time.monotonic() < deadline:
            await asyncio.sleep(.025)
        if self.active:
            self.draining = False
            return False
        return True


update_write_gate = UpdateWriteGate()
manual_shutdown_callback = None


class UpdateWriteMiddleware:
    def __init__(self, app): self.app = app

    async def __call__(self, scope, receive, send):
        write = (scope['type'] == 'http' and scope.get('method') not in ('GET', 'HEAD', 'OPTIONS') and
                 scope.get('path') != '/api/system/manual-update-ready')
        if write and update_write_gate.draining:
            from starlette.responses import JSONResponse
            await JSONResponse({'detail': '업데이트 재시작을 준비하고 있습니다. 잠시 후 다시 저장해 주세요.'}, status_code=503)(scope, receive, send)
            return
        if write: update_write_gate.active += 1
        try:
            await self.app(scope, receive, send)
        finally:
            if write: update_write_gate.active -= 1


app.add_middleware(UpdateWriteMiddleware)


@app.post('/api/system/manual-update-ready')
async def manual_update_ready(request: Request):
    # The helper's random one-use capability supplements Host/Origin protection.
    # A browser admin token alone never grants this shutdown endpoint.
    if not is_loopback_client(request) or manual_shutdown_callback is None:
        raise HTTPException(status_code=403, detail='이 요청을 처리할 수 없습니다.')
    _operator_profiles()
    from updater.manual_update import live_check
    try:
        body = await request.json()
        token = request.headers.get('X-OnAir-Update-Token', '')
        authorized = live_check.authorize_shutdown(body.get('operation'), token)
    except Exception:
        authorized = False
    if not authorized:
        raise HTTPException(status_code=403, detail='업데이트 준비 확인이 만료되었습니다.')
    try:
        if not await update_write_gate.drain():
            raise HTTPException(status_code=409, detail='저장 중인 작업이 끝나지 않아 재시작을 취소했습니다.')
        # Settings/publication may have changed while a save was draining.
        # Bound repository I/O and release the write gate on every failure.
        import asyncio
        try:
            await asyncio.wait_for(asyncio.to_thread(live_check.validate_shutdown), timeout=5)
        except Exception:
            raise HTTPException(status_code=409, detail='업데이트 게시 또는 설정 확인이 끝나지 않아 재시작을 취소했습니다.')
        # Deliver the helper acknowledgement before stopping HTTP/WebSockets.
        from starlette.background import BackgroundTask
        from starlette.responses import JSONResponse
        return JSONResponse({'ok': True}, background=BackgroundTask(manual_shutdown_callback))
    except BaseException:
        update_write_gate.draining = False
        raise


def configure_manual_restart(server, port, stop_tray=None):
    global manual_shutdown_callback
    def shutdown():
        try:
            server.should_exit = True
            if stop_tray is not None: stop_tray()
        except BaseException:
            update_write_gate.draining = False
            raise
    manual_shutdown_callback = shutdown
    if MANAGED_CONFIG and UPDATE_INSTANCE_ID and getattr(sys, 'frozen', False):
        _operator_profiles()
        from updater.manual_update import live_check
        live_check.configure(profile=Path(CONFIG_DIR).parent, role=DISTRIBUTION_ROLE, version=APP_VERSION,
                             instance_id=UPDATE_INSTANCE_ID, port=port,
                             host=startup_health_payload()['host'], executable=sys.executable)


@app.get('/api/system/version-status')
def system_version_status(request: Request):
    """Public header information; repository checks never download or restart."""
    from fastapi.responses import JSONResponse

    local = is_loopback_client(request)
    out = dict(mode=APP_MODE, current_version=APP_VERSION, latest_version=None,
               result='unmanaged', checked_at=None, update_available=False,
               can_update=False, local=local, managed=bool(MANAGED_CONFIG), busy=False)
    if MANAGED_CONFIG:
        _operator_profiles()
        from updater.manual_update import live_check
        from updater.core import UpdateError, parse_version

        # Hold the existing reentrant lock across scheduling and the snapshot:
        # simultaneous browser polls share one worker and one cached offer.
        with live_check.lock:
            live_check.start(Path(CONFIG_DIR).parent, DISTRIBUTION_ROLE, APP_VERSION, automatic=True)
            status = live_check.status()
        latest = status.get('available_version')
        try:
            newer = parse_version(latest) > parse_version(APP_VERSION)
        except UpdateError:
            latest, newer = None, False
        busy = bool(status.get('busy'))
        out.update(latest_version=latest, result=status['result'],
                   checked_at=status.get('checked_at'), update_available=newer, busy=busy,
                   can_update=bool(local and status.get('restart_supported') and newer and
                                   status['result'] == 'available' and status.get('offer_id') and not busy))
    # Deliberately construct the public allowlist rather than exposing the
    # protected diagnostic payload or the restart operation/capabilities.
    return JSONResponse(out, headers={'Cache-Control': 'no-store'})


@app.api_route('/api/system/update-status', methods=['GET', 'POST'])
async def system_update_status(request: Request, x_admin_token: Optional[str] = Header(default=None)):
    """v3.18.0 [운영] 실행기가 남긴 마지막 업데이트 확인 결과(GET)와, 이 PC 에서만 쓰는 운영 동작(POST).

    POST action: open_logs(로그 폴더 열기) / open_config(설정 파일을 메모장으로) / save_settings(자주 쓰는 설정 저장)
    / reset_blocked(차단된 업데이트 다시 허용). 모두 이 PC(루프백)에서만, 암호를 걸었으면 암호까지 필요하다.
    """
    require_diag_access(x_admin_token, request)
    local = is_loopback_client(request)
    profile_dir = os.path.dirname(CONFIG_DIR) if MANAGED_CONFIG else ''
    if request.method == 'POST':
        if not local:
            raise HTTPException(status_code=403, detail='이 동작은 프로그램을 실행한 PC에서만 할 수 있습니다.')
        if ADMIN_PASSWORD and x_admin_token != ADMIN_PASSWORD:
            raise HTTPException(status_code=401, detail='관리자 암호가 필요합니다.')
        try:
            dto = UpdateStatusActionDTO(**(await request.json()))
        except Exception:
            raise HTTPException(status_code=400, detail='요청 형식이 올바르지 않습니다.')
        windows = os.name == 'nt'
        if dto.action == 'check_now':
            if not profile_dir:
                raise HTTPException(status_code=400, detail='업데이트 실행기로 시작한 프로그램이 아닙니다.')
            _operator_profiles()
            from updater.manual_update import live_check
            operation = live_check.start(Path(profile_dir), DISTRIBUTION_ROLE, APP_VERSION)
            return {'ok': True, 'operation': operation, 'message': '게시된 버전을 확인하고 있습니다.'}
        if dto.action == 'prepare_restart':
            if not profile_dir:
                raise HTTPException(status_code=400, detail='업데이트 실행기로 시작한 프로그램이 아닙니다.')
            _operator_profiles()
            from updater.manual_update import live_check
            from updater.core import UpdateError
            try:
                operation = live_check.prepare(dto.offer_id, dto.instance_id)
            except UpdateError as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return {'ok': True, 'operation': operation, 'message': '새 버전을 준비하고 있습니다.'}
        if dto.action == 'open_logs':
            if not windows:
                raise HTTPException(status_code=400, detail='로그 폴더 열기는 Windows 에서만 됩니다.')
            os.startfile(profile_dir or CONFIG_DIR)
            return {'ok': True, 'message': '로그 폴더를 탐색기로 열었습니다.'}
        if dto.action == 'open_config':
            if not windows:
                raise HTTPException(status_code=400, detail='설정 파일 열기는 Windows 에서만 됩니다.')
            import subprocess
            notepad = os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'notepad.exe')
            subprocess.Popen([notepad, CONFIG_PATH], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
            return {'ok': True, 'message': '설정 파일을 메모장으로 열었습니다. 저장한 뒤 프로그램을 다시 시작해야 적용됩니다.'}
        if dto.action == 'save_settings':
            profiles = _operator_profiles()
            blocked = sorted(set(dto.settings or {}) & VIEWER_LOCKED_SETTINGS) if APP_MODE == 'VIEWER' else []
            if blocked:
                # 3.18.0 QA: 조회용에는 관리자 암호·추가 백업 폴더가 쓰이지 않는다. 화면에서 감춘 값을 직접 보내도 받지 않는다.
                raise HTTPException(status_code=403, detail='조회용 프로그램에서는 관리자 암호와 추가 백업 폴더를 바꿀 수 없습니다.')
            try:
                backup = profiles.update_ini_settings(Path(CONFIG_PATH), dict(dto.settings or {}))
            except profiles.UpdateError as e:
                raise HTTPException(status_code=400, detail=str(e))
            log_info(f'설정 화면에서 설정을 바꿨습니다: {sorted((dto.settings or {}).keys())} (원본 사본: {backup.name})')
            return {'ok': True, 'message': f'저장했습니다. 프로그램을 다시 시작해야 적용됩니다. (원래 파일 사본: {backup.name})'}
        if dto.action == 'reset_blocked':
            if not profile_dir:
                raise HTTPException(status_code=400, detail='업데이트 실행기로 시작한 프로그램이 아닙니다.')
            with open(os.path.join(profile_dir, 'reset_blocked.request'), 'w', encoding='utf-8') as fp:
                fp.write(datetime.now().isoformat(timespec='seconds') + '\n')
            return {'ok': True, 'message': '다음에 프로그램을 시작할 때 차단된 업데이트를 다시 시도합니다. 프로그램을 종료한 뒤 다시 실행하세요.'}
        raise HTTPException(status_code=400, detail='알 수 없는 동작입니다.')
    status = None
    if profile_dir:
        try:
            with open(os.path.join(profile_dir, 'update_status.json'), encoding='utf-8') as fp:
                status = json.load(fp)
            if not isinstance(status, dict):
                status = None
        except (OSError, ValueError):
            status = None
    out = {'managed': bool(profile_dir), 'version': APP_VERSION, 'status': status, 'local': local,
           'canOpen': local and os.name == 'nt', 'settings': None}
    if status and not local:
        status['log_path'] = ''
    if local and profile_dir:
        _operator_profiles()
        from updater.manual_update import live_check
        try:
            out['manual'] = live_check.status(Path(profile_dir), request.query_params.get('operation'))
        except Exception:
            raise HTTPException(status_code=400, detail='업데이트 작업 번호가 올바르지 않습니다.')
    out['mode'] = DISTRIBUTION_ROLE
    if local:
        try:
            out['settings'] = _viewer_visible_settings(_operator_profiles().read_ini_settings(Path(CONFIG_PATH)))
        except Exception:
            out['settings'] = None
    return out


# 3.18.0 QA: 조회용 [연결 정보 → 자주 바꾸는 설정]에 보여 주지 않고, 저장도 받지 않는 값.
VIEWER_LOCKED_SETTINGS = frozenset({'admin_password', 'backup_mirror_dir'})


def _viewer_visible_settings(settings):
    """조회용이면 쓸모없는 값을 뺀다. 허용할 접속 이름은 다른 PC가 이 프로그램에 접속할 수 있을 때(루프백이 아닐 때)만 보인다."""
    if APP_MODE != 'VIEWER' or not isinstance(settings, dict):
        return settings
    shown = {k: v for k, v in settings.items() if k not in VIEWER_LOCKED_SETTINGS and k != 'admin_password_set'}
    if BIND_HOST in LOOPBACK_HOSTS:
        shown.pop('allowed_hosts', None)
    return shown


@app.get('/api/system/diagnostics')
def get_diagnostics(request: Request, x_admin_token: Optional[str] = Header(default=None)):
    """
[18] 자가 진단. 사용자가 '무슨 일이 일어났는지' 물어볼 수 있게 한 화면에 모은다.
v3.9.1 [D] 경로·PC 이름·로그 오류가 들어 있어 암호 설정 시 토큰을 요구한다.
"""
    require_diag_access(x_admin_token, request)
    out = {
        'mode': APP_MODE, 'version': APP_VERSION, 'port': SERVER_PORT,
        'hostLabel': HOST_LABEL, 'bindHost': BIND_HOST,
        'storageDir': STORAGE_DIR, 'dbPath': DB_PATH,
        'sharedConfigured': bool(SHARED_PATH), 'sharedPath': SHARED_PATH or '',
        'storageWarning': STORAGE_WARNING,
        'storageWritable': STORAGE_WRITABLE, 'storageWriteDetail': STORAGE_WRITE_DETAIL,
        'passwordSet': bool(ADMIN_PASSWORD),
        'dbOk': False, 'dbSize': 0, 'dbModified': '', 'integrity': '',
        'counts': {}, 'dataVersion': 0, 'lastWrite': {'version': '', 'host': '', 'at': '', 'max': ''},
        'backups': {'dir': '', 'count': 0, 'latest': '', 'latestAt': '', 'ok': BACKUP_STATE.get('ok'),
                    'detail': BACKUP_STATE.get('detail', '')},
        'recentErrors': [], 'detail': '',
        'journal': {'exists': False, 'size': 0, 'modified': '', 'hint': ''},
        'backupMirror': {'dir': BACKUP_STATE.get('mirror', '') or _cfg('backup_mirror_dir', ''),
                         'detail': BACKUP_STATE.get('mirrorDetail', '')},
        'account': ACTOR_LABEL, 'logPath': LOG_PATH,
    }
    # v3.16.0 [D-3] 저장 중 공유 폴더가 끊기면 '-journal' 파일이 남는다. 조회용 PC 는 읽기 전용이라
    #   이 파일을 정리(롤백)할 수 없어 전원이 열기 오류를 볼 수 있다. 있으면 무엇을 하면 되는지 알린다.
    try:
        journal = DB_PATH + '-journal'
        if os.path.isfile(journal):
            size = os.path.getsize(journal)
            out['journal'] = {
                'exists': True, 'size': size,
                'modified': datetime.fromtimestamp(os.path.getmtime(journal)).strftime('%Y-%m-%d %H:%M:%S'),
                'hint': ('저장 도중 끊긴 흔적이 있습니다. 관리자용 프로그램을 한 번 실행하면 자동으로 정리됩니다. '
                         '관리자용 프로그램이 지금 저장 중이라면 잠시 뒤 사라집니다.') if size else '',
            }
    except Exception:
        pass
    conn = None
    try:
        if os.path.isfile(DB_PATH):
            out['dbSize'] = os.path.getsize(DB_PATH)
            out['dbModified'] = datetime.fromtimestamp(
                os.path.getmtime(DB_PATH)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_db_connection()
        cur = conn.cursor()
        out['dbOk'] = True
        try:
            out['integrity'] = cur.execute('PRAGMA quick_check').fetchone()[0]
        except Exception as e:
            out['integrity'] = f'검사 실패: {e}'
        for table in ('members', 'schedules', 'absences', 'custom_holidays', 'change_history', 'change_requests'):
            try:
                out['counts'][table] = cur.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
            except Exception:
                out['counts'][table] = -1
        out['dataVersion'] = read_data_version(cur)
        out['lastWrite'] = read_last_write(cur)
    except HTTPException as e:
        out['detail'] = e.detail
    except Exception as e:
        out['detail'] = f'진단 중 오류: {e}'
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    try:
        backup_dir = os.path.join(STORAGE_DIR, 'backups')
        out['backups']['dir'] = backup_dir
        if os.path.isdir(backup_dir):
            files = sorted(f for f in os.listdir(backup_dir)
                           if f.startswith('smart_duty_') and f.endswith('.db'))
            out['backups']['count'] = len(files)
            if files:
                out['backups']['latest'] = files[-1]
                out['backups']['latestAt'] = datetime.fromtimestamp(
                    os.path.getmtime(os.path.join(backup_dir, files[-1]))
                ).strftime('%Y-%m-%d %H:%M:%S')
    except Exception as e:
        out['backups']['detail'] = f'백업 폴더 확인 실패: {e}'
    try:
        if os.path.isfile(LOG_PATH):
            with open(LOG_PATH, 'r', encoding='utf-8', errors='replace') as fp:
                lines = fp.readlines()
            out['recentErrors'] = [ln.rstrip() for ln in lines if 'ERROR' in ln][-30:]
    except Exception:
        pass
    return out






# BEGIN HOLIDAY INTEGRATION
# Extra-holiday API and existing-workflow boundaries (v3.18.32).
HOLIDAY_REQUEST_CONTRACT = ContextVar('onair_holiday_request_contract', default=None)


class HolidayContractContext:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        headers = dict(scope.get('headers') or [])
        token = HOLIDAY_REQUEST_CONTRACT.set(headers.get(b'x-onair-data-contract', b'').decode('ascii', errors='replace'))
        try:
            await self.app(scope, receive, send)
        finally:
            HOLIDAY_REQUEST_CONTRACT.reset(token)


def require_holiday_write_contract(cur):
    # Check AFTER acquiring the write lock: another PC may have activated the
    # contract while this old browser request was waiting for its transaction.
    verify_holiday_contract(cur)
    row = cur.execute("SELECT value FROM app_settings WHERE key='required_write_contract'").fetchone()
    if row and str(row[0]) == '1' and HOLIDAY_REQUEST_CONTRACT.get() != '1':
        raise HTTPException(status_code=428, detail='추가 공휴일 자료를 보호하기 위해 최신 화면이 필요합니다. 새로고침(F5)한 뒤 다시 시도해 주세요.')


def hs_read_state(cur):
    domain = domain_snapshot(cur)
    members = [dict(r) for r in cur.execute('SELECT id,name,gender FROM members ORDER BY id')]
    row = cur.execute("SELECT rule_json FROM system_rules WHERE rule_key='DEFAULT'").fetchone()
    rules = json.loads(row[0]) if row and row[0] else {}
    columns = {r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)')}
    removed = 'is_removed' if 'is_removed' in columns else '0 AS is_removed'
    holidays = {r['date_key']: {'name': r['name'], 'isMajor': bool(r['is_major']), 'isRemoved': bool(r['is_removed'])}
                for r in cur.execute(f'SELECT date_key,name,is_major,{removed} FROM custom_holidays')}
    no_duty = {r[0]: r[1] or True for r in cur.execute("SELECT date_key,COALESCE(no_duty_reason,'') FROM day_meta WHERE is_no_duty")}
    requests = [dict(r) for r in cur.execute("SELECT * FROM change_requests WHERE status='PENDING' ORDER BY id")]
    return {'members': members, 'schedules': domain['schedules'], 'absences': domain['absences'],
            'rules': rules, 'customHolidays': holidays, 'noDuty': no_duty,
            'closedMonths': sorted(read_closed_months(cur)), 'dataVersion': read_data_version(cur),
            'holidaySupplements': read_holiday_supplements(cur, private=True), 'requests': requests}


def hs_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def hs_normalized_draft(draft):
    return {key: value for key, value in draft.items() if key not in ('baseVersion', '_operationId')}


def hs_request_intersects(item, start, end):
    dates = [item.get('target_date') or '', item.get('swap_date') or '']
    if any(start <= key <= end for key in dates if key):
        return True
    first = item.get('target_date') or ''
    last = item.get('absence_end_date') or first
    if first and first <= end and last >= start:
        return True
    original = item.get('amendment_original')
    original = optional_json(original) if isinstance(original, str) else original
    if isinstance(original, dict):
        spans = original.get('segments') or [original]
        return any(isinstance(s, dict) and s.get('start', '') <= end and s.get('end', '') >= start for s in spans)
    return False


def hs_review_digest(state, draft):
    start = draft.get('date_key') or f"{draft.get('year'):04d}-01-01"
    end = start if draft.get('date_key') else f"{draft.get('year'):04d}-12-31"
    relevant = [r for r in state['requests'] if hs_request_intersects(r, start, end)]
    data = {key: state[key] for key in ('members','schedules','absences','rules','customHolidays','noDuty','closedMonths','holidaySupplements')}
    data['requests'] = relevant
    # Operation receipts do not determine a candidate or approval impact.
    data['holidaySupplements'] = {k: v for k, v in data['holidaySupplements'].items() if k != 'operations'}
    return hs_hash({'draft': hs_normalized_draft(draft), 'state': data})


def hs_public_metadata(state):
    projected = hs_storage_public_projection(state['holidaySupplements'])
    for basis in projected['bases']:
        review = hs_basis_review(state, basis)
        if review:
            basis.update(review)
    return projected


def hs_metadata_snapshot(cur):
    return {k: v for k, v in read_holiday_supplements(cur, private=True).items() if k != 'operations'}


def hs_public_history(history):
    result = copy.deepcopy(history)
    for record in result:
        for side in ('before', 'after'):
            snapshot = record.get(side)
            if isinstance(snapshot, dict) and isinstance(snapshot.get('holidaySupplements'), dict):
                snapshot['holidaySupplements'] = hs_storage_public_projection(snapshot['holidaySupplements'])
    return result


class HolidayDraftDTO(BaseModel):
    model_config = {'extra': 'forbid'}
    holidayIntent: str
    date_key: Optional[str] = None
    year: Optional[int] = None
    pool_key: Optional[str] = None
    expected_basis_revision: Optional[int] = None
    name: Optional[str] = None
    is_major: bool = False
    baseVersion: Optional[int] = None
    anchor: Optional[int | dict] = None
    reconcilePolicy: Optional[str] = None


class HolidayApplyDTO(BaseModel):
    model_config = {'extra': 'forbid'}
    operationId: str
    draft: HolidayDraftDTO
    baseVersion: int
    reviewDigest: str


def hs_draft_dict(dto):
    data = dto.model_dump(exclude_none=True)
    intents = {'SET_CUSTOM','DELETE_CUSTOM','REMOVE_BASE','RESTORE_BASE','RECONCILE'}
    if data['holidayIntent'] not in intents:
        raise HTTPException(status_code=400, detail='공휴일 변경 종류가 올바르지 않습니다.')
    if data.get('is_major'):
        raise HTTPException(status_code=400, detail='명절 근무는 명절 근무표에서 관리해 주세요.')
    if data['holidayIntent'] == 'RECONCILE':
        if (type(data.get('year')) is not int or not 2023 <= data['year'] <= 2029 or
                data.get('pool_key') not in ('holiday|M','weekend|M') or
                type(data.get('expected_basis_revision')) is not int or data['expected_basis_revision'] < 0 or
                data.get('reconcilePolicy') != 'PRESERVE_EXISTING'):
            raise HTTPException(status_code=400, detail='재확인할 연도·순번·기준을 확인해 주세요.')
    else:
        data['date_key'] = strict_iso_date(data.get('date_key'), '공휴일 날짜')
        if data['holidayIntent'] == 'SET_CUSTOM':
            data['name'] = (data.get('name') or '').strip()
            if not data['name'] or len(data['name']) > 100:
                raise HTTPException(status_code=400, detail='공휴일 이름은 1~100자로 입력해 주세요.')
    return data


def build_holiday_supplement_plan(cur, draft, today):
    state = hs_read_state(cur)
    plan = hs_calculate(state, draft, today)
    plan['reviewDigest'] = hs_review_digest(state, draft)
    return plan


@app.post('/api/duty/holiday-supplement/preview')
def preview_holiday_supplement(dto: HolidayDraftDTO, x_admin_token: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    draft = hs_draft_dict(dto)
    conn = get_db_connection(readonly=True)
    try:
        cur = conn.cursor()
        cur.execute('BEGIN')
        plan = build_holiday_supplement_plan(cur, draft, datetime.now(timezone(timedelta(hours=9))).date())
        return {k: v for k, v in plan.items() if not k.startswith('_')}
    finally:
        conn.rollback()
        conn.close()


def hs_operation_current_state(cur, operation):
    supplement_id = operation.get('supplement_id') or operation.get('result', {}).get('supplementId')
    if not supplement_id:
        return 'ACTIVE'
    row = cur.execute('SELECT state FROM holiday_supplements WHERE supplement_id=?', (supplement_id,)).fetchone()
    return row[0] if row else 'RESTORED_OUT'


def hs_write_target(cur, key, workers, custom):
    cur.execute('DELETE FROM schedules WHERE date_key=?', (key,))
    for index, worker in enumerate(workers):
        cur.execute('INSERT INTO schedules(date_key,slot_index,worker_name,role,is_substitute,zero_score) VALUES(?,?,?,?,?,?)',
                    (key,index,worker['name'],worker.get('role') or '',int(bool(worker.get('isSubstitute'))),int(bool(worker.get('zeroScore')))))
    cur.execute('DELETE FROM custom_holidays WHERE date_key=?', (key,))
    if custom is not None:
        cur.execute('INSERT INTO custom_holidays(date_key,name,is_major,is_removed) VALUES(?,?,?,?)',
                    (key,custom.get('name') or '',int(bool(custom.get('isMajor'))),int(bool(custom.get('isRemoved')))))


@app.post('/api/duty/holiday-supplement/apply')
def apply_holiday_supplement(dto: HolidayApplyDTO, x_admin_token: Optional[str] = Header(default=None),
                             x_client_id: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    try:
        operation_id = str(uuid.UUID(dto.operationId))
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail='작업 번호가 올바르지 않습니다. 다시 미리보기해 주세요.')
    draft = hs_draft_dict(dto.draft)
    payload_hash = hs_hash({'draft': hs_normalized_draft(draft), 'baseVersion': dto.baseVersion, 'reviewDigest': dto.reviewDigest})
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        metadata = read_holiday_supplements(cur, private=True)
        previous = next((o for o in metadata.get('operations', []) if o['operation_id'] == operation_id), None)
        if previous:
            if previous['payload_hash'] != payload_hash:
                raise HTTPException(status_code=409, detail='같은 작업 번호로 다른 내용을 보낼 수 없습니다. 다시 미리보기해 주세요.')
            return {**previous['result'], 'historicalResult': previous['result'], 'currentState': hs_operation_current_state(cur, previous)}
        if dto.baseVersion != read_data_version(cur):
            raise HTTPException(status_code=409, detail='다른 PC에서 자료를 변경했습니다. 최신 내용으로 다시 미리보기해 주세요.')
        draft['_operationId'] = operation_id
        plan = build_holiday_supplement_plan(cur, draft, datetime.now(timezone(timedelta(hours=9))).date())
        if not secrets.compare_digest(plan['reviewDigest'], dto.reviewDigest):
            raise HTTPException(status_code=409, detail='일정이나 신청 내용이 바뀌었습니다. 최신 내용으로 다시 미리보기해 주세요.')
        if not plan['eligible']:
            messages = [str(b.get('message') or b.get('code') or '') for b in plan.get('blockers', [])]
            raise HTTPException(status_code=409, detail=' · '.join(messages) or '현재 조건에서는 공휴일 보완을 적용할 수 없습니다.')
        effect = plan['effect']
        before = hs_read_state(cur)
        safety = ''
        if effect != 'NOOP':
            safety = os.path.basename(take_auto_snapshot('holiday'))
        key = draft.get('date_key')
        if key and effect != 'NOOP':
            hs_write_target(cur, key, plan['_schedules'].get(key, []), plan['_customHolidays'].get(key))
        now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
        next_metadata = {'schema': 1, 'events': plan['_events'], 'bases': plan['_bases'],
                         'requiredContract': max(metadata['requiredContract'], int(effect in ('ADD','REMOVE','RECONCILE')))}
        for event in next_metadata['events']:
            if not event.get('created_at'):
                event['created_at'] = now
        for basis in next_metadata['bases']:
            if not basis.get('adopted_at'):
                basis['adopted_at'] = now
        write_holiday_supplements(cur, next_metadata)
        if effect != 'NOOP':
            after = hs_read_state(cur)
            if effect == 'RECONCILE':
                next_metadata = reconcile_holiday_supplements(cur, before, after, 'basis-reconcile')
                after['holidaySupplements'] = next_metadata
            before_history = {'schedules': {key: before['schedules'].get(key, [])} if key else {},
                              'customHolidays': {key: before['customHolidays'].get(key)} if key else {},
                              'holidaySupplements': {k: v for k, v in before['holidaySupplements'].items() if k != 'operations'}}
            after_history = {'schedules': {key: after['schedules'].get(key, [])} if key else {},
                             'customHolidays': {key: after['customHolidays'].get(key)} if key else {},
                             'holidaySupplements': hs_metadata_snapshot(cur)}
            names = ' · '.join(w['name'] for w in plan.get('after', {}).get('workers', []))
            insert_history(cur, now, '공휴일 보완' if effect == 'ADD' else '공휴일 해제' if effect == 'REMOVE' else '공휴일 기준 확인',
                           key or f"{draft.get('year')}-01-01", f'{effect}: {names} · 연간 예약 및 기존 근무 보존', before_history, after_history)
            version = bump_data_version(cur, 'holiday-supplement:' + operation_id)
        else:
            version = read_data_version(cur)
        event = next((e for e in next_metadata['events'] if e['date_key'] == key and e['state'] != 'CANCELLED'), None)
        if event is None and key:
            event = next((e for e in reversed(next_metadata['events']) if e['date_key'] == key), None)
        result = {'status': 'success', 'operationId': operation_id, 'dataVersion': version,
                  'supplementId': event['supplement_id'] if event else None,
                  'changedDates': plan['affectedDates'], 'snapshot': safety}
        receipt = {'operation_id': operation_id, 'payload_hash': payload_hash, 'action': effect, 'result': result,
                   'supplement_id': result['supplementId'], 'created_at': now}
        write_holiday_supplements(cur, {**next_metadata, 'operations': [receipt]})
        conn.commit()
    except HTTPException:
        if conn: conn.rollback()
        raise
    except Exception as error:
        if conn: conn.rollback()
        log_error(f'공휴일 보완 적용 실패: {error}', error)
        if isinstance(error, sqlite3.OperationalError) and any(word in str(error).lower() for word in ('locked', 'busy')):
            raise HTTPException(status_code=503, detail='다른 PC에서 저장 중입니다. 잠시 후 같은 작업으로 다시 시도해 주세요.') from error
        raise HTTPException(status_code=500, detail='공휴일 보완을 저장하지 못했습니다. 기존 자료는 바뀌지 않았습니다.') from error
    finally:
        if conn: conn.close()
    broadcast_from_thread({'event': 'DATA_SYNCED', 'origin': x_client_id or ''})
    return result


def reconcile_holiday_supplements(cur, before, after, source):
    metadata = copy.deepcopy(after['holidaySupplements'])
    if not metadata['events'] and not metadata['bases']:
        return metadata
    now = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec='seconds')
    changed = False
    holidays_before, holidays_after = hs_holidays(before), hs_holidays(after)
    for event in metadata['events']:
        if event['state'] == 'CANCELLED':
            continue
        key = event['date_key']
        if ((key in holidays_before) != (key in holidays_after) or
                hs_major(holidays_before, key) != hs_major(holidays_after, key) or
                bool(before['noDuty'].get(key)) != bool(after['noDuty'].get(key))):
            raise HTTPException(status_code=409, detail=f'{key}은 공휴일 보완 기록이 있습니다. 공휴일 관리의 보완 미리보기에서 해제·변경해 주세요.')
        current = after['schedules'].get(key, [])
        if current != event['current_slot_binding']:
            event['current_slot_binding'] = copy.deepcopy(current)
            event['updated_at'] = now
            changed = True
        basis = hs_latest_basis(after, event['year'], event['pool_key'])
        review = hs_basis_review(after, basis) if basis else {'state': 'NEEDS_REVIEW', 'review_code': 'BASIS_REBASE_REQUIRED'}
        if event['female_credit']['order_snapshot'] != hs_order(after, 'weekday|F'):
            review = {'state': 'NEEDS_REVIEW', 'review_code': 'BASIS_REBASE_REQUIRED'}
        new_state = 'NEEDS_REVIEW' if review['state'] == 'NEEDS_REVIEW' else 'ACTIVE'
        code = review.get('review_code') or ''
        if event['state'] != new_state or event.get('review_code', '') != code:
            event['state'], event['review_code'] = new_state, code
            event['updated_at'] = now
            changed = True
    if changed:
        write_holiday_supplements(cur, metadata)
    return metadata


def confirm_dinner_duty(cur, poll, candidate, body):
    """Apply one approved dinner date inside DinnerAPI's existing write lock.

    DinnerService checks approved CA or verified Master authority, poll revision and idempotency before
    calling this narrow adapter. It must not open another write transaction or
    regenerate assignments. A cancellation deliberately leaves this receipt and
    the duty day unchanged; later duty administration remains authoritative.
    """
    if not cur.connection.in_transaction:
        raise HTTPException(status_code=500, detail='회식 확정의 저장 연결을 확인할 수 없습니다.')
    base_version = body.get('baseVersion')
    if type(base_version) is not int:
        raise HTTPException(status_code=428, detail='당직표 기준 정보가 없습니다. 최신 내용을 불러온 뒤 확정해 주세요.')
    if base_version != read_data_version(cur):
        raise HTTPException(status_code=409, detail='다른 PC에서 당직표를 변경했습니다. 최신 내용을 확인한 뒤 회식 날짜를 확정해 주세요.')
    key = strict_iso_date(candidate['date'], '확정 날짜')
    now = datetime.now(timezone(timedelta(hours=9)))
    if date.fromisoformat(key) < now.date():
        raise HTTPException(status_code=400, detail='지난 날짜는 석식간담회로 확정할 수 없습니다.')
    if closed_month_of(key, read_closed_months(cur)):
        raise HTTPException(status_code=423, detail='마감된 달의 당직표는 회식 확정으로 바꿀 수 없습니다. 관리자에게 해당 달 확인을 요청해 주세요.')

    workers = [dict(row) for row in cur.execute(
        'SELECT date_key,slot_index,worker_name,role,is_substitute,zero_score FROM schedules WHERE date_key=? ORDER BY slot_index', (key,))]
    row = cur.execute('SELECT date_key,comment,is_no_duty,no_duty_reason FROM day_meta WHERE date_key=?', (key,)).fetchone()
    prior_meta = dict(row) if row else None
    before = hs_read_state(cur)
    before_metadata = hs_metadata_snapshot(cur)
    # The snapshot reads the still-committed database while this connection holds
    # the reserved write lock. A failure aborts the entire poll/duty transaction.
    try:
        snapshot_path = take_auto_snapshot('dinner')
        if not snapshot_path or not os.path.isfile(snapshot_path):
            raise RuntimeError('확정 직전 사본을 확인할 수 없습니다.')
    except Exception as error:
        log_error('석식간담회 확정 직전 사본 생성 실패', error)
        raise HTTPException(status_code=503, detail='확정 직전 안전 사본을 만들지 못해 저장하지 않았습니다. 백업 폴더를 확인한 뒤 다시 시도해 주세요.') from None

    stamp = now.isoformat(timespec='seconds')
    metadata = copy.deepcopy(before['holidaySupplements'])
    cancelled = []
    for event in metadata['events']:
        if event['date_key'] == key and event['state'] in ('ACTIVE', 'NEEDS_REVIEW'):
            event.update(state='CANCELLED', current_slot_binding=[], cancelled_at=stamp, updated_at=stamp)
            # Keep its historical reservation consumed so later annual ordinals
            # and bases are not renumbered by this one-day duty removal.
            event['credit_state'] = 'CONSUMED'
            cancelled.append(event['supplement_id'])
    if cancelled:
        write_holiday_supplements(cur, metadata)

    memo = '석식간담회'
    prior_comment = (prior_meta or {}).get('comment') or ''
    comment = prior_comment if memo in prior_comment.splitlines() else (
        prior_comment + '\n' + memo if prior_comment else memo)
    previous_reason = (prior_meta or {}).get('no_duty_reason') or ''
    reason = previous_reason if (prior_meta or {}).get('is_no_duty') and previous_reason not in ('RULE21', 'RULE_MAJOR_DAY') else memo
    cur.execute('DELETE FROM schedules WHERE date_key=?', (key,))
    cur.execute('INSERT INTO day_meta(date_key,comment,is_no_duty,no_duty_reason) VALUES(?,?,1,?) '
                'ON CONFLICT(date_key) DO UPDATE SET comment=excluded.comment,is_no_duty=1,no_duty_reason=excluded.no_duty_reason',
                (key, comment, reason))
    reconcile_holiday_supplements(cur, before, hs_read_state(cur), 'dinner-confirm')

    after_metadata = hs_metadata_snapshot(cur)
    before_history = {'schedules': {key: before['schedules'].get(key, [])}}
    after_history = {'schedules': {key: []}}
    if before_metadata != after_metadata:
        before_history['holidaySupplements'] = before_metadata
        after_history['holidaySupplements'] = after_metadata
    names = ', '.join(row['worker_name'] for row in workers) or '없음'
    details = f"{poll['title']} · {key} 석식간담회 확정 · 기존 당직자: {names} · 해당일 당직 해제 · 기존 메모 보존"
    if cancelled:
        details += ' · 해당일 공휴일 보완 취소(연간 소비 기록 유지)'
    insert_history(cur, stamp, '석식간담회 확정', key, details, before_history, after_history)
    version = bump_data_version(cur, 'dinner-confirm:' + poll['id'])
    return {'date': key, 'memo': memo, 'noDuty': True, 'appliedAt': stamp,
            'dataVersion': version, 'snapshot': os.path.basename(snapshot_path),
            'before': {'dayMeta': prior_meta, 'workers': workers},
            'cancelledSupplements': cancelled}


def hs_request_date_blocker(cur, item):
    kind = canonical_request_type(item.get('request_type'))
    if kind not in ('SWAP', 'SUBSTITUTE'):
        return ''
    state = hs_read_state(cur)
    holidays = hs_holidays(state)
    target = item.get('target_date') or ''
    dates = [target] + ([item.get('swap_date') or ''] if kind == 'SWAP' else [])
    for key in dates:
        if not key:
            return '변경할 날짜를 확인해 주세요.'
        if state['noDuty'].get(key):
            return f'{key}은 당직 없는 날입니다. 관리자가 근무 상태를 먼저 확인해야 합니다.'
        if hs_major(holidays, key):
            return '명절 당직은 관리자에게 변경을 문의해 주세요.'
    if kind == 'SWAP':
        off = lambda key: date.fromisoformat(key).weekday() >= 5 or key in holidays
        if off(dates[0]) != off(dates[1]):
            return '평일은 평일끼리, 주말·공휴일은 서로 교환해 주세요. 공휴일 조건이 변경됐는지 확인해 주세요.'
    return ''


def hs_approval_impact_digest(cur, request):
    item = dict(request)
    for key in ('receipt_token', 'requester_host', 'requester_account'):
        item.pop(key, None)
    state = hs_read_state(cur)
    kind = canonical_request_type(item.get('request_type'))
    start = item.get('target_date') or ''
    end = item.get('absence_end_date') or start
    dates = {start}
    if kind == 'SWAP':
        dates.add(item.get('swap_date') or '')
    if kind in ('ABSENCE', 'ABSENCE_CANCEL'):
        dates.update(key for key in state['schedules'] if start <= key <= end)
        original = item.get('amendment_original')
        original = optional_json(original) if isinstance(original, str) else original
        if isinstance(original, dict):
            first, last = original.get('start') or '', original.get('end') or ''
            dates.update(key for key in state['schedules'] if first <= key <= last)
    plan_item = copy.deepcopy(item)
    add_request_date_plan(cur, plan_item)
    relevant_names = {item.get('requester_name'), item.get('swap_worker'), item.get('substitute_name')}
    holidays = hs_holidays(state)
    envelope = [key for key in holidays if start <= key <= end] if kind in ('ABSENCE','ABSENCE_CANCEL') else sorted(dates)
    return hs_hash({'request': item, 'blocker': approval_blocker(cur, item),
                    'absence_plan': plan_item.get('absence_date_plan'),
                    'schedules': {key: state['schedules'].get(key, []) for key in sorted(dates)},
                    'holidays': {key: holidays.get(key) for key in sorted(envelope)},
                    'noDuty': {key: state['noDuty'].get(key) for key in sorted(dates)},
                    'closedMonths': [month for month in state['closedMonths'] if any(key.startswith(month) for key in dates)],
                    'members': [row for row in state['members'] if row['name'] in relevant_names],
                    'absences': [row for row in state['absences'] if row['name'] in relevant_names],
                    'supplements': [event for event in state['holidaySupplements']['events'] if event['date_key'] in dates]})


def hs_validate_restore_binding(cur, metadata):
    state = hs_read_state(cur)
    state['holidaySupplements'] = metadata
    holidays = hs_holidays(state)
    for event in metadata['events']:
        if event['state'] == 'CANCELLED':
            continue
        key = event['date_key']
        if key not in holidays or hs_major(holidays, key) or state['noDuty'].get(key):
            raise HTTPException(status_code=400, detail=f'가져올 {key} 공휴일과 보완 기록이 일치하지 않습니다. 전체 백업 파일을 사용해 주세요.')
        if event['current_slot_binding'] != state['schedules'].get(key, []):
            raise HTTPException(status_code=400, detail=f'가져올 {key} 근무표와 보완 기록이 일치하지 않습니다. 일부 값만 바뀐 파일은 복원할 수 없습니다.')


def hs_closed_metadata_conflicts(before, after, closed):
    """Protect closed dates and their replay bases without locking unrelated events."""
    keys = {e['date_key'] for data in (before, after) for e in data['events'] if e['date_key'][:7] in closed}
    def footprint(data, key):
        events = []
        for original in data['events']:
            if original['date_key'] != key:
                continue
            event = {k:v for k,v in original.items() if k not in ('updated_at','review_code')}
            if event['state'] == 'NEEDS_REVIEW':
                event['state'] = 'ACTIVE'
            events.append(event)
        references = {(e['year'],e['pool_key'],e['basis_revision']) for e in events}
        for year, pool in {(e['year'],e['pool_key']) for e in events}:
            candidates = [b for b in data['bases'] if b['year']==year and b['pool_key']==pool]
            if candidates:
                references.add((year,pool,max(b['revision'] for b in candidates)))
        bases = [b for b in data['bases'] if (b['year'],b['pool_key'],b['revision']) in references]
        return sorted(events,key=lambda e:e['supplement_id']), sorted(bases,key=lambda b:(b['year'],b['pool_key'],b['revision']))
    return sorted(key for key in keys if footprint(before,key) != footprint(after,key))


class HolidayImportDTO(BaseModel):
    model_config = {'extra': 'forbid'}
    data: dict
    baseVersion: int
    restoreLegacy: bool = False


def hs_export_payload(data):
    return {'formatVersion': 2, 'exportedAt': datetime.now(timezone(timedelta(hours=9))).isoformat(),
            **{key: data[key] for key in ('members','schedules','comments','noDuty','absences','absenceTypes','customHolidays','rules','globalMemo','history','holidaySupplements')}}


@app.get('/api/system/export-json')
def export_holiday_json(x_admin_token: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    return hs_export_payload(_read_duty_data(full_history=True, private_supplements=True))


@app.post('/api/system/import-json')
def import_holiday_json(dto: HolidayImportDTO, x_admin_token: Optional[str] = Header(default=None),
                        x_client_id: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    data = dto.data
    version = data.get('formatVersion', 1)
    if type(version) is not int or version not in (1,2):
        raise HTTPException(status_code=400, detail='이 버전에서 지원하지 않는 백업 파일입니다.')
    required = {'members','schedules','comments','noDuty','absences','absenceTypes','customHolidays','rules'}
    if not required <= set(data):
        raise HTTPException(status_code=400, detail='전체 백업 자료가 필요합니다. 일부 항목만 가져와 보완 기록을 바꿀 수 없습니다.')
    metadata = data.get('holidaySupplements')
    if metadata is not None:
        metadata = validate_holiday_supplements(metadata)
    values = {key: data[key] for key in required}
    values.update(globalMemo=data.get('globalMemo',''), historyBulk=data.get('history', data.get('historyBulk', [])),
                  baseVersion=dto.baseVersion, snapshotKind='import')
    return _save_all_data(SyncFullDataDTO(**values), x_admin_token, x_client_id,
                          holiday_restore={'metadata': metadata, 'restoreLegacy': dto.restoreLegacy})


def hs_reconciled_domain(cur, before):
    reconcile_holiday_supplements(cur, before, hs_read_state(cur), 'approval')
    return domain_snapshot(cur)

# END HOLIDAY INTEGRATION

app.add_middleware(HolidayContractContext)

@app.get('/api/duty/all-data')
def get_all_duty_data():
    return _read_duty_data()


def _read_duty_data(*, full_history=False, private_supplements=False):
    """Read one consistent snapshot; only the authenticated backup requests all history."""
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # v3.16.0 [위험] 예전에는 트랜잭션 없이 SELECT 를 열 번 남짓 따로 돌리고 dataVersion 을
        #   마지막에 읽었다. 그 사이 다른 PC 가 저장하면 '옛 당직표 + 새 버전 번호' 를 받아,
        #   그 상태로 저장하면 충돌 검사를 통과해 남의 저장을 지웠다.
        #   하나의 읽기 트랜잭션으로 묶어 모든 값이 같은 시점을 보게 하고, 버전을 먼저 읽는다.
        cur.execute('BEGIN')
        data_version = read_data_version(cur)
        cur.execute('SELECT id, name, gender FROM members ORDER BY id')
        members = [{'id': r['id'], 'name': r['name'], 'gender': r['gender']} for r in cur.fetchall()]
        cur.execute('SELECT date_key, slot_index, worker_name, role, is_substitute, zero_score FROM schedules ORDER BY date_key, slot_index')
        schedules = {}
        for r in cur.fetchall():
            schedules.setdefault(r['date_key'], []).append({
                'name': r['worker_name'], 'role': r['role'], 'isSubstitute': bool(r['is_substitute']), 'zeroScore': bool(r['zero_score'])
            })
        meta_cols = [r[1] for r in cur.execute('PRAGMA table_info(day_meta)').fetchall()]
        if 'no_duty_reason' in meta_cols:
            cur.execute('SELECT date_key, comment, is_no_duty, no_duty_reason FROM day_meta')
        else:
            cur.execute("SELECT date_key, comment, is_no_duty, '' AS no_duty_reason FROM day_meta")
        comments, noduty = {}, {}
        for r in cur.fetchall():
            if r['comment']:
                comments[r['date_key']] = r['comment']
            if r['is_no_duty']:
                noduty[r['date_key']] = r['no_duty_reason'] or True
        absences = read_absences(cur)
        cur.execute('SELECT type_name FROM absence_types')
        absence_types = [r['type_name'] for r in cur.fetchall()]
        hol_cols = [r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)').fetchall()]
        if 'is_removed' in hol_cols:
            cur.execute('SELECT date_key, name, is_major, is_removed FROM custom_holidays')
            custom_holidays = {
                r['date_key']: {'name': r['name'], 'isMajor': bool(r['is_major']), 'isRemoved': bool(r['is_removed'])}
                for r in cur.fetchall()}
        else:
            cur.execute('SELECT date_key, name, is_major FROM custom_holidays')
            custom_holidays = {
                r['date_key']: {'name': r['name'], 'isMajor': bool(r['is_major']), 'isRemoved': False}
                for r in cur.fetchall()}
        has_settings = cur.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='app_settings'"
        ).fetchone()[0] > 0
        cur.execute("SELECT rule_json FROM system_rules WHERE rule_key = 'DEFAULT'")
        rule_row = cur.fetchone()
        rules = json.loads(rule_row['rule_json']) if rule_row else None
        global_memo = ''
        if has_settings:
            cur.execute("SELECT value FROM app_settings WHERE key = 'global_memo'")
            memo_row = cur.fetchone()
            global_memo = memo_row['value'] if memo_row else ''
        hist_cols = {r[1] for r in cur.execute('PRAGMA table_info(change_history)').fetchall()}
        extra = ', actor, host' if set(HISTORY_EXTRA_COLUMNS) <= hist_cols else ", '' AS actor, '' AS host"
        extra += ''.join(f', {key}' if key in hist_cols else f", '' AS {key}" for key in HISTORY_JSON_COLUMNS)
        # 화면에는 최근 500건만, 수동 JSON 백업에는 같은 읽기 시점의 이력 전체를 담는다.
        history_limit = '' if full_history else ' LIMIT 500'
        cur.execute(f'SELECT id, timestamp, type, date_key, details{extra} FROM change_history ORDER BY id DESC{history_limit}')
        history = [{'id': r['id'], 'timestamp': r['timestamp'], 'type': r['type'], 'dateKey': r['date_key'],
                    'details': r['details'], 'actor': r['actor'] or '', 'host': r['host'] or '',
                    **{field: value for field, column in (('before', 'before_json'), ('after', 'after_json'))
                       if (value := optional_json(r[column])) is not None}} for r in cur.fetchall()]
        closed = sorted(read_closed_months(cur))
        hs_state = hs_read_state(cur)
        supplement_data = hs_state['holidaySupplements'] if private_supplements else hs_public_metadata(hs_state)
        if not private_supplements:
            history = hs_public_history(history)
        conn.rollback()
        return {'mode': APP_MODE, 'members': members, 'schedules': schedules, 'comments': comments,
                'noDuty': noduty, 'absences': absences, 'absenceTypes': absence_types,
                'customHolidays': custom_holidays, 'rules': rules, 'globalMemo': global_memo, 'history': history,
                'closedMonths': closed, 'dataVersion': data_version, 'holidaySupplements': supplement_data}
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'데이터 조회 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '데이터를 불러오'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


@app.post('/api/duty/request-change')
def submit_change_request(dto: ChangeRequestSubmitDTO):
    return _submit_change_request(dto)


def _submit_change_request(dto, *, prepared=None):
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=503, detail='공유 폴더에 연결할 수 없어 신청을 보내지 못했습니다. 연결되면 다시 보내 주세요. 입력한 내용은 화면에 그대로 남아 있습니다.')
    check_request_text_limits(dto)
    request_type = (dto.request_type or '').strip().upper()
    if request_type not in REQUEST_TYPE_LABELS:
        raise HTTPException(status_code=400, detail='알 수 없는 신청 종류입니다. 화면을 새로고침(F5)한 뒤 다시 신청해 주세요.')
    target_date = strict_iso_date(dto.target_date, '대상일')
    swap_date = (dto.swap_date or '').strip()
    swap_worker = (dto.swap_worker or '').strip()
    substitute_name = (dto.substitute_name or '').strip()
    absence_type = (dto.absence_type or '').strip()
    absence_end_date = (dto.absence_end_date or '').strip()
    reason = (dto.reason or '').strip()
    absence_details = normalize_education(dto.absence_details)
    if request_type == 'SWAP':
        swap_date = strict_iso_date(swap_date, '맞교환일')
    elif request_type == 'ABSENCE':
        absence_end_date = strict_iso_date(absence_end_date, '종료일')
        if target_date > absence_end_date:
            raise HTTPException(status_code=400, detail='휴가·부재 종료일은 시작일보다 빠를 수 없습니다.')
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        added_cols = ensure_change_request_schema(cur)
        replay = request_access_api.replay_submission(cur, prepared)
        if replay is not None:
            conn.commit()
            return replay
        requester_name = require_member(cur, dto.requester_name)
        if request_type == 'SWAP':
            swap_worker = require_member(cur, swap_worker, '맞교환 대상자')
            if requester_name == swap_worker:
                raise HTTPException(status_code=400, detail='같은 사람끼리는 맞교환을 신청할 수 없습니다.')
        elif request_type == 'SUBSTITUTE':
            substitute_name = require_member(cur, substitute_name, '대체 근무자')
            if requester_name == substitute_name:
                raise HTTPException(status_code=400, detail='신청자 본인을 대체 근무자로 지정할 수 없습니다.')
        if request_type in ('SWAP', 'SUBSTITUTE'):
            # v3.16.0 [F-2] 같은 날 같은 종류의 신청이 이미 대기 중이면 다시 받지 않는다.
            #   예전에는 휴가·부재에만 중복 검사가 있어 같은 신청이 여러 건 쌓였다.
            cur.execute("SELECT id FROM change_requests WHERE requester_name = ? AND request_type = ? "
                        "AND target_date = ? AND status = 'PENDING' LIMIT 1",
                        (requester_name, request_type, target_date))
            duplicate = cur.fetchone()
            if duplicate:
                raise HTTPException(status_code=409,
                                    detail=f"같은 날짜의 {REQUEST_TYPE_LABELS[request_type]} 신청이 이미 결재 대기 중입니다. [내 신청 내역]에서 확인하세요.")
        closed = read_closed_months(cur)
        span_end = absence_end_date if request_type == 'ABSENCE' else target_date
        plan = absence_date_plan(cur, absence_type, target_date, span_end, True) if request_type == 'ABSENCE' else None
        locked = plan_closed_months(plan, closed) if plan else closed_months_in_range(target_date, span_end, closed)
        if request_type == 'SWAP' and closed_month_of(swap_date, closed):
            locked = sorted(set(locked) | {swap_date[:7]})
        if locked:
            raise HTTPException(status_code=423, detail=f"마감된 달({', '.join(locked)})은 변경 신청을 받을 수 없습니다. 관리자에게 문의해 주세요.")
        if request_type == 'ABSENCE':
            absence_type = require_absence_type(cur, absence_type)
            if pending_absence_overlap(cur, requester_name, target_date, absence_end_date, plan):
                raise HTTPException(status_code=409,
                                    detail="같은 기간과 겹치는 휴가·부재 신청이 이미 결재 대기 중입니다. [내 신청 내역]에서 확인하세요.")
        blocker = hs_request_date_blocker(cur, {'request_type':request_type, 'target_date':target_date, 'swap_date':swap_date})
        if blocker:
            raise HTTPException(status_code=400, detail=blocker)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        receipt_token = prepared.receipt_token if prepared else secrets.token_urlsafe(24)
        cur.execute(
            "INSERT INTO change_requests (requester_name, target_date, request_type, swap_date, swap_worker, substitute_name, is_zero_score, reason, status, created_at, absence_type, absence_end_date, receipt_token, requester_host, requester_account, absence_details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?, ?)",
            (requester_name, target_date, request_type, swap_date, swap_worker,
             substitute_name, 1 if dto.is_zero_score else 0, reason, now_str,
             absence_type, absence_end_date, receipt_token, HOST_LABEL, ACTOR_LABEL,
             json.dumps(absence_details, ensure_ascii=False) if absence_details is not None else ''))
        request_id = cur.lastrowid
        request_access_api.register_submission(cur, prepared, request_id)
        cur.execute("SELECT COUNT(*) FROM change_requests WHERE status = 'PENDING'")
        cnt = cur.fetchone()[0]
        conn.commit()
        for col in added_cols:
            log_info(f'Viewer 신청 접수 전 change_requests.{col} 컬럼을 추가했습니다.')
        log_info(f'신청 접수: id={request_id} / {requester_name} / {target_date} / {request_type} / PC={HOST_LABEL} 계정={ACTOR_LABEL}')
    except (HTTPException, request_access_schema.AccessError):
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_error(f'변경 신청 접수 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '신청을 접수'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    broadcast_from_thread({'event': 'NEW_REQUEST_SUBMITTED', 'pendingCount': cnt,
                             'requestId': request_id, 'requester': requester_name,
                             'target_date': target_date, 'absence_end_date': absence_end_date,
                             'type': request_type, 'requestLabel': REQUEST_TYPE_LABELS[request_type]})
    return {'status': 'success', 'requestId': request_id, 'receiptToken': receipt_token,
            'requestType': request_type,
            'message': f'{REQUEST_TYPE_LABELS[request_type]} 신청이 접수되었습니다. 관리자 승인 후 반영됩니다.'}


@app.get('/api/duty/requests')
def get_change_requests(x_admin_token: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('BEGIN')
        cur.execute("SELECT * FROM change_requests WHERE status = 'PENDING' ORDER BY id DESC")
        reqs = []
        for row in cur.fetchall():
            item = dict(row)
            item['impact_digest'] = hs_approval_impact_digest(cur, item)
            # v3.17.13 접수 토큰은 신청자 PC 만 가진 수정·철회 권한이다. 관리자 목록 응답에는 싣지 않는다.
            item.pop('receipt_token', None)
            item['request_type'] = canonical_request_type(item.get('request_type'))
            item.setdefault('absence_type', '')
            item.setdefault('absence_end_date', '')
            item.setdefault('reject_reason', '')
            item.setdefault('requester_host', '')
            item.setdefault('requester_account', '')
            item['revision'] = item.get('revision') or 0
            item['absence_details'] = optional_json(item.get('absence_details'))
            item['amendment_original'] = optional_json(item.get('amendment_original'))
            item['amends_request_id'] = item.get('amends_request_id') or None
            add_request_date_plan(cur, item)
            item['blocker'] = approval_blocker(cur, item)
            if (item.get('request_type') or '').strip().upper() == 'ABSENCE':
                item['affectedDates'] = count_schedules_in_plan(
                    cur, item.get('requester_name') or '', item.get('absence_date_plan') or {'ranges': []})
            reqs.append(item)
        pending = len(reqs)
        return {'requests': reqs, 'pendingCount': pending}
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'신청 목록 조회 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '신청 목록을 불러오'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


# Keep the shipped calendar's 2023–2029 dates in sync; a regression test checks parity.
# Custom holiday additions/removals are read from the shared DB on every plan.
BASE_HOLIDAY_DATES = frozenset("""
    2023-01-01 2023-01-21 2023-01-22 2023-01-23 2023-01-24 2023-03-01
    2023-05-05 2023-05-27 2023-05-29 2023-06-06 2023-08-15 2023-09-28
    2023-09-29 2023-09-30 2023-10-02 2023-10-03 2023-10-09 2023-12-25
    2024-01-01 2024-02-09 2024-02-10 2024-02-11 2024-02-12 2024-03-01
    2024-04-10 2024-05-05 2024-05-06 2024-05-15 2024-06-06 2024-08-15
    2024-09-16 2024-09-17 2024-09-18 2024-10-01 2024-10-03 2024-10-09
    2024-12-25
    2025-01-01 2025-01-27 2025-01-28 2025-01-29 2025-01-30 2025-03-01
    2025-03-03 2025-05-05 2025-05-06 2025-06-03 2025-06-06 2025-08-15
    2025-10-03 2025-10-05 2025-10-06 2025-10-07 2025-10-08 2025-10-09
    2025-12-25
    2026-01-01 2026-02-16 2026-02-17 2026-02-18 2026-03-01 2026-03-02
    2026-05-01 2026-05-05 2026-05-24 2026-05-25 2026-06-03 2026-06-06
    2026-07-17 2026-08-15 2026-08-17 2026-09-24 2026-09-25 2026-09-26
    2026-10-03 2026-10-05 2026-10-09 2026-12-25
    2027-01-01 2027-02-06 2027-02-07 2027-02-08 2027-02-09 2027-03-01
    2027-05-01 2027-05-03 2027-05-05 2027-05-13 2027-06-06 2027-07-17
    2027-07-19 2027-08-15 2027-08-16 2027-09-14 2027-09-15 2027-09-16
    2027-10-03 2027-10-04 2027-10-09 2027-10-11 2027-12-25 2027-12-27
    2028-01-01 2028-01-26 2028-01-27 2028-01-28 2028-03-01 2028-04-12
    2028-05-01 2028-05-02 2028-05-05 2028-06-06 2028-07-17 2028-08-15
    2028-10-02 2028-10-03 2028-10-04 2028-10-05 2028-10-09 2028-12-25
    2029-01-01 2029-02-12 2029-02-13 2029-02-14 2029-03-01 2029-05-01
    2029-05-05 2029-05-07 2029-05-20 2029-05-21 2029-06-06 2029-07-17
    2029-08-15 2029-09-21 2029-09-22 2029-09-23 2029-09-24 2029-10-03
    2029-10-09 2029-12-25
""".split())


def absence_ranges(record):
    """The real persisted rows of an approval; legacy approvals are a single row."""
    return record.get('segments') or [record]


def date_plan_from_ranges(start, end, ranges):
    first = date.fromisoformat(strict_iso_date(start, '시작일')).toordinal()
    last = date.fromisoformat(strict_iso_date(end, '종료일')).toordinal()
    if first > last:
        raise HTTPException(status_code=400, detail='휴가·부재 종료일은 시작일보다 빠를 수 없습니다.')
    spans = [{'start': r['start'], 'end': r['end']} for r in ranges]
    excluded = []
    cursor = first
    for span in spans:
        excluded.extend(date.fromordinal(day).isoformat()
                        for day in range(cursor, date.fromisoformat(span['start']).toordinal()))
        cursor = date.fromisoformat(span['end']).toordinal() + 1
    excluded.extend(date.fromordinal(day).isoformat() for day in range(cursor, last + 1))
    return {'ranges': spans, 'excluded': excluded, 'days': last - first + 1 - len(excluded)}


def absence_date_plan(cur, absence_type, start, end, require_days=False):
    first = date.fromisoformat(strict_iso_date(start, '시작일')).toordinal()
    last = date.fromisoformat(strict_iso_date(end, '종료일')).toordinal()
    if first > last:
        raise HTTPException(status_code=400, detail='휴가·부재 종료일은 시작일보다 빠를 수 없습니다.')
    leave = '연차' in (absence_type or '') or '휴가' in (absence_type or '')
    if not leave:
        return {'ranges': [{'start': start, 'end': end}], 'excluded': [], 'days': last - first + 1}
    holidays = set(BASE_HOLIDAY_DATES)
    cols = {r[1] for r in cur.execute('PRAGMA table_info(custom_holidays)').fetchall()}
    removed = 'is_removed' if 'is_removed' in cols else '0 AS is_removed'
    for row in cur.execute(f'SELECT date_key, {removed} FROM custom_holidays').fetchall():
        if row['is_removed']:
            holidays.discard(row['date_key'])
        else:
            holidays.add(row['date_key'])
    ranges, excluded = [], []
    previous = None
    for ordinal in range(first, last + 1):
        day = date.fromordinal(ordinal)
        key = day.isoformat()
        if day.weekday() >= 5 or key in holidays:
            excluded.append(key)
            previous = None
        else:
            if previous is None:
                ranges.append({'start': key, 'end': key})
            else:
                ranges[-1]['end'] = key
            previous = key
    plan = {'ranges': ranges, 'excluded': excluded, 'days': last - first + 1 - len(excluded)}
    if require_days and not plan['days']:
        raise HTTPException(status_code=400, detail='주말·공휴일을 제외하면 신청할 연차·휴가 날짜가 없습니다. 평일이 포함된 기간을 선택해 주세요.')
    return plan


def request_absence_plan(cur, item, require_days=False):
    if canonical_request_type(item.get('request_type')) == 'ABSENCE_CANCEL':
        original = item.get('amendment_original')
        original = optional_json(original) if isinstance(original, str) else original
        if not isinstance(original, dict):
            raise HTTPException(status_code=409, detail='취소할 승인 원본의 구간을 확인할 수 없습니다.')
        original = canonical_absence(original)
        return date_plan_from_ranges(original['start'], original['end'], absence_ranges(original))
    return absence_date_plan(cur, item.get('absence_type') or item.get('substitute_name') or '',
                             item.get('target_date') or '',
                             item.get('absence_end_date') or item.get('swap_date') or item.get('target_date') or '',
                             require_days)


def add_request_date_plan(cur, item):
    kind = canonical_request_type(item.get('request_type'))
    if kind not in ('ABSENCE', 'ABSENCE_CANCEL'):
        return
    try:
        if item.get('status') == 'APPROVED' and kind == 'ABSENCE':
            matched = approved_absence_history(cur, item)
            if matched is None:
                return  # Never present today's calendar as an unverified historical approval.
            record = matched[2]
            plan = date_plan_from_ranges(record['start'], record['end'], absence_ranges(record))
        else:
            plan = request_absence_plan(cur, item)
        item['absence_date_plan'] = plan
    except (HTTPException, ValueError):
        pass  # Older malformed requests retain their existing blocker instead of breaking the list.


def ranges_overlap(first, second):
    return any(a['start'] <= b['end'] and b['start'] <= a['end'] for a in first for b in second)


def pending_absence_overlap(cur, name, start, end, plan, exclude_id=None):
    rows = cur.execute("SELECT * FROM change_requests WHERE requester_name = ? AND id IS NOT ? "
                       "AND request_type IN ('ABSENCE', 'ABSENCE_AMEND') AND status = 'PENDING' "
                       "AND target_date <= ? AND COALESCE(NULLIF(absence_end_date, ''), NULLIF(swap_date, ''), target_date) >= ?",
                       (name, exclude_id, end, start)).fetchall()
    return any(ranges_overlap(plan['ranges'], request_absence_plan(cur, dict(row))['ranges']) for row in rows)


def existing_absence_overlap(cur, name, plan, original=None):
    exclude_ids = {r['id'] for r in absence_ranges(original)} if original else set()
    rows = cur.execute('SELECT id, start_date, end_date FROM absences WHERE name = ?', (name,)).fetchall()
    return any(row['id'] not in exclude_ids and ranges_overlap(plan['ranges'], [{'start': row['start_date'], 'end': row['end_date']}]) for row in rows)


def plan_closed_months(plan, closed):
    return sorted({month for r in plan['ranges'] for month in closed_months_in_range(r['start'], r['end'], closed)})


def count_schedules_in_plan(cur, name, plan):
    return sorted({day for r in plan['ranges'] for day in count_schedules_in_range(cur, name, r['start'], r['end'])})


def canonical_request_type(value):
    kind = (value or '').strip().upper()
    return 'ABSENCE' if kind == 'ABSENCE_AMEND' else kind


def request_type_label(item):
    kind = canonical_request_type(item.get('request_type'))
    if kind == 'ABSENCE_CANCEL':
        return '휴가·부재 취소'
    return '휴가·부재 변경' if item.get('amends_request_id') else REQUEST_TYPE_LABELS.get(kind, kind)


def canonical_absence(record):
    """Compare all persisted fields and every constituent row of a grouped approval."""
    result = {'id': normalize_absence_id(record.get('id')), 'name': record.get('name'),
              'type': record.get('type'), 'start': record.get('start'), 'end': record.get('end'),
              'education': normalize_education(record.get('education'))}
    if 'segments' in record:
        if not isinstance(record['segments'], list) or not record['segments'] or any(
                not isinstance(row, dict) or 'segments' in row for row in record['segments']):
            raise HTTPException(status_code=409, detail='승인 원본의 부재 구간을 확인할 수 없습니다.')
        result['segments'] = sorted((canonical_absence(row) for row in record['segments']),
                                    key=lambda row: (row['start'], row['end'], row['id']))
    return result


def approved_absence_history(cur, item):
    """Prove one approval's entire row group from its immutable before/after history."""
    if canonical_request_type(item.get('request_type')) != 'ABSENCE' or item.get('status') != 'APPROVED' or not item.get('processed_at'):
        return None
    try:
        cols = {r[1] for r in cur.execute('PRAGMA table_info(change_history)').fetchall()}
        if not {'before_json', 'after_json'} <= cols:
            return None
        request_cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
        saved = cur.execute('SELECT * FROM change_requests WHERE id = ?', (item.get('id'),)).fetchone()
        saved = dict(saved) if saved else item
        history_id = saved.get('approved_history_id') if 'approved_history_id' in request_cols else None
        amendment = optional_json(saved.get('amendment_original')) if saved.get('amends_request_id') else None
        if saved.get('amends_request_id') and not isinstance(amendment, dict):
            return None
        history_type = '휴가·부재 변경(승인)' if amendment else '휴가·부재(승인)'
        query = "SELECT id, before_json, after_json FROM change_history WHERE type = ? AND timestamp = ? AND date_key = ?"
        params = [history_type, item['processed_at'], item.get('target_date')]
        if history_id is not None:
            query += ' AND id = ?'
            params.append(history_id)
        rows = cur.execute(query, params).fetchall()
        start = item.get('target_date')
        end = item.get('absence_end_date') or item.get('swap_date') or start
        identity = (item.get('requester_name'), item.get('absence_type') or item.get('substitute_name'))
        matches = []
        for row in rows:
            before, after = optional_json(row['before_json']), optional_json(row['after_json'])
            if not isinstance(before, dict) or not isinstance(after, dict):
                continue
            old_absences, new_absences = before.get('absences'), after.get('absences')
            if not isinstance(old_absences, list) or not isinstance(new_absences, list) or not new_absences:
                continue
            if any(not isinstance(a, dict) for a in old_absences + new_absences):
                continue
            old_rows = sorted((canonical_absence(a) for a in old_absences), key=lambda a: (a['start'], a['end'], a['id']))
            segments = sorted((canonical_absence(a) for a in new_absences), key=lambda a: (a['start'], a['end'], a['id']))
            original_rows = absence_ranges(canonical_absence(amendment)) if amendment else []
            if old_rows != original_rows:
                continue
            ids = {a['id'] for a in segments}
            if None in ids or len(ids) != len(segments):
                continue
            if any((a['name'], a['type']) != identity or not (start <= a['start'] <= a['end'] <= end)
                   or a['education'] != segments[0]['education'] for a in segments):
                continue
            if any(left['end'] >= right['start'] for left, right in zip(segments, segments[1:])):
                continue
            if amendment and segments[0]['id'] != amendment['id']:
                continue  # The anchor identity survives every approved amendment.
            if not isinstance(before.get('schedules', {}), dict) or not isinstance(after.get('schedules', {}), dict):
                continue
            envelope = dict(segments[0], start=start, end=end)
            if len(segments) != 1 or segments[0]['start'] != start or segments[0]['end'] != end:
                envelope['segments'] = segments
            matches.append((before, after, envelope, row['id']))
        # Legacy approvals without an explicit history ID must be unambiguous.
        return matches[0] if len(matches) == 1 else None
    except Exception:
        return None


def approved_absence_released_dates(cur, item):
    matched = approved_absence_history(cur, item)
    if matched is None:
        return None
    before, after, absence = matched[:3]
    old, new = before.get('schedules', {}), after.get('schedules', {})
    names = lambda workers: {w.get('name') for w in workers or [] if isinstance(w, dict)}
    return sorted(k for k, workers in old.items() if any(r['start'] <= k <= r['end'] for r in absence_ranges(absence))
                  and absence['name'] in names(workers) and absence['name'] not in names(new.get(k)))


def current_approved_absence(cur, item):
    matched = approved_absence_history(cur, item)
    if matched is None:
        raise HTTPException(status_code=409, detail='승인 원본을 정확히 확인할 수 없는 이전 신청입니다. 관리자에게 일정 변경을 요청해 주세요.')
    expected = matched[2]
    current = {a['id']: canonical_absence(a) for a in read_absences(cur)}
    if any(current.get(row['id']) != row for row in absence_ranges(expected)):
        raise HTTPException(status_code=409, detail='승인된 일정이 관리자에 의해 변경되거나 삭제되었습니다. 최신 일정을 확인하고 관리자에게 문의해 주세요.')
    expected_ids = {row['id'] for row in absence_ranges(expected)}
    # save-all replaces physical rows; only a committed logical deletion invalidates identity.
    for change in cur.execute('SELECT before_json, after_json FROM change_history WHERE id > ? ORDER BY id', (matched[3],)).fetchall():
        before, after = optional_json(change['before_json']), optional_json(change['after_json'])
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        old, new = before.get('absences'), after.get('absences')
        if not isinstance(old, list) or not isinstance(new, list):
            continue
        old_ids = {a.get('id') for a in old if isinstance(a, dict)}
        new_ids = {a.get('id') for a in new if isinstance(a, dict)}
        if expected_ids & (old_ids - new_ids):
            raise HTTPException(status_code=409, detail='승인 원본이 삭제된 이력이 있어 같은 번호의 새 일정을 변경할 수 없습니다. 관리자에게 문의해 주세요.')
    return expected


def amendment_children(cur, request_id):
    cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
    if 'amends_request_id' not in cols:
        return []
    return [dict(r) for r in cur.execute("SELECT id, status, receipt_token, request_type, processed_at FROM change_requests "
            "WHERE amends_request_id = ? ORDER BY id DESC", (request_id,)).fetchall()]


def check_amendment_months(cur, absence):
    locked = plan_closed_months({'ranges': absence_ranges(absence)}, read_closed_months(cur))
    if locked:
        raise HTTPException(status_code=423, detail=f"기존 일정의 마감된 달({', '.join(locked)})은 변경할 수 없습니다. 관리자에게 문의해 주세요.")


def check_cancellation_dates(cur, absence):
    check_amendment_months(cur, absence)
    today = date.today().isoformat()
    if any(row['start'] < today for row in absence_ranges(absence)):
        raise HTTPException(status_code=409, detail='이미 지난 날짜가 포함된 승인 일정은 취소 요청하거나 승인할 수 없습니다. 관리자에게 문의해 주세요.')


def public_cancellation_state(cur, item):
    state = {'cancelled_by_id': None, 'pending_cancellation_id': None, 'cancellation_processed_at': None}
    if item.get('status') == 'APPROVED' and canonical_request_type(item.get('request_type')) == 'ABSENCE':
        children = amendment_children(cur, item['id'])
        for row in children:
            if row['request_type'] == 'ABSENCE_CANCEL':
                if row['status'] == 'APPROVED':
                    state['cancelled_by_id'] = row['id']
                    state['cancellation_processed_at'] = row['processed_at']
                elif row['status'] == 'PENDING':
                    state['pending_cancellation_id'] = row['id']
    return state


def owned_amendment_state(cur, item):
    state = {'can_amend': False, 'amendment_blocker': '', 'pending_amendment_id': None,
             'superseded_by_id': None, 'can_cancel': False, 'cancellation_blocker': '',
             **public_cancellation_state(cur, item)}
    if item.get('status') != 'APPROVED' or canonical_request_type(item.get('request_type')) != 'ABSENCE':
        return state
    children = amendment_children(cur, item['id'])
    approved = next((r for r in children if r['status'] == 'APPROVED'), None)
    pending = next((r for r in children if r['status'] == 'PENDING'), None)
    terminal = next((r for r in children if r['status'] in ('REJECTED', 'CANCELLED')), None)
    linked = approved or pending or terminal
    if linked:
        state['linked_amendment'] = {'id': linked['id'], 'status': linked['status'], 'receiptToken': linked['receipt_token'],
                                     'request_type': canonical_request_type(linked['request_type'])}
    if approved:
        if approved['request_type'] == 'ABSENCE_CANCEL':
            state['amendment_blocker'] = '이미 취소 승인된 일정입니다.'
        else:
            state['superseded_by_id'] = approved['id']
            state['amendment_blocker'] = '이미 변경 승인된 신청입니다. 최신 승인 건에서 변경·취소를 요청해 주세요.'
        state['cancellation_blocker'] = state['amendment_blocker']
        return state
    if pending:
        if pending['request_type'] != 'ABSENCE_CANCEL':
            state['pending_amendment_id'] = pending['id']
        state['amendment_blocker'] = '이 일정의 변경·취소 요청이 이미 결재 대기 중입니다. 해당 신청을 확인하거나 철회해 주세요.'
        state['cancellation_blocker'] = state['amendment_blocker']
        return state
    try:
        current = current_approved_absence(cur, item)
        state['amendable_absence'] = current
        state['cancellable_absence'] = current
    except HTTPException as error:
        state['amendment_blocker'] = state['cancellation_blocker'] = str(error.detail)
        return state
    try:
        check_amendment_months(cur, current)
        require_member(cur, item['requester_name'])
        state['can_amend'] = True
    except HTTPException as error:
        state['amendment_blocker'] = str(error.detail)
    try:
        check_cancellation_dates(cur, current)
        state['can_cancel'] = True
    except HTTPException as error:
        state['cancellation_blocker'] = str(error.detail)
    return state


def amendment_current_absence(cur, item):
    """Validate a pending amendment's server-recorded source before any domain mutation."""
    parent = cur.execute('SELECT * FROM change_requests WHERE id = ?', (item.get('amends_request_id'),)).fetchone()
    if not parent or parent['status'] != 'APPROVED' or canonical_request_type(parent['request_type']) != 'ABSENCE' or parent['requester_name'] != item.get('requester_name'):
        raise HTTPException(status_code=409, detail='변경할 승인 원본을 확인할 수 없습니다. 최신 신청 내역을 확인해 주세요.')
    if any(r['status'] == 'APPROVED' for r in amendment_children(cur, parent['id'])):
        raise HTTPException(status_code=409, detail='다른 변경 요청이 이미 승인되었습니다. 최신 승인 건에서 다시 요청해 주세요.')
    snapshot = item.get('amendment_original')
    snapshot = optional_json(snapshot) if isinstance(snapshot, str) else snapshot
    current = current_approved_absence(cur, dict(parent))
    if not isinstance(snapshot, dict) or canonical_absence(snapshot) != current:
        raise HTTPException(status_code=409, detail='변경 요청 이후 원본 일정이 달라졌습니다. 최신 일정을 확인해 주세요.')
    check_amendment_months(cur, current)
    return current


def cancellation_current_absence(cur, item):
    current = amendment_current_absence(cur, item)
    check_cancellation_dates(cur, current)
    return current


@app.get('/api/duty/request-status/{receipt_token}')
def get_change_request_status(receipt_token: str):
    """Viewer가 자신이 받은 불투명 접수 토큰으로 결과 한 건만 확인한다."""
    token = (receipt_token or '').strip()
    if len(token) < 20 or len(token) > 200:
        raise HTTPException(status_code=404, detail='신청 결과를 찾을 수 없습니다.')
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        req_cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
        fields = ['id', 'requester_name', 'target_date', 'request_type', 'status', 'created_at', 'processed_at',
                  'absence_type', 'absence_end_date', 'reject_reason', 'swap_date', 'swap_worker',
                  'substitute_name', 'is_zero_score', 'reason', 'absence_details', 'amends_request_id', 'amendment_original']
        pick = ', '.join(key if key in req_cols else f"'' AS {key}" for key in fields)
        pick += ', revision' if 'revision' in req_cols else ', 0 AS revision'
        cur.execute(f'SELECT {pick} FROM change_requests WHERE receipt_token = ? LIMIT 1', (token,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='신청 결과를 찾을 수 없습니다.')
        item = dict(row)
        item['reject_reason'] = item.get('reject_reason') or ''
        item['request_type'] = canonical_request_type(item.get('request_type'))
        item['absence_details'] = optional_json(item.get('absence_details'))
        item['amendment_original'] = optional_json(item.get('amendment_original'))
        item['amends_request_id'] = item.get('amends_request_id') or None
        item['revision'] = item.get('revision') or 0
        add_request_date_plan(cur, item)
        released = approved_absence_released_dates(cur, item)
        if released is not None:
            item['released_dates'] = released
        item.update(owned_amendment_state(cur, item))
        return {'request': item}
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'개별 신청 결과 조회 실패: {e}', e)
        raise HTTPException(status_code=500, detail='신청 결과를 확인하지 못했습니다.')
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def validate_owned_edit(cur, dto, original):
    check_request_text_limits(dto)
    if (dto.requester_name or '').strip() != original['requester_name']:
        raise HTTPException(status_code=400, detail='신청자는 변경할 수 없습니다.')
    values = {
        'requester_name': require_member(cur, original['requester_name']),
        'request_type': (dto.request_type or '').strip().upper(),
        'target_date': strict_iso_date(dto.target_date, '대상일'),
        'swap_date': (dto.swap_date or '').strip(),
        'swap_worker': (dto.swap_worker or '').strip(),
        'substitute_name': (dto.substitute_name or '').strip(),
        'is_zero_score': 1 if dto.is_zero_score else 0,
        'reason': (dto.reason or '').strip(),
        'absence_type': (dto.absence_type or '').strip(),
        'absence_end_date': (dto.absence_end_date or '').strip(),
    }
    kind = values['request_type']
    if original.get('amends_request_id'):
        if kind != 'ABSENCE':
            raise HTTPException(status_code=400, detail='승인된 휴가·교육·부재의 변경 요청은 다른 신청 종류로 바꿀 수 없습니다.')
        values['amends_request_id'] = original['amends_request_id']
        values['amendment_original'] = original['amendment_original']
        amendment_current_absence(cur, values)
    if kind not in REQUEST_TYPE_LABELS:
        raise HTTPException(status_code=400, detail='알 수 없는 신청 종류입니다. 화면을 새로고침(F5)한 뒤 다시 신청해 주세요.')
    if kind == 'SWAP':
        values['swap_date'] = strict_iso_date(values['swap_date'], '맞교환일')
        values['swap_worker'] = require_member(cur, values['swap_worker'], '맞교환 대상자')
        if values['swap_worker'] == values['requester_name']:
            raise HTTPException(status_code=400, detail='같은 사람끼리는 맞교환을 신청할 수 없습니다.')
    elif kind == 'SUBSTITUTE':
        values['substitute_name'] = require_member(cur, values['substitute_name'], '대체 근무자')
        if values['substitute_name'] == values['requester_name']:
            raise HTTPException(status_code=400, detail='신청자 본인을 대체 근무자로 지정할 수 없습니다.')
    elif kind == 'ABSENCE':
        values['absence_end_date'] = strict_iso_date(values['absence_end_date'], '종료일')
        if values['target_date'] > values['absence_end_date']:
            raise HTTPException(status_code=400, detail='휴가·부재 종료일은 시작일보다 빠를 수 없습니다.')
        values['absence_type'] = require_absence_type(cur, values['absence_type'])
    details = normalize_education(dto.absence_details)
    values['absence_details'] = json.dumps(details, ensure_ascii=False) if details is not None else ''
    closed = read_closed_months(cur)
    span_end = values['absence_end_date'] if kind == 'ABSENCE' else values['target_date']
    plan = request_absence_plan(cur, values, True) if kind == 'ABSENCE' else None
    locked = plan_closed_months(plan, closed) if plan else closed_months_in_range(values['target_date'], span_end, closed)
    if kind == 'SWAP' and closed_month_of(values['swap_date'], closed):
        locked = sorted(set(locked) | {values['swap_date'][:7]})
    if locked:
        raise HTTPException(status_code=423, detail=f"마감된 달({', '.join(locked)})은 변경 신청을 받을 수 없습니다. 관리자에게 문의해 주세요.")
    params = [values['requester_name'], original['id']]
    condition = "requester_name = ? AND id <> ? AND status = 'PENDING'"
    if kind == 'ABSENCE':
        duplicate = pending_absence_overlap(cur, values['requester_name'], values['target_date'], span_end, plan, original['id'])
    else:
        condition += ' AND request_type = ? AND target_date = ?'
        params += [kind, values['target_date']]
        duplicate = cur.execute(f'SELECT 1 FROM change_requests WHERE {condition} LIMIT 1', params).fetchone()
    if duplicate:
        raise HTTPException(status_code=409, detail='같은 날짜 또는 기간의 신청이 이미 결재 대기 중입니다.')
    blocker = approval_blocker(cur, values)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    return values


@app.post('/api/duty/request/manage')
def manage_change_request(dto: RequestManageDTO):
    return _manage_change_request(dto)


def _manage_change_request(dto, *, prepared=None):
    """Receipt ownership permits pending edits and a separate amendment of an approved absence."""
    token = (dto.receipt_token or '').strip()
    if len(token) < 20 or len(token) > 200:
        raise HTTPException(status_code=404, detail='신청 결과를 찾을 수 없습니다.')
    action = (dto.action or '').strip().upper()
    if action not in ('WITHDRAW', 'EDIT', 'AMEND', 'CANCEL_ABSENCE'):
        raise HTTPException(status_code=400, detail='처리할 수 없는 요청입니다. 화면을 새로고침(F5)한 뒤 다시 시도해 주세요.')
    if action == 'CANCEL_ABSENCE':
        check_request_text_limits(dto)
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=503, detail='공유 폴더에 연결할 수 없습니다. 연결되면 다시 시도해 주세요.')
    conn = None
    new_token = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        ensure_change_request_schema(cur)
        ensure_absence_schema(cur)
        ensure_history_schema(cur)
        row = cur.execute('SELECT * FROM change_requests WHERE receipt_token = ? LIMIT 1', (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail='신청 결과를 찾을 수 없습니다.')
        original = dict(row)
        replay = request_access_api.replay_submission(cur, prepared)
        if replay is not None:
            conn.commit()
            return replay
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        request_revision = original.get('revision') or 0
        request_id = original['id']
        if action in ('AMEND', 'CANCEL_ABSENCE'):
            if original['status'] != 'APPROVED' or canonical_request_type(original['request_type']) != 'ABSENCE':
                raise HTTPException(status_code=409, detail='승인된 휴가·교육·부재 신청만 변경·취소 요청할 수 있습니다.')
            state = owned_amendment_state(cur, original)
            cancelling = action == 'CANCEL_ABSENCE'
            if not state['can_cancel' if cancelling else 'can_amend']:
                # Preserve the closed-month HTTP status as well as its user-facing explanation.
                if state.get('amendable_absence'):
                    if cancelling:
                        check_cancellation_dates(cur, state['amendable_absence'])
                    else:
                        check_amendment_months(cur, state['amendable_absence'])
                raise HTTPException(status_code=409, detail=state['cancellation_blocker' if cancelling else 'amendment_blocker'])
            linked = dict(original, amends_request_id=original['id'],
                          amendment_original=json.dumps(state['amendable_absence'], ensure_ascii=False, sort_keys=True))
            if cancelling:
                source = state['cancellable_absence']
                values = dict(requester_name=original['requester_name'], request_type='ABSENCE_CANCEL',
                              target_date=source['start'], absence_end_date=source['end'], absence_type=source['type'],
                              absence_details=json.dumps(source['education'], ensure_ascii=False) if source['education'] is not None else '',
                              reason=(dto.reason or '').strip(), amends_request_id=original['id'],
                              amendment_original=linked['amendment_original'])
            else:
                if dto.request is None:
                    raise HTTPException(status_code=400, detail='변경할 신청 내용을 입력해 주세요.')
                values = validate_owned_edit(cur, dto.request, linked)
                values['request_type'] = 'ABSENCE_AMEND'
            new_token = prepared.receipt_token if prepared else secrets.token_urlsafe(24)
            values.update(status='PENDING', created_at=now_str, receipt_token=new_token,
                          requester_host=HOST_LABEL, requester_account=ACTOR_LABEL)
            fields = list(values)
            cur.execute(f"INSERT INTO change_requests ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})",
                        [values[key] for key in fields])
            request_id = cur.lastrowid
            request_revision = 0
            request_status = 'PENDING'
            message = '취소 요청을 접수했습니다. 관리자 승인 전까지 기존 일정은 유지되며, 취소 승인 후에도 당직은 자동 복원되지 않습니다.' if cancelling else '변경 요청을 접수했습니다. 관리자가 다시 승인하기 전까지 기존 일정은 유지됩니다.'
        else:
            if original['status'] != 'PENDING':
                done = {'APPROVED': '이미 승인된 신청입니다.', 'REJECTED': '이미 반려된 신청입니다.',
                        'CANCELLED': '이미 철회한 신청입니다.'}.get(original['status'], '대기 중인 신청이 아닙니다.')
                raise HTTPException(status_code=409, detail=f'{done} 대기 중인 신청만 수정하거나 철회할 수 있습니다.')
            if action == 'WITHDRAW':
                cur.execute("UPDATE change_requests SET status = 'CANCELLED', processed_at = ? WHERE id = ? AND receipt_token = ? AND status = 'PENDING'",
                            (now_str, original['id'], token))
                request_status = 'CANCELLED'
                message = '취소 요청을 철회했습니다. 기존 승인 일정은 유지됩니다.' if original['request_type'] == 'ABSENCE_CANCEL' else '변경 요청을 철회했습니다. 기존 승인 일정은 유지됩니다.' if original.get('amends_request_id') else '신청을 철회했습니다.'
            else:
                if original['request_type'] == 'ABSENCE_CANCEL':
                    raise HTTPException(status_code=400, detail='취소 요청은 수정할 수 없습니다. 철회한 뒤 다시 요청해 주세요.')
                if dto.request is None:
                    raise HTTPException(status_code=400, detail='수정할 신청 내용을 입력해 주세요.')
                values = validate_owned_edit(cur, dto.request, original)
                if original.get('amends_request_id'):
                    values['request_type'] = 'ABSENCE_AMEND'
                fields = [key for key in values if key != 'requester_name']
                cur.execute(f"UPDATE change_requests SET {', '.join(key + ' = ?' for key in fields)}, revision = revision + 1 WHERE id = ? AND receipt_token = ? AND status = 'PENDING'",
                            [values[key] for key in fields] + [original['id'], token])
                request_revision += 1
                request_status = 'PENDING'
                message = '대기 중인 신청을 수정했습니다.'
        if cur.rowcount != 1:
            raise HTTPException(status_code=409, detail='신청 상태가 변경되었습니다. 다시 확인해 주세요.')
        if new_token:
            request_access_api.register_submission(cur, prepared, request_id)
        count = cur.execute("SELECT COUNT(*) FROM change_requests WHERE status = 'PENDING'").fetchone()[0]
        conn.commit()
    except (HTTPException, request_access_schema.AccessError):
        if conn:
            conn.rollback()
        raise
    except Exception as error:
        if conn:
            conn.rollback()
        log_error(f'신청 관리 실패: {error}', error)
        raise HTTPException(status_code=500, detail=friendly_db_error(error, '신청을 변경'))
    finally:
        if conn:
            conn.close()
    event = {'event': 'NEW_REQUEST_SUBMITTED' if new_token else 'REQUEST_UPDATED', 'action': action,
             'requestId': request_id, 'status': request_status, 'pendingCount': count, 'revision': request_revision}
    result = {'status': 'success', 'action': action, 'requestId': request_id,
              'requestStatus': request_status, 'requestRevision': request_revision, 'pendingCount': count, 'message': message}
    if new_token:
        kind = canonical_request_type(values['request_type'])
        event.update(requester=original['requester_name'], target_date=values['target_date'],
                     absence_end_date=values['absence_end_date'], type=kind, requestLabel=request_type_label(values),
                     amends_request_id=original['id'])
        result.update(receiptToken=new_token, requestType=kind, amends_request_id=original['id'])
    broadcast_from_thread(event)
    return result


def is_absent_on(cur, name, date_key):
    """해당 날짜가 그 사원의 부재 기간에 포함되는지 확인한다."""
    cur.execute('SELECT 1 FROM absences WHERE name = ? AND start_date <= ? AND ? <= end_date LIMIT 1',
                (name, date_key, date_key))
    return cur.fetchone() is not None


def approval_blocker(cur, r):
    """
v3.9.0 [11] '지금 승인하면 실패'를 미리 알려 주기 위한 사전 판정.
실제 승인 경로(process_request_action)와 같은 기준을 쓴다 — 여기만 고치면 어긋나므로
조건을 바꿀 때는 반드시 양쪽을 함께 고칠 것. 읽기만 하고 아무것도 바꾸지 않는다.
"""
    try:
        rtype = canonical_request_type(r.get('request_type'))
        target = r.get('target_date') or ''
        requester = r.get('requester_name') or ''
        date_blocker = hs_request_date_blocker(cur, r)
        if date_blocker:
            return date_blocker
        if rtype in ('SWAP', 'SUBSTITUTE'):
            assignees = (requester, r.get('swap_worker')) if rtype == 'SWAP' else (r.get('substitute_name'),)
            for name in assignees:
                if name and not cur.execute('SELECT 1 FROM members WHERE name = ? LIMIT 1', (name,)).fetchone():
                    return f"'{name}' 사원을 명단에서 찾을 수 없습니다"
        if rtype == 'SWAP':
            swap_date = r.get('swap_date') or ''
            swap_worker = r.get('swap_worker') or ''
            if requester == swap_worker:
                return '같은 사람끼리는 교환할 수 없습니다'
            cur.execute('SELECT slot_index FROM schedules WHERE date_key = ? AND worker_name = ?',
                        (target, requester))
            src = cur.fetchone()
            cur.execute('SELECT slot_index FROM schedules WHERE date_key = ? AND worker_name = ?',
                        (swap_date, swap_worker))
            tgt = cur.fetchone()
            if not src:
                return f"[{target}]에 '{requester}' 배정이 없습니다"
            if not tgt:
                return f"[{swap_date}]에 '{swap_worker}' 배정이 없습니다"
            cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                        (target, swap_worker, src['slot_index']))
            if cur.fetchone():
                return f"'{swap_worker}' 사원이 이미 [{target}]에 배정되어 있습니다"
            cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                        (swap_date, requester, tgt['slot_index']))
            if cur.fetchone():
                return f"'{requester}' 사원이 이미 [{swap_date}]에 배정되어 있습니다"
            if is_absent_on(cur, swap_worker, target):
                return f"'{swap_worker}' 사원은 {target}에 부재 기간입니다"
            if is_absent_on(cur, requester, swap_date):
                return f"'{requester}' 사원은 {swap_date}에 부재 기간입니다"
        elif rtype == 'SUBSTITUTE':
            sub_name = r.get('substitute_name') or ''
            if not sub_name:
                return '대체 근무할 사원이 지정되지 않았습니다'
            cur.execute('SELECT slot_index FROM schedules WHERE date_key = ? AND worker_name = ?',
                        (target, requester))
            src = cur.fetchone()
            if not src:
                return f"[{target}]에 '{requester}' 배정이 없습니다"
            cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                        (target, sub_name, src['slot_index']))
            if cur.fetchone():
                return f"'{sub_name}' 사원은 이미 [{target}]에 배정되어 있습니다"
            if is_absent_on(cur, sub_name, target):
                return f"'{sub_name}' 사원은 {target}에 부재 기간입니다"
        elif rtype == 'ABSENCE_CANCEL':
            cancellation_current_absence(cur, r)
        elif rtype == 'ABSENCE':
            original_absence = amendment_current_absence(cur, r) if r.get('amends_request_id') else None
            end_date = r.get('absence_end_date') or r.get('swap_date') or target
            if target and end_date and target > end_date:
                return '종료일이 시작일보다 빠릅니다'
            cur.execute('SELECT 1 FROM members WHERE name = ? LIMIT 1', (requester,))
            if not cur.fetchone():
                return f"'{requester}' 사원을 명단에서 찾을 수 없습니다"
            atype = (r.get('absence_type') or r.get('substitute_name') or '').strip()
            if not atype:
                return '휴가·부재 구분이 지정되지 않았습니다'
            cur.execute('SELECT 1 FROM absence_types WHERE type_name = ? LIMIT 1', (atype,))
            if not cur.fetchone():
                return f'등록되지 않은 휴가·부재 구분입니다: {atype}'
            plan = request_absence_plan(cur, r, True)
            locked = plan_closed_months(plan, read_closed_months(cur))
            if locked:
                return f"마감된 달({', '.join(locked)})에 걸린 신청이라 승인할 수 없습니다"
            if existing_absence_overlap(cur, requester, plan, original_absence):
                return '이미 반영된 휴가·부재 기간과 겹칩니다'
    except HTTPException as e:
        return str(e.detail)
    except Exception as e:
        # v3.16.0 [위험] 예전에는 예외를 삼키고 '' (= 막는 사유 없음) 을 돌려줘, 판정이 실패해도
        #   화면에는 '승인 가능' 으로 보였다. 판정하지 못했다고 정직하게 알린다.
        log_error(f'승인 가능 여부 사전 판정 실패: id={r.get("id")} {e}', e)
        return '승인 가능 여부를 확인하지 못했습니다. 승인을 눌러 보면 실제 결과를 알려 드립니다.'
    return ''


def count_schedules_in_range(cur, name, start_date, end_date):
    """[09] 휴가 승인 시 해제될 당직 날짜 목록. 관리자 확인창과 빈자리 채우기에서 함께 쓴다."""
    try:
        cur.execute('SELECT date_key FROM schedules WHERE worker_name = ? AND date_key BETWEEN ? AND ? ORDER BY date_key',
                    (name, start_date, end_date))
        return [r['date_key'] for r in cur.fetchall()]
    except Exception:
        return []


# Recovered from the original v3.14.5 executable's Python code objects.
# This fragment is concatenated after the shared globals, DTOs and DB helpers.

@app.post('/api/duty/request/action')
def process_request_action(dto: RequestActionDTO, x_admin_token: Optional[str] = Header(default=None)):
    require_admin(x_admin_token)
    action = (dto.action or '').strip().upper()
    if action not in ('APPROVE', 'REJECT'):
        raise HTTPException(status_code=400, detail='처리할 수 없는 결재 요청입니다. 신청 결재함을 다시 열어 주세요.')
    ensure_daily_backup()
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        ensure_change_request_schema(cur)
        ensure_absence_schema(cur)
        ensure_history_schema(cur)
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cur.execute('SELECT * FROM change_requests WHERE id = ?', (dto.request_id,))
        req = cur.fetchone()
        if not req:
            raise HTTPException(status_code=404, detail='신청을 찾을 수 없습니다. 신청 결재함을 다시 열어 확인해 주세요.')
        r_dict = dict(req)
        if r_dict['status'] != 'PENDING':
            raise HTTPException(status_code=400, detail='이미 처리된 신청입니다. 신청 결재함을 다시 열어 확인해 주세요.')
        if dto.request_revision is not None and dto.request_revision != (r_dict.get('revision') or 0):
            raise HTTPException(status_code=409, detail='신청자가 내용을 수정했습니다. 최신 신청 내용을 확인한 뒤 다시 처리해 주세요.')
        request_type = canonical_request_type(r_dict.get('request_type'))
        if request_type not in REQUEST_TYPE_LABELS and request_type != 'ABSENCE_CANCEL':
            raise HTTPException(status_code=400, detail='알 수 없는 신청 종류라 처리하지 못했습니다. 관리자에게 알려 주세요.')
        request_label = request_type_label(r_dict)
        removed_schedule_count = 0
        removed_schedule_dates = []
        approval_snapshot = ''
        if action == 'APPROVE':
            contract = read_holiday_supplements(cur)['requiredContract']
            if contract and not dto.impact_digest:
                raise HTTPException(status_code=428, detail='승인 영향 확인 정보가 없습니다. 신청 목록을 다시 불러온 뒤 승인해 주세요.')
            if dto.impact_digest is not None and not secrets.compare_digest(dto.impact_digest, hs_approval_impact_digest(cur, r_dict)):
                raise HTTPException(status_code=409, detail='일정이나 공휴일 조건이 바뀌었습니다. 최신 내용을 확인한 뒤 다시 승인하세요.')
            date_blocker = hs_request_date_blocker(cur, r_dict)
            if date_blocker:
                raise HTTPException(status_code=409, detail=date_blocker)
            before_hs_state = hs_read_state(cur)
            before_domain = domain_snapshot(cur)
            closed = read_closed_months(cur)
            span_end = (r_dict.get('absence_end_date') or r_dict.get('swap_date') or r_dict['target_date']) \
                if request_type in ('ABSENCE', 'ABSENCE_CANCEL') else r_dict['target_date']
            plan = request_absence_plan(cur, r_dict, True) if request_type in ('ABSENCE', 'ABSENCE_CANCEL') else None
            locked = plan_closed_months(plan, closed) if plan else closed_months_in_range(r_dict['target_date'], span_end, closed)
            if request_type == 'SWAP' and closed_month_of(r_dict.get('swap_date'), closed):
                locked = sorted(set(locked) | {r_dict['swap_date'][:7]})
            if locked:
                raise HTTPException(status_code=423,
                                    detail=f"마감된 달({', '.join(locked)})에 걸린 신청이라 승인할 수 없습니다. 마감을 해제한 뒤 승인하거나 반려해 주세요.")
            # v3.18.0 승인 직전 상태를 되돌리기용으로 남긴다. 쓰기 잠금을 쥔 채라 사본은 승인 직전과 같다.
            #   사본을 못 만들어도 승인은 막지 않는다(일일 백업이 따로 있다). 결과에 빈 이름으로 알린다.
            try:
                approval_snapshot = os.path.basename(take_auto_snapshot('approve'))
            except Exception as e:
                approval_snapshot = ''
                log_error(f'승인 직전 자동 사본 실패(승인은 계속): {e}', e)
            target_date = r_dict['target_date']
            requester = r_dict['requester_name']
            if request_type == 'SWAP':
                swap_date = r_dict['swap_date']
                swap_worker = require_member(cur, r_dict['swap_worker'], '맞교환 상대')
                requester = require_member(cur, requester)
                if requester == swap_worker:
                    raise HTTPException(status_code=400, detail='같은 사람끼리는 맞교환할 수 없습니다.')
                cur.execute('SELECT slot_index, is_substitute, zero_score FROM schedules WHERE date_key = ? AND worker_name = ?',
                            (target_date, requester))
                src_row = cur.fetchone()
                cur.execute('SELECT slot_index, is_substitute, zero_score FROM schedules WHERE date_key = ? AND worker_name = ?',
                            (swap_date, swap_worker))
                tgt_row = cur.fetchone()
                if not src_row or not tgt_row:
                    missing = []
                    if not src_row:
                        missing.append(f"[{target_date}]에 '{requester}'")
                    if not tgt_row:
                        missing.append(f"[{swap_date}]에 '{swap_worker}'")
                    raise HTTPException(status_code=409,
                                        detail='신청 이후 당직표가 변경되어 승인할 수 없습니다. ' + ', '.join(missing) + ' 배정이 없습니다. 신청은 결재 대기 상태로 남아 있습니다.')
                cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                            (target_date, swap_worker, src_row['slot_index']))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail=f"'{swap_worker}' 사원이 이미 [{target_date}]에 배정되어 있어 교환할 수 없습니다.")
                cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                            (swap_date, requester, tgt_row['slot_index']))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail=f"'{requester}' 사원이 이미 [{swap_date}]에 배정되어 있어 교환할 수 없습니다.")
                if is_absent_on(cur, swap_worker, target_date):
                    raise HTTPException(status_code=409, detail=f"'{swap_worker}' 사원은 {target_date}에 부재 기간이라 배정할 수 없습니다.")
                if is_absent_on(cur, requester, swap_date):
                    raise HTTPException(status_code=409, detail=f"'{requester}' 사원은 {swap_date}에 부재 기간이라 배정할 수 없습니다.")
                cur.execute('UPDATE schedules SET worker_name = ?, is_substitute = ?, zero_score = ? WHERE date_key = ? AND slot_index = ?',
                            (swap_worker, tgt_row['is_substitute'], tgt_row['zero_score'], target_date, src_row['slot_index']))
                cur.execute('UPDATE schedules SET worker_name = ?, is_substitute = ?, zero_score = ? WHERE date_key = ? AND slot_index = ?',
                            (requester, src_row['is_substitute'], src_row['zero_score'], swap_date, tgt_row['slot_index']))
                insert_history(cur, now_str, '1:1 맞교환(승인)', target_date,
                               f"[{target_date}] '{requester}' ↔ [{swap_date}] '{swap_worker}' 맞교환",
                               *domain_changes(before_domain, hs_reconciled_domain(cur, before_hs_state)))
            elif request_type == 'SUBSTITUTE':
                sub_name = require_member(cur, r_dict['substitute_name'], '대체 근무자')
                is_zero = 1 if r_dict['is_zero_score'] else 0
                # v3.16.0 [F-3] 0점 처리 여부는 신청자가 보낸 값이 기본이고, 승인하는 관리자가 바꿀 수 있다.
                #   바꾼 경우 이력에 원래 값을 함께 남긴다.
                zero_note = ''
                if dto.zero_score is not None and (1 if dto.zero_score else 0) != is_zero:
                    zero_note = f" · 신청 값 {'예' if is_zero else '아니오'} → 관리자 변경"
                    is_zero = 1 if dto.zero_score else 0
                if not sub_name:
                    raise HTTPException(status_code=400, detail='대체 근무할 사원이 지정되지 않았습니다.')
                cur.execute('SELECT slot_index FROM schedules WHERE date_key = ? AND worker_name = ?', (target_date, requester))
                src_row = cur.fetchone()
                if not src_row:
                    raise HTTPException(status_code=409,
                                        detail=f"신청 이후 당직표가 변경되어 승인할 수 없습니다. [{target_date}]에 '{requester}' 배정이 없습니다. 신청은 결재 대기 상태로 남아 있습니다.")
                cur.execute('SELECT 1 FROM schedules WHERE date_key = ? AND worker_name = ? AND slot_index <> ? LIMIT 1',
                            (target_date, sub_name, src_row['slot_index']))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail=f"'{sub_name}' 사원은 이미 [{target_date}]에 배정되어 있습니다.")
                if is_absent_on(cur, sub_name, target_date):
                    raise HTTPException(status_code=409, detail=f"'{sub_name}' 사원은 {target_date}에 부재 기간이라 대체 근무로 배정할 수 없습니다.")
                cur.execute("UPDATE schedules SET worker_name = ?, role = '대체당직', is_substitute = ?, zero_score = ? WHERE date_key = ? AND slot_index = ?",
                            (sub_name, 1, is_zero, target_date, src_row['slot_index']))
                cur.execute('UPDATE change_requests SET is_zero_score = ? WHERE id = ?', (is_zero, dto.request_id))
                insert_history(cur, now_str, '대체근무(승인)', target_date,
                               f"[{target_date}] '{requester}' ➔ 대체 '{sub_name}' 근무 (대체 근무 0점 처리: {'예' if is_zero else '아니오'}{zero_note})",
                               *domain_changes(before_domain, hs_reconciled_domain(cur, before_hs_state)))
            elif request_type == 'ABSENCE_CANCEL':
                original_absence = cancellation_current_absence(cur, r_dict)
                original_rows = absence_ranges(original_absence)
                for record in original_rows:
                    cur.execute('DELETE FROM absences WHERE id = ? AND name = ?', (record['id'], requester))
                    if cur.rowcount != 1:
                        raise HTTPException(status_code=409, detail='취소할 원본 일정이 달라졌습니다. 최신 내용을 확인해 주세요.')
                original_ids = {record['id'] for record in original_rows}
                before_change = {'absences': [a for a in before_domain['absences'] if a['id'] in original_ids]}
                insert_history(cur, now_str, '휴가·부재 취소(승인)', target_date,
                               f"[{original_absence['start']}~{original_absence['end']}] '{requester}' {original_absence['type']} 취소 승인 / {len(original_rows)}개 구간 제거 / 당직 자동 복원 없음",
                               before_change, {'absences': []})
                history_id = cur.lastrowid
                cur.execute('UPDATE change_requests SET approved_history_id = ? WHERE id = ?', (history_id, dto.request_id))
            elif request_type == 'ABSENCE':
                original_absence = amendment_current_absence(cur, r_dict) if r_dict.get('amends_request_id') else None
                start_date = strict_iso_date(target_date, '시작일')
                end_date = strict_iso_date(r_dict.get('absence_end_date') or r_dict.get('swap_date') or target_date, '종료일')
                if start_date > end_date:
                    raise HTTPException(status_code=400, detail='휴가·부재 종료일은 시작일보다 빠를 수 없습니다.')
                requester = require_member(cur, requester)
                absence_type = require_absence_type(cur, r_dict.get('absence_type') or r_dict.get('substitute_name'))
                plan = absence_date_plan(cur, absence_type, start_date, end_date, True)
                if existing_absence_overlap(cur, requester, plan, original_absence):
                    raise HTTPException(status_code=409,
                                        detail=f"'{requester}' 사원의 기존 휴가·부재 기간과 겹쳐 승인할 수 없습니다. 신청은 결재 대기 상태로 남아 있습니다.")
                education = normalize_education(optional_json(r_dict.get('absence_details')))
                education_json = json.dumps(education, ensure_ascii=False) if education is not None else ''
                original_rows = absence_ranges(original_absence) if original_absence else []
                approved_ids = set()
                for index, span in enumerate(plan['ranges']):
                    if index < len(original_rows):
                        record_id = original_rows[index]['id']
                        cur.execute('UPDATE absences SET type = ?, start_date = ?, end_date = ?, education_json = ? WHERE id = ? AND name = ?',
                                    (absence_type, span['start'], span['end'], education_json, record_id, requester))
                        if cur.rowcount != 1:
                            raise HTTPException(status_code=409, detail='변경할 원본 일정이 달라졌습니다. 최신 내용을 확인해 주세요.')
                    else:
                        cur.execute('INSERT INTO absences (name, type, start_date, end_date, education_json) VALUES (?, ?, ?, ?, ?)',
                                    (requester, absence_type, span['start'], span['end'], education_json))
                        record_id = cur.lastrowid
                    approved_ids.add(record_id)
                for record in original_rows[len(plan['ranges']):]:
                    cur.execute('DELETE FROM absences WHERE id = ? AND name = ?', (record['id'], requester))
                    if cur.rowcount != 1:
                        raise HTTPException(status_code=409, detail='변경할 원본 일정이 달라졌습니다. 최신 내용을 확인해 주세요.')
                removed_schedule_dates = count_schedules_in_plan(cur, requester, plan)
                for span in plan['ranges']:
                    cur.execute('DELETE FROM schedules WHERE worker_name = ? AND date_key BETWEEN ? AND ?',
                                (requester, span['start'], span['end']))
                    removed_schedule_count += cur.rowcount
                after_domain = hs_reconciled_domain(cur, before_hs_state)
                before_change, after_change = domain_changes(before_domain, after_domain)
                before_change, after_change = before_change or {}, after_change or {}
                original_ids = {record['id'] for record in original_rows}
                # Include unchanged constituents too: the history proves this approval's whole group.
                before_change['absences'] = [a for a in before_domain['absences'] if a['id'] in original_ids]
                after_change['absences'] = [a for a in after_domain['absences'] if a['id'] in approved_ids]
                history_type = '휴가·부재 변경(승인)' if original_absence else '휴가·부재(승인)'
                previous = f"기존 [{original_absence['start']}~{original_absence['end']}] {original_absence['type']} → " if original_absence else ''
                insert_history(cur, now_str, history_type, start_date,
                               f"{previous}[{start_date}~{end_date}] '{requester}' {absence_type} 반영 / 기존 당직 {removed_schedule_count}건 해제",
                               before_change, after_change)
                history_id = cur.lastrowid
                cur.execute('UPDATE change_requests SET approved_history_id = ? WHERE id = ?', (history_id, dto.request_id))
            else:
                raise HTTPException(status_code=400, detail='알 수 없는 신청 종류라 처리하지 못했습니다. 관리자에게 알려 주세요.')
            cur.execute("UPDATE change_requests SET status = 'APPROVED', processed_at = ? WHERE id = ? AND status = 'PENDING'",
                        (now_str, dto.request_id))
            status_updated = cur.rowcount
            processed_message = '휴가·부재 취소를 승인해 기존 일정 전체를 제거했습니다. 당직은 자동 복원되지 않습니다.' if request_type == 'ABSENCE_CANCEL' else f'{request_label} 신청을 승인해 당직표에 반영했습니다.'
        else:
            reject_reason = (dto.reject_reason or '').strip()[:200]
            cur.execute("UPDATE change_requests SET status = 'REJECTED', processed_at = ?, reject_reason = ? WHERE id = ? AND status = 'PENDING'",
                        (now_str, reject_reason, dto.request_id))
            status_updated = cur.rowcount
            processed_message = f'{request_label} 신청을 반려했습니다.'
            if reject_reason and request_type != 'ABSENCE_CANCEL':
                processed_message += f' (사유: {reject_reason})'
            detail_text = f"[반려] 신청자:{r_dict['requester_name']} 유형:{request_label}"
            if reject_reason and request_type != 'ABSENCE_CANCEL':
                detail_text += f' 반려사유:{reject_reason}'
            insert_history(cur, now_str, f'{request_label} 신청 반려', r_dict['target_date'], detail_text)
        if status_updated != 1:
            raise HTTPException(status_code=409, detail='다른 관리자가 먼저 처리했습니다. 신청 결재함을 다시 열어 확인해 주세요.')
        new_version = bump_data_version(cur, f"request:{dto.request_id}:{'APPROVED' if action == 'APPROVE' else 'REJECTED'}")
        cur.execute("SELECT COUNT(*) FROM change_requests WHERE status = 'PENDING'")
        cnt = cur.fetchone()[0]
        conn.commit()
        log_info(f'결재 처리: id={dto.request_id} action={action} 남은 대기={cnt} PC={HOST_LABEL} 계정={ACTOR_LABEL}')
    except HTTPException as he:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        # v3.16.0 [B-2] 실패한 결재 시도도 한 줄 남긴다. 예전에는 아무 기록도 없었다.
        log_info(f'결재 실패: id={dto.request_id} action={action} 사유={he.status_code} {he.detail} PC={HOST_LABEL} 계정={ACTOR_LABEL}')
        raise
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_error(f'결재 처리 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '결재를 처리'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    broadcast_from_thread({'event': 'REQUEST_PROCESSED', 'action': action,
                             'status': 'APPROVED' if action == 'APPROVE' else 'REJECTED',
                             'pendingCount': cnt, 'requestId': dto.request_id,
                             'requestType': request_type, 'requestLabel': request_label,
                             'requester': r_dict['requester_name'], 'target_date': r_dict['target_date'],
                             'absence_end_date': r_dict.get('absence_end_date') or '',
                             'removedScheduleCount': removed_schedule_count,
                             'dataVersion': new_version, 'message': processed_message})
    return {'status': 'success', 'action': action, 'pendingCount': cnt, 'requestId': dto.request_id,
            'requestType': request_type, 'removedScheduleCount': removed_schedule_count,
            'removedScheduleDates': removed_schedule_dates, 'dataVersion': new_version,
            'message': processed_message, 'snapshot': approval_snapshot,
            'snapshotLabel': snapshot_label(approval_snapshot) if approval_snapshot else ''}


@app.post('/api/duty/save-all')
def save_all_data(dto: SyncFullDataDTO,
                  x_admin_token: Optional[str] = Header(default=None),
                  x_client_id: Optional[str] = Header(default=None)):
    return _save_all_data(dto, x_admin_token, x_client_id)


def _save_all_data(dto, x_admin_token=None, x_client_id=None, holiday_restore=None):
    require_admin(x_admin_token)
    ensure_daily_backup()
    conn = None
    auto_snapshot = ''
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        current_v = read_data_version(cur)
        # v3.16.0 [위험] 예전에는 baseVersion 이 없으면 충돌 검사를 건너뛰고 전체 교체를 했다.
        #   캐시된 옛 화면이나 재전송 한 번으로 다른 PC 의 저장이 조용히 지워질 수 있었다.
        if dto.baseVersion is None:
            raise HTTPException(status_code=428,
                                detail='저장 기준 정보가 없어 저장하지 않았습니다. 화면을 새로고침(F5)한 뒤 다시 시도해 주세요.')
        if dto.baseVersion != current_v:
            raise HTTPException(status_code=409,
                                detail='다른 PC에서 먼저 저장하여 당직표가 변경되었습니다. 최신 내용을 불러온 뒤 변경 사항을 다시 적용해 주세요.')
        if dto.holidaySupplements is not None:
            raise HTTPException(status_code=400, detail='순번 보완 자료는 일반 저장으로 바꿀 수 없습니다. 전체 백업 복원 기능을 사용해 주세요.')
        before_hs_state = hs_read_state(cur)
        if holiday_restore is not None and holiday_restore['metadata'] is None and before_hs_state['holidaySupplements']['requiredContract'] and not holiday_restore['restoreLegacy']:
            raise HTTPException(status_code=409, detail='이 파일에는 공휴일 보완 기록이 없습니다. 보완 이전 상태로 전체 복원할지 확인해 주세요.')
        if dto.members is not None:
            existing_names = {row[0] for row in cur.execute('SELECT name FROM members')}
            for member in dto.members:
                name = member.get('name') if isinstance(member, dict) else None
                if not isinstance(name, str):
                    raise HTTPException(status_code=400, detail='사원 이름은 문자열로 입력해 주세요.')
                # Names link schedules and requests. Keep existing identities and
                # explicit backup restores intact; limit only new registrations.
                if holiday_restore is None and name not in existing_names and len(name) > 4:
                    raise HTTPException(status_code=400, detail='새 사원 이름은 4자 이내로 입력해 주세요.')
        validate_save_payload(dto, cur)
        ensure_absence_schema(cur)
        ensure_history_schema(cur)
        before_domain = domain_snapshot(cur) if dto.schedules is not None or dto.absences is not None else None
        sent_collections = [key for key, _ in HISTORY_COLLECTION_LABELS if getattr(dto, key) is not None]
        before_state = collection_state(cur, sent_collections) if dto.historyLog is None and sent_collections else None
        closed = read_closed_months(cur)
        locked_dates = closed_month_conflicts(cur, dto, closed)
        if locked_dates:
            months = sorted({d[:7] for d in locked_dates})
            sample = ', '.join(locked_dates[:5]) + (' 외' if len(locked_dates) > 5 else '')
            raise HTTPException(status_code=423,
                                detail=f"마감된 달({', '.join(months)})의 당직표·휴가·부재·휴일은 바꿀 수 없습니다. 바뀌려던 날: {sample}. "
                                       '고쳐야 한다면 [더보기 → 이 달 마감 해제]를 먼저 하고(사유가 기록됩니다) 다시 저장해 주세요.')
        if dto.snapshotKind is not None:
            if dto.snapshotKind not in AUTO_SNAPSHOT_KINDS:
                raise HTTPException(status_code=400, detail='자동 사본 종류가 올바르지 않습니다.')
            # 쓰기 잠금을 쥔 채 만든다. 다른 PC 의 저장은 기다리므로 사본에는 이 저장 직전까지의 모든 내용이 들어 있다.
            try:
                auto_snapshot = take_auto_snapshot(dto.snapshotKind)
            except Exception as e:
                log_error(f'자동 사본 실패로 저장을 중단합니다: {e}', e)
                raise HTTPException(status_code=500, detail=friendly_db_error(e, '되돌리기용 자동 사본을 만들')
                                    + ' 지금 데이터는 바뀌지 않았습니다.')
        if dto.members is not None:
            names = [m.get('name') for m in dto.members]
            dupes = {n for n in names if names.count(n) > 1}
            if dupes:
                raise HTTPException(status_code=400, detail=f"중복된 사원 이름이 있어 저장할 수 없습니다: {', '.join(sorted(dupes))}")
            cur.execute('DELETE FROM members')
            for m in dto.members:
                cur.execute('INSERT INTO members (id, name, gender) VALUES (?, ?, ?)',
                            (m.get('id'), m.get('name'), m.get('gender')))
            dinner_schema.after_members_changed(cur)
        if dto.schedules is not None:
            cur.execute('DELETE FROM schedules')
            for dk, wlist in dto.schedules.items():
                for idx, w in enumerate(wlist):
                    cur.execute('INSERT INTO schedules (date_key, slot_index, worker_name, role, is_substitute, zero_score) VALUES (?, ?, ?, ?, ?, ?)',
                                (dk, idx, w.get('name'), w.get('role', '당직'), 1 if w.get('isSubstitute') else 0, 1 if w.get('zeroScore') else 0))
        if dto.comments is not None:
            cur.execute("UPDATE day_meta SET comment = ''")
            for dk, text in dto.comments.items():
                if text is None:
                    continue
                cur.execute('INSERT INTO day_meta (date_key, comment, is_no_duty) VALUES (?, ?, 0) ON CONFLICT(date_key) DO UPDATE SET comment = excluded.comment',
                            (dk, text))
        if dto.noDuty is not None:
            cur.execute("UPDATE day_meta SET is_no_duty = 0, no_duty_reason = ''")
            for dk, flag in dto.noDuty.items():
                if not flag:
                    continue
                reason = flag if isinstance(flag, str) else ''
                cur.execute("INSERT INTO day_meta (date_key, comment, is_no_duty, no_duty_reason) VALUES (?, '', 1, ?) ON CONFLICT(date_key) DO UPDATE SET is_no_duty = 1, no_duty_reason = excluded.no_duty_reason",
                            (dk, reason))
        if dto.comments is not None or dto.noDuty is not None:
            cur.execute("DELETE FROM day_meta WHERE (comment IS NULL OR comment = '') AND is_no_duty = 0")
        if dto.absences is not None:
            old_absences = read_absences(cur)
            old_by_id = {item['id']: item for item in old_absences}
            explicit_ids = []
            for a in dto.absences:
                if not isinstance(a, dict):
                    raise HTTPException(status_code=400, detail='부재 기록 형식이 올바르지 않습니다.')
                explicit_ids.append(normalize_absence_id(a.get('id')))
            reserved_ids = {value for value in explicit_ids if value is not None}
            if len(reserved_ids) != sum(value is not None for value in explicit_ids):
                raise HTTPException(status_code=400, detail='같은 부재 ID를 여러 기록에 사용할 수 없습니다.')
            old_by_key = {}
            for item in reversed(old_absences):
                if item['id'] not in reserved_ids:
                    key = (item['name'], item['type'], item['start'], item['end'])
                    old_by_key.setdefault(key, []).append(item)
            pending_absences = []
            for a, record_id in zip(dto.absences, explicit_ids):
                if record_id is not None:
                    matched = old_by_id.get(record_id)
                else:
                    candidates = old_by_key.get((a.get('name'), a.get('type'), a.get('start'), a.get('end')), [])
                    matched = candidates.pop() if candidates else None
                if matched and matched['name'] != a.get('name'):
                    matched = None
                education = normalize_education(a['education']) if 'education' in a else (matched or {}).get('education')
                record_id = record_id if record_id is not None else (matched or {}).get('id')
                pending_absences.append((record_id, a.get('name'), a.get('type'), a.get('start'), a.get('end'),
                                         json.dumps(education, ensure_ascii=False) if education is not None else ''))
            cur.execute('DELETE FROM absences')
            # 모든 명시/재사용 ID를 먼저 넣어 자동 증가 ID가 뒤에 나올 명시 ID와 충돌하지 않게 한다.
            for values in sorted(pending_absences, key=lambda row: row[0] is None):
                cur.execute('INSERT INTO absences (id, name, type, start_date, end_date, education_json) VALUES (?, ?, ?, ?, ?, ?)',
                            values)
        if dto.absenceTypes is not None:
            cur.execute('DELETE FROM absence_types')
            for t in dict.fromkeys(dto.absenceTypes):
                cur.execute('INSERT INTO absence_types (type_name) VALUES (?)', (t,))
        if dto.customHolidays is not None:
            cur.execute('DELETE FROM custom_holidays')
            for dk, v in dto.customHolidays.items():
                cur.execute('INSERT INTO custom_holidays (date_key, name, is_major, is_removed) VALUES (?, ?, ?, ?)',
                            (dk, v.get('name'), 1 if v.get('isMajor') else 0, 1 if v.get('isRemoved') else 0))
        if dto.rules is not None:
            cur.execute("INSERT OR REPLACE INTO system_rules (rule_key, rule_json) VALUES ('DEFAULT', ?)",
                        (json.dumps(dto.rules, ensure_ascii=False),))
        if dto.globalMemo is not None:
            cur.execute("INSERT INTO app_settings (key, value) VALUES ('global_memo', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (dto.globalMemo,))
        if holiday_restore is not None:
            restore_data = holiday_restore['metadata'] or {'schema':1,'events':[],'bases':[],'requiredContract':0,'operations':[]}
            if hs_closed_metadata_conflicts(before_hs_state['holidaySupplements'], restore_data, closed):
                raise HTTPException(status_code=423, detail='마감된 달의 공휴일 보완 기록은 복원으로 바꿀 수 없습니다.')
            hs_validate_restore_binding(cur, restore_data)
            write_holiday_supplements(cur, restore_data)
        else:
            reconcile_holiday_supplements(cur, before_hs_state, hs_read_state(cur), 'save-all')
        # v3.16.0 [위험] 변경 이력은 서버만 추가한다(append-only).
        #   예전에는 historyBulk 를 받으면 이력 표를 통째로 지우고 화면이 보낸 내용으로 다시 채웠고,
        #   저장할 때마다 500건 넘는 이력을 지웠다. 심사에서 근거를 물으면 이미 사라졌거나
        #   화면이 보낸 대로 바뀐 상태일 수 있었다.
        #   historyBulk 는 '이력이 비어 있는 새 DB 로 옮길 때' 만 받아들이고, 그때도 지우지 않는다.
        before_change, after_change = domain_changes(before_domain, domain_snapshot(cur)) if before_domain is not None else (None, None)
        imported_history = False
        if dto.historyBulk is not None:
            existing = cur.execute('SELECT COUNT(*) FROM change_history').fetchone()[0]
            if existing == 0:
                valid_history = [h for h in dto.historyBulk if isinstance(h, dict)]
                dropped = 0

                def text(value):
                    return '' if value is None else (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
                for h in reversed(valid_history):
                    imported_before, imported_after, bad_snapshot = tolerant_history_snapshots(h)
                    details = '[가져온 이력] ' + text(h.get('details', ''))
                    if bad_snapshot:
                        dropped += 1
                        details += ' (전후 기록 형식 오류로 생략)'
                    insert_history(cur, text(h.get('timestamp', '')), text(h.get('type', '')), text(h.get('dateKey', '')),
                                   details, imported_before, imported_after)
                    imported_history = True
                if dropped:
                    log_info(f'가져온 이력 중 {dropped}건은 전후 기록 형식이 맞지 않아 전후 기록만 빼고 가져왔습니다.')
            else:
                log_info(f'백업 파일의 이력 {len(dto.historyBulk)}건은 가져오지 않았습니다(기존 이력 {existing}건 보존).')
        # v3.17.13 이력 시각은 서버 시각이다. 화면이 보낸 시각으로 과거 이력처럼 꾸밀 수 없게 한다.
        now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        changed_labels = []
        if before_state is not None:
            after_state = collection_state(cur, sent_collections)
            changed_labels = [label for key, label in HISTORY_COLLECTION_LABELS
                              if key in before_state and before_state[key] != after_state[key]]
        if dto.historyLog is not None:
            h = dto.historyLog if isinstance(dto.historyLog, dict) else {}
            insert_history(cur, now_text, str(h.get('type') or '변경'), str(h.get('dateKey') or ''),
                           str(h.get('details') or ''), before_change, after_change)
        elif before_change is not None:
            dates = list((before_change.get('schedules') or {}).keys())
            absent_dates = [item['start'] for item in before_change.get('absences', []) + after_change.get('absences', [])]
            details = '백업 데이터 가져오기' if imported_history else '일정/부재 변경 사항 저장'
            if changed_labels:
                details += ' · 함께 바뀐 항목: ' + ', '.join(changed_labels)
            insert_history(cur, now_text,
                           '데이터 가져오기' if imported_history else '일정/부재 변경',
                           min(dates + absent_dates) if dates or absent_dates else '',
                           details, before_change, after_change)
        elif changed_labels:
            insert_history(cur, now_text, '설정 변경', '', ', '.join(changed_labels) + ' 변경 사항 저장')
        new_v = bump_data_version(cur)
        supplement_response = hs_public_metadata(hs_read_state(cur))
        conn.commit()
    except HTTPException:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_error(f'저장 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '저장'))
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    broadcast_from_thread({'event': 'DATA_SYNCED', 'origin': x_client_id or ''})
    out = {'status': 'success', 'dataVersion': new_v, 'holidaySupplements': supplement_response}
    if auto_snapshot:
        name = os.path.basename(auto_snapshot)
        out.update(snapshot=name, snapshotLabel=snapshot_label(name))
    return out


class SnapshotDTO(BaseModel):
    kind: str


@app.post('/api/system/snapshot')
def create_auto_snapshot(dto: SnapshotDTO, x_admin_token: Optional[str] = Header(default=None)):
    """v3.18.0 저장을 거치지 않는 작업 직전에 되돌리기용 자동 사본을 만든다(관리자 전용)."""
    require_admin(x_admin_token)
    if dto.kind not in AUTO_SNAPSHOT_KINDS:
        raise HTTPException(status_code=400, detail='자동 사본 종류가 올바르지 않습니다.')
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=409, detail='아직 데이터 파일이 없어 사본을 만들 수 없습니다.')
    try:
        path = take_auto_snapshot(dto.kind)
    except Exception as e:
        log_error(f'자동 사본 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '되돌리기용 자동 사본을 만들'))
    name = os.path.basename(path)
    return {'status': 'success', 'name': name, 'label': snapshot_label(name),
            'modified': datetime.fromtimestamp(os.path.getmtime(path)).strftime('%Y-%m-%d %H:%M:%S')}


# ── v3.16.0 추가 API ─────────────────────────────────────────────

class MonthCloseDTO(BaseModel):
    month: str
    closed: bool
    reason: Optional[str] = ''
    baseVersion: int


class RestoreDTO(BaseModel):
    name: str
    baseVersion: int
    restoreLegacy: bool = False


@app.get('/api/duty/my-requests')
def get_my_requests(name: str = '', involving: int = 0, grouped: int = 0):
    """v3.16.0 [D-1] 이름으로 내 신청과 결과(반려 사유 포함)를 본다.
    예전에는 결과를 브라우저 저장소의 접수 토큰으로만 볼 수 있어, 다른 PC·브라우저 정리 뒤에는
    영구히 확인할 수 없었다. PC 마다 독립 프로그램이라 승인·반려 알림도 신청자에게 가지 않았다.
    v3.18.0 involving=1 이면 내가 맞교환 상대·대신 근무할 사람으로 지정된 신청도 함께 준다(role='partner').
    조회용 화면이 '이서연 님과 맞교환 승인' 같은 결과 알림 하나로 일정 변경 알림을 합치는 데 쓴다.
    상대 입장의 행에는 반려 사유를 싣지 않는다(신청자에게만 전하는 내용)."""
    clean = (name or '').strip()
    if not clean:
        return {'requests': []}
    if involving:
        return {'requests': _requests_involving(clean)}
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
        pick = ['id', 'requester_name', 'target_date', 'request_type', 'swap_date', 'swap_worker',
                'substitute_name', 'is_zero_score', 'status', 'created_at', 'processed_at',
                'absence_type', 'absence_end_date', 'reject_reason', 'amends_request_id', 'amendment_original']
        fields = ', '.join(c if c in cols else f"'' AS {c}" for c in pick)
        if grouped and {'amends_request_id', 'request_type'} <= cols:
            # Limit schedules, not individual events: a recent cancellation may
            # belong to an approval older than the previous 50-row window.
            groups = cur.execute(
                "SELECT CASE WHEN r.request_type = 'ABSENCE_CANCEL' AND p.id IS NOT NULL "
                "THEN p.id ELSE r.id END AS group_id, MAX(r.id) AS latest_id "
                "FROM change_requests r LEFT JOIN change_requests p "
                "ON p.id = r.amends_request_id AND p.requester_name = r.requester_name "
                "AND p.request_type IN ('ABSENCE', 'ABSENCE_AMEND') "
                "WHERE r.requester_name = ? GROUP BY group_id ORDER BY latest_id DESC LIMIT 50",
                (clean,)).fetchall()
            ids = [row['group_id'] for row in groups]
            if not ids:
                return {'requests': []}
            marks = ','.join('?' for _ in ids)
            cur.execute(f'SELECT {fields} FROM change_requests WHERE requester_name = ? '
                        f'AND (id IN ({marks}) OR (request_type = \'ABSENCE_CANCEL\' '
                        f'AND amends_request_id IN ({marks}))) ORDER BY id DESC', (clean, *ids, *ids))
        else:
            cur.execute(f'SELECT {fields} FROM change_requests WHERE requester_name = ? ORDER BY id DESC LIMIT 50', (clean,))
        rows = []
        for r in cur.fetchall():
            item = dict(r)
            item['request_type'] = canonical_request_type(item.get('request_type'))
            item['amends_request_id'] = item.get('amends_request_id') or None
            item['amendment_original'] = optional_json(item.get('amendment_original'))
            add_request_date_plan(cur, item)
            item['requestLabel'] = request_type_label(item)
            item.update(public_cancellation_state(cur, item))
            item['reject_reason'] = '' if item['request_type'] == 'ABSENCE_CANCEL' else item.get('reject_reason') or ''
            rows.append(item)
        return {'requests': rows}
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'내 신청 내역 조회 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '신청 내역을 불러오'))
    finally:
        if conn:
            conn.close()


def _requests_involving(name):
    'v3.18.0 name 이 신청자이거나 맞교환 상대·대신 근무할 사람인 최근 신청 50건 (신청 사유는 싣지 않는다).'
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute('PRAGMA table_info(change_requests)').fetchall()}
        pick = ['id', 'requester_name', 'target_date', 'request_type', 'swap_date', 'swap_worker',
                'substitute_name', 'is_zero_score', 'status', 'created_at', 'processed_at',
                'absence_type', 'absence_end_date', 'reject_reason', 'amends_request_id', 'amendment_original']
        fields = ', '.join(c if c in cols else f"'' AS {c}" for c in pick)
        # 휴가·부재 신청은 옛 DB 호환 때문에 substitute_name 에 구분을 담으므로 종류로 한정한다.
        cur.execute(f"SELECT {fields} FROM change_requests WHERE requester_name = ? "
                    f"OR (UPPER(request_type) = 'SWAP' AND swap_worker = ?) "
                    f"OR (UPPER(request_type) = 'SUBSTITUTE' AND substitute_name = ?) "
                    f"ORDER BY id DESC LIMIT 50", (name, name, name))
        rows = []
        for r in cur.fetchall():
            item = dict(r)
            item['request_type'] = canonical_request_type(item.get('request_type'))
            item['amends_request_id'] = item.get('amends_request_id') or None
            item['amendment_original'] = optional_json(item.get('amendment_original'))
            add_request_date_plan(cur, item)
            item['requestLabel'] = request_type_label(item)
            item.update(public_cancellation_state(cur, item))
            item['role'] = 'requester' if item.get('requester_name') == name else 'partner'
            item['reject_reason'] = (item.get('reject_reason') or '') if item['role'] == 'requester' and item['request_type'] != 'ABSENCE_CANCEL' else ''
            rows.append(item)
        return rows
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'관련 신청 조회 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '신청 내역을 불러오'))
    finally:
        if conn:
            conn.close()


@app.get('/api/duty/history-export')
def export_history(start: str = '', end: str = ''):
    """v3.16.0 [B-1] 변경 이력 전체를 기간으로 CSV 로 내려받는다. 화면에는 최근 500건만 보이지만
    표에는 전부 남아 있다(심사 제출용). 엑셀에서 바로 열리도록 UTF-8 BOM 을 붙인다."""
    s_key = strict_iso_date(start, '시작일') if start else '0000-00-00'
    e_key = strict_iso_date(end, '종료일') if end else '9999-12-31'
    if s_key > e_key:
        raise HTTPException(status_code=400, detail='시작일이 종료일보다 늦습니다.')
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cols = {r[1] for r in cur.execute('PRAGMA table_info(change_history)').fetchall()}
        extra = ', actor, host' if set(HISTORY_EXTRA_COLUMNS) <= cols else ", '' AS actor, '' AS host"
        cur.execute(f'SELECT timestamp, type, date_key, details{extra} FROM change_history '
                    'WHERE substr(timestamp, 1, 10) BETWEEN ? AND ? ORDER BY id', (s_key, e_key))
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator='\r\n')
        writer.writerow(['시각', '구분', '대상일', '내용', 'PC', 'Windows 계정'])
        count = 0
        for r in cur.fetchall():
            writer.writerow([r['timestamp'], r['type'], r['date_key'], r['details'], r['host'] or '', r['actor'] or ''])
            count += 1
        body = ('﻿' + buffer.getvalue()).encode('utf-8')
        name = f"onair_history_{s_key if start else 'all'}_{e_key if end else 'all'}.csv"
        return Response(content=body, media_type='text/csv; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{name}"', 'X-Row-Count': str(count)})
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'이력 내보내기 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '이력을 내보내'))
    finally:
        if conn:
            conn.close()


@app.post('/api/duty/month-close')
def set_month_close(dto: MonthCloseDTO, x_admin_token: Optional[str] = Header(default=None)):
    """v3.16.0 [B-3] 월 마감(선택 기능). 마감한 달은 당직·휴무·메모 저장, 변경 신청 접수·승인을 막는다.
    마감·해제는 사유와 함께 이력에 남는다. 해제에는 사유가 반드시 필요하다."""
    require_admin(x_admin_token)
    month = (dto.month or '').strip()
    if not MONTH_RE.match(month):
        raise HTTPException(status_code=400, detail='마감할 달이 올바르지 않습니다. 화면을 새로고침(F5)한 뒤 다시 시도해 주세요.')
    reason = (dto.reason or '').strip()[:200]
    if not dto.closed and not reason:
        raise HTTPException(status_code=400, detail='마감을 해제하려면 사유를 적어 주세요. 심사 때 근거가 됩니다.')
    ensure_daily_backup()
    conn = None
    try:
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        if dto.baseVersion != read_data_version(cur):
            raise HTTPException(status_code=409, detail='다른 PC에서 먼저 저장했습니다. 새로고침 후 다시 시도해 주세요.')
        closed = read_closed_months(cur)
        if dto.closed == (month in closed):
            raise HTTPException(status_code=409, detail=f"{josa(month_label(month), '은/는')} 이미 {'마감' if dto.closed else '마감 해제'} 상태입니다.")
        closed = (closed | {month}) if dto.closed else (closed - {month})
        cur.execute("INSERT INTO app_settings (key, value) VALUES ('closed_months', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (json.dumps(sorted(closed)),))
        now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        insert_history(cur, now_str, '월 마감' if dto.closed else '월 마감 해제', month + '-01',
                       f"{month} {'마감' if dto.closed else '마감 해제'}" + (f' (사유: {reason})' if reason else ''))
        new_v = bump_data_version(cur)
        conn.commit()
    except HTTPException:
        if conn:
            conn.rollback()
        raise
    except Exception as e:
        if conn:
            conn.rollback()
        log_error(f'월 마감 처리 실패: {e}', e)
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '월 마감을 처리'))
    finally:
        if conn:
            conn.close()
    log_info(f"월 마감 {'설정' if dto.closed else '해제'}: {month} PC={HOST_LABEL} 계정={ACTOR_LABEL} 사유={reason}")
    broadcast_from_thread({'event': 'DATA_SYNCED', 'origin': ''})
    return {'status': 'success', 'closedMonths': sorted(closed), 'dataVersion': new_v}


@app.get('/api/system/clients')
def get_clients(request: Request, x_admin_token: Optional[str] = Header(default=None)):
    """v3.16.0 [D-2] PC별 설치 버전 현황. 각 PC 가 켜질 때와 10분마다 스스로 한 줄을 갱신한다."""
    require_diag_access(x_admin_token, request)
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        has = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='client_status'").fetchone()[0]
        rows = [dict(r) for r in cur.execute('SELECT * FROM client_status ORDER BY last_seen DESC')] if has else []
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=friendly_db_error(e, 'PC 현황을 불러오'))
    finally:
        if conn:
            conn.close()
    latest = max((r.get('app_version') or '0' for r in rows), key=version_tuple, default='')
    now = datetime.now()
    for r in rows:
        r['outdated'] = bool(latest) and version_tuple(r.get('app_version')) < version_tuple(latest)
        try:
            r['daysSinceSeen'] = (now - datetime.strptime(r.get('last_seen') or '', '%Y-%m-%d %H:%M:%S')).days
        except Exception:
            r['daysSinceSeen'] = None
    return {'clients': rows, 'latestVersion': latest, 'thisHost': HOST_LABEL, 'thisVersion': APP_VERSION}


def _manual_backup_dir():
    return os.path.join(SHARED_PATH, 'back-up') if SHARED_PATH and os.path.isabs(SHARED_PATH) else ''


def _has_dinner_backup_content():
    conn = get_db_connection(readonly=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return any(conn.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                   for table in dinner_schema.TABLES if table in tables)
    finally:
        conn.close()


def _backup_files():
    out = []
    for folder, manual in ((backup_dir_path(), False), (_manual_backup_dir(), True)):
        if not folder or not os.path.isdir(folder):
            continue
        for f in os.listdir(folder):
            allowed = MANUAL_FULL_BACKUP_RE.fullmatch(f) if manual else (DAILY_BACKUP_RE.match(f) or SNAPSHOT_RE.match(f))
            if not allowed:
                continue
            full = os.path.join(folder, f)
            linked = os.path.islink(full) or getattr(Path(full), 'is_junction', lambda: False)()
            if not os.path.isfile(full) or (manual and linked):
                continue
            try:
                modified = datetime.fromtimestamp(os.path.getmtime(full))
                size = os.path.getsize(full)
            except OSError:
                continue  # A simultaneous cleanup must not break the entire list.
            kind = 'manual' if manual else ('daily' if DAILY_BACKUP_RE.match(f) else f.split('_', 1)[0])
            auto = AUTO_SNAPSHOT_RE.match(f)
            out.append({'name': f, 'kind': kind, 'size': size,
                        'modified': modified.strftime('%Y-%m-%d %H:%M:%S'),
                        'label': snapshot_label(f, modified), 'autoKind': auto.group(1) if auto else ''})
    # 자동 사본·직전 사본은 이름순이 시간순이 아니다. 최근 것이 위로 오게 수정 시각으로 정렬한다.
    out.sort(key=lambda item: (item['modified'], item['name']), reverse=True)
    return out


def _resolve_backup(name):
    clean = (name or '').strip()
    manual = bool(MANUAL_FULL_BACKUP_RE.fullmatch(clean))
    if not (manual or DAILY_BACKUP_RE.match(clean) or SNAPSHOT_RE.match(clean)) or os.path.basename(clean) != clean or (manual and clean != name):
        raise HTTPException(status_code=400, detail='백업 파일 이름이 올바르지 않습니다.')
    folder = _manual_backup_dir() if manual else backup_dir_path()
    if not folder:
        raise HTTPException(status_code=404, detail='백업 파일을 찾을 수 없습니다.')
    path = os.path.join(folder, clean)
    if manual and (os.path.islink(path) or getattr(Path(path), 'is_junction', lambda: False)()):
        raise HTTPException(status_code=400, detail='연결된 백업 파일은 복원 대상이 될 수 없습니다.')
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail='백업 파일을 찾을 수 없습니다.')
    # 같은 파일을 자기 자신에게 복원하면 잠금을 기다리며 멈춘다. 이름 검사와 별도로 한 번 더 막는다.
    try:
        if os.path.exists(DB_PATH) and os.path.samefile(path, DB_PATH):
            raise HTTPException(status_code=400, detail='사용 중인 데이터 파일은 복원 대상이 될 수 없습니다.')
    except OSError:
        pass
    return path


def _summarize_db(conn):
    out = {}
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in ('members', 'schedules', 'absences', 'custom_holidays', 'change_history', 'change_requests',
                  'dinner_polls', 'dinner_responses'):
        out[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] if table in tables else None
    span = conn.execute('SELECT MIN(date_key), MAX(date_key) FROM schedules').fetchone() if 'schedules' in tables else (None, None)
    last = conn.execute('SELECT MAX(timestamp) FROM change_history').fetchone()[0] if 'change_history' in tables else None
    return {'counts': out, 'scheduleFrom': span[0] or '', 'scheduleTo': span[1] or '', 'lastChange': last or '',
            'isOnAir': {'members', 'schedules', 'app_settings'} <= tables,
            'holidaySupplementSchema': 1 if 'holiday_supplements' in tables else 0,
            'holidaySupplementCount': conn.execute("SELECT COUNT(*) FROM holiday_supplements WHERE state IN ('ACTIVE','NEEDS_REVIEW')").fetchone()[0] if 'holiday_supplements' in tables else 0}


@app.post('/api/system/backup')
def save_manual_backup(x_admin_token: Optional[str] = Header(default=None)):
    """Save portable duty JSON and, when needed, a verified complete dinner DB."""
    require_admin(x_admin_token)
    # Legacy profiles can use CONFIG_DIR for data. A server backup must not silently
    # fall back to that user's local profile or create an empty disconnected share.
    if not SHARED_PATH or not os.path.isabs(SHARED_PATH):
        raise HTTPException(status_code=503, detail='서버 백업 경로가 설정되지 않았습니다. 공유 데이터 폴더의 절대 경로(shared_folder_path)를 설정한 뒤 다시 실행해 주세요.')
    require_existing_shared_database()
    temporary = None
    full_target = None
    completed = False
    try:
        data = _read_duty_data(full_history=True, private_supplements=True)
        now = datetime.now().astimezone()
        exported_at = now.isoformat()
        payload = {'formatVersion': 2, 'exportedAt': exported_at,
                   **{key: data[key] for key in ('members', 'schedules', 'comments', 'noDuty',
                      'absences', 'absenceTypes', 'customHolidays', 'rules', 'globalMemo', 'history', 'holidaySupplements')}}
        folder = os.path.join(SHARED_PATH, 'back-up')
        os.makedirs(folder, exist_ok=True)
        # Time plus a random identifier keeps simultaneous backups on different PCs separate.
        identifier = f"{now.strftime('%Y%m%d_%H%M%S_%f')}_{secrets.token_hex(8)}"
        name = f'smart_duty_backup_{identifier}.json'
        target = os.path.join(folder, name)
        descriptor, temporary = tempfile.mkstemp(prefix='.manual-backup-', suffix='.tmp', dir=folder)
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        byte_size = os.path.getsize(temporary)
        result = {'ok': True, 'name': name, 'path': target, 'exportedAt': exported_at,
                  'dataVersion': data['dataVersion'], 'byteSize': byte_size}
        if _has_dinner_backup_content():
            # The JSON remains backward-compatible. SQLite backup supplies a
            # transactionally consistent full copy including polls/responses.
            full_name = f'smart_duty_full_{identifier}.db'
            full_target = os.path.join(folder, full_name)
            _snapshot_to(full_target)
            result.update(fullBackupName=full_name, fullBackupPath=full_target)
        os.replace(temporary, target)
        completed = True
        log_info(f'서버 수동 백업 저장: {target}')
        return result
    except HTTPException:
        raise
    except Exception as e:
        log_error(f'서버 수동 백업 저장 실패: {e}', e)
        raise HTTPException(status_code=503, detail='서버의 back-up 폴더에 백업을 저장하지 못했습니다. 공유 폴더 연결·쓰기 권한·여유 공간을 확인한 뒤 다시 시도해 주세요.') from e
    finally:
        if full_target and not completed:
            # Never leave a new partial pair that looks like a successful backup.
            try:
                os.unlink(full_target)
            except FileNotFoundError:
                pass
            except OSError as e:
                log_error(f'수동 전체 백업 미완료 파일 정리 실패: {e}')
        if temporary:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            except OSError as e:
                log_error(f'수동 백업 임시 파일 정리 실패: {e}')


@app.get('/api/system/backups')
def list_backups(x_admin_token: Optional[str] = Header(default=None)):
    """복원할 수 있는 백업 목록(자동·구조 변경 직전·복원 직전·수동 전체)."""
    require_admin(x_admin_token)
    return {'backups': _backup_files(), 'dir': backup_dir_path()}


@app.get('/api/system/backup-preview')
def preview_backup(name: str = '', x_admin_token: Optional[str] = Header(default=None)):
    """복원 전에 '그 백업에는 무엇이 들어 있나' 를 현재와 나란히 보여 준다. 아무것도 바꾸지 않는다."""
    require_admin(x_admin_token)
    path = _resolve_backup(name)
    problem = _verify_sqlite_file(path)
    if problem:
        return {'name': name, 'ok': False, 'detail': problem}
    uri = Path(os.path.abspath(path)).as_uri() + '?mode=ro'
    if os.path.abspath(path).startswith(chr(92) * 2):
        uri = build_readonly_uri(path)
    src = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        backup = _summarize_db(src)
    finally:
        src.close()
    cur_conn = get_db_connection()
    try:
        current = _summarize_db(cur_conn)
        current['dataVersion'] = read_data_version(cur_conn.cursor())
    finally:
        cur_conn.close()
    return {'name': name, 'ok': backup['isOnAir'], 'detail': '' if backup['isOnAir'] else '당직 데이터 파일이 아닙니다.',
            'backup': backup, 'current': current}


def _readonly_backup_uri(path):
    uri = Path(os.path.abspath(path)).as_uri() + '?mode=ro'
    if os.path.abspath(path).startswith(chr(92) * 2):
        uri = build_readonly_uri(path)
    return uri


def _prepare_restore_copy(path, workdir):
    """백업을 이 PC 의 임시 폴더로 받아 지금 구조로 맞춘다(옛 구조 백업 대비). 백업 파일 자체는 건드리지 않는다.
    공유 폴더의 운영 DB 잠금을 쥐기 전에 끝내, 잠금 시간을 줄인다."""
    copy_path = os.path.join(workdir, 'restore.db')
    src = sqlite3.connect(_readonly_backup_uri(path), uri=True, timeout=5)
    try:
        dst = sqlite3.connect(copy_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    conn = sqlite3.connect(copy_path)
    conn.row_factory = sqlite3.Row
    conn.create_function('wlr_schedule_contract', 0, lambda: 1)
    request_access_schema.register_connection(conn)
    try:
        apply_schema(conn.cursor())
        conn.commit()
    finally:
        conn.close()
    return copy_path


# 복원해도 바꾸지 않는 설정값. data_version·마지막 저장 정보는 새로 쓰고, 월 마감은 지금 상태를 유지한다.
RESTORE_KEEP_SETTINGS = ('data_version', 'closed_months', 'max_write_version', 'required_write_contract',
                         'last_write_version', 'last_write_host', 'last_write_at', 'last_write_source',
                         'request_access_dataset_id')
# 이력은 append-only 라 지금 표를 그대로 둔다. PC 현황은 자료가 아니라 각 PC 의 실행 기록이다.
RESTORE_SKIP_TABLES = ('change_history', 'client_status', 'holiday_supplements', 'holiday_supplement_bases', 'holiday_supplement_operations') + request_access_schema.PROTECTED_TABLES + dinner_schema.RESTORE_SKIP_TABLES


def _table_columns(cur, schema, table):
    return [r[1] for r in cur.execute(f'PRAGMA "{schema}".table_info("{table}")').fetchall()]


@app.post('/api/system/restore')
def restore_backup(dto: RestoreDTO, x_admin_token: Optional[str] = Header(default=None)):
    """v3.16.0 [A-5] 백업으로 되돌린다. 되돌리기 직전 상태를 prerestore 스냅샷으로 먼저 남기므로
    복원 자체도 되돌릴 수 있다. 다른 PC 화면이 따라오도록 데이터 버전은 이전보다 크게 둔다.

    v3.17.13 [위험] 예전에는 버전 확인·이력 읽기·직전 사본·파일 통째 복사·버전 올리기를 서로 다른 연결로 했다.
      그 사이 다른 PC 가 저장하면 그 저장은 직전 사본에도 이력에도 없이 사라졌고, 복원 뒤 버전이 그 PC 의 버전과
      같아져 그 PC 의 낡은 화면 저장이 충돌 검사를 통과했다. 복사 뒤 단계가 실패하면 당직표만 바뀌고 버전은 그대로였다.
      이제 운영 DB 에 쓰기 잠금(BEGIN IMMEDIATE)을 쥔 채 버전 확인 → 직전 사본 → 표 내용 교체 → 버전 올리기를
      한 트랜잭션으로 한다. 잠금을 쥔 동안 다른 PC 의 저장은 기다리므로, 그 전에 끝난 저장은 모두 직전 사본에 들어가고
      그 뒤의 저장은 새 버전에 막힌다(409). 어느 단계에서 실패해도 되돌려져 아무것도 바뀌지 않는다.
      월 마감 상태는 백업 값으로 되돌리지 않는다(사유 없이 마감이 풀리지 않게). 다르면 복원 이력에 남긴다."""
    require_admin(x_admin_token)
    path = _resolve_backup(dto.name)
    problem = _verify_sqlite_file(path)
    if problem:
        raise HTTPException(status_code=400, detail=f'이 백업은 복원할 수 없습니다: {problem}')
    probe = sqlite3.connect(_readonly_backup_uri(path), uri=True, timeout=5)
    try:
        backup_summary = _summarize_db(probe)
        if not backup_summary['isOnAir']:
            raise HTTPException(status_code=400, detail='당직 데이터 파일이 아니라 복원하지 않았습니다.')
    finally:
        probe.close()
    workdir = tempfile.mkdtemp(prefix='wlr-restore-')
    conn = None
    attached = False
    safety = ''
    try:
        try:
            restore_copy = _prepare_restore_copy(path, workdir)
        except Exception as e:
            log_error(f'복원할 백업을 준비하지 못했습니다: {e}', e)
            raise HTTPException(status_code=500, detail=friendly_db_error(e, '복원할 백업을 준비') + ' 지금 데이터는 바뀌지 않았습니다.')
        conn = get_db_connection(readonly=False)
        cur = conn.cursor()
        cur.execute('ATTACH DATABASE ? AS rs', (restore_copy,))
        attached = True
        cur.execute('BEGIN IMMEDIATE')
        require_holiday_write_contract(cur)
        before_version = read_data_version(cur)
        live_hs = read_holiday_supplements(cur, private=True)
        restore_hs = read_holiday_supplements(cur, private=True, schema='rs')
        current_closed = read_closed_months(cur)
        if hs_closed_metadata_conflicts(live_hs, restore_hs, current_closed):
            raise HTTPException(status_code=423, detail='마감된 달의 공휴일 보완 기록은 복원으로 바꿀 수 없습니다. 마감 해제 사유를 남긴 뒤 다시 복원해 주세요.')
        before_restore = hs_read_state(cur) if current_closed else None
        before_comments = dict(cur.execute("SELECT date_key,comment FROM day_meta WHERE comment IS NOT NULL AND comment<>''")) if current_closed else {}
        if live_hs['requiredContract'] and not backup_summary['holidaySupplementSchema'] and not dto.restoreLegacy:
            raise HTTPException(status_code=409, detail='이 백업에는 공휴일 보완 기록이 없습니다. 보완 이전 상태로 전체 복원할지 확인해 주세요.')
        restore_hs['requiredContract'] = max(live_hs['requiredContract'], restore_hs['requiredContract'])
        hs_storage_operation_merge_plan(cur, restore_hs.get('operations', []))
        if dto.baseVersion != before_version:
            raise HTTPException(status_code=409, detail='다른 PC에서 먼저 저장했습니다. 새로고침 후 복원할 백업을 다시 확인해 주세요.')
        # 쓰기 잠금을 쥔 채 직전 사본을 뜬다. 다른 PC 는 지금 저장할 수 없으므로 사본에는 지금까지의 모든 저장이 있다.
        try:
            safety = take_named_snapshot('prerestore', 'v' + str(before_version))
        except Exception as e:
            log_error(f'복원 직전 스냅샷 실패로 복원을 중단합니다: {e}', e)
            raise HTTPException(status_code=500, detail=friendly_db_error(e, '복원 직전 안전 사본을 만들') + ' 지금 데이터는 바뀌지 않았습니다.')
        backup_last_history = cur.execute('SELECT MAX(id) FROM rs.change_history').fetchone()[0] or 0
        carried_count = cur.execute('SELECT COUNT(*) FROM main.change_history WHERE id > ?', (backup_last_history,)).fetchone()[0]
        kept = {r[0]: r[1] for r in cur.execute(
            f"SELECT key, value FROM main.app_settings WHERE key IN ({', '.join('?' for _ in RESTORE_KEEP_SETTINGS)})",
            RESTORE_KEEP_SETTINGS).fetchall()}
        backup_settings = {r[0]: r[1] for r in cur.execute(
            "SELECT key, value FROM rs.app_settings WHERE key IN ('data_version', 'closed_months', 'max_write_version')").fetchall()}
        try:
            backup_closed = {m for m in json.loads(backup_settings.get('closed_months') or '[]')
                             if isinstance(m, str) and MONTH_RE.match(m)}
        except Exception:
            backup_closed = set()
        try:
            restored_version = int(backup_settings.get('data_version') or 0)
        except ValueError:
            restored_version = 0
        tables = [r[0] for r in cur.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()]
        for table in tables:
            if table in RESTORE_SKIP_TABLES:
                continue
            backup_cols = set(_table_columns(cur, 'rs', table))
            common = [c for c in _table_columns(cur, 'main', table) if c in backup_cols]
            cur.execute(f'DELETE FROM main."{table}"')
            if common:
                names = ', '.join(f'"{c}"' for c in common)
                cur.execute(f'INSERT INTO main."{table}" ({names}) SELECT {names} FROM rs."{table}"')
        cur.execute(f"DELETE FROM main.app_settings WHERE key IN ({', '.join('?' for _ in RESTORE_KEEP_SETTINGS)})",
                    RESTORE_KEEP_SETTINGS)
        for key, value in kept.items():
            cur.execute('INSERT INTO main.app_settings (key, value) VALUES (?, ?)', (key, value))
        newest_writer = max([v for v in (kept.get('max_write_version'), backup_settings.get('max_write_version')) if v] or [''],
                            key=version_tuple)
        if newest_writer:
            cur.execute("INSERT INTO main.app_settings (key, value) VALUES ('max_write_version', ?) "
                        'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (newest_writer,))
        if before_restore is not None:
            # Compare symmetrically inside this transaction. Any mismatch rolls the
            # copy back before commit, using the same date protection as JSON restore.
            before_dto = SyncFullDataDTO(**{k:before_restore[k] for k in ('schedules','absences','noDuty','customHolidays')}, comments=before_comments)
            locked_dates = closed_month_conflicts(cur, before_dto, current_closed)
            if locked_dates:
                raise HTTPException(status_code=423, detail='마감된 달의 당직표·휴가·부재·휴일은 복원으로 바꿀 수 없습니다. 바뀌려던 날: '+', '.join(locked_dates[:5]))
        hs_validate_restore_binding(cur, restore_hs)
        write_holiday_supplements(cur, restore_hs)
        # 잠금을 쥔 채 확인한 before_version 이 어떤 PC 가 가질 수 있는 가장 큰 버전이다. 그보다 반드시 크게 둔다.
        new_v = max(before_version, restored_version) + 1
        cur.execute("INSERT INTO main.app_settings (key, value) VALUES ('data_version', ?) "
                    'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (str(new_v),))
        stamp_last_write(cur)
        request_access_schema.after_access_restore(cur)
        dinner_schema.after_restore(cur)
        details = (f'{dto.name} 으로 복원 [{snapshot_label(dto.name)}] (복원 직전 상태: {os.path.basename(safety)}, '
                   f'백업 이후 이력 {carried_count}건 보존)')
        if backup_closed != current_closed:
            details += (f" · 월 마감 상태는 복원하지 않고 현재 값 유지 (현재: {', '.join(sorted(current_closed)) or '없음'}"
                        f" / 백업: {', '.join(sorted(backup_closed)) or '없음'})")
        now = datetime.now()
        insert_history(cur, now.strftime('%Y-%m-%d %H:%M:%S'), '백업 복원', now.strftime('%Y-%m-%d'), details)
        conn.commit()
    except HTTPException:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    except Exception as e:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass
        log_error(f'복원 실패(되돌림): {e}', e)
        note = f' 지금 데이터는 바뀌지 않았습니다.' + (f' 직전 상태 사본: {os.path.basename(safety)}' if safety else '')
        raise HTTPException(status_code=500, detail=friendly_db_error(e, '복원') + note)
    finally:
        if conn:
            if attached:
                try:
                    conn.execute('DETACH DATABASE rs')
                except Exception:
                    pass
            try:
                conn.close()
            except Exception:
                pass
        shutil.rmtree(workdir, ignore_errors=True)
    log_info(f'백업 복원: {dto.name} / 직전 상태 {safety} / PC={HOST_LABEL} 계정={ACTOR_LABEL}')
    broadcast_from_thread({'event': 'DATA_SYNCED', 'origin': ''})
    return {'status': 'success', 'restored': dto.name, 'safetyCopy': os.path.basename(safety), 'dataVersion': new_v,
            'safetyLabel': snapshot_label(os.path.basename(safety)), 'restoredLabel': snapshot_label(dto.name)}


@app.websocket('/ws/duty')
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)


class NoStoreStaticFiles(StaticFiles):
    """Disable browser heuristic caching of the bundled application screen."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers['Cache-Control'] = 'no-store, must-revalidate'
        return response


def open_browser(port=None):
    try:
        host = 'localhost' if BIND_HOST in ('0.0.0.0', '127.0.0.1', 'localhost') else BIND_HOST
        url = f'http://{host}:{port or SERVER_PORT}'
        if MANAGED_CONFIG and host in ('localhost', '127.0.0.1', '::1'):
            try:
                url += '#onair_access=' + REQUEST_ACCESS.issue_ticket(url)
            except request_access_schema.AccessError:
                log_info('개인 권한 보관소 연결을 준비하지 못했습니다. 화면에서 다시 확인할 수 있습니다.')
        webbrowser.open(url)
    except Exception as e:
        log_error(f'브라우저를 열지 못했습니다: {e}')


def port_in_use(host, port):
    """해당 포트가 이미 쓰이는 중인지 확인한다."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
        sk.settimeout(0.6)
        try:
            sk.bind(('' if host == '0.0.0.0' else host, port))
            return False
        except OSError:
            return True


def already_running(port):
    """그 포트에 떠 있는 것이 이 프로그램인지 실제 바인딩 주소로 확인한다."""
    import urllib.request
    host = '127.0.0.1' if BIND_HOST in ('0.0.0.0', '', None) else BIND_HOST
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/api/system/info', timeout=1.5) as r:
            data = json.loads(r.read().decode())
            return data.get('distributionRole', data.get('mode')) == DISTRIBUTION_ROLE
    except Exception:
        return False


def write_notice(title, lines):
    """사용자가 바로 볼 수 있도록 실행 폴더에 안내 파일을 남긴다."""
    try:
        with open(os.path.join(CONFIG_DIR, '실행오류_안내.txt'), 'w', encoding='utf-8') as f:
            f.write(title + NL + NL + NL.join(lines) + NL)
    except Exception:
        pass


def resolve_port():
    """Reuse the running application or find a free port and write a notice."""
    required = os.environ.get('WLR_REQUIRED_PORT')
    if required:
        if str(SERVER_PORT) != required or port_in_use(BIND_HOST, SERVER_PORT):
            raise RuntimeError('기존 접속 포트를 사용할 수 없어 업데이트 실행을 중단했습니다.')
        return SERVER_PORT
    if not port_in_use(BIND_HOST, SERVER_PORT):
        return SERVER_PORT
    if already_running(SERVER_PORT):
        log_error(f'중복 실행 감지: 포트 {SERVER_PORT} 에 이미 같은 프로그램이 실행 중입니다.')
        write_notice('WLR OnAir - 이미 켜져 있습니다', [
            'WLR OnAir가 이미 켜져 있습니다. 열려 있는 당직표 화면을 그대로 쓰세요.',
            f'화면이 보이지 않으면 브라우저 주소창에 http://localhost:{SERVER_PORT} 를 넣으세요.',
            '',
            '[연결 정보 → 자주 바꾸는 설정]에서 설정을 바꾸셨다면',
            '  1) 열려 있는 브라우저 창을 닫고',
            '  2) 화면 오른쪽 아래 알림 영역(트레이)의 WLR OnAir 아이콘을',
            '     마우스 오른쪽 버튼으로 눌러 [종료]를 고른 뒤',
            '     (트레이 아이콘이 보이지 않으면 작업 관리자에서 종료)',
            '  3) 다시 실행해야 새 설정이 적용됩니다.',
        ])
        if OPEN_BROWSER:
            open_browser(SERVER_PORT)
        return None
    for cand in range(SERVER_PORT + 1, SERVER_PORT + 21):
        if not port_in_use(BIND_HOST, cand):
            log_error(f'{SERVER_PORT} 번 포트를 다른 프로그램이 사용 중이라 {cand} 번 포트로 실행합니다.')
            write_notice('WLR OnAir - 다른 포트로 실행했습니다', [
                f'정해 둔 {SERVER_PORT}번 포트를 다른 프로그램이 쓰고 있어 {cand}번 포트로 실행했습니다.',
                '지금은 그대로 쓰시면 됩니다.',
                '',
                f'접속 주소:  http://localhost:{cand}',
                '',
                '항상 같은 주소를 쓰려면 [연결 정보 → 자주 바꾸는 설정]의 포트를',
                '비어 있는 다른 번호로 바꾼 뒤 프로그램을 다시 시작하세요.',
            ])
            return cand
    log_error(f'{SERVER_PORT}~{SERVER_PORT + 20} 번 포트가 모두 사용 중입니다.')
    write_notice('WLR OnAir - 시작하지 못했습니다', [
        f'{SERVER_PORT}번부터 {SERVER_PORT + 20}번까지 쓸 수 있는 포트가 없어 WLR OnAir를 시작하지 못했습니다.',
        'PC를 다시 시작한 뒤 실행해 보세요. 그래도 안 되면 이 창을 캡처해 관리자에게 보내 주세요.',
    ])
    return None


TRAY_WINDOW_CLASS = 'WLROnAirTrayWindow'
TRAY_MENU_OPEN = 1001
TRAY_MENU_INFO = 1002
TRAY_MENU_EXIT = 1003


class TrayIcon:
    """알림 영역 아이콘 하나와 그 팝업 메뉴. Windows 전용."""

    def __init__(self, tooltip, on_open, on_info, on_exit, on_close=None):
        import ctypes
        from ctypes import wintypes
        self.ct = ctypes
        self.tooltip = tooltip[:127]
        self.on_open, self.on_info, self.on_exit = on_open, on_info, on_exit
        self.on_close = on_close or on_exit
        self.hwnd = None
        self.hicon = None
        self._added = False
        self._wts = None
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        self.shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        self.kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
        self.kernel32.GetModuleHandleW.restype = wintypes.HMODULE

        LRESULT = ctypes.c_ssize_t
        HWND = wintypes.HWND
        UINT = wintypes.UINT
        WPARAM = ctypes.c_size_t
        LPARAM = ctypes.c_ssize_t
        self.LRESULT, self.HWND, self.WPARAM, self.LPARAM = LRESULT, HWND, WPARAM, LPARAM
        self.user32.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        self.user32.PostMessageW.restype = wintypes.BOOL
        self.user32.DestroyWindow.argtypes = [HWND]
        self.user32.DestroyWindow.restype = wintypes.BOOL
        self.user32.DefWindowProcW.restype = LRESULT
        self.user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        self.user32.CreateWindowExW.restype = HWND
        self.user32.CreateWindowExW.argtypes = [
            wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
        ]
        self.user32.CreatePopupMenu.restype = wintypes.HMENU
        self.user32.TrackPopupMenu.restype = ctypes.c_int
        self.user32.TrackPopupMenu.argtypes = [
            wintypes.HMENU, UINT, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, HWND, ctypes.c_void_p,
        ]
        self.shell32.ExtractIconW.restype = wintypes.HICON
        self.shell32.ExtractIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, UINT]
        self.user32.LoadIconW.restype = wintypes.HICON
        self.user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
        self.WNDPROC = WNDPROC
        self._wndproc = WNDPROC(self._on_message)

        class WNDCLASSW(ctypes.Structure):
            _fields_ = [
                ('style', UINT), ('lpfnWndProc', WNDPROC), ('cbClsExtra', ctypes.c_int),
                ('cbWndExtra', ctypes.c_int), ('hInstance', wintypes.HINSTANCE),
                ('hIcon', wintypes.HICON), ('hCursor', wintypes.HANDLE),
                ('hbrBackground', wintypes.HBRUSH), ('lpszMenuName', wintypes.LPCWSTR),
                ('lpszClassName', wintypes.LPCWSTR),
            ]

        class NOTIFYICONDATAW(ctypes.Structure):
            _fields_ = [
                ('cbSize', wintypes.DWORD), ('hWnd', HWND), ('uID', UINT), ('uFlags', UINT),
                ('uCallbackMessage', UINT), ('hIcon', wintypes.HICON),
                ('szTip', wintypes.WCHAR * 128), ('dwState', wintypes.DWORD),
                ('dwStateMask', wintypes.DWORD), ('szInfo', wintypes.WCHAR * 256),
                ('uVersion', UINT), ('szInfoTitle', wintypes.WCHAR * 64),
                ('dwInfoFlags', wintypes.DWORD),
                ('guidItem', wintypes.BYTE * 16), ('hBalloonIcon', wintypes.HICON),
            ]
        self.WNDCLASSW, self.NOTIFYICONDATAW = WNDCLASSW, NOTIFYICONDATAW

    def _icon(self):
        """exe 자신의 아이콘을 쓰고, 실패하면 기본 아이콘으로 떨어진다."""
        try:
            if getattr(sys, 'frozen', False):
                h = self.shell32.ExtractIconW(None, sys.executable, 0)
                if h and h > 1:
                    return h
        except Exception:
            pass
        return self.user32.LoadIconW(None, self.ct.c_wchar_p(32512))

    def _nid(self, flags):
        nid = self.NOTIFYICONDATAW()
        nid.cbSize = self.ct.sizeof(self.NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = flags
        return nid

    def _on_message(self, hwnd, msg, wparam, lparam):
        try:
            if msg == 0x02B1 and self._wts:  # WM_WTSSESSION_CHANGE
                # Disconnect, logoff, lock; connect, logon and unlock all invalidate grants in RAM.
                if wparam in (2, 4, 6, 7):
                    MASTER.workstation_changed(True)
                elif wparam in (1, 3, 5, 8):
                    MASTER.workstation_changed(False)
                return 0
            if msg == 0x0016 and wparam and self._wts:  # WM_ENDSESSION
                MASTER.workstation_changed(True)
            if msg == 32769:
                low = lparam & 65535
                if low == 515:
                    self._safe(self.on_open)
                elif low in (517, 520):
                    self._popup()
                return 0
            if msg == 16:
                self._safe(self.on_close)
                return 0
            if msg == 2:
                self.user32.PostQuitMessage(0)
                return 0
        except Exception as e:
            log_error(f'트레이 메시지 처리 오류: {e}', e)
        return self.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup(self):
        from ctypes import wintypes
        menu = self.user32.CreatePopupMenu()
        if not menu:
            return
        try:
            self.user32.AppendMenuW(menu, 0, TRAY_MENU_OPEN, '열기 (당직표 화면)')
            self.user32.AppendMenuW(menu, 0, TRAY_MENU_INFO, '연결 정보')
            self.user32.AppendMenuW(menu, 2048, 0, None)
            self.user32.AppendMenuW(menu, 0, TRAY_MENU_EXIT, '종료')
            pt = wintypes.POINT()
            self.user32.GetCursorPos(self.ct.byref(pt))
            self.user32.SetForegroundWindow(self.hwnd)
            cmd = self.user32.TrackPopupMenu(menu, 258, pt.x, pt.y, 0, self.hwnd, None)
            self.user32.PostMessageW(self.hwnd, 0, 0, 0)
        finally:
            self.user32.DestroyMenu(menu)
        if cmd == TRAY_MENU_OPEN:
            self._safe(self.on_open)
        elif cmd == TRAY_MENU_INFO:
            self._safe(self.on_info)
        elif cmd == TRAY_MENU_EXIT:
            self._safe(self.on_exit)

    def _safe(self, fn):
        try:
            fn()
        except Exception as e:
            log_error(f'트레이 명령 처리 오류: {e}', e)

    def create(self):
        wc = self.WNDCLASSW()
        wc.lpfnWndProc = self._wndproc
        wc.lpszClassName = TRAY_WINDOW_CLASS
        wc.hInstance = self.kernel32.GetModuleHandleW(None)
        if not self.user32.RegisterClassW(self.ct.byref(wc)):
            err = self.ct.get_last_error()
            if err != 1410:
                raise OSError(f'트레이 창 클래스 등록 실패 ({err})')
        self._wc = wc
        self.hwnd = self.user32.CreateWindowExW(
            0, TRAY_WINDOW_CLASS, 'WLR OnAir', 0, 0, 0, 0, 0,
            None, None, wc.hInstance, None)
        if not self.hwnd:
            raise OSError(f'트레이 창 생성 실패 ({self.ct.get_last_error()})')
        if DISTRIBUTION_ROLE == 'MASTER':
            from ctypes import wintypes
            wts = self.ct.WinDLL('wtsapi32', use_last_error=True)
            wts.WTSRegisterSessionNotification.argtypes = [wintypes.HWND, wintypes.DWORD]
            wts.WTSUnRegisterSessionNotification.argtypes = [wintypes.HWND]
            if not wts.WTSRegisterSessionNotification(self.hwnd, 0):  # NOTIFY_FOR_THIS_SESSION
                raise OSError('Windows 잠금 알림을 등록하지 못했습니다.')
            self._wts = wts
            MASTER.monitor_ready(True)
            if not TRAY_ENABLED:
                return
        self.hicon = self._icon()
        nid = self._nid(7)
        nid.uCallbackMessage = 32769
        nid.hIcon = self.hicon
        nid.szTip = self.tooltip
        if not self.shell32.Shell_NotifyIconW(0, self.ct.byref(nid)):
            raise OSError(f'트레이 아이콘 등록 실패 ({self.ct.get_last_error()})')
        self._added = True

    def balloon(self, title, text):
        if not self._added:
            return
        nid = self._nid(16)
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = 1
        self.shell32.Shell_NotifyIconW(1, self.ct.byref(nid))

    def confirm(self, title, text):
        return self.user32.MessageBoxW(None, text, title, 327716) == 6

    def loop(self):
        from ctypes import wintypes
        msg = wintypes.MSG()
        while self.user32.GetMessageW(self.ct.byref(msg), None, 0, 0) > 0:
            self.user32.TranslateMessage(self.ct.byref(msg))
            self.user32.DispatchMessageW(self.ct.byref(msg))

    def remove(self):
        try:
            if self._wts:
                MASTER.monitor_ready(False)
                self._wts.WTSUnRegisterSessionNotification(self.hwnd)
                self._wts = None
            if self._added:
                self.shell32.Shell_NotifyIconW(2, self.ct.byref(self._nid(0)))
                self._added = False
            if self.hwnd:
                self.user32.DestroyWindow(self.hwnd)
                self.hwnd = None
        except Exception:
            pass


def run_with_tray(run_port):
    """트레이 아이콘을 띄우고 uvicorn 은 뒤에서 돌린다. 실패하면 False."""
    if sys.platform != 'win32' or (not TRAY_ENABLED and DISTRIBUTION_ROLE != 'MASTER'):
        return False
    server = uvicorn.Server(uvicorn.Config(app, host=BIND_HOST, port=run_port, log_config=None))
    label = {'MASTER': '최고 관리자용', 'ADMIN': '관리자용', 'VIEWER': '조회용'}[DISTRIBUTION_ROLE]
    tray = None

    def do_open():
        open_browser(run_port)

    def do_info():
        tray.balloon(f'WLR OnAir {APP_VERSION} ({label})', NL.join([
            f'주소: http://localhost:{run_port}',
            f'데이터: {DB_PATH}',
        ]))

    def shutdown(ask, why):
        if ask and not tray.confirm('WLR OnAir 종료',
                f'WLR OnAir({label})를 종료합니다.{NL}'
                f'열려 있는 브라우저 창도 더 이상 쓸 수 없게 됩니다.{NL}{NL}'
                '종료할까요?'):
            return
        log_info(f'{why} 프로그램을 종료합니다.')
        tray.remove()
        server.should_exit = True
        th.join()

    try:
        tray = TrayIcon(f'WLR OnAir {APP_VERSION} ({label}) · 포트 {run_port}',
                        do_open, do_info,
                        lambda: shutdown(True, '트레이에서 종료를 선택해'),
                        lambda: shutdown(False, '닫기 요청을 받아'))
        tray.create()
    except Exception as e:
        log_error(f'트레이 아이콘을 만들지 못해 예전 방식으로 실행합니다: {e}', e)
        if tray:
            tray.remove()
        return False
    configure_manual_restart(server, run_port,
        lambda: tray.user32.PostMessageW(tray.hwnd, 0x0010, 0, 0))
    th = threading.Thread(target=server.run, name='uvicorn', daemon=False)
    th.start()
    log_info('트레이 아이콘 준비 완료. 종료는 트레이 아이콘 우클릭 → 종료.')
    try:
        tray.loop()
    finally:
        tray.remove()
        server.should_exit = True
        th.join()
    return True



REQUEST_ACCESS = request_access_api.install(app, globals())
MASTER = master_auth.install(app, request_access_api.HostNamespace(globals()))


def dinner_master_authorized(request, cursor=None):
    return MASTER.authorized(request, cursor)


DINNER = dinner_api.install(app, globals())
for _logger in (logger, logging.getLogger(), logging.getLogger("uvicorn.access")):
    if not any(isinstance(f, master_auth.PrivateAccessLogFilter) for f in _logger.filters):
        _logger.addFilter(master_auth.PrivateAccessLogFilter())
    for _handler in _logger.handlers:
        if not any(isinstance(f, master_auth.PrivateAccessLogFilter) for f in _handler.filters):
            _handler.addFilter(master_auth.PrivateAccessLogFilter())

if os.path.exists(FRONTEND_DIST):
    app.mount('/', NoStoreStaticFiles(directory=FRONTEND_DIST, html=True), name='static')
else:
    log_error(f'화면 파일을 찾을 수 없습니다: {FRONTEND_DIST}')

if __name__ == '__main__':
    RUN_PORT = resolve_port()
    if RUN_PORT is None:
        sys.exit(0)
    if OPEN_BROWSER:
        threading.Timer(0.8, open_browser, args=(RUN_PORT,)).start()
    ran_with_tray = False
    try:
        ran_with_tray = run_with_tray(RUN_PORT)
        if not ran_with_tray:
            server = uvicorn.Server(uvicorn.Config(app, host=BIND_HOST, port=RUN_PORT, log_config=None))
            configure_manual_restart(server, RUN_PORT)
            server.run()
    except BaseException as e:
        if isinstance(e, KeyboardInterrupt):
            log_info('사용자 요청으로 종료합니다.')
            raise
        log_error(f'서버를 시작하지 못했습니다 (host={BIND_HOST}, port={RUN_PORT}): {e}', e)
        write_notice('WLR OnAir를 시작하지 못했습니다', [
            'WLR OnAir를 시작하지 못했습니다.',
            'PC를 다시 시작한 뒤 실행해 보세요. 그래도 안 되면 이 창을 캡처해 관리자에게 보내 주세요.',
            '',
            f'(관리자 전달용: {"관리자용" if APP_MODE == "ADMIN" else "조회용"} · {BIND_HOST}:{RUN_PORT} · {e} · 기록 파일 {os.path.basename(LOG_PATH)})',
        ])
        raise
