"""Interface tests: the real window, rendered off-screen, with Instagram and hosting simulated."""
import datetime as dt
import os
import time
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
from PIL import Image
from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox

import app as ui
import engine as eng
import settings as cfg
from test_engine import FakeInstagram, FakeProvider, make_mp4


@pytest.fixture(scope='session')
def qapp():
    return ui.make_app()


@pytest.fixture
def settings(tmp_path):
    s = cfg.Settings(tmp_path / 'settings.json')
    s.update({'access_token': 'tok', 'ig_user_id': '178', 'ig_username': 'shop', 'hosting': 'cloudinary', 'cloudinary_cloud': 'c', 'cloudinary_api_key': 'k', 'cloudinary_api_secret': 's',
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
    monkeypatch.setattr(ui.MainWindow, 'ask_how_to_add', lambda self, count: 'each')   # the "one post each / one carousel" question
    monkeypatch.setattr(ui.eng, 'check_for_update', lambda current, fetch=None: None)   # no network for the version check
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
    assert window.banner.isVisible() and 'sign in to Instagram' in window.banner_text.text() and 'media hosting' in window.banner_text.text()
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


def test_preview_shows_the_crop_or_the_borders(window, tmp_path):
    window.add_paths([photo(tmp_path, size=(1000, 2000))])   # teal, too tall for Instagram; the preview is 400x800
    assert window.preview.box == eng.target_box(400, 800, 'crop') == (0, 150, 400, 650) and 'top and bottom will be cropped off' in window.media_info.text()
    shown = window.preview.render(QSize(200, 400)).toImage()
    assert shown.pixelColor(100, 10).green() < 70 < shown.pixelColor(100, 200).green() == 128   # the top strip is dimmed, the middle is not
    window.fit_combo.setCurrentIndex(1)
    assert window.preview.box == (-120, 0, 520, 800) and 'white borders will be added at the sides' in window.media_info.text() and window.dirty
    shown = window.preview.render(QSize(400, 400)).toImage()
    assert shown.width() == 320 and shown.pixelColor(5, 200).lightness() == 255 and shown.pixelColor(160, 200).green() == 128   # white at the sides
    window.add_paths([photo(tmp_path, 'fine.jpg', size=(1200, 900))])
    assert window.preview.box == (0, 0, 800, 600) and 'posted whole' in window.media_info.text()


def test_video_details_show_length_and_warn_about_shape(window, tmp_path, monkeypatch):
    warned = []; monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: warned.append(a[2])))
    window.add_paths([str(make_mp4(tmp_path / 'wide.mp4', 30, (1920, 1080)))])
    assert 'landscape' in window.media_info.text() and ui.WARN_STYLE in window.media_info.styleSheet() and not window.fit_combo.isVisible() and window.preview.box is None
    window.add_paths([str(make_mp4(tmp_path / 'reel.mp4', 42, (1080, 1920)))])
    assert window.media_info.text() == 'Video · 0:42 · 1080×1920 · posted as a Reel' and ui.MUTED_STYLE in window.media_info.styleSheet()
    assert not window.add_paths([str(make_mp4(tmp_path / 'long.mp4', 20 * 60))]) and '15 minutes' in warned[0] and window.tree.topLevelItemCount() == 2


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
    assert window.result.isVisible() and window.result_text.text().startswith('✓ Posted pic.jpg to Instagram at') and window.result_open.isVisible()
    assert window.result_url.endswith('/1789/') and window.result.property('ok') == 'true'
    window.result_dismiss.click(); assert not window.result.isVisible()


def test_post_now_asks_first(window, tmp_path, monkeypatch):
    asked = []; monkeypatch.setattr(QMessageBox, 'question', staticmethod(lambda *a, **k: asked.append(a[2]) or QMessageBox.No))
    window.add_paths([photo(tmp_path)]); post = window.current; window.post_now_button.click()
    assert asked == ['Post pic.jpg to Instagram now?'] and post.status == 'queued' and window.worker is None


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
    assert window.result.isVisible() and window.result_text.text().startswith('✗ Could not post') and not window.result_open.isVisible() and window.result.property('ok') == 'false'


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
    monkeypatch.setattr(ui.hosting, 'make_provider', lambda s, url='': Good())
    dialog = ui.SettingsDialog(window.settings, window); dialog.hosting.findChild(ui.QPushButton, 'test_hosting').click()
    assert wait_until(lambda: 'Hosting works' in dialog.hosting.status.text())


