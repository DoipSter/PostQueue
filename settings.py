"""PostQueue: data folder, settings file, secret protection, start at sign-in and the single-instance lock.
Works on Windows and macOS; no Qt in this module.

Windows: secrets are protected with DPAPI (tied to the user account); start at sign-in is the Run entry the MSI
writes, turned off through StartupApproved as Task Manager does.
macOS: secrets go to the login keychain through the `security` tool; start at sign-in is a LaunchAgent."""
import base64
import contextlib
import datetime as dt
import json
import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path

APP_KEY = 'PostQueue'
BUNDLE_ID = 'com.postqueue.app'
SECRET_FIELDS = ('access_token', 'app_secret', 'cloudinary_api_secret')
RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN_VALUE = 'PostQueue Startup'                      # the same name the MSI writes (installer\PostQueue.wxs)
APPROVED_KEY = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'
DEFAULTS = {
    # Instagram account
    'login_kind': 'facebook',          # facebook (account linked to a Facebook Page) | instagram (Instagram Login)
    'app_id': '', 'app_secret': '', 'access_token': '', 'token_expires': '',
    'api_host': 'graph.facebook.com', 'api_version': 'v25.0', 'ig_user_id': '', 'ig_username': '',
    # Media hosting (Instagram fetches media from a public URL)
    'hosting': 'cloudinary', 'cloudinary_cloud': '', 'cloudinary_api_key': '', 'cloudinary_api_secret': '',
    # Behaviour
    # start_with_windows: None = not chosen in the app (the installer's entry decides); True/False = the user's choice
    'start_with_windows': None, 'close_to_tray': True, 'grace_hours': 12, 'fit_mode': 'crop',
    'default_hour': 9, 'default_minute': 0, 'paused': False, 'tray_hint_shown': False, 'setup_done': False, 'window': None,
}

MAC = sys.platform == 'darwin'
WINDOWS = os.name == 'nt'


def data_dir():
    """Where the queue, media copies, settings and log live. POSTQUEUE_DATA overrides it (tests, portable use)."""
    override = os.environ.get('POSTQUEUE_DATA')
    if override: return Path(override)
    if MAC: return Path.home() / 'Library' / 'Application Support' / APP_KEY
    base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
    return Path(base) / APP_KEY


def atomic_json(path, data):
    """Write JSON so that a crash or power loss leaves either the old file or the new one, never a partial file."""
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f: json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(OSError): os.unlink(tmp)


# --- Secret protection -------------------------------------------------------------------------------------------
def _dpapi(data, protect):
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32')
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(BLOB), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(BLOB)]
    fn.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    buf = ctypes.create_string_buffer(data, len(data))
    inp, out = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), BLOB()
    if not fn(ctypes.byref(inp), None, None, None, None, 1, ctypes.byref(out)):   # 1 = CRYPTPROTECT_UI_FORBIDDEN
        raise ctypes.WinError(ctypes.get_last_error())
    try: return ctypes.string_at(out.pbData, out.cbData)
    finally: kernel32.LocalFree(out.pbData)


def _keychain(args, secret=None):
    """Run macOS's `security` tool. Returns stdout, or raises OSError with its message."""
    result = subprocess.run(['security', *args] + (['-w', secret] if secret is not None else []), capture_output=True, text=True)
    if result.returncode: raise OSError(result.stderr.strip() or f'security exited with {result.returncode}')
    return result.stdout.strip()


def protect(text, name='secret'):
    """Make a secret safe for the settings file. Windows: DPAPI, tied to this user account. macOS: the login
    keychain, with only a marker in the file. Elsewhere: encoded only, which the prefix makes visible."""
    if not text: return ''
    if WINDOWS: return 'dpapi:' + base64.b64encode(_dpapi(text.encode('utf-8'), True)).decode('ascii')
    if MAC:
        _keychain(['add-generic-password', '-U', '-a', APP_KEY, '-s', f'{APP_KEY} {name}'], secret=text)
        return f'keychain:{name}'
    return 'plain:' + base64.b64encode(text.encode('utf-8')).decode('ascii')


def unprotect(text, name='secret'):
    if not text: return ''
    kind, _, body = text.partition(':')
    if kind == 'dpapi': return _dpapi(base64.b64decode(body), False).decode('utf-8')
    if kind == 'keychain': return _keychain(['find-generic-password', '-a', APP_KEY, '-s', f'{APP_KEY} {body}', '-w'])
    if kind == 'plain': return base64.b64decode(body).decode('utf-8')
    return text   # an unprotected value written by hand


def forget_secret(name):
    """Remove a secret from the macOS keychain (Windows keeps nothing outside the settings file)."""
    if MAC:
        with contextlib.suppress(OSError): _keychain(['delete-generic-password', '-a', APP_KEY, '-s', f'{APP_KEY} {name}'])


