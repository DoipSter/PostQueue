r"""Render the picture-by-picture walkthrough in docs/walkthrough: from the first screen to a posted photo and its
confirmation, with numbered badges on what to press. Run: python tools\walkthrough.py [docs/walkthrough]"""
import datetime as dt
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
HERE = Path(__file__).resolve().parent; sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(HERE))
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QToolBar

import app as ui
import engine as eng
import settings as cfg
from screenshots import annotate, sample_photo


def main(out):
    out = Path(out); out.mkdir(parents=True, exist_ok=True)
    app = ui.make_app(); data = Path(tempfile.mkdtemp(prefix='postqueue_walk_'))
    cfg.set_start_at_login = lambda on, command=None, home=None: True; cfg.starts_at_login = lambda home=None: True
    ui.branding.META_APP_ID, ui.branding.SERVICE_URL = '424242', 'https://postqueue.example.workers.dev'
    settings = cfg.Settings(data / 'settings.json'); settings['start_with_windows'] = False
    engine = eng.Engine(data, settings)
    window = ui.MainWindow(engine, settings); window.setAttribute(Qt.WA_DontShowOnScreen, True); window.resize(1180, 720); window.show(); app.processEvents()
    bar = window.findChild(QToolBar); n = [0]

    def shot(name, widget, items=()):
        n[0] += 1; app.processEvents(); pm = widget.grab(); annotate(widget, pm, items, out / f'{n[0]:02d}-{name}.png')

    # 1. First launch: the window with the setup bar
    window.refresh(); shot('first-launch', window, [(window.banner_button, 1)])
    # 2-7. Setup, one screen at a time
    flow = ui.SetupFlow(settings, window); flow.setAttribute(Qt.WA_DontShowOnScreen, True); flow.show(); app.processEvents()
    shot('setup-welcome', flow, [(flow.primary, 1)]); flow.next()
    shot('setup-professional-account', flow, [(flow.primary, 1)]); flow.next()
    shot('setup-sign-in', flow, [(flow.primary, 1)])
    flow.signin_status.setText('Waiting for the browser… approve the sign-in there, then come back here.'); shot('setup-sign-in-waiting', flow)
    settings.update({'access_token': 'x', 'ig_user_id': '1', 'ig_username': 'studio.north', 'token_expires': eng.fmt_when(eng.now_local() + dt.timedelta(days=60))})
    flow.found_accounts = [{'id': '1', 'username': 'studio.north'}]; flow.signin_status.setText('Connected as @studio.north.'); shot('setup-connected', flow); flow.advance()
    shot('setup-posting-time', flow, [(flow.time_edit, 1), (flow.primary, 2)]); flow.next()
    shot('setup-done', flow, [(flow.primary, 1)]); flow.accept(); window.refresh()
    # 8. Ready: the empty queue
    shot('ready-to-add', window, [(bar.widgetForAction(window.act_add), 1), (window.empty, 2)])
    # 9. A photo added: scheduled for the next free slot
    post = window.add_paths([sample_photo(data / 'harbour-morning.jpg', (1600, 1000), (52, 120, 160), 'HARBOUR')])[0]
    shot('photo-added', window, [(window.when_edit.date_button, 1), (window.when_edit.time_edit, 2), (window.caption, 3)])
    # 10. The calendar
    cal = window.when_edit.calendar; cal.setSelectedDate(window.when_edit.date); cal.resize(420, 340); n[0] += 1; cal.grab().save(str(out / f'{n[0]:02d}-pick-a-date.png'))
    # 11. Caption written, saved
    window.caption.setPlainText('Early light over the harbour. #morning #harbour #seaside'); app.processEvents()
    shot('caption-and-save', window, [(window.caption, 1), (window.save_button, 2)])
    window.save_button.click(); app.processEvents(); shot('saved-and-scheduled', window, [(window.next_label, 1)])
    # 12. Posting in progress
    engine.update(post, status='posting'); window.progress.setValue(55); window.progress.show(); window.progress_text.setText(f'{post.name()}: Creating the Instagram post'); window.refresh()
    shot('posting', window, [(window.progress, 1)])
    # 13. Posted: the confirmation
    window.progress.hide(); window.progress_text.setText('')
    engine.update(post, status='posted', media_id='1789', permalink='https://www.instagram.com/p/example/', posted_at=eng.fmt_when(eng.now_local()))
    window.refresh(); window.show_result(post); shot('posted-confirmation', window, [(window.result_text, 1), (window.result_open, 2), (window.open_button, 3)])
    # 14. When something goes wrong
    other = window.add_paths([sample_photo(data / 'needs-attention.jpg', (1200, 800), (150, 110, 60), 'FAILED')])[0]
    engine.update(other, status='failed', error='The Instagram sign-in has expired or was revoked. Open Settings > Instagram and sign in again.')
    window.refresh(); window.show_result(other); shot('failed-with-reason', window, [(window.result_text, 1), (bar.widgetForAction(window.act_settings), 2)])
    if window.tray: window.tray.hide()
    print('wrote', sorted(p.name for p in out.iterdir()))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'docs/walkthrough')
