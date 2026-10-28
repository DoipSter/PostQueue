"""PostQueue: the Qt interface. The queue on the left, the selected post on the right, a tray (menu bar) icon, the
timer that publishes posts when they are due, a first-run wizard that asks one thing per screen, and the settings
dialog. Network work runs in threads so the window stays responsive. Runs on Windows and macOS."""
import contextlib
import datetime as dt
import signal
import sys
import webbrowser
from pathlib import Path

from PySide6.QtCore import QDate, QDateTime, QEvent, QPoint, QPointF, QRect, QSize, Qt, QThread, QTime, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QGuiApplication, QIcon, QImage, QKeySequence, QPainter, QPen, QPixmap, QRegion, QTextCharFormat
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCalendarWidget, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QDoubleSpinBox, QFileDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QSplitter, QStackedWidget,
                               QStyle, QSystemTrayIcon, QTabWidget, QTimeEdit, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

import branding
import engine as eng
import hosting
import instagram as ig
import settings as cfg
from version import PRODUCT, VERSION

TITLE = f'{PRODUCT} {VERSION}'
FILE_FILTER = 'Photos and videos (*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif *.mp4 *.mov *.m4v);;All files (*)'
CHECK_SECONDS = 30
STATUS_COLOR = {'queued': None, 'posting': '#8ec5ff', 'posted': '#7fe0a0', 'failed': '#ff8a7a', 'missed': '#ffcf6e'}
TRAY_NAME = 'the menu bar' if cfg.MAC else 'the notification area'
OK_STYLE, BAD_STYLE, MUTED_STYLE, WARN_STYLE = 'color: #7fe0a0;', 'color: #ff8a7a;', 'color: rgba(255,255,255,0.62);', 'color: #ffcf6e;'


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
    """Shows a pixmap scaled to whatever size the layout gives the label, without forcing a minimum size. With a box
    from engine.target_box it also shows what Instagram will get: the parts a crop removes are dimmed, and white
    borders are drawn around the picture."""
    def __init__(self):
        super().__init__(); self.source = None; self.box = None; self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored); self.setMinimumSize(240, 180)

    def set_source(self, pixmap, box=None):
        self.source = pixmap; self.box = box; self.rescale()

    def set_box(self, box):
        self.box = box; self.rescale()

    def rescale(self):
        if self.source is None: self.clear(); return
        self.setPixmap(self.render(self.size()))

    def render(self, area):
        """The picture as shown, fitted into `area`: dimmed outside a crop box, or on a white canvas for a padding box."""
        src = self.source; box = self.box
        if not box or box == (0, 0, src.width(), src.height()): return src.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        left, top, right, bottom = box
        if left >= 0 and top >= 0:   # crop: dim what is cut off and outline what stays
            shown = src.scaled(area, Qt.KeepAspectRatio, Qt.SmoothTransformation); scale = shown.width() / src.width()
            keep = QRect(round(left * scale), round(top * scale), round((right - left) * scale), round((bottom - top) * scale))
            painter = QPainter(shown); painter.setClipRegion(QRegion(shown.rect()).subtracted(QRegion(keep)))
            painter.fillRect(shown.rect(), QColor(0, 0, 0, 150)); painter.setClipping(False)
            painter.setPen(QPen(QColor(255, 255, 255, 220), 1)); painter.drawRect(keep.adjusted(0, 0, -1, -1)); painter.end()
            return shown
        cw, ch = right - left, bottom - top; scale = min(area.width() / cw, area.height() / ch)   # pad: the picture on its white canvas
        canvas = QPixmap(max(1, round(cw * scale)), max(1, round(ch * scale))); canvas.fill(QColor('white'))
        picture = src.scaled(max(1, round(src.width() * scale)), max(1, round(src.height() * scale)), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(canvas); painter.drawPixmap(round(-left * scale), round(-top * scale), picture); painter.end()
        return canvas

    def resizeEvent(self, event):
        super().resizeEvent(event); self.rescale()


class DateTimePicker(QWidget):
    """A date button that opens a calendar, next to a time control. Same interface as QDateTimeEdit for the parts
    the window uses: dateTime(), setDateTime() and dateTimeChanged."""
    dateTimeChanged = Signal(QDateTime)

    def __init__(self, parent=None):
        super().__init__(parent); row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0)
        self.date = QDate.currentDate()
        self.date_button = QPushButton(); self.date_button.setObjectName('date'); self.date_button.setToolTip('Pick the date on a calendar')
        self.date_button.setCursor(Qt.PointingHandCursor); self.date_button.clicked.connect(self.show_calendar)
        self.time_edit = QTimeEdit(); self.time_edit.setObjectName('time'); self.time_edit.setDisplayFormat('h:mm AP'); self.time_edit.setToolTip('The time of day')
        self.time_edit.timeChanged.connect(lambda t: self.dateTimeChanged.emit(self.dateTime()))
        row.addWidget(self.date_button, 1); row.addWidget(self.time_edit)
        self.calendar = QCalendarWidget(); self.calendar.setWindowFlags(Qt.Popup); self.calendar.setGridVisible(False)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader); self.calendar.setMinimumSize(400, 320); self.calendar.setStyleSheet(CALENDAR_STYLE)
        weekend = QTextCharFormat(); weekend.setForeground(QColor('#ffc6bf'))
        for day in (Qt.Saturday, Qt.Sunday): self.calendar.setWeekdayTextFormat(day, weekend)
        self.calendar.clicked.connect(self.pick_date); self.calendar.activated.connect(self.pick_date)
        self.update_text()

    def update_text(self): self.date_button.setText(self.date.toString('ddd d MMM yyyy') + '   ▾')

    def show_calendar(self):
        self.calendar.setSelectedDate(self.date)
        self.calendar.move(self.date_button.mapToGlobal(QPoint(0, self.date_button.height() + 2))); self.calendar.show(); self.calendar.setFocus()

    def pick_date(self, qdate):
        self.date = qdate; self.calendar.hide(); self.update_text(); self.dateTimeChanged.emit(self.dateTime())

    def dateTime(self): return QDateTime(self.date, self.time_edit.time())

    def setDateTime(self, qdt):
        self.date = qdt.date(); self.time_edit.blockSignals(True); self.time_edit.setTime(qdt.time()); self.time_edit.blockSignals(False)
        self.update_text(); self.dateTimeChanged.emit(self.dateTime())


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
                                     'and a <b>Meta app</b> you create once at <a href="https://developers.facebook.com/">developers.facebook.com</a> (log in, then My Apps). '
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
        layout.addWidget(intro_label('Instagram\'s API does not accept photo uploads: it <b>fetches each photo from a public link</b>. PostQueue puts the file somewhere '
                                     'public just before posting and removes it right after.'))
        self.mode = QComboBox(); self.mode.setObjectName('hosting_mode')
        if branding.SERVICE_URL: self.mode.addItem('PostQueue hosting (built in, nothing to set up)', 'service')
        self.mode.addItem('My own Cloudinary account', 'cloudinary')
        self.mode.setCurrentIndex(max(0, self.mode.findData(self.settings.get('hosting')))); self.mode.currentIndexChanged.connect(self.mode_changed)
        layout.addWidget(self.mode)
        group = QGroupBox('Cloudinary'); self.cloud_group = group; form = QFormLayout(group); layout.addWidget(group)
        form.addRow(intro_label('A free <a href="https://cloudinary.com/users/register_free">Cloudinary</a> account is enough. In the Cloudinary console, '
                                'open Settings > API Keys and copy the three values.'))
        self.cloud = QLineEdit(self.settings.get('cloudinary_cloud', '')); self.cloud.setObjectName('cloud'); form.addRow('Cloud name', self.cloud)
        self.ckey = QLineEdit(self.settings.get('cloudinary_api_key', '')); self.ckey.setObjectName('ckey'); form.addRow('API key', self.ckey)
        self.csecret = QLineEdit(self.settings.get('cloudinary_api_secret', '')); self.csecret.setObjectName('csecret'); self.csecret.setEchoMode(QLineEdit.Password); form.addRow('API secret', self.csecret)
        for w in (self.cloud, self.ckey, self.csecret): w.editingFinished.connect(self.save)
        row = QHBoxLayout(); test = QPushButton('Test hosting'); test.setObjectName('test_hosting'); test.clicked.connect(self.test); row.addWidget(test); row.addStretch(1); form.addRow('', row)
        self.status = QLabel(''); self.status.setWordWrap(True); layout.addWidget(self.status)
        self.mode_changed(self.mode.currentIndex(), announce=False)

    def is_ready(self): return self.settings.hosting_ready(branding.SERVICE_URL)

    def mode_changed(self, index, announce=True):
        mode = self.mode.itemData(index) or 'cloudinary'
        if announce: self.settings['hosting'] = mode; self.settings.save(); self.changed.emit()
        self.cloud_group.setVisible(mode == 'cloudinary')
        if mode == 'service': self.status.setText('Photos are hosted by the PostQueue service for the moment it takes to post them. Nothing to set up.'); self.status.setStyleSheet(MUTED_STYLE)
        elif self.settings.hosting_ready(): self.status.setText('Hosting details entered. Click Test hosting to check them.'); self.status.setStyleSheet(MUTED_STYLE)
        else: self.status.setText('')

    def save(self):
        self.settings['cloudinary_cloud'] = self.cloud.text().strip(); self.settings['cloudinary_api_key'] = self.ckey.text().strip(); self.settings['cloudinary_api_secret'] = self.csecret.text().strip()
        self.settings.save(); self.changed.emit()

    def test(self):
        self.save()
        try: provider = hosting.make_provider(self.settings, branding.SERVICE_URL) or hosting.Cloudinary('', '', '')
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
        self.update_box = QCheckBox('Tell me when a new version of PostQueue is available'); self.update_box.setChecked(bool(self.settings.get('update_check', True)))
        self.update_box.toggled.connect(lambda on: self.set('update_check', on)); form.addRow(self.update_box)
        folder = QLabel(f'<a href="{Path(cfg.data_dir()).as_uri()}">Open the data folder</a> (queue, media copies, settings and log)'); folder.setOpenExternalLinks(True); form.addRow(folder)

    def set(self, key, value): self.settings[key] = value; self.settings.save()


