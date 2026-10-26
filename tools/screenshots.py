r"""Render the main window and the settings dialog off-screen with sample posts, for review.
Run: python tools\screenshots.py <output folder>"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')   # the offscreen platform has no fonts on Windows
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw
from PySide6.QtCore import QPoint, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QToolBar, QWizard

import app as ui
import engine as eng
import settings as cfg

BADGE = QColor('#e5392b')


def annotate(top, pixmap, items, path):
    """Draw numbered badges at the top-left corner of each widget (for the user guide) and save the result.
    items: (widget, number) pairs; widgets are children of `top`."""
    pm = pixmap.copy(); painter = QPainter(pm); painter.setRenderHint(QPainter.Antialiasing)
    painter.setFont(QFont('Segoe UI', 11, QFont.Bold))
    for widget, number in items:
        pos = widget.mapTo(top, QPoint(0, 0)); r = 13
        painter.setPen(QPen(BADGE, 2)); painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(QRectF(pos.x() - 3, pos.y() - 3, widget.width() + 6, widget.height() + 6), 4, 4)
        cx, cy = max(r + 2, pos.x() - 6), max(r + 2, pos.y() - 6)   # keep the badge inside the picture
        painter.setPen(QPen(QColor('white'), 2)); painter.setBrush(BADGE); painter.drawEllipse(QRectF(cx - r, cy - r, 2 * r, 2 * r))
        painter.setPen(QColor('white')); painter.drawText(QRectF(cx - r, cy - r, 2 * r, 2 * r), Qt.AlignCenter, str(number))
    painter.end(); pm.save(str(path))


def sample_photo(path, size, color, text):
    im = Image.new('RGB', size, color); d = ImageDraw.Draw(im)
    for i in range(0, size[0], 60): d.line([(i, 0), (i + size[1] // 2, size[1])], fill=(255, 255, 255), width=3)
    d.rectangle([20, 20, 20 + 18 * len(text), 70], fill=(0, 0, 0)); d.text((30, 35), text, fill=(255, 255, 255))
    im.save(path); return str(path)


def main(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    app = ui.make_app()
    data = Path(tempfile.mkdtemp(prefix='postqueue_shots_'))
    settings = cfg.Settings(data / 'settings.json')
    settings.update({'access_token': 'x', 'ig_user_id': '1', 'ig_username': 'studio.north', 'cloudinary_cloud': 'c', 'cloudinary_api_key': 'k', 'cloudinary_api_secret': 's',
                     'start_with_windows': False, 'token_expires': eng.fmt_when(eng.now_local() + dt.timedelta(days=45))})
    engine = eng.Engine(data, settings); now = eng.now_local()
    p1 = engine.add(sample_photo(data / 'harbour-morning.jpg', (1600, 1000), (52, 120, 160), 'HARBOUR'), now.replace(hour=9, minute=0) + dt.timedelta(days=1),
                    'Early light over the harbour. #morning #harbour #seaside')
    p2 = engine.add(sample_photo(data / 'studio-portrait.png', (1000, 1600), (160, 80, 110), 'PORTRAIT'), now.replace(hour=18, minute=30) + dt.timedelta(days=2), 'New prints in the shop this week.')
    clip = data / 'behind-the-scenes.mp4'; clip.write_bytes(b'\0' * 2048)
    p3 = engine.add(clip, now.replace(hour=12, minute=0) + dt.timedelta(days=3), 'Behind the scenes #reel')
    p4 = engine.add(sample_photo(data / 'last-week.jpg', (1200, 1200), (90, 140, 90), 'POSTED'), now - dt.timedelta(days=2))
    engine.update(p4, status='posted', permalink='https://www.instagram.com/p/example/', posted_at=eng.fmt_when(now - dt.timedelta(days=2)))
    p5 = engine.add(sample_photo(data / 'needs-attention.jpg', (1200, 800), (150, 110, 60), 'FAILED'), now - dt.timedelta(hours=3))
    engine.update(p5, status='failed', error='The Instagram sign-in has expired or was revoked. Open Settings > Instagram and sign in again.')
    cfg.set_start_at_login = lambda on, command=None, home=None: True; cfg.starts_at_login = lambda home=None: True
    window = ui.MainWindow(engine, settings); window.setAttribute(Qt.WA_DontShowOnScreen, True); window.resize(1100, 660); window.show(); app.processEvents()
    for i in range(window.tree.topLevelItemCount()):
        if window.tree.topLevelItem(i).data(0, ui.Qt.UserRole) == p1.id: window.tree.topLevelItem(i).setSelected(True)
    app.processEvents(); shot = window.grab(); shot.save(str(out / 'main.png'))
    bar = window.findChild(QToolBar)
    annotate(window, shot, [(bar.widgetForAction(window.act_add), 1), (window.when_edit, 2), (window.caption, 3), (window.save_button, 4),
                            (window.post_now_button, 5), (window.next_label, 6), (bar.widgetForAction(window.act_settings), 7)], out / 'guide-main.png')
    window.settings['access_token'] = ''; window.refresh(); app.processEvents(); shot = window.grab(); shot.save(str(out / 'main-not-set-up.png'))
    annotate(window, shot, [(window.banner_button, 1)], out / 'guide-banner.png')
    window.settings['access_token'] = 'x'; window.refresh()
    dialog = ui.SettingsDialog(settings, window); dialog.setAttribute(Qt.WA_DontShowOnScreen, True); dialog.show(); app.processEvents()
    for i, name in enumerate(('settings-instagram', 'settings-hosting', 'settings-general')):
        dialog.tabs.setCurrentIndex(i); app.processEvents(); dialog.grab().save(str(out / f'{name}.png'))
    fresh = cfg.Settings(data / 'fresh.json'); wizard = ui.SetupWizard(fresh, window); wizard.setAttribute(Qt.WA_DontShowOnScreen, True); wizard.show(); app.processEvents()
    nxt = wizard.button(QWizard.NextButton); ig, host = wizard.instagram, wizard.hosting
    guide = {'wizard-welcome': [(nxt, 1)],
             'wizard-instagram': [(ig.kind, 1), (ig.app_id, 2), (ig.app_secret, 3), (ig.signin_button, 4), (ig.token_edit, 5), (ig.accounts, 6), (nxt, 7)],
             'wizard-hosting': [(host.cloud, 1), (host.ckey, 2), (host.csecret, 3), (host.findChild(ui.QPushButton, 'test_hosting'), 4), (nxt, 5)],
             'wizard-schedule': [(wizard.default_time, 1), (wizard.startup, 2), (nxt, 3)],
             'wizard-done': [(wizard.button(QWizard.FinishButton), 1)]}
    for i, name in enumerate(('wizard-welcome', 'wizard-instagram', 'wizard-hosting', 'wizard-schedule', 'wizard-done')):
        app.processEvents(); shot = wizard.grab(); shot.save(str(out / f'{name}.png'))
        annotate(wizard, shot, guide[name], out / f'guide-{name}.png')
        if i < 4: wizard.currentPage().ready = None; wizard.next()
    if window.tray: window.tray.hide()
    print('wrote', sorted(p.name for p in out.iterdir()))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/screenshots')
