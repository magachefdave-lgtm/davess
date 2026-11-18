"""Stable per-role profiles and non-destructive migration of existing settings."""
from __future__ import annotations

import configparser
import io
import os
from pathlib import Path
import sqlite3
import tempfile

from .core import DEFAULT_PORTS, UpdateError, profile_lock, sha256_file, validate_role

DB_NAME = 'smart_duty.db'


def legacy_ini_names(role: str) -> tuple[str, ...]:
    if role == 'MASTER':
        return ('config_master.ini', 'config_admin.ini', 'config.ini')
    return (f'config_{validate_role(role).lower()}.ini', 'config.ini')


def profile_dir(role: str) -> Path:
    role = validate_role(role)
    override = os.environ.get('WLR_PROFILE_ROOT')
    if override is not None:
        root = Path(override)
        if not override or not root.is_absolute():
            raise UpdateError('WLR_PROFILE_ROOT에는 절대 폴더 경로를 지정하세요.')
    elif os.name == 'nt':
        local = os.environ.get('LOCALAPPDATA', '')
        if not local or not Path(local).is_absolute():
            raise UpdateError('Windows 사용자 LOCALAPPDATA 경로를 확인할 수 없습니다.')
        root = Path(local) / 'WLR_OnAir'
    else:
        root = Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'WLR_OnAir'
        if not root.is_absolute():
            raise UpdateError('사용자 데이터 폴더는 절대 경로여야 합니다.')
    return root / role


def _directory(path: Path, label: str) -> Path:
    path = Path(path)
    if not path.is_absolute():
        raise UpdateError(f'{label}에는 절대 경로를 지정하세요.')
    if not path.is_dir():
        raise UpdateError(f'{label}에 접근할 수 없습니다: {path}')
    return path


def _read_ini(path: Path) -> configparser.ConfigParser:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise UpdateError(f'기존 설정을 읽을 수 없습니다: {path}') from error
    for encoding in ('utf-8-sig', 'cp949'):
        config = configparser.ConfigParser(interpolation=None)
        try:
            config.read_string(raw.decode(encoding))
        except (UnicodeError, configparser.Error):
            continue
        if 'NETWORK' not in config:
            raise UpdateError('기존 설정에 [NETWORK] 항목이 없습니다.')
        return config
    raise UpdateError('기존 설정은 UTF-8 또는 CP949 INI 형식이어야 합니다.')


def _existing_db(folder: Path) -> None:
    _directory(folder, '데이터 폴더')
    db = folder / DB_NAME
    if not db.is_file():
        raise UpdateError(f'기존 당직 데이터 파일을 찾을 수 없습니다: {db}')
    try:
        # URI mode=ro does not create a database if the file disappears mid-check.
        uri = db.as_uri() + '?mode=ro'
        if str(db).startswith('\\\\'):
            from urllib.parse import quote
            uri = 'file:////' + quote(str(db)[2:].replace('\\', '/')) + '?mode=ro'
        connection = sqlite3.connect(uri, uri=True, timeout=2)
        try:
            connection.execute('PRAGMA schema_version').fetchone()
        finally:
            connection.close()
    except sqlite3.Error as error:
        raise UpdateError(f'기존 당직 데이터 파일을 읽을 수 없습니다: {db}') from error



def validate_profile_storage(role: str, config_dir: Path) -> None:
    """Check a stable profile's original DB without creating or migrating files.

    Used before update staging and after a failed child start, so a disconnected
    share is not classified as a defective release. Older valid schemas remain
    eligible for the backend's existing migrations.
    """
    role = validate_role(role)
    config_dir = _directory(Path(config_dir), '프로필 설정 폴더')
    config = _read_ini(config_dir / f'config_{role.lower()}.ini')
    configured = config.get('NETWORK', 'shared_folder_path', fallback='').strip()
    if not configured or not Path(configured).is_absolute():
        raise UpdateError('프로필의 shared_folder_path에는 기존 데이터의 절대 폴더 경로가 필요합니다.')
    _existing_db(Path(configured))