def fresh_settings(window):
    s = window.settings
    for key in ('access_token', 'ig_user_id', 'cloudinary_cloud', 'app_id', 'app_secret'): s[key] = ''
    s['setup_done'] = False; s['start_with_windows'] = None; return s


def test_setup_flow_one_thing_per_screen(window, qapp, monkeypatch):
    s = fresh_settings(window); assert window.needs_setup()
    monkeypatch.setattr(ui.ig, 'run_login', lambda kind, app_id, secret, version, cancelled, service_url='': ('LONG', 5184000, 'http://localhost:48215/'))
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '9', 'username': 'wiz', 'host': ui.ig.FB_HOST, 'via': 'Page'}])
    class Good:
        name = 'Cloudinary'
        def test(self): return 'https://ok'
    monkeypatch.setattr(ui.hosting, 'make_provider', lambda st, url='': Good())
    flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    keys = []
    def at(key):
        keys.append(flow.step().key); assert flow.step().key == key, f'expected {key}, at {flow.step().key}'
    at('welcome'); assert flow.primary.text() == 'Start' and not flow.back_button.isVisible() and flow.progress.text() == ''
    flow.next(); at('professional'); assert flow.progress.text().startswith('STEP 1 OF')
    flow.next(); at('login_kind'); assert not flow.primary.isVisible()   # the two big choices are the only buttons
    flow.choose_login('facebook'); at('create_app'); assert s['login_kind'] == 'facebook'
    flow.next(); at('app_id'); assert not flow.primary.isEnabled()        # one field, Next off until it has a value
    monkeypatch.setattr(ui.ig, 'app_info', lambda app_id, version=None: {'id': '123', 'name': 'Shop App'})
    monkeypatch.setattr(ui.ig, 'app_token', lambda app_id, secret, version=None: '123|tok')
    flow.app_id_edit.setText(' 123 '); assert flow.primary.isEnabled(); flow.next()
    assert wait_until(lambda: flow.step().key == 'app_secret') and s['app_id'] == '123' and 'Found: Shop App' in flow.app_id_status.text()
    assert flow.primary.isEnabled() and flow.skip_button.isVisible()        # optional for Facebook Login
    flow.app_secret_edit.setText('sec'); flow.next()
    assert wait_until(lambda: flow.step().key == 'redirect') and s['app_secret'] == 'sec' and 'match' in flow.secret_status.text()
    flow.next(); at('roles'); flow.next(); at('signin'); assert flow.primary.text() == 'Sign in in the browser'
    flow.next()                                                               # starts the sign-in; the screen moves on by itself
    assert wait_until(lambda: flow.step().key == 'cloud_signup') and s.connected() and s['ig_username'] == 'wiz'   # one account: the chooser is skipped
    flow.next(); at('cloud_name'); flow.cloud_edit.setText('c'); flow.next(); at('api_key'); flow.ckey_edit.setText('k'); flow.next()
    at('api_secret'); flow.csecret_edit.setText('sek'); flow.next(); at('test_hosting'); assert s.hosting_ready()
    flow.next(); assert wait_until(lambda: flow.step().key == 'time')         # the test passed, so it moved on
    flow.time_edit.setTime(ui.QTime(18, 30)); flow.next(); at('startup'); assert not flow.primary.isVisible()
    flow.choose_startup(True); at('done'); assert flow.primary.text() == 'Start using PostQueue'
    flow.back(); at('startup'); flow.choose_startup(True); at('done')     # Back works
    flow.next()                                                               # finishes
    again = cfg.Settings(s.path)
    assert again['setup_done'] and again['default_hour'] == 18 and again['default_minute'] == 30 and again['start_with_windows'] is True and again.ready()
    window.refresh(); assert not window.needs_setup() and not window.banner.isVisible()


