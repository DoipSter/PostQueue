"""Looping "show me" demos for the setup screens.

Each demo is a small mock of the page the user has to visit (Meta's developer dashboard, the Instagram app, the
Cloudinary console) with the pointer travelling to the thing to click, a click ripple, and a one-line caption. It is
drawn with QPainter, so it needs no pictures, stays sharp at any size, and replays by itself until the screen changes.
Clicking the demo skips to the next frame.

A scene is a list of frames; a frame is (caption, page, target). A page is a dict describing a web page or a phone
screen; the target is the id of the element the pointer goes to.
"""
import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

W, H, CAPTION = 480, 270, 28                     # the mock is drawn on a 480x270 canvas and scaled to the widget
FRAME_MS, LAST_EXTRA_MS, TICK_MS = 2600, 1400, 40
MOVE, CLICK = 0.42, 0.58                         # phases within a frame: the pointer travels, clicks, then holds
RED, BLUE, GREEN = QColor('#e5392b'), QColor('#1877f2'), QColor('#31a24c')
PAGE, INK, MUTED, LINE, WHITE = QColor('#f0f2f5'), QColor('#1c1e21'), QColor('#8a8d91'), QColor('#dadde1'), QColor('white')
CHROME, CLOUD_DARK = QColor('#2b2b33'), QColor('#1b2033')
REDIRECT = 'http://localhost:48215/'


# --- page descriptions -------------------------------------------------------------------------------------------
def el(kind, id=None, rect=None, **kw):
    d = dict(kind=kind, id=id, rect=rect); d.update(kw); return d


META_MENU = ('Build with us ⌄', 'Docs', 'Blog', 'Resources ⌄', 'Developer centers ⌄')


def web(url, body, nav=(), side=(), title='Meta', menu=META_MENU, dark_side=False, white=False):
    """A browser window: `menu` is the site's own menu after its name, `nav` a list of (id, text, style) on the right of
    the top bar, `side` a list of (id, text, indent, active) rows in a left sidebar, `body` the elements on the page."""
    return dict(kind='web', url=url, nav=list(nav), side=list(side), body=list(body), title=title, menu=list(menu), dark_side=dark_side, white=white)


def phone(header, rows, back=True, note=''):
    """An Instagram-style settings screen: `rows` is a list of (id, text, extra) where extra is a small hint on the right."""
    return dict(kind='phone', header=header, rows=list(rows), back=back, note=note)


META_URL, APPS_URL, DASH_URL = 'developers.facebook.com', 'developers.facebook.com/apps', 'developers.facebook.com/apps/1234567890'
NAV_OUT = [('login', 'Login', 'plain'), ('search', '', 'search')]
NAV_IN = [('myapps', 'My Apps', 'button'), ('avatar', '', 'avatar')]
FRONT = [el('heading', rect=(0, 76, W, 30), text='Social technologies', size=22, center=True),
         el('text', rect=(0, 112, W, 14), text='Build technologies that help people connect, find communities and grow businesses.', center=True),
         el('heading', rect=(0, 150, W, 20), text='Get started', size=13, center=True),
         el('lines', rect=(40, 184, 400, 30), n=1)]


def dashboard(active, extra_side=()):
    side = [('dash', 'Dashboard', 0, active == 'dash'), ('usecases', 'Use cases', 0, False), ('settings', 'App settings', 0, False),
            ('basic', 'Basic', 1, active == 'basic'), ('roles', 'App roles', 0, False), ('roles_roles', 'Roles', 1, active == 'roles'),
            ('review', 'App review', 0, False), ('instagram', 'Instagram', 0, False), ('fblogin', 'Facebook Login', 0, False),
            ('fbsettings', 'Settings', 1, active == 'fbsettings')]
    return side + list(extra_side)


def basic_settings(secret_shown=False):
    return [el('heading', rect=(140, 56, 300, 18), text='Basic settings'),
            el('field', 'appid', rect=(140, 84, 150, 36), label='App ID', value='1234567890', copy=True),
            el('field', 'secret', rect=(300, 84, 160, 36), label='App secret', value='a1b2c3d4e5f6a7b8' if secret_shown else '••••••••••••••••',
               button=('show', 'Show') if not secret_shown else None, copy=secret_shown),
            el('field', None, rect=(140, 132, 320, 36), label='Display name', value='My PostQueue'),
            el('field', None, rect=(140, 180, 320, 36), label='Contact email', value='you@example.com'),
            el('button', None, rect=(400, 236, 64, 20), text='Save changes', style='blue')]