def _publish_new_file(target: Path, write_temp) -> None:
    """Publish a fully flushed file without replacing an existing path."""
    temporary = None
    stage = '임시파일 생성'
    try:
        descriptor, name = tempfile.mkstemp(prefix='.' + target.name + '.', suffix='.tmp', dir=target.parent)
        temporary = Path(name)
        os.close(descriptor)
        stage = '내용 기록'
        write_temp(temporary)
        stage = '디스크 동기화'
        # Windows _commit/FlushFileBuffers requires a writable handle.
        # r+b preserves the complete INI/SQLite bytes written above.
        with temporary.open('r+b') as stream:
            stream.flush()
            os.fsync(stream.fileno())
        stage = '최종파일 등록'
        if os.name == 'nt':
            # Windows rename refuses an existing destination, including races.
            # Both files are in the same folder; hard-link support is not needed.
            os.rename(temporary, target)
        else:
            os.link(temporary, target)
    except FileExistsError as error:
        raise UpdateError(f'이미 존재하는 파일은 덮어쓰지 않습니다: {target}') from error
    except OSError as error:
        code = (f'WinError {error.winerror}' if getattr(error, 'winerror', None) is not None
                else f'errno {error.errno}')
        raise UpdateError(f'프로필 파일을 안전하게 저장하지 못했습니다: {target}\n'
                          f'실패 단계: {stage} ({code})') from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _create_database(target: Path) -> None:
    def write(temporary):
        connection = sqlite3.connect(temporary)
        try:
            connection.execute('PRAGMA user_version=0')
            connection.commit()
        finally:
            connection.close()
    _publish_new_file(target, write)


def prepare_profile(role: str, legacy_dir: Path | None = None,
                    shared_dir: Path | None = None, allow_new: bool = False) -> Path:
    role = validate_role(role)
    profile = profile_dir(role)
    target = profile / 'config' / f'config_{role.lower()}.ini'
    if target.exists():
        return _prepare_profile(role, legacy_dir, shared_dir, allow_new)
    # Setup can run before the supervisor acquires launcher.lock. A separate
    # stable lock serializes DB creation/publication/rollback across installers.
    with profile_lock(profile / 'setup'):
        return _prepare_profile(role, legacy_dir, shared_dir, allow_new)


