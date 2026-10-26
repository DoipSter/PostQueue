"""Interface tests: the real window, rendered off-screen, with Instagram and hosting simulated."""
import datetime as dt
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

import app as ui
import engine as eng
import settings as cfg
from test_engine import FakeInstagram, FakeProvider


@pytest.fixture(scope='session')
def qapp():
    return ui.make_app()


@pytest.fixture
def settings(tmp_path):
    s = cfg.Settings(tmp_path / 'settings.json')
    s.update({'access_token': 'tok', 'ig_user_id': '178', 'ig_username': 'shop', 'cloudinary_cloud': 'c', 'cloudinary_api_key': 'k', 'cloudinary_api_secret': 's',
              'start_with_windows': False, 'close_to_tray': False, 'default_hour': 9, 'default_minute': 30})
    s.save(); return s


@pytest.fixture
def fakes(monkeypatch):
    state = {'instagram': FakeInstagram(), 'provider': FakeProvider()}
    monkeypatch.setattr(ui.MainWindow, 'make_clients', lambda self: (state['instagram'], state['provider']))
    return state


@pytest.fixture
def window(qapp, tmp_path, settings, fakes, monkeypatch):
    monkeypatch.setattr(cfg, 'set_start_at_login', lambda on, command=None, home=None: True)
    monkeypatch.setattr(cfg, 'starts_at_login', lambda home=None: False)
    monkeypatch.setattr(QMessageBox, 'question', staticmethod(lambda *a, **k: QMessageBox.Yes))
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: QMessageBox.Ok))
    engine = eng.Engine(tmp_path / 'data', settings)
    w = ui.MainWindow(engine, settings); w.show(); qapp.processEvents()
    yield w
    w.timer.stop(); w.poll.stop(); w.close(); qapp.processEvents()


def photo(tmp_path, name='pic.jpg', size=(1200, 900)):
    p = tmp_path / name; Image.new('RGB', size, 'teal').save(p); return str(p)


def wait_until(cond, timeout=10):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if cond(): return True
        time.sleep(0.02)
    return cond()


def test_window_starts_empty_with_account_shown(window):
    assert window.windowTitle() == ui.TITLE and window.empty.isVisible() and not window.tree.isVisible()
    assert window.account_label.text() == '@shop' and not window.banner.isVisible() and window.next_label.text() == 'Nothing scheduled'
    assert not window.detail.isEnabled()


def test_setup_banner_when_not_connected(window):
    window.settings['access_token'] = ''; window.settings['cloudinary_cloud'] = ''; window.refresh()
    assert window.banner.isVisible() and 'connect your Instagram account' in window.banner_text.text() and 'media hosting' in window.banner_text.text()
    assert window.account_label.text() == 'Not connected'


def test_add_files_schedules_next_slot_and_selects(window, tmp_path, monkeypatch):
    monkeypatch.setattr(QFileDialog, 'getOpenFileNames', staticmethod(lambda *a, **k: ([photo(tmp_path), photo(tmp_path, 'two.png')], '')))
    window.act_add.trigger()
    assert window.tree.topLevelItemCount() == 2 and window.tree.isVisible() and not window.empty.isVisible()
    posts = sorted(window.engine.posts, key=lambda p: p.when)
    assert posts[0].when_dt().hour == 9 and posts[0].when_dt().minute == 30 and posts[1].when_dt() == posts[0].when_dt() + dt.timedelta(days=1)
    assert window.current and window.current.name() == 'two.png' and window.detail.isEnabled()
    assert window.when_edit.dateTime().toPython().hour == 9 and 'Next:' in window.next_label.text()


def test_drop_and_bad_files(window, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: warned.append(a[2])))
    bad = tmp_path / 'notes.txt'; bad.write_text('x')
    added = window.add_paths([photo(tmp_path), str(bad)])
    assert len(added) == 1 and 'not a supported' in warned[0] and window.tree.topLevelItemCount() == 1


def test_edit_caption_and_time_then_save(window, tmp_path):
    window.add_paths([photo(tmp_path)]); post = window.current
    assert not window.save_button.isEnabled()
    window.caption.setPlainText('Golden hour #sunset'); window.shift_when(dt.timedelta(days=3)); window.fit_combo.setCurrentIndex(1)
    assert window.save_button.isEnabled() and window.dirty and '19 of 2200' in window.caption_info.text()
    window.save_button.click()
    reloaded = eng.Engine(window.engine.dir).get(post.id)
    assert reloaded.caption == 'Golden hour #sunset' and reloaded.fit == 'pad' and reloaded.when_dt() == post.when_dt() and not window.dirty
    assert (reloaded.when_dt() - eng.now_local()).days >= 3


def test_caption_warning_shown(window, tmp_path):
    window.add_paths([photo(tmp_path)]); window.caption.setPlainText(' '.join(f'#t{i}' for i in range(31)))
    assert 'allows 30' in window.caption_info.text()