class Settings(dict):
    """The settings file. Secrets are protected on disk and plain in memory."""
    def __init__(self, path):
        super().__init__(DEFAULTS); self.path = Path(path); self.load()

    def load(self):
        if not self.path.exists(): return
        try: data = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError): return
        for key in SECRET_FIELDS:
            try: data[key] = unprotect(data.get(key, ''), key)
            except Exception: data[key] = ''   # protected by another user account or machine: must be entered again
        self.update({k: v for k, v in data.items() if k in DEFAULTS})

    def save(self):
        data = dict(self)
        for key in SECRET_FIELDS:
            data[key] = protect(data.get(key, ''), key)
            if not data[key]: forget_secret(key)
        atomic_json(self.path, data)

    def connected(self): return bool(self.get('access_token') and self.get('ig_user_id'))

    def hosting_ready(self):
        return self.get('hosting') == 'cloudinary' and all(self.get(k) for k in ('cloudinary_cloud', 'cloudinary_api_key', 'cloudinary_api_secret'))

    def ready(self): return self.connected() and self.hosting_ready()

    def token_expiry(self):
        """When the Instagram token expires, as an aware datetime, or None when unknown."""
        with contextlib.suppress(ValueError, TypeError):
            if self.get('token_expires'): return dt.datetime.fromisoformat(self['token_expires'])
        return None


# --- Start at sign-in --------------------------------------------------------------------------------------------
def startup_program():
    """The program the sign-in entry starts: the packaged EXE or app, or pythonw with the script."""
    if getattr(sys, 'frozen', False): return [sys.executable]
    pyw = Path(sys.executable).with_name('pythonw.exe')
    script = Path(__file__).resolve().with_name('PostQueue.pyw')
    return [str(pyw if pyw.exists() else sys.executable), str(script)]


def startup_command():
    return ' '.join(f'"{part}"' for part in startup_program()) + ' --minimized'


def launch_agent_path(home=None):
    return Path(home or Path.home()) / 'Library' / 'LaunchAgents' / f'{BUNDLE_ID}.plist'


def set_start_at_login(on, command=None, home=None):
    """Turn starting at sign-in on or off.

    Windows, the way Task Manager does it: the Run entry stays (the MSI owns it when the app was installed, and
    removes it on uninstall) and StartupApproved records the choice. The Run entry is only written when it is
    missing, as with the portable EXE. It is never deleted: Windows Installer stops re-creating a Run value whose
    name was deleted by hand before an uninstall, so a later install would silently lose the entry.
    macOS: a LaunchAgent in ~/Library/LaunchAgents, written or removed.
    Returns False when neither mechanism is available."""
    if MAC or home is not None:
        plist = launch_agent_path(home)
        if on:
            plist.parent.mkdir(parents=True, exist_ok=True)
            with plist.open('wb') as f:
                plistlib.dump({'Label': BUNDLE_ID, 'ProgramArguments': [*startup_program(), '--minimized'], 'RunAtLoad': True, 'ProcessType': 'Interactive'}, f)
        else:
            with contextlib.suppress(OSError): plist.unlink()
        return True
    try: import winreg
    except ImportError: return False
    if on:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            try: winreg.QueryValueEx(key, RUN_VALUE)
            except FileNotFoundError: winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, command or startup_command())
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:   # DWORD state (2 on, 3 off) + FILETIME
        winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_BINARY, bytes([2 if on else 3]) + bytes(11))
    return True


def starts_at_login(home=None):
    """True when the sign-in entry exists and is not disabled."""
    if MAC or home is not None: return launch_agent_path(home).exists()
    try: import winreg
    except ImportError: return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if not winreg.QueryValueEx(key, RUN_VALUE)[0]: return False
    except OSError: return False
    with contextlib.suppress(OSError):
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, APPROVED_KEY) as key:
            state = winreg.QueryValueEx(key, RUN_VALUE)[0]
            if state and state[0] == 3: return False
    return True


# The names the first version used; kept so nothing else has to change at once.
set_start_with_windows = set_start_at_login
starts_with_windows = starts_at_login


# --- Single instance ---------------------------------------------------------------------------------------------
def process_alive(pid):
    if not pid: return False
    if not WINDOWS:
        try: os.kill(int(pid), 0); return True
        except ProcessLookupError: return False
        except PermissionError: return True
        except OSError: return False
    import ctypes
    kernel32 = ctypes.WinDLL('kernel32')
    handle = kernel32.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle: return False
    try:
        code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
    finally: kernel32.CloseHandle(handle)


class InstanceLock:
    """One PostQueue per user. The lock file records the window's process; a second launch asks the first to show."""
    def __init__(self, path):
        self.path = Path(path); self.held = False

    def other_pid(self):
        with contextlib.suppress(OSError, ValueError, AttributeError):
            pid = int(json.loads(self.path.read_text(encoding='utf-8')).get('pid', 0))
            if pid != os.getpid() and process_alive(pid): return pid
        return None

    def acquire(self):
        atomic_json(self.path, {'pid': os.getpid(), 'started': dt.datetime.now().astimezone().isoformat(timespec='seconds')})
        self.held = True

    def release(self):
        if self.held:
            with contextlib.suppress(OSError): self.path.unlink()
            self.held = False

    def request_show(self):
        """Ask the running instance to show its window (it watches for this file)."""
        with contextlib.suppress(OSError): self.path.with_name('show.request').write_text('show', encoding='utf-8')

    def take_show_request(self):
        marker = self.path.with_name('show.request')
        if marker.exists():
            with contextlib.suppress(OSError): marker.unlink()
            return True
        return False
