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
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import app as ui
import engine as eng
import settings as cfg


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
    app.processEvents(); window.grab().save(str(out / 'main.png'))
    window.settings['access_token'] = ''; window.refresh(); app.processEvents(); window.grab().save(str(out / 'main-not-set-up.png')); window.settings['access_token'] = 'x'; window.refresh()
    dialog = ui.SettingsDialog(settings, window); dialog.setAttribute(Qt.WA_DontShowOnScreen, True); dialog.show(); app.processEvents()
    for i, name in enumerate(('settings-instagram', 'settings-hosting', 'settings-general')):
        dialog.tabs.setCurrentIndex(i); app.processEvents(); dialog.grab().save(str(out / f'{name}.png'))
    fresh = cfg.Settings(data / 'fresh.json'); wizard = ui.SetupWizard(fresh, window); wizard.setAttribute(Qt.WA_DontShowOnScreen, True); wizard.show(); app.processEvents()
    for i, name in enumerate(('wizard-welcome', 'wizard-instagram', 'wizard-hosting', 'wizard-schedule', 'wizard-done')):
        app.processEvents(); wizard.grab().save(str(out / f'{name}.png'))
        if i < 4: wizard.currentPage().ready = None; wizard.next()
    if window.tray: window.tray.hide()
    print('wrote', sorted(p.name for p in out.iterdir()))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/screenshots')