def cloudinary(target_rows=True):
    side = [('home', 'Home', 0, False), ('media', 'Media Library', 0, False), ('assets', 'Assets', 0, False), ('settings', 'Settings', 0, True),
            ('apikeys', 'API Keys', 1, True)]
    body = [el('heading', rect=(140, 56, 300, 18), text='API Keys'),
            el('field', 'cloud', rect=(140, 84, 150, 36), label='Cloud name', value='my-cloud', copy=True),
            el('field', 'key', rect=(140, 132, 200, 36), label='API key', value='123456789012345', copy=True),
            el('field', 'secret', rect=(140, 180, 240, 36), label='API secret', value='••••••••••••••••••', button=('reveal', 'Reveal'), copy=True)]
    return web('console.cloudinary.com/settings/api-keys', body, side=side, title='Cloudinary', menu=(), dark_side=True)


def scenes():
    create_app = [
        ('Login, at the top right (next to the search icon)', web(META_URL, FRONT, nav=NAV_OUT, white=True), 'login'),
        ('My Apps', web(META_URL, FRONT, nav=NAV_IN, white=True), 'myapps'),
        ('Create app', web(APPS_URL, [el('heading', rect=(30, 60, 200, 20), text='Apps'), el('lines', rect=(30, 96, 420, 60), n=3),
                                      el('button', 'create', rect=(370, 58, 80, 22), text='Create app', style='green')], nav=NAV_IN), 'create'),
        ('Choose Business (or the Instagram use case), then Create app',
         web(APPS_URL + '/creation', [el('heading', rect=(30, 56, 400, 20), text='Which type of app?'),
                                      el('option', 'consumer', rect=(30, 86, 300, 28), text='Consumer', on=False),
                                      el('option', 'business', rect=(30, 120, 300, 28), text='Business', on=True),
                                      el('option', 'other', rect=(30, 154, 300, 28), text='Other', on=False),
                                      el('button', 'next', rect=(370, 236, 80, 22), text='Create app', style='blue')], nav=NAV_IN), 'business'),
        ('Add the Instagram product (Set up)',
         web(DASH_URL, [el('heading', rect=(140, 56, 320, 18), text='Add products to your app'),
                        el('card', 'fb_setup', rect=(140, 84, 100, 70), text='Facebook Login'), el('card', 'ig_setup', rect=(252, 84, 100, 70), text='Instagram'),
                        el('card', 'wh_setup', rect=(364, 84, 100, 70), text='Webhooks')], nav=NAV_IN, side=dashboard('dash')), 'ig_setup'),
    ]
    app_id = [
        ('App settings › Basic', web(DASH_URL + '/settings/basic', basic_settings(), nav=NAV_IN, side=dashboard('basic')), 'basic'),
        ('Copy the App ID and paste it below', web(DASH_URL + '/settings/basic', basic_settings(), nav=NAV_IN, side=dashboard('basic')), 'appid'),
    ]
    app_secret = [
        ('App settings › Basic', web(DASH_URL + '/settings/basic', basic_settings(), nav=NAV_IN, side=dashboard('basic')), 'basic'),
        ('App secret › Show', web(DASH_URL + '/settings/basic', basic_settings(), nav=NAV_IN, side=dashboard('basic')), 'show'),
        ('Copy the secret and paste it below', web(DASH_URL + '/settings/basic', basic_settings(secret_shown=True), nav=NAV_IN, side=dashboard('basic')), 'secret'),
    ]
    login_settings = lambda uri: web(DASH_URL + '/fb-login/settings', [
        el('heading', rect=(140, 56, 320, 18), text='Facebook Login › Settings'),
        el('field', 'uris', rect=(140, 84, 320, 36), label='Valid OAuth Redirect URIs', value=uri),
        el('field', None, rect=(140, 132, 320, 36), label='Deauthorize callback URL', value=''),
        el('button', 'save', rect=(400, 236, 64, 20), text='Save changes', style='blue')], nav=NAV_IN, side=dashboard('fbsettings'))
    redirect = [
        ('Facebook Login › Settings (Instagram Login: Instagram › Business login settings)', login_settings(''), 'fbsettings'),
        ('Paste the address into Valid OAuth Redirect URIs', login_settings(REDIRECT), 'uris'),
        ('Save changes', login_settings(REDIRECT), 'save'),
    ]
    roles_page = lambda dialog: web(DASH_URL + '/roles/roles', [
        el('heading', rect=(140, 56, 200, 18), text='Roles'), el('button', 'add', rect=(380, 54, 84, 22), text='Add people', style='green'),
        el('text', rect=(140, 90, 300, 14), text='Administrators', bold=True), el('lines', rect=(140, 108, 300, 14), n=1),
        el('text', rect=(140, 136, 300, 14), text='Testers', bold=True), el('lines', rect=(140, 154, 300, 14), n=1)] + dialog,
        nav=NAV_IN, side=dashboard('roles'))
    add_people = [el('dialog', rect=(150, 60, 220, 190), text='Add people to this app', items=[
        el('option', 'admin', rect=(166, 92, 180, 24), text='Administrator', on=False), el('option', 'dev', rect=(166, 118, 180, 24), text='Developer', on=False),
        el('option', 'tester', rect=(166, 144, 180, 24), text='Tester', on=True),
        el('field', 'who', rect=(166, 176, 188, 34), label='Facebook name', value='Your name'), el('button', 'addbtn', rect=(300, 220, 54, 20), text='Add', style='blue')])]
    roles = [
        ('App roles › Roles', roles_page([]), 'roles_roles'),
        ('Add people', roles_page([]), 'add'),
        ('Choose Tester (or Administrator) and add your own account', roles_page(add_people), 'tester'),
        ('Instagram Login too: accept the invitation in the Instagram app (Settings › Apps and websites › Tester invites)',
         phone('Apps and websites', [('active', 'Active', ''), ('expired', 'Expired', ''), ('invites', 'Tester invites', '1'), ('removed', 'Removed', '')]), 'invites'),
    ]
    professional = [
        ('Instagram app › your profile › the menu at the top right',
         phone('yourname', [('edit', 'Edit profile', ''), ('share', 'Share profile', '')], back=False, note='profile'), 'menu'),
        ('Settings › Account type and tools',
         phone('Settings and activity', [('saved', 'Saved', ''), ('archive', 'Archive', ''), ('activity', 'Your activity', ''),
                                         ('notifications', 'Notifications', ''), ('tools', 'Account type and tools', '')]), 'tools'),
        ('Switch to professional account (Business or Creator, both free)',
         phone('Account type and tools', [('switch', 'Switch to professional account', ''), ('branded', 'Branded content', ''), ('bestpractices', 'Best practices', '')]), 'switch'),
    ]
    cloud_name = [('Cloudinary console › Settings › API Keys', cloudinary(), 'apikeys'), ('Copy the Cloud name and paste it below', cloudinary(), 'cloud')]
    api_key = [('Settings › API Keys', cloudinary(), 'apikeys'), ('Copy the API key and paste it below', cloudinary(), 'key')]
    api_secret = [('Settings › API Keys', cloudinary(), 'apikeys'), ('Reveal the API secret, copy it and paste it below', cloudinary(), 'reveal')]
    return dict(create_app=create_app, app_id=app_id, app_secret=app_secret, redirect=redirect, roles=roles, professional=professional,
                cloud_name=cloud_name, api_key=api_key, api_secret=api_secret)


