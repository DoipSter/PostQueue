"""PostQueue: the Qt interface. The queue on the left, the selected post on the right, a tray (menu bar) icon, the
timer that publishes posts when they are due, a first-run wizard that asks one thing per screen, and the settings
dialog. Network work runs in threads so the window stays responsive. Runs on Windows and macOS."""
import contextlib
import datetime as dt
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QDateTime, QEvent, QPointF, QSize, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSizePolicy, QSpinBox, QSplitter, QStyle,
                               QSystemTrayIcon, QTabWidget, QTimeEdit, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
                               QWizard, QWizardPage)

import engine as eng
import hosting
import instagram as ig
import settings as cfg
from version import PRODUCT, VERSION

TITLE = f'{PRODUCT} {VERSION}'
FILE_FILTER = 'Photos and videos (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.mp4 *.mov *.m4v);;All files (*)'
DATE_FORMAT = 'ddd d MMM yyyy   h:mm AP'
CHECK_SECONDS = 30
STATUS_COLOR = {'queued': None, 'posting': '#2b6cb0', 'posted': '#2f855a', 'failed': '#c53030', 'missed': '#b7791f'}
TRAY_NAME = 'the menu bar' if cfg.MAC else 'the notification area'
OK_STYLE, BAD_STYLE, MUTED_STYLE = 'color: #2f855a;', 'color: #c53030;', 'color: #666;'


def resource(name):
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
    return base / name


def make_app():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(PRODUCT); app.setApplicationVersion(VERSION); app.setOrganizationName(PRODUCT)
    icon = resource('assets/app.icns' if cfg.MAC else 'assets/app.ico')
    if icon.exists(): app.setWindowIcon(QIcon(str(icon)))
    app.setQuitOnLastWindowClosed(False)
    return app


def to_qdt(when): return QDateTime(when.astimezone().replace(tzinfo=None))
def from_qdt(qdt): return qdt.toPython().astimezone()


def thumbnail(path, kind, size=72):
    """A small preview. Photos are read with Pillow (so the Qt image plugins are not needed); videos get a film tile."""
    if kind == 'image':
        with contextlib.suppress(Exception):
            from PIL import Image, ImageOps
            with Image.open(path) as opened:
                im = ImageOps.exif_transpose(opened); im.thumbnail((size, size)); im = im.convert('RGBA')
                data = im.tobytes('raw', 'RGBA')
                return QPixmap.fromImage(QImage(data, im.width, im.height, im.width * 4, QImage.Format_RGBA8888).copy())
    pm = QPixmap(size, size); pm.fill(Qt.transparent)
    painter = QPainter(pm); painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor('#3a3a3f')); painter.setPen(Qt.NoPen); painter.drawRoundedRect(2, int(size * 0.2), size - 4, int(size * 0.6), 6, 6)
    painter.setBrush(QColor('#f0f0f0'))
    cx, cy, r = size * 0.5, size * 0.5, size * 0.16
    painter.drawPolygon([QPointF(cx - r * 0.8, cy - r), QPointF(cx - r * 0.8, cy + r), QPointF(cx + r, cy)])
    painter.end()
    return pm


class PreviewLabel(QLabel):
    """Shows a pixmap scaled to whatever size the layout gives the label, without forcing a minimum size."""
    def __init__(self):
        super().__init__(); self.source = None; self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored); self.setMinimumSize(240, 180)

    def set_source(self, pixmap):
        self.source = pixmap; self.rescale()

    def rescale(self):
        if self.source is None: self.clear(); return
        self.setPixmap(self.source.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event); self.rescale()


class Task(QThread):
    """Run a function off the interface thread; result or error comes back as a signal."""
    done = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, describe=None, parent=None):
        super().__init__(parent); self.fn, self.describe = fn, describe or eng.Engine.describe

    def run(self):
        try: self.done.emit(self.fn())
        except Exception as exc: self.failed.emit(self.describe(exc))


class PublishWorker(QThread):
    progress = Signal(str, float, str)
    finished_post = Signal(str)

    def __init__(self, engine, post, instagram, provider, parent=None):
        super().__init__(parent); self.engine, self.post, self.instagram, self.provider = engine, post, instagram, provider

    def run(self):
        self.engine.publish(self.post, self.instagram, self.provider, lambda f, t: self.progress.emit(self.post.id, f, t))
        self.finished_post.emit(self.post.id)


def intro_label(html):
    label = QLabel(html); label.setWordWrap(True); label.setOpenExternalLinks(True); label.setTextFormat(Qt.RichText); return label


