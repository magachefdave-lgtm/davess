"""Check the Windows DPAPI vault and delivery of lock messages to the real Master window.

This uses a temporary profile. It sends messages only to its own hidden window,
so the runner's interactive desktop and other user sessions stay available.
"""
import configparser
import ctypes
import logging
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def main():
    if sys.platform != 'win32':
        print('SKIP Windows DPAPI and WTS require Windows.')
        return
    from ctypes import wintypes

    sys.path.insert(0, str(ROOT))
    from backend.request_access_store import ReceiptVault

    with tempfile.TemporaryDirectory(prefix='onair-master-native-') as temp:
        base = Path(temp)
        share = base / 'data'
        share.mkdir()
        sqlite3.connect(share / 'smart_duty.db').close()
        profile = base / 'Master'
        config = profile / 'config'
        config.mkdir(parents=True)
        cfg = configparser.ConfigParser()
        cfg['NETWORK'] = dict(shared_folder_path=str(share), bind_host='127.0.0.1',
                              open_browser='false', tray_icon='false')
        with (config / 'config_master.ini').open('w', encoding='utf-8') as stream:
            cfg.write(stream)
        os.environ.update(SMART_DUTY_MODE='MASTER', WLR_CONFIG_DIR=str(config))
        try:
            from backend import main as app
            vault = app.REQUEST_ACCESS.vault()
            device = vault.device_id
            blob = vault.path.read_bytes()
            assert blob.startswith(b'WLRDP1\0') and device.encode() not in blob
            assert ReceiptVault(profile).device_id == device
            print('PASS Windows User DPAPI device vault persisted and reopened.', flush=True)

            authority = app.MASTER
            assert not authority._monitor_ready
            tray = app.TrayIcon('Temporary Master WTS check', lambda: None, lambda: None, lambda: None)
            try:
                tray.create()
                assert tray.hwnd and tray._wts and authority._monitor_ready and not tray._added
                send = tray.user32.SendMessageW
                send.argtypes = [wintypes.HWND, wintypes.UINT, tray.WPARAM, tray.LPARAM]
                send.restype = ctypes.c_ssize_t
                send(tray.hwnd, 0x02B1, 7, 0)  # WM_WTSSESSION_CHANGE / WTS_SESSION_LOCK
                assert authority._desktop_locked and not authority._desktop_available()
                send(tray.hwnd, 0x02B1, 8, 0)  # WM_WTSSESSION_CHANGE / WTS_SESSION_UNLOCK
                assert not authority._desktop_locked
            finally:
                tray.remove()
            assert not tray.hwnd and not tray._wts and not authority._monitor_ready
            print('PASS OS WTS registration, native lock/unlock message dispatch, and monitor cleanup.', flush=True)
        finally:
            logging.shutdown()


if __name__ == '__main__':
    main()