SCENES = scenes()


# --- drawing ------------------------------------------------------------------------------------------------------
def rectf(r): return QRectF(*r)


def ease(t): return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


class Page:
    """Draws one page description, recording the rectangle of every element that has an id so the pointer can find it."""
    def __init__(self, painter, family):
        self.p, self.family, self.rects = painter, family, {}

    def font(self, size=9, bold=False):
        f = QFont(self.family); f.setPixelSize(size); f.setBold(bold); return f

    def text(self, rect, text, size=9, color=INK, bold=False, align=Qt.AlignLeft | Qt.AlignVCenter):
        self.p.setFont(self.font(size, bold)); self.p.setPen(color); self.p.drawText(rectf(rect), align, text)

    def width(self, text, size=9, bold=False): return QFontMetricsF(self.font(size, bold)).horizontalAdvance(text)

    def rounded(self, rect, fill, border=None, radius=4, width=1):
        self.p.setPen(QPen(border, width) if border else Qt.NoPen); self.p.setBrush(fill); self.p.drawRoundedRect(rectf(rect), radius, radius)

    def draw(self, page):
        getattr(self, 'draw_' + page['kind'])(page)

    def draw_web(self, page):
        p = self.p
        self.rounded((0, 0, W, H), CHROME, radius=0)
        for i, c in enumerate(('#ff5f57', '#febc2e', '#28c840')): self.rounded((8 + i * 11, 6, 7, 7), QColor(c), radius=3.5)
        self.rounded((46, 4, W - 60, 12), QColor('#44444f'), radius=6); self.text((54, 4, W - 76, 12), page['url'], 7, QColor('#d0d0d8'))
        p.fillRect(QRectF(0, 20, W, H - 20), WHITE if page['white'] else PAGE)
        p.fillRect(QRectF(0, 20, W, 24), WHITE); p.setPen(LINE); p.drawLine(QPointF(0, 44), QPointF(W, 44))
        self.text((10, 20, 200, 24), page['title'], 9, INK, bold=True); mx = 14 + self.width(page['title'], 9, True)
        for item in page['menu']:
            self.text((mx, 20, 200, 24), item, 7, INK); mx += self.width(item, 7) + 10
        x = W - 10
        for id, label, style in reversed(page['nav']):
            if style == 'avatar':
                x -= 16; self.rounded((x, 24, 16, 16), QColor('#c4c7cc'), radius=8); self.rects[id] = (x, 24, 16, 16); x -= 10; continue
            if style == 'search':
                x -= 12; self.rounded((x, 26, 9, 9), WHITE, BLUE, radius=4.5, width=1.5); p.drawLine(QPointF(x + 8, y0 := 34), QPointF(x + 11, y0 + 3)); self.rects[id] = (x, 24, 12, 14); x -= 10; continue
            w = self.width(label, 8, bold=(style == 'button')) + (16 if style == 'button' else 4); x -= w
            if style == 'button': self.rounded((x, 24, w, 16), BLUE if label == 'Log In' else QColor('#e4e6eb'), radius=5)
            self.text((x, 24, w, 16), label, 8, WHITE if label == 'Log In' else INK, bold=(style == 'button'), align=Qt.AlignCenter)
            self.rects[id] = (x, 24, w, 16); x -= 10
        if page['side']:
            dark = page['dark_side']
            p.fillRect(QRectF(0, 44, 124, H - 44), CLOUD_DARK if dark else WHITE)
            if not dark: p.setPen(LINE); p.drawLine(QPointF(124, 44), QPointF(124, H))
            y = 52
            for id, label, indent, active in page['side']:
                r = (6, y, 112, 15)
                if active: self.rounded(r, QColor('#e7f0fd') if not dark else QColor('#33406a'), radius=4)
                self.text((12 + indent * 12, y, 100, 15), label, 8, (BLUE if not dark else WHITE) if active else (QColor('#c8cde0') if dark else INK), bold=active)
                self.rects[id] = r; y += 17
        for e in page['body']: self.draw_el(e)

    def draw_el(self, e):
        kind, r = e['kind'], e['rect']
        centre = Qt.AlignCenter if e.get('center') else Qt.AlignLeft | Qt.AlignVCenter
        if kind == 'heading': self.text(r, e['text'], e.get('size', 12), INK, bold=True, align=centre)
        elif kind == 'text': self.text(r, e['text'], e.get('size', 9), e.get('color', INK), bold=e.get('bold', False), align=centre)
        elif kind == 'lines':
            x, y, w, h = r; n = e.get('n', 3); step = h / n
            for i in range(n): self.rounded((x, y + i * step + 2, w * (0.9 if i % 2 else 1.0), max(2, step - 6)), QColor('#dde0e5'), radius=2)
        elif kind == 'button':
            style = e.get('style', 'plain'); fill = {'blue': BLUE, 'green': GREEN}.get(style, QColor('#e4e6eb'))
            self.rounded(r, fill, radius=5); self.text(r, e['text'], 8, WHITE if style in ('blue', 'green') else INK, bold=True, align=Qt.AlignCenter)
            if e['id']: self.rects[e['id']] = r
        elif kind == 'field':
            x, y, w, h = r; self.text((x, y, w, 12), e['label'], 7, MUTED)
            box = (x, y + 13, w, h - 13); self.rounded(box, WHITE, LINE, radius=4)
            self.text((x + 6, y + 13, w - 12, h - 13), e['value'], 8, INK if e['value'] else MUTED)
            if e['id']: self.rects[e['id']] = box
            cx = x + w - 6
            if e.get('button'):
                bid, label = e['button']; bw = self.width(label, 7, True) + 10; br = (cx - bw, y + 16, bw, h - 19)
                self.rounded(br, QColor('#e4e6eb'), radius=4); self.text(br, label, 7, INK, bold=True, align=Qt.AlignCenter); self.rects[bid] = br; cx -= bw + 4
            if e.get('copy'):
                self.rounded((cx - 10, y + 17, 8, 10), WHITE, MUTED, radius=1.5); self.rounded((cx - 13, y + 20, 8, 10), WHITE, MUTED, radius=1.5)
        elif kind == 'card':
            x, y, w, h = r; self.rounded(r, WHITE, LINE, radius=6); self.text((x + 8, y + 6, w - 16, 16), e['text'], 8, INK, bold=True)
            self.rounded((x + 8, y + 16, 20, 20), QColor('#e4e6eb'), radius=10)
            br = (x + 8, y + h - 24, 50, 16); self.rounded(br, QColor('#e4e6eb'), radius=4); self.text(br, 'Set up', 7, INK, bold=True, align=Qt.AlignCenter); self.rects[e['id']] = br
        elif kind == 'option':
            x, y, w, h = r; self.rounded(r, WHITE, BLUE if e['on'] else LINE, radius=6)
            self.rounded((x + 8, y + h / 2 - 6, 12, 12), WHITE, BLUE if e['on'] else MUTED, radius=6, width=1.5)
            if e['on']: self.rounded((x + 11, y + h / 2 - 3, 6, 6), BLUE, radius=3)
            self.text((x + 26, y, w - 30, h), e['text'], 9, INK, bold=e['on']); self.rects[e['id']] = r
        elif kind == 'dialog':
            self.p.fillRect(QRectF(0, 44, W, H - 44), QColor(0, 0, 0, 70)); self.rounded(r, WHITE, radius=8)
            x, y, w, h = r; self.text((x + 12, y + 6, w - 24, 20), e['text'], 10, INK, bold=True)
            for item in e['items']: self.draw_el(item)

    def draw_phone(self, page):
        p = self.p; p.fillRect(QRectF(0, 0, W, H), QColor('#15131c'))
        fx, fy, fw, fh = 172, 8, 136, H - 16
        self.rounded((fx, fy, fw, fh), QColor('#2a2a33'), radius=16); self.rounded((fx + 4, fy + 4, fw - 8, fh - 8), WHITE, radius=13)
        self.rounded((fx + fw / 2 - 18, fy + 7, 36, 5), QColor('#2a2a33'), radius=2.5)
        y = fy + 18; p.setPen(LINE); p.drawLine(QPointF(fx + 4, y + 22), QPointF(fx + fw - 4, y + 22))
        if page['note'] == 'profile':
            self.text((fx + 10, y, 80, 22), page['header'], 9, INK, bold=True)
            m = (fx + fw - 26, y + 3, 16, 16)
            for i in range(3): self.rounded((m[0] + 3, m[1] + 3 + i * 4.5, 10, 2), INK, radius=1)
            self.rects['menu'] = m
            self.rounded((fx + 12, y + 32, 30, 30), QColor('#e4e6eb'), radius=15); self.text((fx + 50, y + 32, 80, 30), '12 posts   340 followers', 6, INK)
            ry = y + 72
        else:
            if page['back']: self.text((fx + 8, y, 16, 22), '‹', 12, INK, bold=True)
            self.text((fx + 22, y, fw - 30, 22), page['header'], 8, INK, bold=True); ry = y + 30
        for id, label, extra in page['rows']:
            r = (fx + 8, ry, fw - 16, 20); self.text((fx + 12, ry, fw - 40, 20), label, 7.5, INK)
            if extra: self.rounded((fx + fw - 34, ry + 5, 12, 10), RED, radius=5); self.text((fx + fw - 34, ry + 5, 12, 10), extra, 6, WHITE, bold=True, align=Qt.AlignCenter)
            self.text((fx + fw - 20, ry, 10, 20), '›', 9, MUTED); self.rects[id] = r; ry += 22


