"""Hash-checked reuse, including real pip through a local test index."""
import hashlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load():
    spec = importlib.util.spec_from_file_location('bootstrap_cache', ROOT / 'build_windows.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup(tmp_path, monkeypatch):
    m = load(); root = tmp_path / 'source'; root.mkdir(); work = tmp_path / 'short'; work.mkdir()
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local data'))
    wheels = {'alpha-1.0-py3-none-any.whl': b'alpha valid content', 'beta-2.0-py3-none-any.whl': b'beta valid content'}
    lock = root / 'requirements-build-py312.txt'
    lock.write_text(''.join(f'{name.split("-")[0]}=={name.split("-")[1]} --hash=sha256:{hashlib.sha256(data).hexdigest()}\n' for name,data in wheels.items()))
    return m, root, work, lock, wheels


def fill_cache(m, lock, wheels):
    cache = m.wheel_cache_dir(lock); cache.mkdir(parents=True)
    for name,data in wheels.items(): (cache/name).write_bytes(data)
    return cache


def fake_download(m, monkeypatch, wheels, commands):
    def run(command, log, cwd, **kwargs):
        commands.append((command, kwargs))
        requested = Path(command[command.index('-r')+1]).read_text()
        destination = Path(command[command.index('--dest')+1])
        for name,data in wheels.items():
            if name.split('-')[0]+'==' in requested: (destination/name).write_bytes(data)
    monkeypatch.setattr(m,'run_logged',run)


def test_complete_verified_cache_never_contacts_index(setup, monkeypatch):
    m,root,work,lock,wheels=setup; cache=fill_cache(m,lock,wheels)
    monkeypatch.setattr(m,'run_logged',lambda *a,**kw:pytest.fail('cache hit must stay offline'))
    assert m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)==cache


def test_first_download_is_reused_by_another_source_folder(setup, monkeypatch, tmp_path):
    m,root,work,lock,wheels=setup; calls=[]; fake_download(m,monkeypatch,wheels,calls)
    house=m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    assert len(calls)==1 and all((house/n).read_bytes()==data for n,data in wheels.items())
    newer=tmp_path/'new version'; newer.mkdir(); newlock=newer/lock.name; newlock.write_bytes(lock.read_bytes())
    another=tmp_path/'second build'; another.mkdir()
    assert m.prepare_wheelhouse(sys.executable,newlock,another,io.StringIO(),newer)==m.wheel_cache_dir(lock)
    assert len(calls)==1


def test_partial_cache_downloads_only_missing_or_corrupt_wheels(setup, monkeypatch):
    m,root,work,lock,wheels=setup; cache=fill_cache(m,lock,wheels)
    (cache/'beta-2.0-py3-none-any.whl').write_bytes(b'corrupted')
    calls=[]; fake_download(m,monkeypatch,wheels,calls)
    house=m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    requested=Path(calls[0][0][calls[0][0].index('-r')+1]).read_text()
    assert 'beta==' in requested and 'alpha==' not in requested
    assert all((house/n).read_bytes()==data for n,data in wheels.items())
    assert (cache/'beta-2.0-py3-none-any.whl').read_bytes()==wheels['beta-2.0-py3-none-any.whl']


def test_bad_download_is_never_published_as_valid_cache(setup, monkeypatch):
    m,root,work,lock,wheels=setup; bad={n:b'wrong bytes' for n in wheels}; fake_download(m,monkeypatch,bad,[])
    with pytest.raises(ValueError,match='패키지'):
        m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    assert not list(m.wheel_cache_dir(lock).glob('*.whl'))


def test_failed_download_leaves_previous_valid_cache_intact(setup,monkeypatch):
    m,root,work,lock,wheels=setup; name=next(iter(wheels)); cache=fill_cache(m,lock,{name:wheels[name]})
    def fail(*a,**kw):raise subprocess.CalledProcessError(1,a[0],output='offline')
    monkeypatch.setattr(m,'run_logged',fail)
    with pytest.raises(subprocess.CalledProcessError):m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    assert list(cache.iterdir())==[cache/name] and (cache/name).read_bytes()==wheels[name]


def test_explicit_offline_wheels_take_priority_and_empty_folder_still_errors(setup,monkeypatch):
    m,root,work,lock,wheels=setup; house=root/'wheels';house.mkdir()
    monkeypatch.setattr(m,'run_logged',lambda *a,**kw:pytest.fail('explicit wheels must stay offline'))
    with pytest.raises(FileNotFoundError):m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    (house/next(iter(wheels))).write_bytes(b'explicit wheel verified later by pip')
    assert m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)==house