def test_setup_flow_checks_the_app_id_and_secret(window, qapp, monkeypatch):
    s = fresh_settings(window)
    flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    flow.goto('app_id')
    monkeypatch.setattr(ui.ig, 'app_info', lambda app_id, version=None: None)        # an app in development mode: not visible publicly
    flow.app_id_edit.setText('777'); flow.next()
    assert wait_until(lambda: flow.step().key == 'app_secret') and 'development mode' in flow.app_id_status.text()
    monkeypatch.setattr(ui.ig, 'app_token', lambda app_id, secret, version=None: (_ for _ in ()).throw(ui.ig.InstagramError('The App secret does not match this App ID. Copy it again.')))
    flow.app_secret_edit.setText('wrong'); flow.next()
    assert wait_until(lambda: flow.task is None and 'does not match' in flow.secret_status.text()) and flow.step().key == 'app_secret'   # stays put
    flow.app_secret_edit.clear(); flow.next(); assert flow.step().key == 'redirect'   # Facebook Login may go on without a secret
    monkeypatch.setattr(ui.ig, 'app_info', lambda app_id, version=None: (_ for _ in ()).throw(ui.ig.InstagramError('A Meta App ID is a number (App settings › Basic › App ID).')))
    flow.goto('app_id'); flow.app_id_edit.setText('abc'); flow.next()
    assert wait_until(lambda: flow.task is None and 'is a number' in flow.app_id_status.text()) and flow.step().key == 'app_id'


def test_setup_flow_token_shortcut_and_account_choice(window, qapp, monkeypatch):
    s = fresh_settings(window)
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '1', 'username': 'one', 'host': ui.ig.FB_HOST, 'via': 'A'}, {'id': '2', 'username': 'two', 'host': ui.ig.FB_HOST, 'via': 'B'}])
    flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    flow.next(); flow.next(); flow.choose_token(); assert flow.step().key == 'token' and not flow.primary.isEnabled()
    flow.token_edit.setText('TOK'); assert flow.primary.isEnabled(); flow.next()
    assert wait_until(lambda: flow.step().key == 'account') and flow.account_combo.count() == 2
    flow.account_combo.setCurrentIndex(1); flow.next(); assert s['ig_username'] == 'two' and flow.step().key == 'cloud_signup'
    total = int(flow.progress.text().split()[-1]); assert total < len(flow.steps)   # the app-creation screens were skipped
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: (_ for _ in ()).throw(ui.ig.InstagramError('Invalid OAuth access token', code=190)))
    flow.goto('token'); flow.token_edit.setText('BAD'); flow.next()
    assert wait_until(lambda: flow.task is None and 'expired' in flow.token_status.text()) and flow.step().key == 'token'


def test_setup_flow_simple_path_with_built_in_app(window, qapp, monkeypatch):
    """With branding.py filled in, setup is: professional account, sign in, posting time. No developer screens."""
    s = fresh_settings(window); s['start_with_windows'] = None
    monkeypatch.setattr(ui.branding, 'META_APP_ID', '424242'); monkeypatch.setattr(ui.branding, 'SERVICE_URL', 'https://svc.example')
    seen = {}
    def fake_login(kind, app_id, secret, version, cancelled, service_url=''):
        seen.update(kind=kind, app_id=app_id, secret=secret, service_url=service_url); return ('LONG', 5184000, 'http://localhost:48215/')
    monkeypatch.setattr(ui.ig, 'run_login', fake_login)
    monkeypatch.setattr(ui.ig, 'accounts_for_token', lambda token, version: [{'id': '9', 'username': 'wiz', 'host': ui.ig.FB_HOST, 'via': 'Page'}])
    flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    assert not flow.advanced and s['hosting'] == 'service' and 'Two things' in flow.step().lead
    flow.next(); assert flow.step().key == 'professional' and flow.progress.text() == 'STEP 1 OF 3'
    flow.next(); assert flow.step().key == 'signin' and flow.progress.text() == 'STEP 2 OF 3'   # no login-kind or Meta app screens
    flow.kind_link.click(); assert s['login_kind'] == 'instagram' and 'Facebook' in flow.kind_link.text(); flow.kind_link.click(); assert s['login_kind'] == 'facebook'
    flow.next()
    assert wait_until(lambda: flow.step().key == 'time') and s.connected()
    assert seen == {'kind': 'facebook', 'app_id': '424242', 'secret': '', 'service_url': 'https://svc.example'}   # the built-in app, the service does the exchange
    flow.next(); assert flow.step().key == 'done'                                                                 # hosting and start-up screens skipped
    flow.next()
    again = cfg.Settings(s.path)
    assert again['setup_done'] and again['start_with_windows'] is True and again['hosting'] == 'service' and again.ready('https://svc.example')
    window.refresh(); assert not window.needs_setup() and not window.banner.isVisible()