def _prepare_profile(role: str, legacy_dir: Path | None = None,
                     shared_dir: Path | None = None, allow_new: bool = False) -> Path:
    """Prepare stable settings, retaining the original database at its location.

    Existing initialized settings are never rewritten. A fresh administrative database is
    created only for an explicit allow_new request; VIEWER always needs an
    existing database. No source database, INI or backup is copied or renamed.
    """
    role = validate_role(role)
    config_dir = profile_dir(role) / 'config'
    target = config_dir / f'config_{role.lower()}.ini'
    if target.exists():
        if not target.is_file() or target.is_symlink():
            raise UpdateError('프로필 설정 파일의 형식이 올바르지 않습니다.')
        return config_dir
    if legacy_dir is not None and shared_dir is not None:
        raise UpdateError('기존 프로그램 폴더와 데이터 폴더 중 하나만 선택하세요.')
    if allow_new and (role == 'VIEWER' or legacy_dir is not None or shared_dir is not None):
        raise UpdateError('[새 데이터 만들기]는 관리자용 첫 설정에서만 고를 수 있습니다.')
    config = configparser.ConfigParser(interpolation=None)
    if legacy_dir is not None:
        legacy = _directory(Path(legacy_dir), '기존 프로그램 폴더')
        candidates = legacy_ini_names(role)
        source = next((legacy / name for name in candidates if (legacy / name).is_file()), None)
        if source is None:
            raise UpdateError(f'기존 설정 파일을 찾을 수 없습니다: {", ".join(candidates)}')
        config = _read_ini(source)
        if role == 'MASTER' and source.name != target.name:
            config.set('NETWORK', 'server_port', str(DEFAULT_PORTS[role]))
        configured = config.get('NETWORK', 'shared_folder_path', fallback='').strip()
        data_dir = Path(configured) if configured else legacy
        if not data_dir.is_absolute():
            data_dir = legacy / data_dir
        data_dir = Path(os.path.abspath(data_dir))
        _existing_db(data_dir)
    elif shared_dir is not None:
        data_dir = _directory(Path(shared_dir), '데이터 폴더')
        _existing_db(data_dir)
        config['NETWORK'] = {}
    elif allow_new:
        data_dir = config_dir
        config['NETWORK'] = {}
    else:
        raise UpdateError('기존 프로그램/데이터 폴더를 선택하세요. 처음 도입하는 관리자용이면 [새 데이터 만들기]를 고르세요.')
    config.set('NETWORK', 'shared_folder_path', str(data_dir))
    defaults = {'server_port': str(DEFAULT_PORTS[role]),
                'db_timeout_seconds': '30.0', 'bind_host': '127.0.0.1',
                'admin_password': '', 'open_browser': 'true', 'tray_icon': 'true'}
    for key, value in defaults.items():
        if not config.has_option('NETWORK', key):
            config.set('NETWORK', key, value)
    contents = io.StringIO()
    config.write(contents)
    payload = contents.getvalue().encode('utf-8')
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    database = config_dir / DB_NAME
    created = None
    try:
        if allow_new:
            _create_database(database)
            created = (database.stat(), sha256_file(database))
        _publish_new_file(target, lambda temporary: temporary.write_bytes(payload))
    except BaseException:
        if created is not None and not os.path.lexists(target):
            try:
                original, digest = created
                current = database.lstat()
                # An externally adopted, replaced or edited DB is no longer
                # ours to remove, even when setup itself failed or was cancelled.
                if (not database.is_symlink() and
                        (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) ==
                        (original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns) and
                        not any(Path(str(database) + suffix).exists() for suffix in ('-journal', '-wal', '-shm')) and
                        sha256_file(database) == digest):
                    database.unlink()
            except (OSError, UpdateError):
                pass  # Retain uncertain files; never mask the original setup error.
        raise
    return config_dir


# ------------------------------------------------------------------ 화면에서 바꾸는 자주 쓰는 설정

EDITABLE_SETTINGS = ('admin_password', 'server_port', 'allowed_hosts', 'backup_mirror_dir')
_HOST_TOKEN = __import__('re').compile(r'[A-Za-z0-9](?:[A-Za-z0-9.-]{0,252})')


def _validate_setting(key: str, value: str) -> str:
    import ntpath
    import re
    if not isinstance(value, str) or any(c in value for c in '\r\n\x00'):
        raise UpdateError('설정 값에 줄바꿈을 넣을 수 없습니다.')
    if key == 'admin_password':
        if value != value.strip():
            raise UpdateError('관리자 암호의 앞뒤에는 공백을 넣을 수 없습니다.')
        if len(value) > 128:
            raise UpdateError('관리자 암호는 128자 이하로 정해 주세요.')
        return value
    if key == 'server_port':
        if not re.fullmatch(r'\d{1,5}', value.strip()) or not 1024 <= int(value) <= 65535:
            raise UpdateError('포트는 1024~65535 사이의 숫자로 입력하세요.')
        return str(int(value))
    if key == 'allowed_hosts':
        items = [item.strip() for item in value.split(',') if item.strip()]
        if len(items) > 20 or any(item != '*' and not _HOST_TOKEN.fullmatch(item) for item in items):
            raise UpdateError('허용할 접속 이름은 영문·숫자·점·하이픈으로 된 이름을 쉼표로 구분해 적어 주세요.')
        return ', '.join(items)
    if key == 'backup_mirror_dir':
        value = value.strip()
        if value and not (ntpath.isabs(value) or os.path.isabs(value)):
            raise UpdateError('추가 백업 폴더는 전체 경로로 입력하세요. 예: D:\\OnAir_Backup')
        return value
    raise UpdateError('이 화면에서 바꿀 수 없는 설정입니다.')


