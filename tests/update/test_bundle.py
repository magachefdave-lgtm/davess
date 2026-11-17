"""Role gate regression. Legacy extend_pyz test retired with the template builder.
Clean-source staging/archive contract is covered in tests/source_build instead.
"""
import importlib.util
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]


def test_viewer_entry_cannot_open_publisher(monkeypatch):
    s = importlib.util.spec_from_file_location('onair_entry', ROOT / 'entrypoint.py')
    entry = importlib.util.module_from_spec(s)
    s.loader.exec_module(entry)
    monkeypatch.setattr(entry, 'BUILD_ROLE', 'VIEWER')
    monkeypatch.setattr(sys, 'argv', ['WLR_OnAir_Viewer.exe', '--publish'])
    monkeypatch.setenv('SMART_DUTY_MODE', 'ADMIN')
    import pytest
    with pytest.raises(RuntimeError, match='조회용'):
        entry.main()