# --- Setup panels (shared by the first-run wizard and the settings dialog) -------------------------------------------
class InstagramPanel(QWidget):
    """Connect the Instagram account: sign in with the browser, or paste a token. Emits `changed` when the connection
    state changes, so the wizard can enable Next."""
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.task = None; self.cancelled = False
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro_label('Posts go through Meta\'s official Instagram API. It needs an <b>Instagram professional account</b> (Business or Creator) '
                                     'and a <b>Meta app</b> you create once at <a href="https://developers.facebook.com/apps/">developers.facebook.com/apps</a>. '
                                     'The user guide walks through it.'))
        group = QGroupBox('Sign in with the browser'); form = QFormLayout(group); layout.addWidget(group)
        self.kind = QComboBox(); self.kind.addItem('Facebook Login (Instagram account linked to a Facebook Page)', 'facebook'); self.kind.addItem('Instagram Login (Instagram account only)', 'instagram')
        self.kind.setCurrentIndex(max(0, self.kind.findData(self.settings.get('login_kind', 'facebook')))); form.addRow('Sign-in type', self.kind)
        self.app_id = QLineEdit(self.settings.get('app_id', '')); self.app_id.setPlaceholderText('From the Meta app dashboard'); form.addRow('Meta app ID', self.app_id)
        self.app_secret = QLineEdit(self.settings.get('app_secret', '')); self.app_secret.setEchoMode(QLineEdit.Password)
        self.app_secret.setPlaceholderText('Needed for a 60-day sign-in; Instagram Login always needs it'); form.addRow('Meta app secret', self.app_secret)
        hint = QLabel(f'Add <code>http://localhost:{ig.LOGIN_PORT}/</code> to the app\'s Valid OAuth Redirect URIs. Without the app secret, a Facebook sign-in lasts about two hours.')
        hint.setWordWrap(True); hint.setStyleSheet(MUTED_STYLE); form.addRow('', hint)
        row = QHBoxLayout(); self.signin_button = QPushButton('Sign in in the browser…'); self.signin_button.setObjectName('signin'); self.signin_button.clicked.connect(self.sign_in)
        self.cancel_button = QPushButton('Cancel'); self.cancel_button.clicked.connect(self.cancel_task); self.cancel_button.hide()
        row.addWidget(self.signin_button); row.addWidget(self.cancel_button); row.addStretch(1); form.addRow('', row)
        code_row = QHBoxLayout(); self.code_edit = QLineEdit(); self.code_edit.setPlaceholderText('If the browser ends on an error page, paste the code=… value from its address bar')
        code_button = QPushButton('Use code'); code_button.clicked.connect(self.use_code); code_row.addWidget(self.code_edit, 1); code_row.addWidget(code_button); form.addRow('', code_row)

        group2 = QGroupBox('Or paste an access token'); form2 = QFormLayout(group2); layout.addWidget(group2)
        self.token_edit = QLineEdit(); self.token_edit.setObjectName('token'); self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText('A user or system-user token with Instagram content publishing permission')
        row2 = QHBoxLayout(); row2.addWidget(self.token_edit, 1); check = QPushButton('Check token'); check.setObjectName('check_token'); check.clicked.connect(self.check_token); row2.addWidget(check)
        form2.addRow('Access token', row2)

        group3 = QGroupBox('Account'); form3 = QFormLayout(group3); layout.addWidget(group3)
        self.accounts = QComboBox(); self.accounts.setObjectName('accounts'); self.accounts.currentIndexChanged.connect(self.account_chosen); form3.addRow('Post as', self.accounts)
        self.status = QLabel(''); self.status.setWordWrap(True); self.status.setTextInteractionFlags(Qt.TextSelectableByMouse); form3.addRow('', self.status)
        self.disconnect_button = QPushButton('Disconnect'); self.disconnect_button.clicked.connect(self.disconnect); form3.addRow('', self.disconnect_button)
        self.show_account()

    def is_ready(self): return self.settings.connected()

    def show_account(self):
        self.accounts.blockSignals(True); self.accounts.clear()
        if self.settings.connected():
            self.accounts.addItem(f'@{self.settings.get("ig_username") or self.settings["ig_user_id"]}', {'id': self.settings['ig_user_id'], 'username': self.settings.get('ig_username', ''), 'host': self.settings.get('api_host')})
            expiry = self.settings.token_expiry()
            self.status.setText(f'Connected. {"The sign-in lasts until " + expiry.strftime("%d %b %Y") + "." if expiry else "The token has no known expiry."}'); self.status.setStyleSheet(OK_STYLE)
        else: self.status.setText('Not connected.'); self.status.setStyleSheet('')
        self.accounts.blockSignals(False); self.disconnect_button.setVisible(self.settings.connected()); self.changed.emit()

    def busy(self, text):
        self.signin_button.setEnabled(text is None); self.cancel_button.setVisible(text is not None)
        if text is not None: self.status.setText(text); self.status.setStyleSheet('')

    def run_task(self, fn, on_done, waiting):
        if self.task is not None: return
        self.cancelled = False; self.busy(waiting)
        self.task = Task(fn)   # kept alive by this attribute, not by a Qt parent: a parent would delete it under Python at exit
        def finish(): self.task = None; self.busy(None)
        def ok(result): finish(); on_done(result)
        def bad(message): finish(); self.status.setText(message); self.status.setStyleSheet(BAD_STYLE)
        self.task.done.connect(ok); self.task.failed.connect(bad); self.task.start()

    def cancel_task(self): self.cancelled = True

    def save_app(self):
        self.settings['login_kind'] = self.kind.currentData(); self.settings['app_id'] = self.app_id.text().strip(); self.settings['app_secret'] = self.app_secret.text().strip()
        self.settings.save()

    def sign_in(self):
        self.save_app(); kind, app_id, secret, version = self.kind.currentData(), self.app_id.text().strip(), self.app_secret.text().strip(), self.settings.get('api_version')
        self.run_task(lambda: ig.run_login(kind, app_id, secret, version, cancelled=lambda: self.cancelled), self.token_received,
                      'Waiting for the browser… approve the sign-in there, then come back here.')

    def use_code(self):
        self.save_app(); code = self.code_edit.text().strip()
        if not code: return
        kind, app_id, secret, version = self.kind.currentData(), self.app_id.text().strip(), self.app_secret.text().strip(), self.settings.get('api_version')
        redirect = f'http://localhost:{ig.LOGIN_PORT}/'
        def exchange():
            token, expires = ig.exchange_code(kind, app_id, secret, redirect, code, version)
            if secret:
                with contextlib.suppress(ig.InstagramError): token, expires = ig.long_lived(kind, app_id, secret, token, version)
            return token, expires, redirect
        self.run_task(exchange, self.token_received, 'Exchanging the code…')

    def token_received(self, result):
        token, expires, _ = result
        self.settings['access_token'] = token
        self.settings['token_expires'] = eng.fmt_when(eng.now_local() + dt.timedelta(seconds=int(expires))) if expires else ''
        self.settings.save(); self.find_accounts(token)

    def check_token(self):
        token = self.token_edit.text().strip()
        if not token: self.status.setText('Paste a token first.'); return
        self.settings['access_token'] = token; self.settings['token_expires'] = ''; self.settings.save(); self.find_accounts(token)

    def find_accounts(self, token):
        version = self.settings.get('api_version')
        self.run_task(lambda: ig.accounts_for_token(token, version), self.accounts_found, 'Looking up the Instagram account…')

    def accounts_found(self, found):
        self.accounts.blockSignals(True); self.accounts.clear()
        for acc in found: self.accounts.addItem(f'@{acc["username"] or acc["id"]}' + (f'  (via {acc["via"]})' if acc.get('via') else ''), acc)
        self.accounts.blockSignals(False); self.accounts.setCurrentIndex(0); self.account_chosen(0)
        self.status.setText(f'Connected as @{found[0]["username"]}.' + (f' {len(found)} accounts found; choose one above.' if len(found) > 1 else ''))
        self.status.setStyleSheet(OK_STYLE); self.disconnect_button.show(); self.token_edit.clear(); self.changed.emit()

    def account_chosen(self, index):
        acc = self.accounts.itemData(index)
        if not acc: return
        self.settings['ig_user_id'] = acc['id']; self.settings['ig_username'] = acc.get('username', ''); self.settings['api_host'] = acc.get('host') or ig.FB_HOST
        self.settings['login_kind'] = 'instagram' if self.settings['api_host'] == ig.IG_HOST else self.kind.currentData(); self.settings.save(); self.changed.emit()

    def disconnect(self):
        for key in ('access_token', 'token_expires', 'ig_user_id', 'ig_username'): self.settings[key] = ''
        self.settings.save(); self.show_account()