def _read_ini_text(path: Path) -> str:
    raw = Path(path).read_bytes()
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise UpdateError('설정 파일을 읽지 못했습니다(UTF-8 또는 CP949 가 아님).')


def update_ini_settings(path: Path, changes: dict) -> Path:
    """[NETWORK] 의 자주 쓰는 값만 바꾼다. 주석과 다른 줄은 그대로 두고, 원본은 .bak-시각 으로 남긴다.

    값을 바꾼 뒤 다시 읽어 그대로 들어갔는지 확인하고, 실패하면 원래 파일을 둔다. UTF-8 로 저장한다.
    돌려주는 값은 백업 사본 경로. 프로그램을 다시 시작해야 적용된다.
    """
    import datetime
    import re
    path = Path(path)
    if not path.is_file() or path.is_symlink():
        raise UpdateError('설정 파일을 찾을 수 없습니다.')
    clean = {}
    for key, value in changes.items():
        if key not in EDITABLE_SETTINGS:
            raise UpdateError('이 화면에서 바꿀 수 없는 설정입니다.')
        clean[key] = _validate_setting(key, value)
    if not clean:
        raise UpdateError('바꿀 값이 없습니다.')
    text = _read_ini_text(path)
    newline = '\r\n' if '\r\n' in text else '\n'
    lines = text.splitlines()
    section, start, end = None, None, len(lines)
    for index, line in enumerate(lines):
        match = re.match(r'\s*\[([^\]]+)\]\s*$', line)
        if match:
            if section == 'NETWORK' and end == len(lines):
                end = index
            section = match.group(1).strip()
            if section == 'NETWORK' and start is None:
                start = index
    if start is None:
        lines += ['', '[NETWORK]']
        start, end = len(lines) - 1, len(lines)
    remaining = dict(clean)
    for index in range(start + 1, end):
        match = re.match(r'\s*([A-Za-z_][A-Za-z0-9_]*)\s*[=:]', lines[index])
        if match and match.group(1).lower() in remaining:
            key = match.group(1).lower()
            lines[index] = f'{key} = {remaining.pop(key)}'
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines[insert_at:insert_at] = [f'{key} = {value}' for key, value in remaining.items()]
    payload = (newline.join(lines) + newline).encode('utf-8')
    check = configparser.ConfigParser(interpolation=None)
    try:
        check.read_string(payload.decode('utf-8'))
    except configparser.Error as exc:
        raise UpdateError(f'설정 파일 형식이 깨져 있어 바꾸지 않았습니다: {exc}') from exc
    for key, value in clean.items():
        if check.get('NETWORK', key, fallback=None) != value:
            raise UpdateError(f'{key} 값을 안전하게 쓸 수 없어 바꾸지 않았습니다.')
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = path.with_name(f'{path.name}.bak-{stamp}')
    number = 1
    while os.path.lexists(backup):
        number += 1
        backup = path.with_name(f'{path.name}.bak-{stamp}-{number}')
    backup.write_bytes(path.read_bytes())
    descriptor, name = tempfile.mkstemp(prefix='.' + path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return backup


def read_ini_settings(path: Path) -> dict:
    """화면에 보여 줄 현재 값(암호는 설정 여부만)."""
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(_read_ini_text(path))
    get = lambda key: config.get('NETWORK', key, fallback='').strip()  # noqa: E731
    return {'admin_password_set': bool(get('admin_password')), 'server_port': get('server_port'),
            'allowed_hosts': get('allowed_hosts'), 'backup_mirror_dir': get('backup_mirror_dir')}