class DemoPanel(QWidget):
    """The replaying demo for one setup screen: `DemoPanel('create_app')`."""
    def __init__(self, scene, parent=None):
        super().__init__(parent); self.key = scene; self.frames = SCENES[scene]; self.index = 0; self.elapsed = 0; self.frozen = False
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred); self.setMinimumSize(300, int(300 * H / W) + CAPTION); self.setMaximumHeight(int(528 * H / W) + CAPTION)
        self.setFixedWidth(528); self.setCursor(Qt.PointingHandCursor); self.setToolTip('Click to skip ahead')
        self.timer = QTimer(self); self.timer.setInterval(TICK_MS); self.timer.timeout.connect(self.tick)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def sizeHint(self): return self.maximumSize()

    def duration(self): return FRAME_MS + (LAST_EXTRA_MS if self.index == len(self.frames) - 1 else 0)

    def phase(self): return min(1.0, self.elapsed / FRAME_MS)

    def tick(self, ms=TICK_MS):
        self.elapsed += ms
        if self.elapsed >= self.duration(): self.index = (self.index + 1) % len(self.frames); self.elapsed = 0
        self.update()

    def show_frame(self, index):
        self.index = index % len(self.frames); self.elapsed = 0; self.update()

    def settle(self):
        """Stop at the hold position of the current frame (for screenshots and tests)."""
        self.frozen = True; self.timer.stop(); self.elapsed = int(FRAME_MS * 0.9); self.update()

    def showEvent(self, event):
        super().showEvent(event)
        if not self.frozen: self.timer.start()

    def hideEvent(self, event): super().hideEvent(event); self.timer.stop()

    def mousePressEvent(self, event): self.show_frame(self.index + 1)

    def target_rect(self, index, rects_cache):
        """The target rectangle of frame `index`, measured by drawing the page into nothing."""
        if index not in rects_cache:
            caption, page, target = self.frames[index]
            pm = QPixmap(1, 1); img = QPainter(pm); pg = Page(img, self.font().family()); pg.draw(page); img.end()
            rects_cache[index] = pg.rects.get(target)
        return rects_cache[index]

    def paintEvent(self, event):
        p = QPainter(self); p.setRenderHint(QPainter.Antialiasing); p.setRenderHint(QPainter.TextAntialiasing)
        w, h = self.width(), self.height() - CAPTION; s = min(w / W, h / H); ox, oy = (w - W * s) / 2, (h - H * s) / 2
        caption, page, target = self.frames[self.index]; phase = self.phase()
        p.save(); p.translate(ox, oy); p.scale(s, s)
        clip = QPainterPath(); clip.addRoundedRect(QRectF(0, 0, W, H), 10, 10); p.setClipPath(clip)
        pg = Page(p, self.font().family()); pg.draw(page)
        end = pg.rects.get(target)
        if end:
            cache = {}; start_rect = self.target_rect((self.index - 1) % len(self.frames), cache) if len(self.frames) > 1 else None
            sx, sy = (start_rect[0] + start_rect[2] / 2, start_rect[1] + start_rect[3] / 2) if start_rect else (W - 40, H - 30)
            ex, ey = end[0] + min(end[2] / 2, 40), end[1] + end[3] / 2
            t = ease(phase / MOVE); x, y = sx + (ex - sx) * t, sy + (ey - sy) * t
            pulse = 0.5 + 0.5 * math.sin(self.elapsed / 180.0)
            p.setBrush(Qt.NoBrush); p.setPen(QPen(QColor(RED.red(), RED.green(), RED.blue(), int(150 + 100 * pulse)), 2))
            pad = 3 + 2 * pulse; p.drawRoundedRect(QRectF(end[0] - pad, end[1] - pad, end[2] + 2 * pad, end[3] + 2 * pad), 5, 5)
            if MOVE <= phase < CLICK:
                k = (phase - MOVE) / (CLICK - MOVE); p.setPen(Qt.NoPen); p.setBrush(QColor(RED.red(), RED.green(), RED.blue(), int(160 * (1 - k))))
                r = 4 + 14 * k; p.drawEllipse(QPointF(x, y), r, r)
            arrow = QPolygonF([QPointF(x + dx, y + dy) for dx, dy in ((0, 0), (0, 15), (4, 11.5), (7.5, 18), (10, 17), (6.8, 10.5), (11.5, 10.5))])
            p.setPen(QPen(WHITE, 1.4)); p.setBrush(QColor('#111111')); p.drawPolygon(arrow)
        p.restore()
        f = QFont(self.font()); f.setPointSizeF(max(9.0, self.font().pointSizeF() + 1)); p.setFont(f); p.setPen(WHITE)
        p.drawText(QRectF(0, h, w, CAPTION), Qt.AlignCenter, f'{self.index + 1} of {len(self.frames)}  ·  {caption}')
        p.end()