class SettingsDialog(QDialog):
    """Instagram account, media hosting and general behaviour, for changes after setup."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.setWindowTitle(f'{PRODUCT} Settings'); self.resize(760, 720); self.setMinimumSize(620, 640)
        self.setObjectName('settings'); self.setStyleSheet(APP_STYLE)
        layout = QVBoxLayout(self); layout.setContentsMargins(16, 16, 16, 16); self.tabs = QTabWidget(); layout.addWidget(self.tabs, 1)
        self.instagram, self.hosting, self.general = InstagramPanel(settings), HostingPanel(settings), GeneralPanel(settings)
        for panel, name in ((self.instagram, 'Instagram'), (self.hosting, 'Media hosting'), (self.general, 'General')):
            page = QWidget(); pl = QVBoxLayout(page); pl.setContentsMargins(12, 12, 12, 12); pl.addWidget(panel); pl.addStretch(1)
            area = QScrollArea(); area.setWidgetResizable(True); area.setFrameShape(QFrame.NoFrame); area.setWidget(page); self.tabs.addTab(area, name)
        buttons = QDialogButtonBox(QDialogButtonBox.Close); buttons.rejected.connect(self.accept); layout.addWidget(buttons)
        if not settings.connected(): self.tabs.setCurrentIndex(0)
        elif not settings.hosting_ready(branding.SERVICE_URL): self.tabs.setCurrentIndex(1)


# --- The look: a soft gradient behind everything, frosted-glass controls, large type ---------------------------------
GRADIENT = 'qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #3a1f45, stop:0.55 #22203a, stop:1 #4a2424)'
APP_STYLE = f'''
QMainWindow#main, QDialog#settings {{ background: {GRADIENT}; }}
#main QWidget, #settings QWidget {{ font-size: 12pt; }}
#main QLabel, #settings QLabel {{ color: white; background: transparent; }}
#main QToolBar {{ background: transparent; border: none; padding: 6px 4px; spacing: 8px; }}
#main QToolButton {{ color: white; font-size: 12pt; padding: 8px 16px; border-radius: 12px; background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.32); }}
#main QToolButton:hover {{ background: rgba(255,255,255,0.19); }}
#main QToolButton:checked {{ background: rgba(229,57,43,0.45); border-color: rgba(255,255,255,0.6); }}
#main QTreeWidget {{ background: rgba(255,255,255,0.06); alternate-background-color: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.18);
  border-radius: 12px; color: white; padding: 4px; outline: none; }}
#main QTreeWidget::item {{ padding: 6px; border-radius: 8px; }}
#main QTreeWidget::item:selected {{ background: rgba(229,57,43,0.55); color: white; }}
#main QTreeWidget::item:hover {{ background: rgba(255,255,255,0.08); }}
#main QHeaderView::section {{ background: transparent; color: rgba(255,255,255,0.6); border: none; padding: 6px; font-size: 10pt; letter-spacing: 1px; }}
#main QSplitter::handle {{ background: transparent; }}
#main QLineEdit, #main QPlainTextEdit, #main QComboBox, #main QTimeEdit, #settings QLineEdit, #settings QComboBox, #settings QTimeEdit, #settings QSpinBox {{
  color: white; padding: 8px 12px; border-radius: 10px; background: rgba(255,255,255,0.09); border: 1px solid rgba(255,255,255,0.26); selection-background-color: #e5392b; }}
#main QLineEdit:focus, #main QPlainTextEdit:focus, #settings QLineEdit:focus {{ border: 1px solid rgba(255,255,255,0.7); }}
#main QComboBox QAbstractItemView, #settings QComboBox QAbstractItemView {{ background: #2a2440; color: white; selection-background-color: #e5392b; }}
#main QPushButton, #settings QPushButton {{ color: white; padding: 9px 20px; border-radius: 12px; background: rgba(255,255,255,0.10); border: 1px solid rgba(255,255,255,0.32); }}
#main QPushButton:hover, #settings QPushButton:hover {{ background: rgba(255,255,255,0.19); border-color: rgba(255,255,255,0.55); }}
#main QPushButton:pressed, #settings QPushButton:pressed {{ background: rgba(255,255,255,0.28); }}
#main QPushButton:disabled, #settings QPushButton:disabled {{ color: rgba(255,255,255,0.35); border-color: rgba(255,255,255,0.12); background: rgba(255,255,255,0.04); }}
#main QPushButton#save, #main QPushButton#post_now, #main QPushButton#banner_button {{ background: rgba(255,255,255,0.24); border: 1px solid rgba(255,255,255,0.7); font-weight: 600; }}
#main QPushButton#save:hover, #main QPushButton#post_now:hover {{ background: rgba(255,255,255,0.34); }}
#main QPushButton#date {{ text-align: left; padding-left: 16px; }}
#main QPushButton#quick {{ padding: 5px 12px; font-size: 11pt; border-radius: 9px; }}
#settings QScrollArea {{ background: transparent; border: none; }}
#settings QScrollArea > QWidget > QWidget {{ background: transparent; }}
#settings QScrollBar:vertical {{ background: transparent; width: 10px; }}
#settings QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 5px; min-height: 30px; }}
#settings QScrollBar::add-line:vertical, #settings QScrollBar::sub-line:vertical {{ height: 0; }}
#main QFrame#banner {{ background: rgba(255,196,0,0.14); border: 1px solid rgba(255,196,0,0.5); border-radius: 12px; }}
#main QFrame#banner QLabel {{ color: white; }}
#main QFrame#result[ok="true"] {{ background: rgba(80,200,120,0.18); border: 1px solid rgba(127,224,160,0.65); border-radius: 12px; }}
#main QFrame#result[ok="false"] {{ background: rgba(229,57,43,0.18); border: 1px solid rgba(255,138,122,0.65); border-radius: 12px; }}
#main QFrame#result[ok="update"] {{ background: rgba(100,160,255,0.16); border: 1px solid rgba(142,197,255,0.6); border-radius: 12px; }}
#main QListWidget#items {{ background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.18); border-radius: 10px; padding: 4px; }}
#main QListWidget#items::item {{ margin: 2px; border-radius: 6px; }}
#main QListWidget#items::item:selected {{ background: rgba(229,57,43,0.55); }}
#main QCheckBox {{ color: white; spacing: 8px; }}
#main QScrollArea#detail_scroll, #main QScrollArea#detail_scroll > QWidget > QWidget {{ background: transparent; border: none; }}
#main QScrollBar:vertical {{ background: transparent; width: 10px; }}
#main QScrollBar::handle:vertical {{ background: rgba(255,255,255,0.25); border-radius: 5px; min-height: 30px; }}
#main QScrollBar::add-line:vertical, #main QScrollBar::sub-line:vertical {{ height: 0; }}
#main QDoubleSpinBox {{ color: white; padding: 6px 10px; border-radius: 10px; background: rgba(255,255,255,0.09); border: 1px solid rgba(255,255,255,0.26); }}
#main QLabel#result_text {{ font-size: 13pt; font-weight: 600; }}
#main QPushButton#result_open {{ background: rgba(255,255,255,0.24); border: 1px solid rgba(255,255,255,0.7); font-weight: 600; }}
#main QStatusBar {{ background: transparent; color: rgba(255,255,255,0.7); }}
#main QStatusBar::item {{ border: none; }}
#main QProgressBar {{ border: 1px solid rgba(255,255,255,0.3); border-radius: 6px; background: rgba(255,255,255,0.08); text-align: center; color: white; }}
#main QProgressBar::chunk {{ background: #e5392b; border-radius: 5px; }}
#main QLabel#empty {{ color: rgba(255,255,255,0.7); font-size: 15pt; background: rgba(255,255,255,0.05); border: 1px dashed rgba(255,255,255,0.3); border-radius: 16px; padding: 40px; }}
#main QLabel#name {{ font-size: 14pt; font-weight: 600; }}
#settings QTabWidget::pane {{ border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; background: rgba(255,255,255,0.04); top: -1px; }}
#settings QTabBar::tab {{ color: rgba(255,255,255,0.75); padding: 10px 22px; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.18); border-bottom: none; border-top-left-radius: 10px; border-top-right-radius: 10px; margin-right: 4px; }}
#settings QTabBar::tab:selected {{ color: white; background: rgba(255,255,255,0.16); }}
#settings QGroupBox {{ color: rgba(255,255,255,0.85); border: 1px solid rgba(255,255,255,0.18); border-radius: 12px; margin-top: 14px; padding-top: 10px; }}
#settings QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
#settings QCheckBox {{ color: white; spacing: 10px; }}
#settings QDialogButtonBox QPushButton {{ min-width: 100px; }}
'''
CALENDAR_STYLE = '''
QCalendarWidget { background: #2a2440; color: white; border: 1px solid rgba(255,255,255,0.3); border-radius: 12px; }
QCalendarWidget QWidget { background: #2a2440; color: white; font-size: 12pt; }
QCalendarWidget QWidget#qt_calendar_navigationbar { background: rgba(255,255,255,0.08); }
QCalendarWidget QToolButton { color: white; background: transparent; border-radius: 8px; padding: 6px 12px; font-size: 13pt; }
QCalendarWidget QToolButton:hover { background: rgba(255,255,255,0.15); }
QCalendarWidget QAbstractItemView { background: #2a2440; color: white; selection-background-color: #e5392b; selection-color: white; outline: none; }
QCalendarWidget QAbstractItemView:disabled { color: rgba(255,255,255,0.3); }
QCalendarWidget QSpinBox { color: white; background: rgba(255,255,255,0.1); border-radius: 6px; }
'''


# --- First-run setup: one thing per screen, large type, glass buttons ------------------------------------------------
SETUP_STYLE = f'''
#setup {{ background: {GRADIENT}; }}
#setup QLabel {{ color: white; background: transparent; }}
#setup QLabel#title {{ font-size: 26pt; font-weight: 600; }}
#setup QLabel#lead {{ font-size: 15pt; color: rgba(255,255,255,0.88); }}
#setup QLabel#hint {{ font-size: 11pt; color: rgba(255,255,255,0.62); }}
#setup QLabel#status {{ font-size: 13pt; color: rgba(255,255,255,0.85); }}
#setup QLabel#progress {{ font-size: 10pt; color: rgba(255,255,255,0.55); letter-spacing: 2px; }}
#setup QLineEdit, #setup QComboBox, #setup QTimeEdit {{
  font-size: 17pt; padding: 10px 16px; border-radius: 12px; color: white;
  background: rgba(255,255,255,0.09); border: 1px solid rgba(255,255,255,0.28); selection-background-color: #e5392b; }}
#setup QLineEdit:focus, #setup QComboBox:focus, #setup QTimeEdit:focus {{ border: 1px solid rgba(255,255,255,0.7); }}
#setup QComboBox QAbstractItemView {{ font-size: 14pt; background: #2a2440; color: white; selection-background-color: #e5392b; }}
#setup QPushButton {{
  font-size: 15pt; padding: 13px 34px; border-radius: 14px; color: white;
  background: rgba(255,255,255,0.11); border: 1px solid rgba(255,255,255,0.38); }}
#setup QPushButton:hover {{ background: rgba(255,255,255,0.20); border-color: rgba(255,255,255,0.6); }}
#setup QPushButton:pressed {{ background: rgba(255,255,255,0.28); }}
#setup QPushButton:disabled {{ color: rgba(255,255,255,0.35); border-color: rgba(255,255,255,0.14); background: rgba(255,255,255,0.04); }}
#setup QPushButton#primary {{ background: rgba(255,255,255,0.24); border: 1px solid rgba(255,255,255,0.7); font-weight: 600; }}
#setup QPushButton#primary:hover {{ background: rgba(255,255,255,0.34); }}
#setup QPushButton#choice {{ text-align: left; padding: 16px 24px; }}
#setup QPushButton#link {{ background: transparent; border: none; color: rgba(255,255,255,0.72); font-size: 12pt; padding: 8px 10px; text-decoration: underline; }}
#setup QPushButton#link:hover {{ color: white; }}
'''


class Step:
    """One screen of the setup flow: a title, one instruction, optionally one control, and what Next does."""
    def __init__(self, key, title, lead, hint='', primary='Next', build=None, ready=None, on_next=None, skip_if=None, enter=None, skippable=False):
        self.key, self.title, self.lead, self.hint, self.primary = key, title, lead, hint, primary
        self.build, self.ready, self.on_next, self.skip_if, self.enter, self.skippable = build, ready, on_next, skip_if, enter, skippable
        self.page = None


class SetupFlow(QDialog):
    """The first-run setup: every screen asks for exactly one thing, in large type, with one big button. Steps that
    cannot apply are skipped; the sign-in and the hosting test move on by themselves when they succeed."""
    def __init__(self, settings, parent=None):
        super().__init__(parent); self.settings = settings; self.setObjectName('setup'); self.setWindowTitle(f'Set up {PRODUCT}')
        self.resize(900, 640); self.setMinimumSize(760, 560); self.setStyleSheet(SETUP_STYLE)
        self.task = None; self.cancelled = False; self.use_token = False; self.found_accounts = []; self.history = []
        # Simple path: the built-in Meta app and hosting service, so the user only signs in. Advanced path: their own
        # Meta app and Cloudinary account (the only path while branding.py is not filled in).
        self.advanced = not branding.simple_setup()
        settings['hosting'] = 'cloudinary' if self.advanced else 'service'
        if not self.advanced: settings['login_kind'] = branding.DEFAULT_LOGIN_KIND
        self.steps = self.make_steps(); self.index = 0
        outer = QVBoxLayout(self); outer.setContentsMargins(56, 28, 56, 28); outer.setSpacing(12)
        self.progress = QLabel(''); self.progress.setObjectName('progress'); outer.addWidget(self.progress, 0, Qt.AlignHCenter)
        outer.addStretch(1)
        self.stack = QStackedWidget(); self.stack.setMaximumWidth(760); outer.addWidget(self.stack, 0, Qt.AlignHCenter)
        outer.addStretch(2)
        nav = QHBoxLayout()
        self.back_button = QPushButton('Back'); self.back_button.setObjectName('link'); self.back_button.setCursor(Qt.PointingHandCursor); self.back_button.clicked.connect(self.back)
        self.skip_button = QPushButton('Skip this'); self.skip_button.setObjectName('link'); self.skip_button.setCursor(Qt.PointingHandCursor); self.skip_button.clicked.connect(self.skip)
        self.primary = QPushButton('Next'); self.primary.setObjectName('primary'); self.primary.setCursor(Qt.PointingHandCursor); self.primary.setDefault(True); self.primary.clicked.connect(self.next)
        nav.addWidget(self.back_button); nav.addStretch(1); nav.addWidget(self.skip_button); nav.addWidget(self.primary); outer.addLayout(nav)
        for step in self.steps: self.stack.addWidget(self.page_for(step))
        self.show_step(0)

    # --- screens --------------------------------------------------------------------------------------------------
    def page_for(self, step):
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(18); page.setMinimumWidth(640)
        title = QLabel(step.title); title.setObjectName('title'); title.setWordWrap(True); title.setAlignment(Qt.AlignHCenter); layout.addWidget(title)
        lead = QLabel(step.lead); lead.setObjectName('lead'); lead.setWordWrap(True); lead.setAlignment(Qt.AlignHCenter); lead.setOpenExternalLinks(True); layout.addWidget(lead)
        if step.build:
            control = step.build(); layout.addWidget(control, 0, Qt.AlignHCenter if control.maximumWidth() < 2000 else Qt.Alignment())
        if step.hint:
            hint = QLabel(step.hint); hint.setObjectName('hint'); hint.setWordWrap(True); hint.setAlignment(Qt.AlignHCenter); hint.setOpenExternalLinks(True); layout.addWidget(hint)
        layout.addStretch(1)   # keep the title, instruction and control together at the top of the page
        step.page = page; return page

    def field(self, name, placeholder='', password=False, text=''):
        edit = QLineEdit(text); edit.setObjectName(name); edit.setPlaceholderText(placeholder); edit.setMinimumWidth(560); edit.setAlignment(Qt.AlignHCenter)
        if password: edit.setEchoMode(QLineEdit.Password)
        edit.textChanged.connect(self.refresh_ready); edit.returnPressed.connect(lambda: self.primary.isEnabled() and self.next())
        setattr(self, name, edit); return edit

    def link(self, text, handler):
        button = QPushButton(text); button.setObjectName('link'); button.setCursor(Qt.PointingHandCursor); button.clicked.connect(handler); return button

    def choice(self, text, handler):
        button = QPushButton(text); button.setObjectName('choice'); button.setCursor(Qt.PointingHandCursor); button.setMinimumWidth(560); button.clicked.connect(handler); return button

    def open_url(self, url): QDesktopServices.openUrl(QUrl(url))

    def status_label(self, name):
        label = QLabel(''); label.setObjectName('status'); label.setWordWrap(True); label.setAlignment(Qt.AlignHCenter); label.setMinimumWidth(560); setattr(self, name, label); return label

    def make_steps(self):
        s = self.settings; redirect = f'http://localhost:{ig.LOGIN_PORT}/'
        token_path = lambda: self.use_token or not self.advanced   # the developer screens: only on the advanced path, and not with a pasted token
        app_path = lambda: not self.use_token
        own_hosting = lambda: not self.advanced

        def welcome_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0)
            if branding.simple_setup(): col.addWidget(self.link('Advanced: use my own Meta app and hosting account', self.choose_advanced), 0, Qt.AlignHCenter)
            return box

        def login_kind_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(12)
            col.addWidget(self.choice('Facebook Login\nMy Instagram account is linked to a Facebook Page', lambda: self.choose_login('facebook')))
            col.addWidget(self.choice('Instagram Login\nJust my Instagram account', lambda: self.choose_login('instagram')))
            col.addWidget(self.link('I already have an access token', self.choose_token), 0, Qt.AlignHCenter); return box

        def open_meta_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(self.choice('Open developers.facebook.com', lambda: self.open_url('https://developers.facebook.com/'))); return box

        def redirect_control():
            box = QWidget(); row = QHBoxLayout(box); row.setContentsMargins(0, 0, 0, 0)
            edit = QLineEdit(redirect); edit.setReadOnly(True); edit.setMinimumWidth(420); edit.setAlignment(Qt.AlignHCenter); row.addWidget(edit)
            copy = QPushButton('Copy'); copy.setCursor(Qt.PointingHandCursor); copy.clicked.connect(lambda: (QGuiApplication.clipboard().setText(redirect), copy.setText('Copied'))); row.addWidget(copy); return box

        def signin_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(10)
            col.addWidget(self.status_label('signin_status'))
            self.code_edit = QLineEdit(); self.code_edit.setPlaceholderText('Paste the code=… part of the browser\'s address here'); self.code_edit.setMinimumWidth(560); self.code_edit.hide()
            self.code_edit.returnPressed.connect(self.use_code); col.addWidget(self.code_edit)
            links = QHBoxLayout(); links.addStretch(1)
            links.addWidget(self.link('The browser ended on an error page', lambda: (self.code_edit.show(), self.code_edit.setFocus())))
            links.addWidget(self.link('Paste a token instead', self.choose_token)); links.addStretch(1); col.addLayout(links)
            if not self.advanced:
                self.kind_link = self.link('', self.toggle_kind); col.addWidget(self.kind_link, 0, Qt.AlignHCenter); self.update_kind_link()
            return box

        def token_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(10)
            col.addWidget(self.field('token_edit', 'Access token', password=True)); col.addWidget(self.status_label('token_status')); return box

        def account_control():
            self.account_combo = QComboBox(); self.account_combo.setMinimumWidth(560); return self.account_combo

        def open_cloudinary_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0)
            col.addWidget(self.choice('Open cloudinary.com and create a free account', lambda: self.open_url('https://cloudinary.com/users/register_free'))); return box

        def test_control():
            return self.status_label('hosting_status')

        def time_control():
            self.time_edit = QTimeEdit(); self.time_edit.setDisplayFormat('h:mm AP'); self.time_edit.setMinimumWidth(260); self.time_edit.setAlignment(Qt.AlignHCenter)
            self.time_edit.setTime(QTime(int(s.get('default_hour', 9)), int(s.get('default_minute', 0)))); return self.time_edit

        def startup_control():
            box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(12)
            col.addWidget(self.choice('Yes, start PostQueue when I sign in', lambda: self.choose_startup(True)))
            col.addWidget(self.choice('No, I will open it myself', lambda: self.choose_startup(False))); return box

        return [
            Step('welcome', "Let's set up PostQueue",
                 'Two things: sign in to Instagram, and pick your usual posting time. Then you only ever drop in photos and pick a date.'
                 if not self.advanced else
                 'Three things, one at a time: your Instagram account, a place to host your photos, and your usual posting time. '
                 'About fifteen minutes the first time, then you only ever drop in photos and pick a date.', build=welcome_control, primary='Start'),
            Step('professional', 'Use a professional Instagram account',
                 'In the Instagram app: Settings › Account type and tools › Switch to professional account. Business or Creator, both are free.',
                 hint='Already professional? Just carry on. For Facebook Login, the account also needs a linked Facebook Page: Instagram › Settings › Business tools › Connect a Facebook Page.',
                 primary='Done, next'),
            Step('login_kind', 'How will you sign in?', 'Both work. Facebook Login needs the account linked to a Facebook Page '
                 '(Instagram › Settings › Business tools › Connect a Facebook Page).', build=login_kind_control, primary=None, skip_if=lambda: not self.advanced),
            Step('create_app', 'Create a Meta app', 'On the developer site, use Log In (top right) with your Facebook account, open My Apps, click Create app, '
                 'choose the Business type (or the Instagram use case), and add the Instagram product. Keep the app in development mode.',
                 hint='Going straight to the apps list while logged out shows "page not found"; log in first.',
                 build=open_meta_control, primary='I created it, next', skip_if=token_path),
            Step('app_id', 'Paste the App ID', 'In the app dashboard: App settings › Basic › App ID. PostQueue checks it with Meta when you press Next.',
                 build=lambda: self.checked_field('app_id_edit', 'App ID', 'app_id_status', text=s.get('app_id', '')), ready=lambda: bool(self.app_id_edit.text().strip()),
                 on_next=self.check_app_id, skip_if=token_path, enter=lambda: self.app_id_status.setText('')),
            Step('app_secret', 'Paste the App secret', 'App settings › Basic › App secret (click Show). PostQueue proves the ID and secret match before moving on.',
                 hint='With the secret, the sign-in lasts 60 days. Instagram Login always needs it; a Facebook sign-in without it lasts about two hours.',
                 build=lambda: self.checked_field('app_secret_edit', 'App secret', 'secret_status', password=True, text=s.get('app_secret', '')),
                 ready=lambda: bool(self.app_secret_edit.text().strip()) or s.get('login_kind') == 'facebook', on_next=self.check_secret,
                 skip_if=token_path, skippable=True, enter=lambda: self.secret_status.setText('')),
            Step('redirect', 'Let the sign-in come back to PostQueue', 'In the app dashboard, open Facebook Login › Settings (or Instagram › Business login settings) '
                 'and add this address to Valid OAuth Redirect URIs:', build=redirect_control, primary='Added, next', skip_if=token_path),
            Step('roles', 'Add yourself as a tester', 'App roles › Roles: add your own account as an administrator or tester. For Instagram Login, also add the '
                 'Instagram account under Instagram testers and accept the invitation in the Instagram app (Settings › Apps and websites › Tester invites).',
                 primary='Done, next', skip_if=token_path),
            Step('signin', 'Sign in', 'The browser opens Meta\'s sign-in. Approve it there, then come back here. This screen moves on by itself.',
                 build=signin_control, primary='Sign in in the browser', on_next=self.sign_in, skip_if=lambda: self.use_token, enter=lambda: self.signin_status.setText('')),
            Step('token', 'Paste the access token', 'A Meta Business Suite system-user token (never expires), or a Graph API Explorer token with '
                 'instagram_basic and instagram_content_publish.', build=token_control, primary='Check token', on_next=self.check_token,
                 ready=lambda: bool(self.token_edit.text().strip()), skip_if=app_path),
            Step('account', 'Which account?', 'Several Instagram accounts were found. Choose the one to post as.', build=account_control,
                 on_next=lambda: self.pick_account(), skip_if=lambda: len(self.found_accounts) <= 1, enter=self.fill_accounts),
            Step('cloud_signup', 'Create a free Cloudinary account', 'Instagram fetches each photo from a link, so PostQueue needs somewhere to host it for a moment. '
                 'Cloudinary\'s free plan is far more than a personal queue needs.', build=open_cloudinary_control, primary='I have an account, next', skip_if=own_hosting),
            Step('cloud_name', 'Paste the Cloud name', 'Cloudinary console › Settings › API Keys (or the dashboard\'s Product environment credentials).',
                 build=lambda: self.field('cloud_edit', 'Cloud name', text=s.get('cloudinary_cloud', '')), ready=lambda: bool(self.cloud_edit.text().strip()), on_next=lambda: self.save_hosting(), skip_if=own_hosting),
            Step('api_key', 'Paste the API key', 'From the same Cloudinary page.',
                 build=lambda: self.field('ckey_edit', 'API key', text=s.get('cloudinary_api_key', '')), ready=lambda: bool(self.ckey_edit.text().strip()), on_next=lambda: self.save_hosting(), skip_if=own_hosting),
            Step('api_secret', 'Paste the API secret', 'From the same Cloudinary page (click to reveal it).',
                 build=lambda: self.field('csecret_edit', 'API secret', password=True, text=s.get('cloudinary_api_secret', '')), ready=lambda: bool(self.csecret_edit.text().strip()), on_next=lambda: self.save_hosting(), skip_if=own_hosting),
            Step('test_hosting', 'Test the hosting', 'PostQueue uploads a tiny test image and removes it again. This screen moves on when it works.',
                 build=test_control, primary='Test hosting', on_next=self.test_hosting, skippable=True, enter=lambda: self.hosting_status.setText(''), skip_if=own_hosting),
            Step('time', 'When do you usually post?', 'Each photo you add is scheduled for the next free day at this time. You can change any of them.',
                 build=time_control, on_next=lambda: self.save_time()),
            Step('startup', 'Start PostQueue when you sign in?', 'Posts only go out while PostQueue is running, so this is the safe choice.', build=startup_control, primary=None,
                 skip_if=lambda: not self.advanced),
            Step('done', 'All set', f'Drop photos or videos onto the window and pick a date for each. Closing the window keeps PostQueue running in {TRAY_NAME}.',
                 primary='Start using PostQueue'),
        ]

    # --- navigation -----------------------------------------------------------------------------------------------
    def step(self): return self.steps[self.index]

    def visible_indexes(self): return [i for i, st in enumerate(self.steps) if not (st.skip_if and st.skip_if())]

    def show_step(self, index):
        self.index = index; step = self.steps[index]; self.stack.setCurrentIndex(index)
        if step.enter: step.enter()
        middle = [i for i in self.visible_indexes() if 0 < i < len(self.steps) - 1]   # welcome and done are not steps
        self.progress.setText(f'STEP {middle.index(index) + 1} OF {len(middle)}' if index in middle else '')
        self.primary.setVisible(step.primary is not None); self.primary.setText(step.primary or '')
        self.skip_button.setVisible(step.skippable); self.back_button.setVisible(bool(self.history))
        self.refresh_ready()
        if step.primary: self.primary.setFocus()
        for child in step.page.findChildren(QLineEdit):
            if child.isVisible() and not child.isReadOnly(): child.setFocus(); break

    def refresh_ready(self, *_):
        step = self.step(); self.primary.setEnabled(self.task is None and (step.ready() if step.ready else True))

    def next(self):
        step = self.step()
        if step.on_next and step.on_next() is False: return   # the step is doing something first and advances itself
        self.advance()

    def advance(self):
        later = [i for i in self.visible_indexes() if i > self.index]
        if not later: return self.accept()
        self.history.append(self.index); self.show_step(later[0])

    def goto(self, key):
        self.history.append(self.index); self.show_step(next(i for i, st in enumerate(self.steps) if st.key == key))

    def skip(self):
        if self.step().key == 'app_secret': self.app_secret_edit.clear(); self.save_app()
        self.advance()

    def back(self):
        if self.history: self.show_step(self.history.pop())

    # --- choices and actions --------------------------------------------------------------------------------------
    def checked_field(self, name, placeholder, status_name, password=False, text=''):
        """A single field with a line under it for the result of the check that runs when Next is pressed."""
        box = QWidget(); col = QVBoxLayout(box); col.setContentsMargins(0, 0, 0, 0); col.setSpacing(10)
        col.addWidget(self.field(name, placeholder, password=password, text=text)); col.addWidget(self.status_label(status_name)); return box

    def check_app_id(self):
        """Look the App ID up at Meta: a public app shows its name; an app in development mode cannot be seen this way and is
        confirmed on the next screen together with the secret."""
        self.save_app(); app_id, version = self.settings['app_id'], self.settings.get('api_version')
        def done(info):
            self.app_id_status.setText(f'Found: {info["name"]}.' if info else 'Meta keeps apps in development mode private, so this ID is confirmed on the next screen with the secret.')
            QTimer.singleShot(900 if info else 1600, self.advance)
        self.run_task(lambda: ig.app_info(app_id, version), done, self.app_id_status, 'Checking with Meta…')
        return False

    def check_secret(self):
        """Prove the ID and secret belong together by asking Meta for an app token. Without a secret (Facebook Login), move on."""
        self.save_app(); app_id, secret, version = self.settings['app_id'], self.settings['app_secret'], self.settings.get('api_version')
        if not secret: return True
        def done(token):
            self.secret_status.setText('The App ID and secret match.'); QTimer.singleShot(700, self.advance)
        self.run_task(lambda: ig.app_token(app_id, secret, version), done, self.secret_status, 'Checking the ID and secret with Meta…')
        return False

    def choose_login(self, kind): self.use_token = False; self.settings['login_kind'] = kind; self.settings.save(); self.advance()
    def choose_token(self): self.use_token = True; self.goto('token')

    def choose_advanced(self):
        self.advanced = True; self.settings['hosting'] = 'cloudinary'; self.settings.save(); self.advance()

    def toggle_kind(self):
        self.settings['login_kind'] = 'instagram' if self.settings.get('login_kind') == 'facebook' else 'facebook'; self.settings.save(); self.update_kind_link()

    def update_kind_link(self):
        self.kind_link.setText('Use Instagram Login instead (no Facebook Page)' if self.settings.get('login_kind') == 'facebook' else 'Use Facebook Login instead')
    def choose_startup(self, on): self.settings['start_with_windows'] = bool(on); self.settings.save(); self.advance()

    def save_app(self):
        self.settings['app_id'] = self.app_id_edit.text().strip(); self.settings['app_secret'] = self.app_secret_edit.text().strip(); self.settings.save()

    def save_hosting(self):
        self.settings['cloudinary_cloud'] = self.cloud_edit.text().strip(); self.settings['cloudinary_api_key'] = self.ckey_edit.text().strip()
        self.settings['cloudinary_api_secret'] = self.csecret_edit.text().strip(); self.settings.save()

    def save_time(self):
        t = self.time_edit.time(); self.settings['default_hour'], self.settings['default_minute'] = t.hour(), t.minute(); self.settings.save()

    def run_task(self, fn, on_done, label, waiting):
        if self.task is not None: return
        self.cancelled = False; label.setText(waiting); self.refresh_ready()
        self.task = Task(fn)
        def finish(): self.task = None; self.refresh_ready()
        def ok(result): finish(); on_done(result)
        def bad(message): finish(); label.setText(message)
        self.task.done.connect(ok); self.task.failed.connect(bad); self.task.start()

    def sign_in(self):
        kind, version = self.settings.get('login_kind', 'facebook'), self.settings.get('api_version')
        if self.advanced: app_id, secret, service = self.settings.get('app_id', ''), self.settings.get('app_secret', ''), ''
        else: app_id, secret, service = branding.META_APP_ID, '', branding.SERVICE_URL
        self.run_task(lambda: ig.run_login(kind, app_id, secret, version, cancelled=lambda: self.cancelled, service_url=service), self.token_received, self.signin_status,
                      'Waiting for the browser… approve the sign-in there, then come back here.')
        return False

    def use_code(self):
        code = self.code_edit.text().strip()
        if not code: return
        kind, app_id, secret, version = self.settings.get('login_kind', 'facebook'), self.settings.get('app_id', ''), self.settings.get('app_secret', ''), self.settings.get('api_version')
        redirect = f'http://localhost:{ig.LOGIN_PORT}/'
        def exchange():
            token, expires = ig.exchange_code(kind, app_id, secret, redirect, code, version)
            if secret:
                with contextlib.suppress(ig.InstagramError): token, expires = ig.long_lived(kind, app_id, secret, token, version)
            return token, expires, redirect
        self.run_task(exchange, self.token_received, self.signin_status, 'Exchanging the code…')

    def token_received(self, result):
        token, expires, _ = result
        self.settings['access_token'] = token
        self.settings['token_expires'] = eng.fmt_when(eng.now_local() + dt.timedelta(seconds=int(expires))) if expires else ''
        self.settings.save(); self.find_accounts(token, self.signin_status)

    def check_token(self):
        token = self.token_edit.text().strip()
        self.settings['access_token'] = token; self.settings['token_expires'] = ''; self.settings.save(); self.find_accounts(token, self.token_status)
        return False

    def find_accounts(self, token, label):
        version = self.settings.get('api_version')
        self.run_task(lambda: ig.accounts_for_token(token, version), self.accounts_found, label, 'Looking up the Instagram account…')

    def accounts_found(self, found):
        self.found_accounts = found; self.apply_account(found[0])
        label = self.token_status if self.use_token else self.signin_status
        label.setText(f'Connected as @{found[0]["username"]}.'); QTimer.singleShot(700, self.advance)

    def fill_accounts(self):
        self.account_combo.clear()
        for acc in self.found_accounts: self.account_combo.addItem(f'@{acc["username"] or acc["id"]}' + (f'  (via {acc["via"]})' if acc.get('via') else ''), acc)

    def pick_account(self):
        acc = self.account_combo.currentData()
        if acc: self.apply_account(acc)

    def apply_account(self, acc):
        self.settings['ig_user_id'] = acc['id']; self.settings['ig_username'] = acc.get('username', ''); self.settings['api_host'] = acc.get('host') or ig.FB_HOST
        if self.settings['api_host'] == ig.IG_HOST: self.settings['login_kind'] = 'instagram'
        self.settings.save()

    def test_hosting(self):
        try: provider = hosting.make_provider(self.settings, branding.SERVICE_URL) or hosting.Cloudinary('', '', '')
        except hosting.HostingError as exc: self.hosting_status.setText(str(exc)); return False
        self.run_task(provider.test, lambda url: (self.hosting_status.setText('Hosting works.'), QTimer.singleShot(700, self.advance)), self.hosting_status, 'Uploading a test image…')
        return False

    def accept(self):
        if self.settings.get('start_with_windows') is None: self.settings['start_with_windows'] = True   # the simple path does not ask
        self.settings['setup_done'] = True; self.settings.save(); super().accept()


SetupWizard = SetupFlow   # the first version's name


class MainWindow(QMainWindow):
    def __init__(self, engine, settings, lock=None):
        super().__init__()
        self.engine, self.settings, self.lock = engine, settings, lock
        self.worker = None; self.current = None; self.dirty = False; self.thumbs = {}; self.video_infos = {}; self.quitting = False
        self.setWindowTitle(TITLE); self.setObjectName('main'); self.resize(1180, 720); self.setAcceptDrops(True); self.setStyleSheet(APP_STYLE)
        self.build(); self.build_tray()
        self.refresh()
        self.timer = QTimer(self); self.timer.timeout.connect(self.tick); self.timer.start(CHECK_SECONDS * 1000)
        self.poll = QTimer(self); self.poll.timeout.connect(self.poll_show_request); self.poll.start(1500)
        QTimer.singleShot(1500, self.tick)
        QTimer.singleShot(3000, self.maintain_token)
        if settings.get('update_check', True): QTimer.singleShot(6000, self.check_update)
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
        self.act_sheet = QAction(style.standardIcon(QStyle.SP_FileDialogListView), 'Add from a sheet…', self)
        self.act_sheet.setToolTip('A CSV with the columns file, date, time, caption, first_comment, alt_text, target'); self.act_sheet.triggered.connect(self.add_sheet)
        self.act_settings = QAction(style.standardIcon(QStyle.SP_FileDialogDetailedView), 'Settings…', self)
        self.act_settings.setShortcut(QKeySequence.Preferences if cfg.MAC else 'Ctrl+,'); self.act_settings.triggered.connect(self.open_settings)
        self.act_quit = QAction('Quit PostQueue', self); self.act_quit.setShortcut(QKeySequence.Quit if cfg.MAC else 'Ctrl+Q'); self.act_quit.triggered.connect(self.quit)
        self.act_quit.setMenuRole(QAction.QuitRole)
        for act in (self.act_add, self.act_sheet, self.act_pause, self.act_settings): bar.addAction(act)
        spacer = QWidget(); spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred); bar.addWidget(spacer)
        self.account_label = QLabel(''); self.account_label.setContentsMargins(8, 0, 8, 0); bar.addWidget(self.account_label)
        self.addAction(self.act_quit)

        central = QWidget(); outer = QVBoxLayout(central); outer.setContentsMargins(8, 8, 8, 8); outer.setSpacing(8)
        self.banner = QFrame(); self.banner.setObjectName('banner'); self.banner.setFrameShape(QFrame.StyledPanel)
        row = QHBoxLayout(self.banner); row.setContentsMargins(14, 10, 14, 10); self.banner_text = QLabel(''); self.banner_text.setWordWrap(True); row.addWidget(self.banner_text, 1)
        self.banner_button = QPushButton('Finish setup'); self.banner_button.setObjectName('banner_button'); self.banner_button.clicked.connect(self.open_setup); row.addWidget(self.banner_button)
        outer.addWidget(self.banner)
        # The confirmation after a post goes out (green) or fails (red); stays until dismissed.
        self.result = QFrame(); self.result.setObjectName('result'); self.result.setFrameShape(QFrame.StyledPanel); self.result_url = ''
        rrow = QHBoxLayout(self.result); rrow.setContentsMargins(14, 10, 14, 10)
        self.result_text = QLabel(''); self.result_text.setObjectName('result_text'); self.result_text.setWordWrap(True); rrow.addWidget(self.result_text, 1)
        self.result_open = QPushButton('Open on Instagram'); self.result_open.setObjectName('result_open'); self.result_open.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.result_url))); rrow.addWidget(self.result_open)
        self.result_dismiss = QPushButton('Dismiss'); self.result_dismiss.setObjectName('result_dismiss'); self.result_dismiss.clicked.connect(self.result.hide); rrow.addWidget(self.result_dismiss)
        outer.addWidget(self.result); self.result.hide()

        split = QSplitter(Qt.Horizontal); outer.addWidget(split, 1)
        self.tree = QTreeWidget(); self.tree.setObjectName('queue'); self.tree.setAccessibleName('Scheduled posts')
        self.tree.setHeaderLabels(['', 'Media', 'Scheduled for', 'Status']); self.tree.setIconSize(QSize(64, 64)); self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection); self.tree.setUniformRowHeights(True); self.tree.setAlternatingRowColors(True)
        header = self.tree.header(); header.setStretchLastSection(False); header.setSectionResizeMode(1, QHeaderView.Stretch)
        for column, width in ((0, 76), (2, 210), (3, 110)): self.tree.setColumnWidth(column, width)
        self.tree.itemSelectionChanged.connect(self.selection_changed); self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.context_menu)
        self.empty = QLabel('Drop photos or videos here, or click Add photos or videos.\n\nEach one is scheduled for the next free posting time; change it on the right.')
        self.empty.setAlignment(Qt.AlignCenter); self.empty.setWordWrap(True); self.empty.setObjectName('empty')
        left = QWidget(); ll = QVBoxLayout(left); ll.setContentsMargins(0, 0, 0, 0); ll.addWidget(self.tree, 1); ll.addWidget(self.empty, 1)
        split.addWidget(left)

        self.detail = QWidget(); self.detail.setObjectName('detail'); dl = QVBoxLayout(self.detail); dl.setContentsMargins(4, 0, 0, 0)
        self.preview = PreviewLabel(); self.preview.setStyleSheet('background: #1f1f23; border-radius: 6px;'); self.preview.setMinimumHeight(220); self.preview.setMaximumHeight(360)
        dl.addWidget(self.preview, 1)
        self.name_label = QLabel(''); self.name_label.setObjectName('name'); self.name_label.setWordWrap(True); dl.addWidget(self.name_label)
        self.media_info = QLabel(''); self.media_info.setObjectName('media_info'); self.media_info.setWordWrap(True); self.media_info.setStyleSheet(MUTED_STYLE); dl.addWidget(self.media_info)
        form = QFormLayout(); form.setLabelAlignment(Qt.AlignRight); dl.addLayout(form)
        when_box = QVBoxLayout(); when_box.setSpacing(4)
        self.when_edit = DateTimePicker(); self.when_edit.setObjectName('when'); self.when_edit.setAccessibleName('Scheduled date and time')
        self.when_edit.dateTimeChanged.connect(self.mark_dirty)
        when_box.addWidget(self.when_edit)
        quick = QHBoxLayout(); quick.setSpacing(6)
        for text, delta in (('+1 hour', dt.timedelta(hours=1)), ('+1 day', dt.timedelta(days=1)), ('+1 week', dt.timedelta(days=7))):
            b = QPushButton(text); b.setObjectName('quick'); b.setToolTip(f'Move the scheduled time by {text[1:]}'); b.clicked.connect(lambda _=False, d=delta: self.shift_when(d)); quick.addWidget(b)
        slot = QPushButton('Next free slot'); slot.setObjectName('quick'); slot.setToolTip('The next free default posting time (set in Settings > General)'); slot.clicked.connect(self.use_next_slot); quick.addWidget(slot)
        quick.addStretch(1); when_box.addLayout(quick)
        form.addRow('Post on', when_box)
        self.fit_combo = QComboBox(); self.fit_combo.setObjectName('fit'); self.fit_combo.addItem('Crop to fit Instagram', 'crop'); self.fit_combo.addItem('Add white borders', 'pad')
        self.fit_combo.setToolTip('Instagram accepts photos between 4:5 (portrait) and 1.91:1 (landscape). Photos outside that range are cropped or padded.')
        self.fit_combo.currentIndexChanged.connect(self.fit_changed); form.addRow('If the shape is off', self.fit_combo); self.fit_label = form.labelForField(self.fit_combo)
        self.target_combo = QComboBox(); self.target_combo.setObjectName('target'); self.target_combo.addItem('Feed post', 'feed'); self.target_combo.addItem('Story (gone after 24 hours, no caption)', 'story')
        self.target_combo.currentIndexChanged.connect(self.target_changed); form.addRow('Post as', self.target_combo); self.target_label = form.labelForField(self.target_combo)
        # The photos and videos in this post: one for a plain post, two to ten for a carousel
        items_box = QWidget(); ib = QVBoxLayout(items_box); ib.setContentsMargins(0, 0, 0, 0); ib.setSpacing(4)
        self.items_list = QListWidget(); self.items_list.setObjectName('items'); self.items_list.setFlow(QListWidget.LeftToRight); self.items_list.setIconSize(QSize(56, 56))
        self.items_list.setFixedHeight(84); self.items_list.setWrapping(False); self.items_list.setSelectionMode(QAbstractItemView.SingleSelection); ib.addWidget(self.items_list)
        items_buttons = QHBoxLayout(); items_buttons.setSpacing(6)
        self.add_item_button = QPushButton('Add another photo or video…'); self.add_item_button.setObjectName('quick'); self.add_item_button.clicked.connect(self.add_item_file)
        self.remove_item_button = QPushButton('Remove selected'); self.remove_item_button.setObjectName('quick'); self.remove_item_button.clicked.connect(self.remove_item)
        items_buttons.addWidget(self.add_item_button); items_buttons.addWidget(self.remove_item_button); items_buttons.addStretch(1); ib.addLayout(items_buttons)
        form.addRow('In this post', items_box); self.items_box = items_box; self.items_label = form.labelForField(items_box)
        self.caption = QPlainTextEdit(); self.caption.setObjectName('caption'); self.caption.setAccessibleName('Caption'); self.caption.setPlaceholderText('Caption, #hashtags and @mentions')
        self.caption.setMaximumHeight(140); self.caption.textChanged.connect(self.caption_changed); form.addRow('Caption', self.caption)
        self.caption_info = QLabel(''); self.caption_info.setStyleSheet(MUTED_STYLE); form.addRow('', self.caption_info)
        self.first_comment = QLineEdit(); self.first_comment.setObjectName('first_comment'); self.first_comment.setPlaceholderText('Posted as the first comment, right after publishing (hashtags go well here)')
        self.first_comment.textChanged.connect(self.mark_dirty); form.addRow('First comment', self.first_comment); self.first_comment_label = form.labelForField(self.first_comment)
        self.alt_text = QLineEdit(); self.alt_text.setObjectName('alt_text'); self.alt_text.setPlaceholderText('What the photo shows, for people who cannot see it')
        self.alt_text.textChanged.connect(self.mark_dirty); form.addRow('Alt text', self.alt_text); self.alt_text_label = form.labelForField(self.alt_text)
        self.cover_spin = QDoubleSpinBox(); self.cover_spin.setObjectName('cover'); self.cover_spin.setRange(0, 900); self.cover_spin.setDecimals(1); self.cover_spin.setSuffix(' s')
        self.cover_spin.setToolTip('Which moment of the video becomes its cover picture'); self.cover_spin.valueChanged.connect(self.mark_dirty)
        form.addRow('Cover at', self.cover_spin); self.cover_label = form.labelForField(self.cover_spin)
        # The status line and the buttons stay visible below the scrolling form
        self.actions = QWidget(); self.actions.setObjectName('actions'); al = QVBoxLayout(self.actions); al.setContentsMargins(4, 6, 0, 0); al.setSpacing(6)
        self.status_label = QLabel(''); self.status_label.setWordWrap(True); self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse); al.addWidget(self.status_label)
        buttons = QHBoxLayout(); al.addLayout(buttons)
        self.save_button = QPushButton('Save changes'); self.save_button.setObjectName('save'); self.save_button.setShortcut(QKeySequence.Save); self.save_button.clicked.connect(self.save_current)
        self.post_now_button = QPushButton('Post now'); self.post_now_button.setObjectName('post_now'); self.post_now_button.clicked.connect(self.post_now)
        self.open_button = QPushButton('Open on Instagram'); self.open_button.clicked.connect(self.open_permalink)
        self.duplicate_button = QPushButton('Duplicate'); self.duplicate_button.setObjectName('duplicate'); self.duplicate_button.setToolTip('A copy of this post, scheduled for the next free slot'); self.duplicate_button.clicked.connect(self.duplicate_current)
        self.remove_button = QPushButton('Remove'); self.remove_button.setObjectName('remove'); self.remove_button.clicked.connect(self.remove_selected)
        for b in (self.save_button, self.post_now_button, self.open_button): buttons.addWidget(b)
        buttons.addStretch(1)
        more = QHBoxLayout(); al.addLayout(more)
        self.draft_box = QCheckBox('Draft: keep, do not post yet'); self.draft_box.setObjectName('draft'); self.draft_box.toggled.connect(self.mark_dirty)
        for b in (self.duplicate_button, self.remove_button): more.addWidget(b)
        more.addWidget(self.draft_box); more.addStretch(1)
        dl.addStretch(1); self.detail.setMinimumWidth(380); left.setMinimumWidth(360)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.NoFrame); scroll.setWidget(self.detail); scroll.setObjectName('detail_scroll')
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); scroll.setMinimumWidth(400)
        right = QWidget(); rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(0); rl.addWidget(scroll, 1); rl.addWidget(self.actions)
        split.addWidget(right); split.setStretchFactor(0, 1); split.setStretchFactor(1, 1); split.setSizes([540, 520])
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
        order = {'posting': 0, 'queued': 1, 'missed': 2, 'failed': 3, 'draft': 4, 'posted': 5}
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
            self.preview.set_source(None); self.name_label.setText(''); self.media_info.setText(''); self.status_label.setText('')
            self.caption.blockSignals(True); self.caption.clear(); self.caption.blockSignals(False); self.caption_info.setText(''); self.items_list.clear()
            for b in (self.save_button, self.post_now_button, self.open_button, self.duplicate_button, self.remove_button, self.draft_box): b.setVisible(False)
            return
        self.preview.set_source(thumbnail(post.media, 'image' if post.kind == 'carousel' and eng.media_kind(post.media) == 'image' else post.kind, 800))
        self.name_label.setText(f'{post.name()}  ({post.type_text()})')
        fields = (self.when_edit, self.fit_combo, self.caption, self.target_combo, self.first_comment, self.alt_text, self.cover_spin, self.draft_box)
        for w in fields: w.blockSignals(True)
        self.when_edit.setDateTime(to_qdt(post.when_dt())); self.fit_combo.setCurrentIndex(max(0, self.fit_combo.findData(post.fit)))
        self.target_combo.setCurrentIndex(max(0, self.target_combo.findData(post.target))); self.caption.setPlainText(post.caption)
        self.first_comment.setText(post.first_comment); self.alt_text.setText(post.alt_text); self.cover_spin.setValue(float(post.cover_seconds or 0)); self.draft_box.setChecked(post.status == 'draft')
        for w in fields: w.blockSignals(False)
        self.fill_items(post); self.apply_kind_visibility(post); self.update_caption_info(); self.update_preview()
        editable = post.status in ('queued', 'failed', 'missed', 'draft')
        for w in (self.when_edit, self.fit_combo, self.target_combo, self.first_comment, self.alt_text, self.cover_spin, self.add_item_button, self.remove_item_button, self.draft_box): w.setEnabled(editable)
        self.caption.setEnabled(editable and post.target != 'story')
        text = post.status_text()
        if post.status == 'posted' and post.posted_at: text += f' on {eng.parse_when(post.posted_at):%a %d %b %Y at %H:%M}'
        if post.status == 'draft': text += ': kept, not scheduled. Untick Draft and save to schedule it.'
        if post.error: text += f'\n{post.error}'
        if post.status == 'posted' and post.warning: text += f'\n{post.warning}'
        if post.status in ('failed', 'missed'): text += '\nChange the time and save to schedule it again, or use Post now.'
        self.status_label.setText(text); self.status_label.setStyleSheet(f'color: {STATUS_COLOR.get(post.status) or "palette(text)"};')
        self.save_button.setVisible(editable); self.post_now_button.setVisible(editable); self.post_now_button.setEnabled(self.worker is None)
        self.open_button.setVisible(bool(post.permalink)); self.remove_button.setVisible(post.status != 'posting'); self.duplicate_button.setVisible(post.status != 'posting'); self.draft_box.setVisible(editable)
        self.save_button.setEnabled(False)

    def fill_items(self, post):
        self.items_list.clear()
        for n, item in enumerate(post.media_items()):
            kind = eng.media_kind(item) if Path(item).suffix else 'image'
            entry = QListWidgetItem(QIcon(thumbnail(item, kind, 56)), ''); entry.setToolTip((post.sources[n] if n < len(post.sources) else Path(item).name)); self.items_list.addItem(entry)

    def apply_kind_visibility(self, post):
        story, image, video, carousel = post.target == 'story', post.kind == 'image', post.kind == 'video', post.kind == 'carousel'
        for w in (self.fit_combo, self.fit_label): w.setVisible((image or carousel) and True)
        for w in (self.target_combo, self.target_label): w.setVisible(not carousel)
        for w in (self.items_box, self.items_label): w.setVisible(not story)
        for w in (self.first_comment, self.first_comment_label): w.setVisible(not story)
        for w in (self.alt_text, self.alt_text_label): w.setVisible(image and not story)
        for w in (self.cover_spin, self.cover_label): w.setVisible(video and not story)
        self.caption.setPlaceholderText('Stories have no caption' if story else 'Caption, #hashtags and @mentions')

    def target_changed(self, *_):
        if self.current and self.detail.isEnabled():
            self.current.target = self.target_combo.currentData() or 'feed'   # shown at once; saved with Save changes
            self.apply_kind_visibility(self.current); self.caption.setEnabled(self.current.target != 'story'); self.update_preview(); self.mark_dirty()

    def add_item_file(self):
        if not self.current: return
        path, _ = QFileDialog.getOpenFileName(self, 'Add a photo or video to this post', str(Path.home() / 'Pictures'), FILE_FILTER)
        if path: self.add_item_path(path)

    def add_item_path(self, path):
        try: self.engine.add_item(self.current, path)
        except (eng.EngineError, OSError) as exc: QMessageBox.warning(self, PRODUCT, str(exc)); return
        self.thumbs.pop(self.current.id, None); self.refresh(); self.statusBar().showMessage(f'{self.current.name()} is now a carousel of {len(self.current.items)}.', 6000)

    def remove_item(self):
        if not self.current or self.items_list.currentRow() < 0: return
        try: self.engine.remove_item(self.current, self.items_list.currentRow())
        except eng.EngineError as exc: QMessageBox.warning(self, PRODUCT, str(exc)); return
        self.thumbs.pop(self.current.id, None); self.refresh()

    def duplicate_current(self):
        if not self.current: return
        copy = self.engine.duplicate(self.current); self.refresh(); self.select_post(copy)
        self.statusBar().showMessage(f'Copied as a new post on {copy.when_dt():%a %d %b at %H:%M}.', 6000)

    def select_post(self, post):
        self.tree.clearSelection()
        for i in range(self.tree.topLevelItemCount()):
            if self.tree.topLevelItem(i).data(0, Qt.UserRole) == post.id: self.tree.topLevelItem(i).setSelected(True); self.tree.scrollToItem(self.tree.topLevelItem(i))

    def mark_dirty(self, *_):
        if self.current and self.detail.isEnabled(): self.dirty = True; self.save_button.setEnabled(True)

    def caption_changed(self): self.update_caption_info(); self.mark_dirty()
    def fit_changed(self, *_): self.update_preview(); self.mark_dirty()

    def update_preview(self):
        """Draw on the preview what Instagram will get, and say so under it: for a photo, the crop or the white borders
        the chosen fit gives; for a video, its length and size, or the warning from when it was added."""
        post, pm = self.current, self.preview.source
        if not post or pm is None: self.media_info.setText(''); return
        story = post.target == 'story'
        if post.kind == 'video' or (post.kind == 'carousel' and eng.media_kind(post.media) == 'video'):
            if post.id not in self.video_infos: self.video_infos[post.id] = eng.video_info(post.media)
            summary = eng.video_summary(self.video_infos[post.id])
            what = 'posted as a story' if story else 'posted as a Reel'
            self.media_info.setText(post.warning or (f'Video · {summary} · {what}' if summary else f'Video · {what}'))
            self.media_info.setStyleSheet(WARN_STYLE if post.warning else MUTED_STYLE); self.preview.set_box(None); return
        fit = self.fit_combo.currentData() or 'crop'
        box = eng.target_box(pm.width(), pm.height(), fit, eng.STORY_RATIO, eng.STORY_RATIO) if story else eng.target_box(pm.width(), pm.height(), fit); self.preview.set_box(box)
        whole = box == (0, 0, pm.width(), pm.height()); tall = pm.height() > pm.width()
        noun = 'Story · 9:16' if story else ('Carousel · first photo' if post.kind == 'carousel' else 'Photo')
        if whole: text = f'{noun} · within Instagram\'s shape range, posted whole'
        elif fit == 'crop': text = f'{noun} · the dimmed {"top and bottom" if tall else "sides"} will be cropped off'
        else: text = f'{noun} · white borders will be added {"at the sides" if tall else "above and below"}'
        self.media_info.setText(text); self.media_info.setStyleSheet(MUTED_STYLE)

    def update_caption_info(self):
        text = self.caption.toPlainText(); warnings = eng.caption_warnings(text)
        self.caption_info.setText(' '.join(warnings) if warnings else f'{len(text)} of {eng.CAPTION_LIMIT} characters')
        self.caption_info.setStyleSheet(BAD_STYLE if warnings else MUTED_STYLE)

    def save_fields(self):
        """Copy what is in the details pane into the current post (everything but the time and status)."""
        self.engine.update(self.current, caption=self.caption.toPlainText(), fit=self.fit_combo.currentData() or 'crop', target=self.target_combo.currentData() or 'feed',
                           first_comment=self.first_comment.text().strip(), alt_text=self.alt_text.text().strip(), cover_seconds=float(self.cover_spin.value()))

    def save_current(self):
        if not self.current: return
        when = from_qdt(self.when_edit.dateTime()); self.save_fields()
        if self.draft_box.isChecked():
            self.engine.update(self.current, status='draft', when=eng.fmt_when(when), error='', retry_at='', attempts=0)
            self.dirty = False; self.refresh(); self.statusBar().showMessage(f'Saved as a draft. {self.current.name()} will not post until you untick Draft.', 5000); return
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

    def ask_how_to_add(self, count):
        """Several files at once: one post each, or all of them in one carousel post."""
        box = QMessageBox(self); box.setWindowTitle(PRODUCT); box.setText(f'{count} files. How should they be posted?')
        box.setInformativeText('One carousel post shows them together, swiped through; up to 10.')
        each = box.addButton('One post each', QMessageBox.AcceptRole); together = box.addButton('One carousel post', QMessageBox.ActionRole); box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(each); box.exec()
        return 'each' if box.clickedButton() is each else 'carousel' if box.clickedButton() is together else None

    def add_paths(self, paths, mode=None):
        """Add files as posts. `mode`: 'each' (default for one file), 'carousel', or None to ask when there are several."""
        paths = list(paths); added, problems = [], []
        if len(paths) > 1 and mode is None: mode = self.ask_how_to_add(len(paths))
        if mode is None and len(paths) > 1: return []
        if mode == 'carousel':
            try: added.append(self.engine.add_carousel(paths[:eng.CAROUSEL_MAX], self.engine.next_slot()))
            except (eng.EngineError, OSError) as exc: problems.append(str(exc))
            if len(paths) > eng.CAROUSEL_MAX: problems.append(f'Only the first {eng.CAROUSEL_MAX} files went into the carousel; the rest were left out.')
        else:
            for path in paths:
                try: added.append(self.engine.add(path, self.engine.next_slot()))
                except eng.EngineError as exc: problems.append(str(exc))
                except OSError as exc: problems.append(f'{Path(path).name}: {exc.strerror or exc}')
        self.refresh()
        if added:
            self.select_post(added[-1])
            self.statusBar().showMessage(f'Added {len(added)} post{"s" if len(added) > 1 else ""}. Pick the date and time on the right, then Save changes.', 8000)
        if problems: QMessageBox.warning(self, PRODUCT, '\n'.join(problems))
        return added

    def add_sheet(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Add posts from a sheet', str(Path.home()), 'Sheets (*.csv);;All files (*)')
        if path: self.add_sheet_path(path)

    def add_sheet_path(self, path):
        try: added, problems = self.engine.import_sheet(path)
        except (OSError, UnicodeDecodeError) as exc: QMessageBox.warning(self, PRODUCT, f'Could not read the sheet: {exc}'); return []
        self.refresh()
        if added: self.select_post(added[-1])
        summary = f'Added {len(added)} post{"s" if len(added) != 1 else ""} from {Path(path).name}.'
        if problems: QMessageBox.warning(self, PRODUCT, summary + '\n\nNot added:\n' + '\n'.join(problems))
        else: self.statusBar().showMessage(summary, 8000)
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
        return instagram, hosting.make_provider(self.settings, branding.SERVICE_URL)

    def set_paused(self, paused):
        self.settings['paused'] = bool(paused); self.settings.save(); self.act_pause.setText('Resume posting' if paused else 'Pause posting'); self.update_next()

    def tick(self):
        if self.worker is not None or self.settings.get('paused'): return
        post = self.engine.check()
        if post: self.start_publish(post)
        else: self.update_next()

    def post_now(self):
        if not self.current or self.worker is not None: return
        if QMessageBox.question(self, PRODUCT, f'Post {self.current.name()} to Instagram now?', QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes: return
        if self.dirty: self.save_fields(); self.dirty = False
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
            self.show_result(post); self.statusBar().showMessage(f'Posted {post.name()} to Instagram.', 10000)
            if self.tray and not self.isVisible(): self.tray.showMessage(PRODUCT, f'Posted {post.name()} to Instagram.', QSystemTrayIcon.Information, 8000)
        elif post.status == 'failed':
            self.show_result(post)
            if self.tray and not self.isVisible(): self.tray.showMessage(PRODUCT, f'Could not post {post.name()}: {post.error}', QSystemTrayIcon.Warning, 15000)
        QTimer.singleShot(500, self.tick)   # more posts may be due

    def show_result(self, post):
        """The confirmation strip at the top of the window: what was posted and when, with a link to it, or why it failed."""
        ok = post.status == 'posted'
        if ok:
            when = eng.parse_when(post.posted_at) if post.posted_at else eng.now_local()
            self.result_text.setText(f'✓ Posted {post.name()} to Instagram at {when:%H:%M} on {when:%a %d %b}.' + (f' {post.warning}' if post.warning else '')); self.result_url = post.permalink
        else:
            self.result_text.setText(f'✗ Could not post {post.name()}: {post.error}'); self.result_url = ''
        self.result_open.setText('Open on Instagram'); self.result_open.setVisible(bool(self.result_url))
        self.show_strip('true' if ok else 'false')

    def show_strip(self, state):
        self.result.setProperty('ok', state); self.result.style().unpolish(self.result); self.result.style().polish(self.result); self.result.show()

    def check_update(self):
        """Once a day at most: is there a newer release on GitHub? Quiet unless there is."""
        task = Task(lambda: eng.check_for_update(VERSION))
        def found(result):
            if not result or self.result.isVisible(): return
            tag, url = result
            self.result_text.setText(f'PostQueue {tag.lstrip("v")} is available. You have {VERSION}.'); self.result_url = url
            self.result_open.setText('Download'); self.result_open.setVisible(bool(url)); self.show_strip('update')
        task.done.connect(found); task.start(); self.update_task = task

    def update_next(self):
        if self.settings.get('paused'): self.next_label.setText('Posting is paused'); return
        nxt = self.engine.next_scheduled()
        self.next_label.setText(f'Next: {nxt.name()} on {nxt.when_dt():%a %d %b at %H:%M}' if nxt else 'Nothing scheduled')

    def update_banner(self):
        problems = []
        if not self.settings.connected(): problems.append('sign in to Instagram')
        if not self.settings.hosting_ready(branding.SERVICE_URL): problems.append('set up media hosting (Instagram fetches photos from a public link)')
        expiry = self.settings.token_expiry()
        if not problems and expiry and expiry - eng.now_local() < dt.timedelta(days=7):
            problems.append(f'the Instagram sign-in expires on {expiry:%d %b}; sign in again in Settings')
        self.banner.setVisible(bool(problems))
        if problems:
            self.banner_text.setText('Before posts can go out: ' + '; '.join(problems) + '.')
            self.banner_button.setText('Finish setup' if not self.settings.ready(branding.SERVICE_URL) else 'Open Settings')
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
    def needs_setup(self): return not self.settings.get('setup_done') and not self.settings.ready(branding.SERVICE_URL)

    def open_setup(self):
        """The first-run flow (one thing per screen); the settings dialog once set-up is complete."""
        if self.settings.ready(branding.SERVICE_URL): return self.open_settings()
        flow = SetupFlow(self.settings, self); flow.exec()
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
        if not cfg.WINDOWS:   # a termination signal (log-out, `kill`) ends the app the clean way, so the lock is released
            def terminate(*_): window.quitting = True; window.close()
            with contextlib.suppress(Exception): signal.signal(signal.SIGTERM, terminate)
        if minimized and window.tray: window.hide()
        else:
            window.show()
            if window.needs_setup(): QTimer.singleShot(300, window.open_setup)
        return app.exec()
    finally:
        lock.release()