def test_setup_flow_advanced_link_restores_developer_path(window, qapp, monkeypatch):
    s = fresh_settings(window)
    monkeypatch.setattr(ui.branding, 'META_APP_ID', '424242'); monkeypatch.setattr(ui.branding, 'SERVICE_URL', 'https://svc.example')
    flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    link = [b for b in flow.step().page.findChildren(ui.QPushButton) if 'Advanced' in b.text()]; assert len(link) == 1
    link[0].click(); assert flow.advanced and s['hosting'] == 'cloudinary' and flow.step().key == 'professional'
    flow.next(); assert flow.step().key == 'login_kind' and flow.progress.text().endswith('OF 15')


def test_hosting_panel_offers_the_built_in_service(window, monkeypatch):
    monkeypatch.setattr(ui.branding, 'SERVICE_URL', 'https://svc.example')
    window.settings['hosting'] = 'service'
    panel = ui.HostingPanel(window.settings)
    assert panel.mode.count() == 2 and panel.mode.currentData() == 'service' and not panel.cloud_group.isVisibleTo(panel) and panel.is_ready()
    panel.mode.setCurrentIndex(1); assert window.settings['hosting'] == 'cloudinary' and panel.cloud_group.isVisibleTo(panel) and panel.is_ready()   # cloud details were in the fixture
    monkeypatch.setattr(ui.branding, 'SERVICE_URL', '')
    assert ui.HostingPanel(window.settings).mode.count() == 1


def test_several_files_become_a_carousel_when_asked(window, tmp_path, monkeypatch):
    monkeypatch.setattr(ui.MainWindow, 'ask_how_to_add', lambda self, count: 'carousel')
    added = window.add_paths([photo(tmp_path, 'a.jpg'), photo(tmp_path, 'b.jpg'), photo(tmp_path, 'c.jpg')])
    assert len(added) == 1 and added[0].kind == 'carousel' and window.current is added[0] and window.items_list.count() == 3
    assert window.name_label.text().startswith('a.jpg +2  (carousel of 3)') and not window.target_combo.isVisibleTo(window.detail) and 'Carousel' in window.media_info.text()
    window.items_list.setCurrentRow(2); window.remove_item(); assert len(window.current.items) == 2 and window.items_list.count() == 2
    window.add_item_path(photo(tmp_path, 'd.jpg')); assert len(window.current.items) == 3
    monkeypatch.setattr(ui.MainWindow, 'ask_how_to_add', lambda self, count: None)
    assert window.add_paths([photo(tmp_path, 'e.jpg'), photo(tmp_path, 'f.jpg')]) == []   # Cancel adds nothing


def test_story_switch_disables_caption_and_saves(window, tmp_path):
    window.add_paths([photo(tmp_path, size=(2000, 1000))]); post = window.current
    window.target_combo.setCurrentIndex(1)
    assert not window.caption.isEnabled() and 'Story' in window.media_info.text() and not window.first_comment.isVisibleTo(window.detail) and window.dirty
    window.save_button.click(); assert eng.Engine(window.engine.dir).get(post.id).target == 'story' and window.name_label.text().endswith('(story)')
    window.target_combo.setCurrentIndex(0); window.save_button.click(); assert post.target == 'feed' and window.caption.isEnabled()