def test_post_now_publishes_and_offers_link(window, tmp_path, fakes):
    window.add_paths([photo(tmp_path, size=(1000, 2000))]); post = window.current
    window.caption.setPlainText('Now!'); window.post_now_button.click()
    assert wait_until(lambda: window.worker is None and post.status == 'posted')
    assert fakes['instagram'].calls[0][2] == 'Now!' and fakes['provider'].deleted and window.open_button.isVisible() and not window.post_now_button.isVisible()
    assert window.tree.topLevelItem(0).text(3) == 'Posted' and not window.progress.isVisible()
    assert window.status_label.text().startswith('Posted on')


def test_scheduler_posts_due_items_and_marks_missed(window, tmp_path, fakes):
    now = eng.now_local()
    due = window.engine.add(photo(tmp_path, 'due.jpg'), now - dt.timedelta(minutes=2))
    old = window.engine.add(photo(tmp_path, 'old.jpg'), now - dt.timedelta(hours=30))
    window.tick()
    assert wait_until(lambda: window.worker is None and due.status == 'posted')
    assert old.status == 'missed' and window.tree.topLevelItemCount() == 2
    window.tree.clearSelection()
    for i in range(2):
        if window.tree.topLevelItem(i).data(0, Qt.UserRole) == old.id: window.tree.topLevelItem(i).setSelected(True)
    assert 'Missed' in window.status_label.text() and window.post_now_button.isVisible()


def test_pause_stops_posting(window, tmp_path, fakes):
    post = window.engine.add(photo(tmp_path), eng.now_local() - dt.timedelta(minutes=1))
    window.act_pause.setChecked(True); window.tick()
    assert window.worker is None and post.status == 'queued' and window.next_label.text() == 'Posting is paused' and window.settings['paused']
    window.act_pause.setChecked(False); window.tick()
    assert wait_until(lambda: window.worker is None and post.status == 'posted')


def test_failure_is_shown_with_retry_offer(window, tmp_path, fakes):
    fakes['instagram'] = FakeInstagram(fail='create')
    window.add_paths([photo(tmp_path)]); post = window.current; window.post_now_button.click()
    assert wait_until(lambda: window.worker is None and post.status == 'failed')
    assert 'could not fetch' in window.status_label.text() and 'Post now' in window.post_now_button.text() and window.post_now_button.isVisible()


def test_not_set_up_fails_without_network(window, tmp_path, monkeypatch):
    monkeypatch.setattr(ui.MainWindow, 'make_clients', lambda self: (None, None))
    window.add_paths([photo(tmp_path)]); post = window.current; window.post_now_button.click()
    assert post.status == 'failed' and 'connect your Instagram account' in post.error and window.worker is None


def test_remove_selected(window, tmp_path):
    window.add_paths([photo(tmp_path), photo(tmp_path, 'b.jpg')]); window.tree.selectAll()
    window.remove_button.click()
    assert not window.engine.posts and window.empty.isVisible() and not list(window.engine.media_dir.iterdir())


def test_startup_entry_follows_explicit_choice_only(window, monkeypatch):
    calls = []
    monkeypatch.setattr(cfg, 'set_start_at_login', lambda on, command=None, home=None: calls.append(on))
    window.settings['start_with_windows'] = None; window.apply_startup_setting(); assert calls == []   # the installer's entry decides
    window.settings['start_with_windows'] = True; window.apply_startup_setting(); assert calls == [True]
    monkeypatch.setattr(cfg, 'starts_at_login', lambda home=None: True)
    window.settings['start_with_windows'] = None
    dialog = ui.SettingsDialog(window.settings, window); assert dialog.general.startup.isChecked()   # shows the installer's entry
    dialog.general.startup.setChecked(False); assert window.settings['start_with_windows'] is False


def test_settings_dialog_roundtrip(window, qapp, monkeypatch):
    dialog = ui.SettingsDialog(window.settings, window); h, g, i = dialog.hosting, dialog.general, dialog.instagram
    h.cloud.setText('mycloud'); h.cloud.editingFinished.emit(); g.grace.setValue(48); g.tray_box.setChecked(True)
    g.default_time.setTime(g.default_time.time().addSecs(3600)); g.fit_default.setCurrentIndex(1)
    assert i.accounts.currentText() == '@shop' and 'Connected' in i.status.text()
    i.disconnect_button.click(); assert not window.settings.connected() and i.status.text() == 'Not connected.'
    dialog.accept()
    again = cfg.Settings(window.settings.path)
    assert again['cloudinary_cloud'] == 'mycloud' and again['grace_hours'] == 48 and again['close_to_tray'] and again['default_hour'] == 10 and again['fit_mode'] == 'pad'


def test_settings_check_token_finds_accounts(window, qapp, monkeypatch):
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '555', 'username': 'found', 'host': ui.ig.IG_HOST, 'via': 'Instagram Login'}])
    dialog = ui.SettingsDialog(window.settings, window); i = dialog.instagram; i.token_edit.setText('NEWTOKEN'); i.findChild(ui.QPushButton, 'check_token').click()
    assert wait_until(lambda: i.task is None and 'found' in i.status.text())
    assert window.settings['access_token'] == 'NEWTOKEN' and window.settings['ig_user_id'] == '555' and window.settings['api_host'] == ui.ig.IG_HOST and window.settings['login_kind'] == 'instagram'
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: (_ for _ in ()).throw(ui.ig.InstagramError('Invalid OAuth access token', code=190)))
    i.token_edit.setText('BAD'); i.findChild(ui.QPushButton, 'check_token').click()
    assert wait_until(lambda: i.task is None and 'expired' in i.status.text())