class HostingPanel(QWidget):
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.task = None
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(intro_label('Instagram\'s API does not accept photo uploads: it <b>fetches each photo from a public link</b>. PostQueue uploads the file to your '
                                     'hosting account just before posting and removes it afterwards. A free <a href="https://cloudinary.com/users/register_free">Cloudinary</a> '
                                     'account is enough. In the Cloudinary console, open Settings > API Keys and copy the three values.'))
        group = QGroupBox('Cloudinary'); form = QFormLayout(group); layout.addWidget(group)
        self.cloud = QLineEdit(self.settings.get('cloudinary_cloud', '')); self.cloud.setObjectName('cloud'); form.addRow('Cloud name', self.cloud)
        self.ckey = QLineEdit(self.settings.get('cloudinary_api_key', '')); self.ckey.setObjectName('ckey'); form.addRow('API key', self.ckey)
        self.csecret = QLineEdit(self.settings.get('cloudinary_api_secret', '')); self.csecret.setObjectName('csecret'); self.csecret.setEchoMode(QLineEdit.Password); form.addRow('API secret', self.csecret)
        for w in (self.cloud, self.ckey, self.csecret): w.editingFinished.connect(self.save)
        row = QHBoxLayout(); test = QPushButton('Test hosting'); test.setObjectName('test_hosting'); test.clicked.connect(self.test); row.addWidget(test); row.addStretch(1); form.addRow('', row)
        self.status = QLabel(''); self.status.setWordWrap(True); form.addRow('', self.status)
        if settings.hosting_ready(): self.status.setText('Hosting details entered. Click Test hosting to check them.'); self.status.setStyleSheet(MUTED_STYLE)

    def is_ready(self): return self.settings.hosting_ready()

    def save(self):
        self.settings['cloudinary_cloud'] = self.cloud.text().strip(); self.settings['cloudinary_api_key'] = self.ckey.text().strip(); self.settings['cloudinary_api_secret'] = self.csecret.text().strip()
        self.settings.save(); self.changed.emit()

    def test(self):
        self.save()
        try: provider = hosting.make_provider(self.settings) or hosting.Cloudinary('', '', '')
        except hosting.HostingError as exc: self.status.setText(str(exc)); self.status.setStyleSheet(BAD_STYLE); return
        self.status.setText('Uploading a test image…'); self.status.setStyleSheet('')
        self.task = Task(provider.test)
        self.task.done.connect(lambda url: (self.status.setText('Hosting works: a test image was uploaded and removed.'), self.status.setStyleSheet(OK_STYLE), self.changed.emit()))
        self.task.failed.connect(lambda m: (self.status.setText(m), self.status.setStyleSheet(BAD_STYLE)))
        self.task.start()


class GeneralPanel(QWidget):
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings
        form = QFormLayout(self); form.setContentsMargins(0, 0, 0, 0)
        self.startup = QCheckBox('Start PostQueue when I sign in (needed for posts to go out on time)')
        chosen = self.settings.get('start_with_windows')
        self.startup.setChecked(cfg.starts_at_login() if chosen is None else bool(chosen))
        self.startup.toggled.connect(lambda on: self.set('start_with_windows', on)); form.addRow(self.startup)
        self.tray_box = QCheckBox(f'Closing the window keeps PostQueue running in {TRAY_NAME}'); self.tray_box.setChecked(bool(self.settings.get('close_to_tray', True)))
        self.tray_box.toggled.connect(lambda on: self.set('close_to_tray', on)); form.addRow(self.tray_box)
        self.default_time = QTimeEdit(); self.default_time.setDisplayFormat('h:mm AP')
        self.default_time.setTime(QTime(int(self.settings.get('default_hour', 9)), int(self.settings.get('default_minute', 0))))
        self.default_time.timeChanged.connect(lambda t: (self.set('default_hour', t.hour()), self.set('default_minute', t.minute()))); form.addRow('Default posting time for new posts', self.default_time)
        self.grace = QSpinBox(); self.grace.setRange(1, 168); self.grace.setSuffix(' hours'); self.grace.setValue(int(self.settings.get('grace_hours', 12)))
        self.grace.valueChanged.connect(lambda v: self.set('grace_hours', v)); form.addRow('If the PC was off at the scheduled time, still post within', self.grace)
        self.fit_default = QComboBox(); self.fit_default.addItem('Crop to fit Instagram', 'crop'); self.fit_default.addItem('Add white borders', 'pad')
        self.fit_default.setCurrentIndex(max(0, self.fit_default.findData(self.settings.get('fit_mode', 'crop')))); self.fit_default.currentIndexChanged.connect(lambda i: self.set('fit_mode', self.fit_default.itemData(i)))
        form.addRow('Photos outside Instagram\'s shape range', self.fit_default)
        folder = QLabel(f'<a href="{Path(cfg.data_dir()).as_uri()}">Open the data folder</a> (queue, media copies, settings and log)'); folder.setOpenExternalLinks(True); form.addRow(folder)

    def set(self, key, value): self.settings[key] = value; self.settings.save()