def test_first_comment_alt_text_cover_and_draft(window, tmp_path):
    window.add_paths([photo(tmp_path)]); post = window.current
    assert window.alt_text.isVisibleTo(window.detail) and not window.cover_spin.isVisibleTo(window.detail)
    window.first_comment.setText('#one #two'); window.alt_text.setText('A teal square'); window.draft_box.setChecked(True); window.save_button.click()
    again = eng.Engine(window.engine.dir).get(post.id)
    assert again.first_comment == '#one #two' and again.alt_text == 'A teal square' and again.status == 'draft' and 'Draft' in window.status_label.text()
    assert window.engine.check() is None and window.tree.topLevelItem(0).text(3) == 'Draft' and window.post_now_button.isVisible()
    window.draft_box.setChecked(False); window.save_button.click(); assert post.status == 'queued'
    clip = tmp_path / 'c.mp4'; clip.write_bytes(b'\0' * 100); window.add_paths([str(clip)])
    assert window.cover_spin.isVisibleTo(window.detail) and not window.alt_text.isVisibleTo(window.detail)
    window.cover_spin.setValue(3.5); window.save_button.click(); assert window.current.cover_seconds == 3.5


def test_duplicate_makes_a_scheduled_copy(window, tmp_path):
    window.add_paths([photo(tmp_path)]); original = window.current; window.caption.setPlainText('Again'); window.save_button.click()
    window.duplicate_button.click()
    assert window.current is not original and window.current.caption == 'Again' and len(window.engine.posts) == 2 and window.current.media != original.media


def test_add_from_a_sheet(window, tmp_path, monkeypatch):
    photo(tmp_path, 'one.jpg'); photo(tmp_path, 'two.jpg')
    sheet = tmp_path / 'plan.csv'; sheet.write_text('file,date,time,caption\none.jpg,2027-05-01,10:00,First\ntwo.jpg,2027-05-02,,Second\nnope.jpg,,,\n', encoding='utf-8')
    warned = []; monkeypatch.setattr(QMessageBox, 'warning', staticmethod(lambda *a, **k: warned.append(a[2])))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', staticmethod(lambda *a, **k: (str(sheet), '')))
    window.act_sheet.trigger()
    assert len(window.engine.posts) == 2 and window.current.caption == 'Second' and 'Added 2 posts' in warned[0] and 'Row 4' in warned[0]


def test_update_notice(window, monkeypatch):
    monkeypatch.setattr(ui.eng, 'check_for_update', lambda current, fetch=None: ('v9.0.0', 'https://x/releases/v9.0.0'))
    window.check_update()
    assert wait_until(lambda: window.result.isVisible() and '9.0.0 is available' in window.result_text.text())
    assert window.result_open.text() == 'Download' and window.result.property('ok') == 'update'
    window.result_dismiss.click(); assert not window.result.isVisible()


def test_date_picker_has_calendar_and_time(window, tmp_path):
    window.add_paths([photo(tmp_path)]); picker = window.when_edit
    assert '▾' in picker.date_button.text() and picker.time_edit.time().hour() == 9
    picker.pick_date(ui.QDate(2027, 3, 4)); assert window.dirty and picker.dateTime().toPython().month == 3
    picker.time_edit.setTime(ui.QTime(17, 45)); assert picker.dateTime().toPython().hour == 17
    window.save_button.click(); assert window.current.when_dt().year == 2027 and window.current.when_dt().minute == 45


def test_banner_button_opens_wizard_until_ready(window, monkeypatch):
    opened = []
    monkeypatch.setattr(ui.SetupFlow, 'exec', lambda self: opened.append('wizard'))
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