def test_cache_unavailable_still_allows_current_build(setup,monkeypatch):
    m,root,work,lock,wheels=setup; cache=m.wheel_cache_dir(lock);cache.parent.mkdir(parents=True);cache.write_text('not a directory')
    fake_download(m,monkeypatch,wheels,[])
    house=m.prepare_wheelhouse(sys.executable,lock,work,io.StringIO(),root)
    assert all((house/n).read_bytes()==data for n,data in wheels.items())
    assert cache.read_text()=='not a directory'


def test_dependency_or_abi_lock_change_uses_separate_cache(setup):
    m,root,work,lock,wheels=setup; first=m.wheel_cache_dir(lock)
    lock.write_text(lock.read_text().replace('alpha==1.0','alpha==1.1'))
    assert m.wheel_cache_dir(lock)!=first
    other=root/'requirements-build-py313.txt';other.write_bytes(lock.read_bytes())
    assert m.wheel_cache_dir(other)!=m.wheel_cache_dir(lock)


def test_download_and_install_skip_unneeded_resolution_and_bytecode_work(setup):
    m,root,work,lock,wheels=setup
    download=m.download_command(sys.executable,lock,work)
    assert '--no-deps' in download and '--require-hashes' in download and '--only-binary=:all:' in download
    assert download[download.index('--retries')+1]=='1' and download[download.index('--timeout')+1]=='20'
    install=m.install_command(sys.executable,lock,work)
    assert '--no-deps' in install and '--no-compile' in install and '--no-index' in install
    assert '--require-hashes' in install


def test_build_locks_do_not_download_a_replacement_for_bundled_pip():
    for path in ROOT.glob('requirements-build*.txt'):
        assert not any(line.strip().startswith('pip==') for line in path.read_text().splitlines()),path.name


def test_real_pip_download_then_two_fresh_offline_environments(tmp_path, monkeypatch):
    import functools
    import http.server
    import threading
    import zipfile
    m=load(); monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'cache'))
    root=tmp_path/'source';root.mkdir();repo=tmp_path/'repo';(repo/'packages').mkdir(parents=True)
    rows=[]
    for name,version,code,dependency in [('alpha','1.0','from beta import VALUE\n','Requires-Dist: beta==2.0\n'),('beta','2.0','VALUE = "installed from verified wheel"\n','')]:
        wheel=repo/'packages'/f'{name}-{version}-py3-none-any.whl';info=f'{name}-{version}.dist-info'
        with zipfile.ZipFile(wheel,'w') as z:
            z.writestr(f'{name}/__init__.py',code)
            z.writestr(f'{info}/METADATA',f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n{dependency}')
            z.writestr(f'{info}/WHEEL','Wheel-Version: 1.0\nGenerator: OnAir regression\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
            z.writestr(f'{info}/RECORD','')
        digest=hashlib.sha256(wheel.read_bytes()).hexdigest();rows.append(f'{name}=={version} --hash=sha256:{digest}')
        index=repo/'simple'/name;index.mkdir(parents=True)
        (index/'index.html').write_text(f'<a href="../../packages/{wheel.name}#sha256={digest}">{wheel.name}</a>')
    lock=root/'requirements-build-py312.txt';lock.write_text('\n'.join(rows)+'\n')
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(repo)))
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    actual_run=m.run_logged;downloads=[]
    def local_index(command,*args,**kwargs):
        command=list(command)
        if 'download' in command:
            downloads.append(command.copy());command[command.index('--index-url')+1]=f'http://127.0.0.1:{server.server_port}/simple'
        return actual_run(command,*args,**kwargs)
    monkeypatch.setattr(m,'run_logged',local_index)
    try:
        for number in range(2):
            work=tmp_path/f'build{number}';work.mkdir();log=io.StringIO()
            python=m.create_build_environment(work/'env',log,root)
            pip_before=subprocess.check_output([str(python),'-m','pip','--version'])
            house=m.prepare_wheelhouse(python,lock,work,log,root)
            m.run_logged(m.install_command(python,lock,house),log,root,timeout=30)
            m.run_logged([str(python),'-m','pip','check'],log,root,timeout=30)
            assert not list((work/'env').rglob('alpha/__pycache__'))
            result=subprocess.check_output([str(python),'-I','-c','import alpha; print(alpha.VALUE)'],text=True)
            assert 'installed from verified wheel' in result
            assert subprocess.check_output([str(python),'-m','pip','--version'])==pip_before
            if number==0:
                server.shutdown();server.server_close();thread.join(timeout=5)
        assert len(downloads)==1,'Second fresh environment must need no network'
    finally:
        server.shutdown();server.server_close()