class SettingsDialog(QDialog):
    """Instagram account, media hosting and general behaviour, for changes after setup."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.setWindowTitle(f'{PRODUCT} Settings'); self.resize(680, 660); self.setMinimumSize(560, 600)
        layout = QVBoxLayout(self); self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self.instagram, self.hosting, self.general = InstagramPanel(settings), HostingPanel(settings), GeneralPanel(settings)
        for panel, name in ((self.instagram, 'Instagram'), (self.hosting, 'Media hosting'), (self.general, 'General')):
            page = QWidget(); pl = QVBoxLayout(page); pl.addWidget(panel); pl.addStretch(1); self.tabs.addTab(page, name)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.accept); layout.addWidget(buttons)
        if not settings.connected(): self.tabs.setCurrentIndex(0)
        elif not settings.hosting_ready(): self.tabs.setCurrentIndex(1)


# --- First-run wizard: one decision per screen -----------------------------------------------------------------------
class StepPage(QWizardPage):
    """A wizard page that is complete when its panel says so."""
    def __init__(self, title, subtitle, panel=None, ready=None):
        super().__init__(); self.setTitle(title); self.setSubTitle(subtitle); self.panel, self.ready = panel, ready
        layout = QVBoxLayout(self)
        if panel is not None:
            layout.addWidget(panel); panel.changed.connect(self.completeChanged)
        layout.addStretch(1)

    def isComplete(self):
        return self.ready() if self.ready else True


class SetupWizard(QWizard):
    """Welcome, Instagram, hosting, schedule, done. Each screen asks for one thing; Next stays off until it is there."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings
        self.setWindowTitle(f'Set up {PRODUCT}'); self.setWizardStyle(QWizard.ModernStyle); self.setOption(QWizard.NoBackButtonOnStartPage); self.resize(720, 640)
        self.setButtonText(QWizard.NextButton, 'Next'); self.setButtonText(QWizard.FinishButton, 'Start using PostQueue')
        welcome = StepPage('Welcome to PostQueue', 'Three short steps, then you only ever drop in photos and pick a time.')
        wl = welcome.layout(); wl.insertWidget(0, intro_label(
            '<p style="font-size: 12pt">PostQueue posts your photos and videos to Instagram at the times you choose, from your own PC, through Meta\'s official API.</p>'
            '<p><b>1.</b> Connect your Instagram professional account.<br><b>2.</b> Connect a free media hosting account (Instagram fetches photos from a link).<br>'
            '<b>3.</b> Choose your usual posting time.</p><p>You can change any of this later in Settings.</p>'))
        self.instagram = InstagramPanel(settings); self.hosting = HostingPanel(settings)
        self.addPage(welcome)
        self.addPage(StepPage('Step 1 of 3: Instagram', 'Sign in once. Next turns on when an account is connected.', self.instagram, self.instagram.is_ready))
        self.addPage(StepPage('Step 2 of 3: Media hosting', 'Enter the three Cloudinary values and click Test hosting.', self.hosting, self.hosting.is_ready))
        schedule = StepPage('Step 3 of 3: When to post', 'New posts are scheduled for the next free day at this time. You can change each one.')
        form = QFormLayout(); schedule.layout().insertLayout(0, form)
        self.default_time = QTimeEdit(); self.default_time.setDisplayFormat('h:mm AP'); self.default_time.setTime(QTime(int(settings.get('default_hour', 9)), int(settings.get('default_minute', 0))))
        form.addRow('Usual posting time', self.default_time)
        self.startup = QCheckBox('Start PostQueue when I sign in, so posts go out even if I forget to open it'); self.startup.setChecked(True); form.addRow(self.startup)
        self.addPage(schedule)
        done = StepPage('All set', f'Drop photos or videos onto the window. Closing the window keeps PostQueue running in {TRAY_NAME}.')
        self.addPage(done)

    def accept(self):
        t = self.default_time.time(); self.settings['default_hour'], self.settings['default_minute'] = t.hour(), t.minute()
        self.settings['start_with_windows'] = bool(self.startup.isChecked()); self.settings['setup_done'] = True; self.settings.save()
        super().accept()