def test_setup_demos_replay_and_point_at_real_targets(window, qapp):
    """Every demo frame points at an element its page really draws, the panels replay on their own, and a click skips ahead."""
    import demo
    from PySide6.QtGui import QPainter, QPixmap
    for key, frames in demo.SCENES.items():
        for caption, page, target in frames:
            pm = QPixmap(1, 1); painter = QPainter(pm); pg = demo.Page(painter, 'Segoe UI'); pg.draw(page); painter.end()
            assert target in pg.rects, f'{key}: "{caption}" points at {target}, which the page does not draw'
    s = fresh_settings(window); flow = ui.SetupFlow(s, window); flow.show(); qapp.processEvents()
    with_demo = {st.key for st in flow.steps if st.demo}
    assert with_demo == {'professional', 'create_app', 'app_id', 'app_secret', 'redirect', 'roles', 'cloud_name', 'api_key', 'api_secret'}
    flow.goto('create_app'); qapp.processEvents()
    panel = flow.step().page.findChild(demo.DemoPanel); assert panel.isVisible() and panel.timer.isActive() and len(panel.frames) == 7
    panel.tick(demo.FRAME_MS); assert panel.index == 1                       # the next frame after one frame's time
    for _ in range(6): panel.tick(demo.FRAME_MS + demo.LAST_EXTRA_MS)
    assert panel.index == 0                                                   # and round again
    for i in range(len(panel.frames)):
        panel.show_frame(i); panel.elapsed = int(demo.FRAME_MS * 0.5); assert not panel.grab().isNull()   # every frame paints, mid-click
    panel.show_frame(0); panel.mousePressEvent(None); assert panel.index == 1   # a click skips ahead
    flow.goto('app_id'); qapp.processEvents(); assert not panel.timer.isActive()   # hidden pages stop replaying
    assert flow.step().page.findChild(demo.DemoPanel).timer.isActive()
    flow.resize(760, 600); qapp.processEvents()
    flow.goto('app_secret'); qapp.processEvents()
    assert not flow.step().page.findChild(ui.QLabel, 'hint').isVisible()      # short window: the demo wins over the hint
    flow.resize(940, 720); qapp.processEvents(); assert flow.step().page.findChild(ui.QLabel, 'hint').isVisible()
    flow.reject()


def test_first_run_shows_only_the_setup_and_quits_if_it_is_closed(window, qapp, monkeypatch, tmp_path):
    s = fresh_settings(window); assert window.needs_setup()
    monkeypatch.setattr(ui.SetupFlow, 'exec', lambda self: ui.QDialog.Rejected)
    assert window.first_run() is False                                       # closed early: the window never comes
    def finish(self): self.accept(); return ui.QDialog.Accepted
    monkeypatch.setattr(ui.SetupFlow, 'exec', finish)
    assert window.first_run() is True and s['setup_done'] and not window.needs_setup()
    assert window.first_run() is True                                        # set up already: nothing to show
    # main() ends with 0, without the window or the event loop, when the first setup is closed
    monkeypatch.setenv('POSTQUEUE_DATA', str(tmp_path / 'fresh')); monkeypatch.setattr(ui, 'make_app', lambda: QApplication.instance())
    monkeypatch.setattr(ui.SetupFlow, 'exec', lambda self: ui.QDialog.Rejected)
    shown = []; monkeypatch.setattr(ui.MainWindow, 'show', lambda self: shown.append(True))
    assert ui.main([]) == 0 and not shown and not (tmp_path / 'fresh' / 'postqueue.lock').exists()


def test_setup_window_fits_the_screen(window, qapp):
    flow = ui.SetupFlow(fresh_settings(window), window); avail = flow.avail
    assert flow.width() <= avail.width() - 40 and flow.height() <= avail.height() - 70 and flow.height() <= 720
    assert flow.minimumHeight() <= flow.height() and flow.minimumWidth() <= flow.width()
    flow.show(); qapp.processEvents(); flow.move(avail.right() + 300, avail.top() - 400); flow.keep_on_screen(); qapp.processEvents()
    frame = flow.frameGeometry(); assert avail.contains(frame.topLeft()) and avail.contains(frame.bottomRight())   # title bar and edges reachable
    assert flow.windowFlags() & ui.Qt.WindowCloseButtonHint
    flow.reject()