def test_settings_sign_in_uses_browser_flow(window, qapp, monkeypatch):
    monkeypatch.setattr(ui.ig, 'run_login', lambda kind, app_id, secret, version, cancelled: ('LONGTOKEN', 5184000, 'http://localhost:48215/'))
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '7', 'username': 'page_ig', 'host': ui.ig.FB_HOST, 'via': 'My Page'}])
    dialog = ui.SettingsDialog(window.settings, window); i = dialog.instagram; i.app_id.setText('123'); i.app_secret.setText('sec'); i.signin_button.click()
    assert wait_until(lambda: i.task is None and 'page_ig' in i.status.text())
    assert window.settings['access_token'] == 'LONGTOKEN' and window.settings['app_id'] == '123' and window.settings.token_expiry() > eng.now_local() + dt.timedelta(days=50)
    assert 'via My Page' in i.accounts.currentText()


def test_test_hosting_reports(window, qapp, monkeypatch):
    class Good:
        name = 'Cloudinary'
        def test(self): return 'https://ok'
    monkeypatch.setattr(ui.hosting, 'make_provider', lambda s: Good())
    dialog = ui.SettingsDialog(window.settings, window); dialog.hosting.findChild(ui.QPushButton, 'test_hosting').click()
    assert wait_until(lambda: 'Hosting works' in dialog.hosting.status.text())


def test_wizard_asks_one_thing_per_screen(window, qapp, monkeypatch):
    s = window.settings
    for key in ('access_token', 'ig_user_id', 'cloudinary_cloud'): s[key] = ''
    s['setup_done'] = False; assert window.needs_setup()
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '9', 'username': 'wiz', 'host': ui.ig.FB_HOST, 'via': 'Page'}])
    wizard = ui.SetupWizard(s, window); wizard.show(); qapp.processEvents()
    titles = [wizard.page(i).title() for i in wizard.pageIds()]
    assert titles[0] == 'Welcome to PostQueue' and titles[1].startswith('Step 1 of 3') and titles[-1] == 'All set' and len(titles) == 5
    wizard.next(); assert wizard.currentPage().title().startswith('Step 1') and not wizard.currentPage().isComplete()   # Next is off until connected
    wizard.instagram.token_edit.setText('TOK'); wizard.instagram.findChild(ui.QPushButton, 'check_token').click()
    assert wait_until(lambda: wizard.instagram.task is None and s.connected()) and wizard.currentPage().isComplete()
    wizard.next(); assert wizard.currentPage().title().startswith('Step 2') and not wizard.currentPage().isComplete()
    wizard.hosting.cloud.setText('c'); wizard.hosting.ckey.setText('k'); wizard.hosting.csecret.setText('s'); wizard.hosting.save()
    assert wizard.currentPage().isComplete()
    wizard.next(); assert wizard.currentPage().title().startswith('Step 3')
    wizard.default_time.setTime(ui.QTime(18, 30)); wizard.startup.setChecked(True)
    wizard.next(); assert wizard.currentPage().title() == 'All set'
    wizard.accept()
    again = cfg.Settings(s.path)
    assert again['setup_done'] and again['default_hour'] == 18 and again['default_minute'] == 30 and again['start_with_windows'] is True and again.ready()
    window.refresh(); assert not window.needs_setup() and not window.banner.isVisible()


def test_banner_button_opens_wizard_until_ready(window, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.SetupWizard, 'exec', lambda self: opened.append('wizard'))
    monkeypatch.setattr(ui.SettingsDialog, 'exec', lambda self: opened.append('settings'))
    window.settings['access_token'] = ''; window.refresh()
    assert window.banner.isVisible() and window.banner_button.text() == 'Finish setup'
    window.banner_button.click(); assert opened == ['wizard']
    window.settings['access_token'] = 'tok'; window.refresh(); window.open_setup(); assert opened == ['wizard', 'settings']


def test_second_launch_focuses_first(tmp_path, monkeypatch):
    lock = cfg.InstanceLock(tmp_path / 'postqueue.lock'); lock.acquire()
    monkeypatch.setattr(ui, 'make_app', lambda: (_ for _ in ()).throw(AssertionError('a second window must not be created')))
    monkeypatch.setenv('POSTQUEUE_DATA', str(tmp_path))
    monkeypatch.setattr(ui, 'focus_running_instance', lambda l: l.request_show())
    monkeypatch.setattr(ui.cfg.InstanceLock, 'other_pid', lambda self: 4242)
    monkeypatch.setattr(ui, 'make_app', lambda: QApplication.instance())
    assert ui.main([]) == 0 and lock.take_show_request()


def test_close_to_tray_hides_or_quits(window, qapp):
    window.tray = None; window.settings['close_to_tray'] = True
    quits = []; qapp.quit = lambda: quits.append(1)
    window.close(); assert quits and isinstance(window.settings['window'], list)
    del qapp.quit