class MainWindow(QMainWindow):
    def __init__(self, engine, settings, lock=None):
        super().__init__()
        self.engine, self.settings, self.lock = engine, settings, lock
        self.worker = None; self.current = None; self.dirty = False; self.thumbs = {}; self.quitting = False
        self.setWindowTitle(TITLE); self.setObjectName('main'); self.resize(1040, 640); self.setAcceptDrops(True)
        self.build(); self.build_tray()
        self.refresh()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(CHECK_SECONDS * 1000)
        self.poll = QTimer(self); self.poll.timeout.connect(self.poll_show_request); self.poll.start(1500)
        QTimer.singleShot(1500, self.tick)
        QTimer.singleShot(3000, self.maintain_token)
        geometry = settings.get('window')
        if isinstance(geometry, list) and len(geometry) == 4:
            with contextlib.suppress(Exception): self.setGeometry(*geometry)
        QApplication.instance().installEventFilter(self)

    # --- building -------------------------------------------------------------------------------------------------
    def build(self):
        bar = QToolBar('Main'); bar.setMovable(False); bar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon); bar.setIconSize(QSize(20, 20))
        self.addToolBar(bar); style = self.style()
        self.act_add = QAction(style.standardIcon(QStyle.SP_DialogOpenButton), 'Add photos or videos…', self)
        self.act_add.setShortcut(QKeySequence.Open); self.act_add.triggered.connect(self.add_files)
        self.act_pause = QAction(style.standardIcon(QStyle.SP_MediaPause), 'Pause posting', self, checkable=True)
        self.act_pause.setChecked(bool(self.settings.get('paused'))); self.act_pause.toggled.connect(self.set_paused)
        self.act_settings = QAction(style.standardIcon(QStyle.SP_FileDialogDetailedView), 'Settings…', self)
        self.act_settings.setShortcut(QKeySequence.Preferences if cfg.MAC else 'Ctrl+,'); self.act_settings.triggered.connect(self.open_settings)
        self.act_quit = QAction('Quit PostQueue', self); self.act_quit.setShortcut(QKeySequence.Quit if cfg.MAC else 'Ctrl+Q'); self.act_quit.triggered.connect(self.quit)
        self.act_quit.setMenuRole(QAction.QuitRole)
        for act in (self.act_add, self.act_pause, self.act_settings): bar.addAction(act)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); bar.addWidget(spacer)
        self.account_label = QLabel(''); self.account_label.setContentsMargins(8, 0, 8, 0); bar.addWidget(self.account_label)
        self.addAction(self.act_quit)

        central = QWidget(); outer = QVBoxLayout(central); outer.setContentsMargins(8, 8, 8, 8); outer.setSpacing(8)
        self.banner = QFrame(); self.banner.setObjectName('banner'); self.banner.setFrameShape(QFrame.StyledPanel)
        self.banner.setStyleSheet('#banner { background: #fff4d6; border: 1px solid #e0c070; border-radius: 6px; } #banner QLabel { color: #3b2f00; }')
        row = QHBoxLayout(self.banner); self.banner_text = QLabel(''); self.banner_text.setWordWrap(True); row.addWidget(self.banner_text, 1)
        self.banner_button = QPushButton('Finish setup'); self.banner_button.setObjectName('banner_button'); self.banner_button.clicked.connect(self.open_setup); row.addWidget(self.banner_button)
        outer.addWidget(self.banner)

        split = QSplitter(Qt.Horizontal); outer.addWidget(split, 1)
        self.tree = QTreeWidget(); self.tree.setObjectName('queue'); self.tree.setAccessibleName('Scheduled posts')
        self.tree.setHeaderLabels(['', 'Media', 'Scheduled for', 'Status']); self.tree.setIconSize(QSize(64, 64)); self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection); self.tree.setUniformRowHeights(True); self.tree.setAlternatingRowColors(True)
        self.tree.setColumnWidth(0, 76); self.tree.setColumnWidth(1, 220); self.tree.setColumnWidth(2, 200)
        self.tree.itemSelectionChanged.connect(self.selection_changed); self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.empty = QLabel('Drop photos or videos here, or click Add photos or videos.\n\nEach one is scheduled for the next free posting time; change it on the right.')
        self.empty.setAlignment(Qt.AlignCenter); self.empty.setWordWrap(True); self.empty.setStyleSheet('color: #666; font-size: 11pt;')
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.addWidget(self.tree, 1); ll.addWidget(self.empty, 1)
        split.addWidget(left)

        self.detail = QWidget(); self.detail.setObjectName('detail'); dl = QVBoxLayout(self.detail); dl.setContentsMargins(4, 0, 0, 0)
        self.preview = PreviewLabel(); self.preview.setStyleSheet('background: #1f1f23; border-radius: 6px;')
        dl.addWidget(self.preview, 1)
        self.name_label = QLabel(''); self.name_label.setStyleSheet('font-weight: 600;'); self.name_label.setWordWrap(True); dl.addWidget(self.name_label)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignRight); dl.addLayout(form)
        when_row = QHBoxLayout()
        self.when_edit = QDateTimeEdit(); self.when_edit.setObjectName('when'); self.when_edit.setAccessibleName('Scheduled date and time')
        self.when_edit.setCalendarPopup(True); self.when_edit.setDisplayFormat(DATE_FORMAT); self.when_edit.dateTimeChanged.connect(self.mark_dirty)
        when_row.addWidget(self.when_edit, 1)
        for text, delta in (('+1 hour', dt.timedelta(hours=1)), ('+1 day', dt.timedelta(days=1))):
            b = QPushButton(text); b.setToolTip(f'Move the scheduled time by {text[1:]}'); b.clicked.connect(lambda _=False, d=delta: self.shift_when(d)); when_row.addWidget(b)
        slot = QPushButton('Next slot'); slot.setToolTip('The next free default posting time (set in Settings > General)'); slot.clicked.connect(self.use_next_slot); when_row.addWidget(slot)
        form.addRow('Post on', when_row)
        self.fit_combo = QComboBox(); self.fit_combo.setObjectName('fit'); self.fit_combo.addItem('Crop to fit Instagram', 'crop'); self.fit_combo.addItem('Add white borders', 'pad')
        self.fit_combo.setToolTip('Instagram accepts photos between 4:5 (portrait) and 1.91:1 (landscape). Photos outside that range are cropped or padded.')
        self.fit_combo.currentIndexChanged.connect(self.mark_dirty); form.addRow('If the shape is off', self.fit_combo)
        self.caption = QPlainTextEdit(); self.caption.setObjectName('caption'); self.caption.setAccessibleName('Caption'); self.caption.setPlaceholderText('Caption, #hashtags and @mentions')
        self.caption.setMaximumHeight(140); self.caption.textChanged.connect(self.caption_changed); form.addRow('Caption', self.caption)
        self.caption_info = QLabel(''); self.caption_info.setStyleSheet(MUTED_STYLE); form.addRow('', self.caption_info)
        self.status_label = QLabel(''); self.status_label.setWordWrap(True); self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse); dl.addWidget(self.status_label)
        buttons = QHBoxLayout(); dl.addLayout(buttons)
        self.save_button = QPushButton('Save changes'); self.save_button.setObjectName('save'); self.save_button.setShortcut(QKeySequence.Save); self.save_button.clicked.connect(self.save_current)
        self.post_now_button = QPushButton('Post now'); self.post_now_button.setObjectName('post_now'); self.post_now_button.clicked.connect(self.post_now)
        self.open_button = QPushButton('Open on Instagram'); self.open_button.clicked.connect(self.open_permalink)
        self.remove_button = QPushButton('Remove'); self.remove_button.setObjectName('remove'); self.remove_button.clicked.connect(self.remove_selected)
        for b in (self.save_button, self.post_now_button, self.open_button, self.remove_button): buttons.addWidget(b)
        buttons.addStretch(1)
        self.detail.setMinimumWidth(380); left.setMinimumWidth(360)
        split.addWidget(self.detail); split.setStretchFactor(0, 1); split.setStretchFactor(1, 1); split.setSizes([540, 500])
        self.setCentralWidget(central)

        self.progress = QProgressBar(); self.progress.setRange(0, 100); self.progress.setFixedWidth(160); self.progress.hide()
        self.progress_text = QLabel(''); self.next_label = QLabel('')
        sb = self.statusBar(); sb.addWidget(self.progress_text, 1); sb.addPermanentWidget(self.progress); sb.addPermanentWidget(self.next_label)
        self.show_detail(None)

    def build_tray(self):
        self.tray = None
        if not QSystemTrayIcon.isSystemTrayAvailable(): return
        self.tray = QSystemTrayIcon(self.windowIcon(), self); self.tray.setToolTip(TITLE)
        menu = QMenu(); show = menu.addAction('Open PostQueue'); show.triggered.connect(self.show_window)
        menu.addAction(self.act_add); menu.addAction(self.act_pause); menu.addSeparator(); menu.addAction(self.act_quit)
        self.tray.setContextMenu(menu)
        if not cfg.MAC: self.tray.activated.connect(lambda reason: self.show_window() if reason != QSystemTrayIcon.Context else None)
        self.tray.show()

    def eventFilter(self, obj, event):
        """macOS: clicking the Dock icon activates the app; bring the window back then."""
        if event.type() == QEvent.ApplicationActivate and cfg.MAC and not self.isVisible() and not self.quitting: self.show_window()
        return super().eventFilter(obj, event)

    # --- queue view -----------------------------------------------------------------------------------------------
    def refresh(self):
        selected = {i.data(0, Qt.UserRole) for i in self.tree.selectedItems()} or ({self.current.id} if self.current else set())
        self.tree.blockSignals(True); self.tree.clear()
        order = {'posting': 0, 'queued': 1, 'missed': 2, 'failed': 3, 'posted': 4}
        for post in sorted(self.engine.posts, key=lambda p: (order.get(p.status, 9), p.when if p.status != 'posted' else '')):
            item = QTreeWidgetItem(['', post.name(), post.when_dt().strftime('%a %d %b %Y  %H:%M'), post.status_text()])
            item.setData(0, Qt.UserRole, post.id); item.setIcon(0, QIcon(self.thumb(post))); item.setSizeHint(0, QSize(72, 68))
            if post.caption: item.setToolTip(1, post.caption[:300])
            if post.error: item.setToolTip(3, post.error)
            color = STATUS_COLOR.get(post.status)
            if color: item.setForeground(3, QColor(color))
            self.tree.addTopLevelItem(item)
            if post.id in selected: item.setSelected(True)
        self.tree.blockSignals(False)
        has = bool(self.engine.posts); self.tree.setVisible(has); self.empty.setVisible(not has)
        if self.current and not self.engine.get(self.current.id): self.current = None
        self.show_detail(self.engine.get(self.current.id) if self.current else None)
        self.update_banner(); self.update_next()

    def thumb(self, post):
        if post.id not in self.thumbs: self.thumbs[post.id] = thumbnail(post.media, post.kind, 64)
        return self.thumbs[post.id]

    def selected_posts(self):
        return [p for p in (self.engine.get(i.data(0, Qt.UserRole)) for i in self.tree.selectedItems()) if p]

    def selection_changed(self):
        posts = self.selected_posts()
        if self.dirty and self.current and (not posts or posts[0].id != self.current.id):
            if QMessageBox.question(self, PRODUCT, f'Save the changes to {self.current.name()}?', QMessageBox.Yes | QMessageBox.No) == QMessageBox.Yes: self.save_current()
        self.show_detail(posts[0] if posts else None)

    def show_detail(self, post):
        self.current = post; self.dirty = False
        self.detail.setEnabled(post is not None)
        if post is None:
            self.preview.set_source(None); self.name_label.setText(''); self.status_label.setText('')
            self.caption.blockSignals(True); self.caption.clear(); self.caption.blockSignals(False); self.caption_info.setText('')
            for b in (self.save_button, self.post_now_button, self.open_button, self.remove_button): b.setVisible(False)
            return
        self.preview.set_source(thumbnail(post.media, post.kind, 800))
        self.name_label.setText(f'{post.name()}  ({"video" if post.kind == "video" else "photo"})')
        for w in (self.when_edit, self.fit_combo, self.caption): w.blockSignals(True)
        self.when_edit.setDateTime(to_qdt(post.when_dt())); self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(post.fit)))
        self.caption.setPlainText(post.caption)
        for w in (self.when_edit, self.fit_combo, self.caption): w.blockSignals(False)
        self.fit_combo.setVisible(post.kind == 'image'); self.update_caption_info()
        editable = post.status in ('queued', 'failed', 'missed')
        self.when_edit.setEnabled(editable); self.caption.setEnabled(editable); self.fit_combo.setEnabled(editable)
        text = post.status_text()
        if post.status == 'posted' and post.posted_at: text += f' on {eng.parse_when(post.posted_at):%a %d %b %Y at %H:%M}'
        if post.error: text += f'\n{post.error}'
        if post.status in ('failed', 'missed'): text += '\nChange the time and save to schedule it again, or use Post now.'
        self.status_label.setText(text); self.status_label.setStyleSheet(f'color: {STATUS_COLOR.get(post.status) or "palette(text)"};')
        self.save_button.setVisible(editable); self.post_now_button.setVisible(editable); self.post_now_button.setEnabled(self.worker is None)
        self.open_button.setVisible(bool(post.permalink)); self.remove_button.setVisible(post.status != 'posting')
        self.save_button.setEnabled(False)

    def mark_dirty(self, *_):
        if self.current and self.detail.isEnabled(): self.dirty = True; self.save_button.setEnabled(True)

    def caption_changed(self): self.update_caption_info(); self.mark_dirty()

    def update_caption_info(self):
        text = self.caption.toPlainText(); warnings = eng.caption_warnings(text)
        self.caption_info.setText(' '.join(warnings) if warnings else f'{len(text)} of {eng.CAPTION_LIMIT} characters')
        self.caption_info.setStyleSheet(BAD_STYLE if warnings else MUTED_STYLE)

    def save_current(self):
        if not self.current: return
        when = from_qdt(self.when_edit.dateTime())
        self.engine.update(self.current, caption=self.caption.toPlainText(), fit=self.fit_combo.currentData() or 'crop')
        if self.current.status != 'queued' or eng.fmt_when(when) != self.current.when: self.engine.reschedule(self.current, when)
        self.dirty = False; self.refresh(); self.statusBar().showMessage(f'Saved. {self.current.name()} will post on {when:%a %d %b at %H:%M}.', 5000)

    def shift_when(self, delta): self.when_edit.setDateTime(to_qdt(from_qdt(self.when_edit.dateTime()) + delta))
    def use_next_slot(self): self.when_edit.setDateTime(to_qdt(self.engine.next_slot()))

    def context_menu(self, pos):
        posts = self.selected_posts()
        if not posts: return
        menu = QMenu(self)
        if len(posts) == 1 and posts[0].status in ('queued', 'failed', 'missed'): menu.addAction('Post now', self.post_now)
        menu.addAction(f'Remove {len(posts)} post{"s" if len(posts) > 1 else ""}', self.remove_selected)
        menu.exec(self.tree.viewport().mapToGlobal(pos))

    # --- adding and removing --------------------------------------------------------------------------------------
    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Add photos or videos', str(Path.home() / 'Pictures'), FILE_FILTER)
        if files: self.add_paths(files)

    def add_paths(self, paths):
        added, problems = [], []
        for path in paths:
            try: added.append(self.engine.add(path, self.engine.next_slot()))
            except eng.EngineError as exc: problems.append(str(exc))
            except OSError as exc: problems.append(f'{Path(path).name}: {exc.strerror or exc}')
        self.refresh()
        if added:
            self.tree.clearSelection()
            for i in range(self.tree.topLevelItemCount()):
                if self.tree.topLevelItem(i).data(0, Qt.UserRole) == added[-1].id: self.tree.topLevelItem(i).setSelected(True); self.tree.scrollToItem(self.tree.topLevelItem(i))
            self.statusBar().showMessage(f'Added {len(added)} post{"s" if len(added) > 1 else ""}. Pick the date and time on the right, then Save changes.', 8000)
        if problems: QMessageBox.warning(self, PRODUCT, '\n'.join(problems))
        return added

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths: self.add_paths(paths); event.acceptProposedAction()

    def remove_selected(self):
        posts = [p for p in self.selected_posts() if p.status != 'posting']
        if not posts: return
        names = posts[0].name() if len(posts) == 1 else f'{len(posts)} posts'
        if QMessageBox.question(self, PRODUCT, f'Remove {names} from the queue? Posts already on Instagram stay there.', QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        for post in posts: self.engine.remove(post); self.thumbs.pop(post.id, None)
        self.current = None; self.refresh()

    # --- publishing -----------------------------------------------------------------------------------------------
    def make_clients(self):
        """The Instagram client and hosting provider from the settings; either may be None when not set up."""
        instagram = None
        if self.settings.connected():
            instagram = ig.Instagram(self.settings['access_token'], self.settings['ig_user_id'], self.settings.get('api_host'), self.settings.get('api_version'))
        return instagram, hosting.make_provider(self.settings)

    def set_paused(self, paused):
        self.settings['paused'] = bool(paused); self.settings.save(); self.act_pause.setText('Resume posting' if paused else 'Pause posting'); self.update_next()

    def tick(self):
        if self.worker is not None or self.settings.get('paused'): return
        post = self.engine.check()
        if post: self.start_publish(post)
        else: self.update_next()

    def post_now(self):
        if not self.current or self.worker is not None: return
        if self.dirty: self.engine.update(self.current, caption=self.caption.toPlainText(), fit=self.fit_combo.currentData() or 'crop'); self.dirty = False
        self.engine.reschedule(self.current, eng.now_local()); self.start_publish(self.current)

    def start_publish(self, post):
        instagram, provider = self.make_clients()
        if instagram is None or provider is None:
            self.engine.update(post, status='failed', error='Not set up yet: ' + ('connect your Instagram account' if instagram is None else 'set up media hosting') + ' in Settings.')
            self.refresh(); return
        self.worker = PublishWorker(self.engine, post, instagram, provider)
        self.worker.progress.connect(self.on_progress); self.worker.finished_post.connect(self.on_published); self.worker.start()
        self.progress.setValue(0); self.progress.show(); self.progress_text.setText(f'Posting {post.name()}…'); self.refresh()

    def on_progress(self, post_id, fraction, text):
        self.progress.setValue(int(fraction * 100)); post = self.engine.get(post_id)
        self.progress_text.setText(f'{post.name() if post else "Post"}: {text}')

    def on_published(self, post_id):
        self.worker.wait(); self.worker = None; self.progress.hide(); self.progress_text.setText('')
        post = self.engine.get(post_id); self.refresh()
        if not post: return
        if post.status == 'posted':
            self.statusBar().showMessage(f'Posted {post.name()} to Instagram.', 10000)
            if self.tray and not self.isVisible(): self.tray.showMessage(PRODUCT, f'Posted {post.name()} to Instagram.', QSystemTrayIcon.Information, 8000)
        elif post.status == 'failed':
            if self.tray and not self.isVisible(): self.tray.showMessage(PRODUCT, f'Could not post {post.name()}: {post.error}', QSystemTrayIcon.Warning, 15000)
        QTimer.singleShot(500, self.tick)   # more posts may be due

    def update_next(self):
        if self.settings.get('paused'): self.next_label.setText('Posting is paused'); return
        nxt = self.engine.next_scheduled()
        self.next_label.setText(f'Next: {nxt.name()} on {nxt.when_dt():%a %d %b at %H:%M}' if nxt else 'Nothing scheduled')

    def update_banner(self):
        problems = []
        if not self.settings.connected(): problems.append('connect your Instagram account')
        if not self.settings.hosting_ready(): problems.append('set up media hosting (Instagram fetches photos from a public link)')
        expiry = self.settings.token_expiry()
        if not problems and expiry and expiry - eng.now_local() < dt.timedelta(days=7):
            problems.append(f'the Instagram sign-in expires on {expiry:%d %b}; sign in again in Settings')
        self.banner.setVisible(bool(problems))
        if problems:
            self.banner_text.setText('Before posts can go out: ' + '; '.join(problems) + '.')
            self.banner_button.setText('Finish setup' if not self.settings.ready() else 'Open Settings')
        text = f'@{self.settings["ig_username"]}' if self.settings.connected() and self.settings.get('ig_username') else ('Connected' if self.settings.connected() else 'Not connected')
        self.account_label.setText(text)

    def maintain_token(self):
        """Instagram Login tokens can be refreshed; do it when under 15 days remain."""
        expiry = self.settings.token_expiry()
        if not (self.settings.connected() and self.settings.get('api_host') == ig.IG_HOST and expiry): return
        if expiry - eng.now_local() > dt.timedelta(days=15): return
        task = Task(lambda: ig.refresh_instagram(self.settings['access_token']))
        def done(result):
            token, expires = result
            self.settings['access_token'] = token
            if expires: self.settings['token_expires'] = eng.fmt_when(eng.now_local() + dt.timedelta(seconds=int(expires)))
            self.settings.save(); self.update_banner(); self.engine.log.info('refreshed the Instagram token')
        task.done.connect(done); task.failed.connect(lambda m: self.engine.log.warning('token refresh failed: %s', m)); task.start(); self.refresh_task = task

    def open_permalink(self):
        if self.current and self.current.permalink: QDesktopServices.openUrl(QUrl(self.current.permalink))

    # --- setup, settings, tray, lifecycle -------------------------------------------------------------------------
    def needs_setup(self): return not self.settings.get('setup_done') and not self.settings.ready()

    def open_setup(self):
        """The first-run wizard (one decision per screen); the settings dialog once set-up is complete."""
        if self.settings.ready(): return self.open_settings()
        wizard = SetupWizard(self.settings, self); wizard.exec()
        self.settings.save(); self.refresh(); self.apply_startup_setting()

    def open_settings(self):
        dialog = SettingsDialog(self.settings, self)
        dialog.exec()
        self.settings.save(); self.refresh(); self.apply_startup_setting()

    def apply_startup_setting(self):
        """Write the user's explicit choice to the sign-in entry. When nothing was chosen, the installer's entry stands."""
        choice = self.settings.get('start_with_windows')
        if choice is None: return
        with contextlib.suppress(OSError): cfg.set_start_at_login(bool(choice))

    def show_window(self):
        self.show(); self.setWindowState(self.windowState() & ~Qt.WindowMinimized); self.raise_(); self.activateWindow()

    def poll_show_request(self):
        if self.lock and self.lock.take_show_request(): self.show_window()

    def closeEvent(self, event):
        g = self.geometry(); self.settings['window'] = [g.x(), g.y(), g.width(), g.height()]
        if self.tray and self.settings.get('close_to_tray', True) and not self.quitting:
            event.ignore(); self.hide()
            if not self.settings.get('tray_hint_shown'):
                self.tray.showMessage(PRODUCT, f'PostQueue keeps running in {TRAY_NAME} and posts on schedule. Use its menu to quit.', QSystemTrayIcon.Information, 8000)
                self.settings['tray_hint_shown'] = True
            self.settings.save(); return
        self.settings.save(); event.accept(); QApplication.instance().quit()

    def quit(self):
        if self.worker is not None and QMessageBox.question(self, PRODUCT, 'A post is being published right now. Quit anyway?', QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        self.quitting = True; self.close()


def focus_running_instance(lock):
    """A second launch: ask the running window to show, and bring it forward with the Windows API when possible."""
    lock.request_show()
    if cfg.WINDOWS:
        with contextlib.suppress(Exception):
            import ctypes
            user32 = ctypes.WinDLL('user32'); handle = user32.FindWindowW(None, TITLE)
            if handle: user32.ShowWindow(handle, 9); user32.SetForegroundWindow(handle)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    minimized = '--minimized' in argv
    app = make_app()
    data = cfg.data_dir(); data.mkdir(parents=True, exist_ok=True)
    settings = cfg.Settings(data / 'settings.json')
    lock = cfg.InstanceLock(data / 'postqueue.lock')
    if lock.other_pid():
        focus_running_instance(lock); return 0
    lock.acquire()
    try:
        engine = eng.Engine(data, settings)
        window = MainWindow(engine, settings, lock)
        window.apply_startup_setting()
        if minimized and window.tray: window.hide()
        else:
            window.show()
            if window.needs_setup(): QTimer.singleShot(300, window.open_setup)
        return app.exec()
    finally:
        lock.release()
