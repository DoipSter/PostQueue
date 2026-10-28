"""Engine tests: queue storage, timing rules, photo preparation, publishing steps, hosting and the Instagram client,
all against simulated services (no network)."""
import datetime as dt
import io
import json
import os
import struct
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

import engine as eng
import hosting
import instagram as ig
import settings as cfg
from engine import Engine, EngineError, Post


def make_image(path, size=(1200, 800), mode='RGB', fmt=None):
    im = Image.new(mode, size, (200, 60, 90) if mode == 'RGB' else (200, 60, 90, 128))
    im.save(path, fmt); return Path(path)


def atom(name, body): return struct.pack('>I4s', 8 + len(body), name) + body


def make_mp4(path, seconds=10.0, size=(1080, 1920), rotated=False, moov_last=True, long_header=False, tkhd_size=True):
    """A minimal MP4/MOV container with the header atoms PostQueue reads (ftyp, mvhd, tkhd, stsd) and a dummy mdat.
    `rotated` writes a 90-degree track matrix as phones do; `long_header` uses the 64-bit variants; `tkhd_size`
    False leaves the track header's size empty so the sample description has to be used."""
    w, h = size; scale = 1000
    if long_header: mvhd = b'\1\0\0\0' + b'\0' * 16 + struct.pack('>IQ', scale, int(seconds * scale)) + b'\0' * 80
    else: mvhd = b'\0' * 12 + struct.pack('>II', scale, int(seconds * scale)) + b'\0' * 80
    matrix = struct.pack('>9i', 0, 0x10000, 0, -0x10000, 0, 0, 0, 0, 0x40000000) if rotated else struct.pack('>9i', 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd = b'\0' * 40 + matrix + (struct.pack('>II', w << 16, h << 16) if tkhd_size else b'\0' * 8)
    stsd = atom(b'stsd', b'\0' * 4 + struct.pack('>I', 1) + atom(b'avc1', b'\0' * 24 + struct.pack('>HH', w, h) + b'\0' * 50))
    trak = atom(b'trak', atom(b'tkhd', tkhd) + atom(b'mdia', atom(b'minf', atom(b'stbl', stsd))))
    moov = atom(b'moov', atom(b'mvhd', mvhd) + trak)
    mdat = struct.pack('>I4sQ', 1, b'mdat', 16 + 64) + b'\0' * 64 if long_header else atom(b'mdat', b'\0' * 64)
    ftyp = atom(b'ftyp', b'isom' + b'\0' * 4 + b'isomiso2mp41')
    Path(path).write_bytes(ftyp + (mdat + moov if moov_last else moov + mdat)); return Path(path)


@pytest.fixture
def now():
    return dt.datetime(2026, 10, 26, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=-7)))


@pytest.fixture
def engine(tmp_path):
    return Engine(tmp_path / 'data', {'default_hour': 9, 'default_minute': 0, 'grace_hours': 12, 'fit_mode': 'crop'})


class FakeInstagram:
    """Records calls; `statuses` drives the video processing poll."""
    def __init__(self, statuses=('IN_PROGRESS', 'FINISHED'), fail=None):
        self.calls, self.statuses, self.fail = [], list(statuses), fail
    def create_image(self, url, caption='', alt_text='', carousel_item=False):
        self.calls.append(('create_image', url, caption) if not (alt_text or carousel_item) else ('create_image', url, caption, alt_text, carousel_item))
        if self.fail == 'create': raise ig.InstagramError('Media not fetched', code=9004)
        if self.fail == 'rate': raise ig.InstagramError('Application request limit reached', code=4)
        return f'C1-{len(self.calls)}' if carousel_item else 'C1'
    def create_video(self, url, caption='', share_to_feed=True, cover_offset_ms=None, carousel_item=False):
        self.calls.append(('create_video', url, caption) if not (cover_offset_ms or carousel_item) else ('create_video', url, caption, cover_offset_ms, carousel_item))
        return f'C2-{len(self.calls)}' if carousel_item else 'C2'
    def create_story(self, url, video=False): self.calls.append(('create_story', url, video)); return 'S1'
    def create_carousel(self, children, caption=''): self.calls.append(('create_carousel', list(children), caption)); return 'CAR'
    def comment(self, media_id, text):
        self.calls.append(('comment', media_id, text))
        if self.fail == 'comment': raise ig.InstagramError('Comments are off for this post', code=100)
        return 'K1'
    def wait_container(self, cid, timeout=900, interval=5, on_progress=None, sleep=None):
        for s in self.statuses:
            if s == 'FINISHED': return
            on_progress and on_progress(s)
        raise ig.InstagramError('ERROR')
    def publish(self, cid): self.calls.append(('publish', cid)); return '1789'
    def permalink(self, mid): return f'https://www.instagram.com/p/{mid}/'


class FakeProvider:
    name = 'FakeHost'
    def __init__(self, fail=False): self.uploaded, self.deleted, self.fail = [], [], fail
    def upload(self, path, kind, on_progress=None):
        if self.fail: raise hosting.HostingError('Could not reach the media hosting service', transient=True)
        self.uploaded.append((Path(path).name, kind, Path(path).stat().st_size)); on_progress and on_progress(1.0)
        return f'https://host.example/{Path(path).name}', 'h1'
    def delete(self, handle, kind): self.deleted.append((handle, kind))


# --- queue -------------------------------------------------------------------------------------------------------
def test_add_copies_media_and_queues(engine, tmp_path, now):
    src = make_image(tmp_path / 'holiday.PNG')
    post = engine.add(src, now + dt.timedelta(days=1), caption='Sun #beach')
    assert post.kind == 'image' and post.status == 'queued' and post.caption == 'Sun #beach' and post.fit == 'crop'
    assert Path(post.media).exists() and Path(post.media).parent == engine.media_dir and Path(post.media).suffix == '.png'
    assert post.name() == 'holiday.PNG' and post.when_dt() == now + dt.timedelta(days=1)
    src.unlink()   # the original can go: the queue keeps its own copy
    assert Engine(engine.dir).get(post.id).media == post.media


def test_unsupported_files_are_refused(engine, tmp_path, now):
    bad = tmp_path / 'notes.txt'; bad.write_text('x')
    with pytest.raises(EngineError, match='not a supported photo or video'): engine.add(bad, now)
    with pytest.raises(EngineError, match='file not found'): engine.add(tmp_path / 'missing.jpg', now)
    empty = tmp_path / 'empty.mp4'; empty.write_bytes(b'')
    with pytest.raises(EngineError, match='empty'): engine.add(empty, now)
    assert eng.media_kind('clip.MOV') == 'video'


def test_queue_survives_reload_and_interrupted_posting(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); engine.update(post, status='posting')
    again = Engine(engine.dir)
    assert again.get(post.id).status == 'queued'   # a crash mid-post is retried, not lost


def test_remove_deletes_the_copy(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); media = Path(post.media)
    engine.remove(post)
    assert not media.exists() and engine.get(post.id) is None and not Engine(engine.dir).posts


def test_posts_file_is_written_atomically(engine, tmp_path, now):
    engine.add(make_image(tmp_path / 'a.jpg'), now)
    assert not list(engine.dir.glob('posts.json*.tmp')) and json.loads(engine.posts_file.read_text(encoding='utf-8'))[0]['kind'] == 'image'


# --- timing ------------------------------------------------------------------------------------------------------
def test_next_slot_skips_taken_days(engine, tmp_path, now):
    slot = engine.next_slot(now)
    assert slot == now.replace(hour=9, minute=0) + dt.timedelta(days=1)   # 9:00 today has passed
    engine.add(make_image(tmp_path / 'a.jpg'), slot)
    assert engine.next_slot(now) == slot + dt.timedelta(days=1)
    assert engine.next_slot(now.replace(hour=8)) == now.replace(hour=9, minute=0)


def test_check_returns_due_post_and_marks_old_ones_missed(engine, tmp_path, now):
    due = engine.add(make_image(tmp_path / 'a.jpg'), now - dt.timedelta(minutes=3))
    old = engine.add(make_image(tmp_path / 'b.jpg'), now - dt.timedelta(hours=20))
    later = engine.add(make_image(tmp_path / 'c.jpg'), now + dt.timedelta(hours=1))
    assert engine.check(now) is due
    assert engine.get(old.id).status == 'missed' and 'not running' in old.error and later.status == 'queued'
    assert Engine(engine.dir).get(old.id).status == 'missed'
    assert engine.next_scheduled(now) is due


def test_check_waits_for_retry_time(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now - dt.timedelta(minutes=10))
    engine.update(post, attempts=1, retry_at=eng.fmt_when(now + dt.timedelta(minutes=4)))
    assert engine.check(now) is None
    assert engine.check(now + dt.timedelta(minutes=5)) is post
    engine.update(post, when=eng.fmt_when(now - dt.timedelta(hours=30)))
    assert engine.check(now + dt.timedelta(minutes=5)) is post   # a post that already tried is not marked missed


def test_reschedule_clears_failure(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); engine.update(post, status='failed', error='x', attempts=3)
    engine.reschedule(post, now + dt.timedelta(days=2))
    assert post.status == 'queued' and post.error == '' and post.attempts == 0 and post.when_dt() == now + dt.timedelta(days=2)


def test_caption_warnings():
    assert eng.caption_warnings('fine #one #two') == []
    assert 'over' in eng.caption_warnings('x' * 2201)[0]
    assert '31 hashtags' in eng.caption_warnings(' '.join(f'#t{i}' for i in range(31)))[0]


# --- photos ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize('size,fit,expected', [
    ((1000, 2000), 'crop', (0, 375, 1000, 1625)),    # too tall: the middle 4:5 window is kept
    ((1000, 2000), 'pad', (-300, 0, 1300, 2000)),    # too tall: white at both sides
    ((3000, 1000), 'crop', (545, 0, 2455, 1000)),    # too wide: the middle 1.91:1 window
    ((3000, 1000), 'pad', (0, -285, 3000, 1286)),    # too wide: white above and below
    ((1200, 800), 'crop', (0, 0, 1200, 800)),        # fine as it is, whichever fit
    ((1200, 800), 'pad', (0, 0, 1200, 800)),
])
def test_target_box(size, fit, expected):
    assert eng.target_box(*size, fit) == expected


@pytest.mark.parametrize('size,fit,expected', [
    ((1000, 2000), 'crop', (1000, 1250)),   # too tall: cropped to 4:5
    ((1000, 2000), 'pad', (1440, 1800)),    # too tall: padded to 4:5, then limited to 1440 wide
    ((3000, 1000), 'crop', (1440, 754)),    # too wide: cropped to 1.91:1 and scaled down
    ((3000, 2000), 'crop', (1440, 960)),    # fine shape, just large
    ((200, 200), 'crop', (320, 320)),       # tiny: scaled up to the minimum width
])
def test_prepare_image_shapes(tmp_path, size, fit, expected):
    out = Engine.prepare_image(make_image(tmp_path / 'in.png', size), tmp_path / 'out.jpg', fit)
    with Image.open(out) as im: assert im.format == 'JPEG' and im.mode == 'RGB' and im.size == expected


def test_prepare_image_flattens_transparency_and_orientation(tmp_path):
    src = make_image(tmp_path / 'in.png', (800, 800), mode='RGBA')
    out = Engine.prepare_image(src, tmp_path / 'out.jpg')
    with Image.open(out) as im:
        r, g, b = im.getpixel((10, 10)); assert r > 200 and g > 150   # half-transparent red on white
    # EXIF orientation 6 (rotated 90 degrees) is applied, so a 1200x800 file becomes 800x1200 -> 4:5 crop
    im = Image.new('RGB', (1200, 800), 'blue'); exif = im.getexif(); exif[0x0112] = 6; im.save(tmp_path / 'rot.jpg', exif=exif)
    out = Engine.prepare_image(tmp_path / 'rot.jpg', tmp_path / 'rot_out.jpg')
    with Image.open(out) as im: assert im.size == (800, 1000)


def test_prepare_image_stays_under_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(eng, 'MAX_IMAGE_BYTES', 120_000)
    Image.effect_noise((1440, 1440), 100).convert('RGB').save(tmp_path / 'noise.png')   # noise barely compresses
    out = Engine.prepare_image(tmp_path / 'noise.png', tmp_path / 'out.jpg')
    with Image.open(out) as im: assert out.stat().st_size <= 120_000 and 320 <= im.width < 1440   # quality down, then shrunk


# --- videos ------------------------------------------------------------------------------------------------------
def test_video_info_reads_the_container_header(tmp_path):
    info = eng.video_info(make_mp4(tmp_path / 'a.mp4', 42.5, (1080, 1920)))
    assert info == {'seconds': 42.5, 'width': 1080, 'height': 1920} and eng.video_summary(info) == '0:42 · 1080×1920'
    assert eng.video_info(make_mp4(tmp_path / 'b.mov', 7, (1920, 1080), moov_last=False, long_header=True)) == {'seconds': 7.0, 'width': 1920, 'height': 1080}
    assert eng.video_info(make_mp4(tmp_path / 'c.mp4', 7, (1920, 1080), rotated=True))['width'] == 1080   # filmed on a phone held upright
    assert eng.video_info(make_mp4(tmp_path / 'd.mp4', 7, (640, 480), tkhd_size=False)) == {'seconds': 7.0, 'width': 640, 'height': 480}
    junk = tmp_path / 'junk.mp4'; junk.write_bytes(b'\0' * 100)
    assert eng.video_info(junk) is None and eng.video_summary(None) == ''


def test_add_checks_videos_right_away(engine, tmp_path, now):
    with pytest.raises(EngineError, match='16 min 0 s long. Instagram Reels must be 15 minutes'): engine.add(make_mp4(tmp_path / 'long.mp4', 16 * 60), now)
    with pytest.raises(EngineError, match='1.5 s long. Instagram Reels must be at least 3 seconds'): engine.add(make_mp4(tmp_path / 'short.mp4', 1.5), now)
    assert engine.add(make_mp4(tmp_path / 'reel.mp4', 30, (1080, 1920)), now).warning == ''
    assert engine.add(make_mp4(tmp_path / 'square.mp4', 30, (1080, 1080)), now).warning == ''
    wide = engine.add(make_mp4(tmp_path / 'wide.mp4', 30, (1920, 1080)), now)
    assert 'landscape (1920×1080)' in wide.warning and 'black bars' in wide.warning
    assert 'very tall' in engine.add(make_mp4(tmp_path / 'tall.mp4', 30, (500, 2000)), now).warning
    junk = tmp_path / 'junk.mp4'; junk.write_bytes(b'\0' * 100)
    assert 'Could not read' in engine.add(junk, now).warning
    assert Engine(engine.dir).get(wide.id).warning == wide.warning   # kept with the post


# --- publishing --------------------------------------------------------------------------------------------------
def test_publish_photo_end_to_end(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.png', (1000, 2000)), now, caption='Hello')
    insta, host, steps = FakeInstagram(), FakeProvider(), []
    engine.publish(post, insta, host, lambda f, t: steps.append((f, t)))
    assert post.status == 'posted' and post.media_id == '1789' and post.permalink.endswith('/1789/') and post.posted_at
    assert host.uploaded[0][0] == f'{post.id}-1.jpg' and host.uploaded[0][1] == 'image' and host.deleted == [('h1', 'image')]
    assert insta.calls[0] == ('create_image', f'https://host.example/{post.id}-1.jpg', 'Hello') and insta.calls[1] == ('publish', 'C1')
    assert not list(engine.tmp_dir.iterdir()) and steps[-1] == (1.0, 'Posted') and steps[0][0] < steps[-1][0]
    assert Engine(engine.dir).get(post.id).status == 'posted'


def test_publish_video_waits_for_processing(engine, tmp_path, now):
    clip = tmp_path / 'clip.mp4'; clip.write_bytes(b'\0' * 1000)
    post = engine.add(clip, now); insta, host, steps = FakeInstagram(('IN_PROGRESS', 'IN_PROGRESS', 'FINISHED')), FakeProvider(), []
    engine.publish(post, insta, host, lambda f, t: steps.append(t))
    assert post.status == 'posted' and host.uploaded[0] == (Path(post.media).name, 'video', 1000)
    assert insta.calls[0][0] == 'create_video' and any('processing the video (in progress)' in s for s in steps)


def test_publish_video_processing_error_fails(engine, tmp_path, now):
    clip = tmp_path / 'clip.mp4'; clip.write_bytes(b'\0' * 10)
    post = engine.add(clip, now); engine.publish(post, FakeInstagram(('ERROR',)), FakeProvider())
    assert post.status == 'failed' and 'ERROR' in post.error


def test_transient_failure_requeues_then_fails(engine, tmp_path, now, monkeypatch):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); host = FakeProvider(fail=True)
    engine.publish(post, FakeInstagram(), host)
    assert post.status == 'queued' and post.attempts == 1 and post.retry_at and 'Trying again' in post.error
    engine.publish(post, FakeInstagram(fail='rate'), FakeProvider())
    assert post.status == 'queued' and post.attempts == 2
    engine.publish(post, FakeInstagram(fail='rate'), FakeProvider())
    assert post.status == 'failed' and post.attempts == 3 and 'Instagram error 4' in post.error


def test_permanent_failure_explains(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); host = FakeProvider()
    engine.publish(post, FakeInstagram(fail='create'), host)
    assert post.status == 'failed' and 'could not fetch the media' in post.error and host.deleted == [('h1', 'image')]


def test_carousel_add_publish_and_cleanup(engine, tmp_path, now):
    files = [make_image(tmp_path / f'p{i}.jpg', (1000, 1000)) for i in range(2)]; clip = tmp_path / 'c.mp4'; clip.write_bytes(b'\0' * 500)
    post = engine.add_carousel(files + [clip], now, caption='Three in one')
    assert post.kind == 'carousel' and len(post.items) == 3 and post.name() == 'p0.jpg +2' and post.type_text() == 'carousel of 3'
    assert all(Path(i).exists() for i in post.items) and Path(post.items[0]).name == f'{post.id}-1.jpg' and post.media == post.items[0]
    assert Engine(engine.dir).get(post.id).items == post.items
    insta, host, steps = FakeInstagram(), FakeProvider(), []
    engine.publish(post, insta, host, lambda f, t: steps.append(t))
    assert post.status == 'posted'
    kinds = [c[0] for c in insta.calls]
    assert kinds == ['create_image', 'create_image', 'create_video', 'create_carousel', 'publish'] and insta.calls[0][4] is True and insta.calls[2][4] is True
    assert insta.calls[3][1] == ['C1-1', 'C1-2', 'C2-3'] and insta.calls[3][2] == 'Three in one'
    assert len(host.uploaded) == 3 and len(host.deleted) == 3 and not list(engine.tmp_dir.iterdir())
    assert any('2 of 3' in s for s in steps) and any('assembling' in s for s in steps)
    with pytest.raises(EngineError, match='2 to 10'): engine.add_carousel([files[0]], now)


def test_carousel_items_added_and_removed(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now); assert post.items == [post.media]
    engine.add_item(post, make_image(tmp_path / 'b.jpg'))
    assert post.kind == 'carousel' and len(post.items) == 2 and Path(post.items[1]).name == f'{post.id}-2.jpg'
    gone = post.items[0]; engine.remove_item(post, 0)
    assert post.kind == 'image' and len(post.items) == 1 and post.media == post.items[0] and not Path(gone).exists() and post.name() == 'b.jpg'
    with pytest.raises(EngineError, match='at least one'): engine.remove_item(post, 0)
    story = engine.add(make_image(tmp_path / 's.jpg'), now, target='story')
    with pytest.raises(EngineError, match='story holds one'): engine.add_item(story, tmp_path / 'a.jpg')
    for i in range(9): engine.add_item(post, make_image(tmp_path / f'x{i}.jpg'))
    with pytest.raises(EngineError, match='at most 10'): engine.add_item(post, tmp_path / 'a.jpg')
    engine.remove(post); assert not any(Path(i).exists() for i in post.items)


def test_story_is_9_16_and_has_no_caption(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'wide.jpg', (2000, 1000)), now, caption='ignored', target='story')
    assert post.type_text() == 'story'
    insta, host = FakeInstagram(), FakeProvider(); engine.publish(post, insta, host)
    assert post.status == 'posted' and insta.calls[0] == ('create_story', f'https://host.example/{post.id}-1.jpg', False) and insta.calls[1] == ('publish', 'S1')
    out = Engine.prepare_image(tmp_path / 'wide.jpg', tmp_path / 'story.jpg', 'crop', story=True)
    with Image.open(out) as im: assert abs(im.width / im.height - 9 / 16) < 0.01
    clip = tmp_path / 'c.mp4'; clip.write_bytes(b'\0' * 10); video = engine.add(clip, now, target='story')
    insta = FakeInstagram(); engine.publish(video, insta, FakeProvider())
    assert insta.calls[0][0] == 'create_story' and insta.calls[0][2] is True and video.status == 'posted'


def test_first_comment_alt_text_and_cover(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now, caption='Cap'); engine.update(post, first_comment='#tags #here', alt_text='A red square')
    insta = FakeInstagram(); engine.publish(post, insta, FakeProvider())
    assert insta.calls[0] == ('create_image', f'https://host.example/{post.id}-1.jpg', 'Cap', 'A red square', False)
    assert insta.calls[-1] == ('comment', '1789', '#tags #here') and post.status == 'posted' and not post.warning
    clip = tmp_path / 'c.mp4'; clip.write_bytes(b'\0' * 10); video = engine.add(clip, now); engine.update(video, cover_seconds=2.5, first_comment='hi')
    insta = FakeInstagram(fail='comment'); engine.publish(video, insta, FakeProvider())
    assert insta.calls[0][3] == 2500 and video.status == 'posted' and 'first comment could not be added' in video.warning


def test_drafts_wait_and_duplicate_copies(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now - dt.timedelta(minutes=1), caption='c'); engine.update(post, status='draft', alt_text='alt')
    assert engine.check(now) is None and engine.next_scheduled(now) is None and post.status_text() == 'Draft'
    copy = engine.duplicate(post)
    assert copy.id != post.id and copy.caption == 'c' and copy.alt_text == 'alt' and copy.status == 'queued' and copy.when_dt() == engine.next_slot(now) or copy.when_dt() > now
    assert Path(copy.media).exists() and copy.media != post.media and copy.items == [copy.media]
    engine.reschedule(post, now); assert engine.check(now) is post


def test_import_sheet(engine, tmp_path):
    make_image(tmp_path / 'one.jpg'); make_image(tmp_path / 'two.jpg'); make_image(tmp_path / 'three.jpg')
    (tmp_path / 'plan.csv').write_text('file,date,time,caption,first_comment,alt_text,target\n'
                                       'one.jpg,2027-03-04,18:30,Hello,#a #b,An image,\n'
                                       'two.jpg|three.jpg,2027-03-05,,Pair,,,\n'
                                       'missing.jpg,2027-03-06,09:00,,,,\n'
                                       'one.jpg,,,Story,,,story\n', encoding='utf-8')
    added, problems = engine.import_sheet(tmp_path / 'plan.csv')
    assert [p.caption for p in added] == ['Hello', 'Pair', 'Story'] and len(problems) == 1 and 'Row 4' in problems[0] and 'not found' in problems[0]
    assert added[0].when_dt().hour == 18 and added[0].first_comment == '#a #b' and added[0].alt_text == 'An image'
    assert added[1].kind == 'carousel' and added[1].when_dt().hour == 9 and added[2].target == 'story'


def test_update_check():
    assert eng.version_tuple('v1.1.0') > eng.version_tuple('1.1.0-rc1') > eng.version_tuple('1.0.0') and eng.version_tuple('v1.0.0-rc1') > eng.version_tuple('1.0.0-rc0')
    assert eng.check_for_update('1.0.0', fetch=lambda: {'tag_name': 'v1.1.0', 'html_url': 'https://x/r'}) == ('v1.1.0', 'https://x/r')
    assert eng.check_for_update('1.1.0', fetch=lambda: {'tag_name': 'v1.1.0', 'html_url': 'u'}) is None
    assert eng.check_for_update('1.0.0', fetch=lambda: {'tag_name': 'v2.0.0', 'draft': True}) is None
    assert eng.check_for_update('1.0.0', fetch=lambda: (_ for _ in ()).throw(OSError('offline'))) is None


def test_publish_without_setup_fails_clearly(engine, tmp_path, now):
    post = engine.add(make_image(tmp_path / 'a.jpg'), now)
    engine.publish(post, None, FakeProvider()); assert post.status == 'failed' and 'No Instagram account' in post.error
    engine.publish(post, FakeInstagram(), None); assert 'No media hosting' in post.error
    Path(post.media).unlink(); engine.publish(post, FakeInstagram(), FakeProvider()); assert 'missing' in post.error


def test_describe_errors():
    assert 'No connection' in Engine.describe(urllib.error.URLError('dns'))
    assert Engine.transient(urllib.error.URLError('dns')) and not Engine.transient(EngineError('x'))
    assert 'expired' in ig.InstagramError('bad', code=190).friendly()
    assert 'format' in ig.InstagramError('x', code=352, subcode=2207026).friendly()
    assert ig.InstagramError('x', http_status=503).transient and not ig.InstagramError('x', code=100).transient
    assert ig.InstagramError('Some message', code=100, user_msg='Shown to users').friendly() == 'Shown to users (Instagram error 100)'


# --- settings ----------------------------------------------------------------------------------------------------
def test_secrets_are_protected_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, 'MAC', False)   # the keychain path is covered below; this test must not touch a real keychain
    s = cfg.Settings(tmp_path / 'settings.json'); s['access_token'] = 'EAAB-secret'; s['cloudinary_api_secret'] = 'shh'; s['ig_user_id'] = '42'; s.save()
    raw = (tmp_path / 'settings.json').read_text(encoding='utf-8')
    assert 'EAAB-secret' not in raw and 'shh' not in raw and ('dpapi:' in raw or 'plain:' in raw)
    again = cfg.Settings(tmp_path / 'settings.json')
    assert again['access_token'] == 'EAAB-secret' and again['cloudinary_api_secret'] == 'shh' and again.connected() and not again.hosting_ready()
    assert cfg.unprotect('handwritten') == 'handwritten' and cfg.protect('') == ''


def test_settings_ignore_unknown_and_broken_files(tmp_path):
    (tmp_path / 'settings.json').write_text('{"grace_hours": 3, "bogus": 1, "access_token": "dpapi:notbase64!!"}', encoding='utf-8')
    s = cfg.Settings(tmp_path / 'settings.json')
    assert s['grace_hours'] == 3 and 'bogus' not in s and s['access_token'] == ''
    (tmp_path / 'settings.json').write_text('not json', encoding='utf-8')
    assert cfg.Settings(tmp_path / 'settings.json')['grace_hours'] == 12


def test_token_expiry(tmp_path):
    s = cfg.Settings(tmp_path / 's.json'); assert s.token_expiry() is None
    s['token_expires'] = '2026-12-01T10:00-07:00'; assert s.token_expiry().month == 12
    s['token_expires'] = 'soon'; assert s.token_expiry() is None


def test_instance_lock(tmp_path):
    lock = cfg.InstanceLock(tmp_path / 'postqueue.lock')
    assert lock.other_pid() is None
    lock.acquire(); assert json.loads(lock.path.read_text())['pid'] == os.getpid() and lock.other_pid() is None
    lock.request_show(); assert lock.take_show_request() and not lock.take_show_request()
    lock.release(); assert not lock.path.exists()
    lock.path.write_text('{"pid": 999999999}'); assert lock.other_pid() is None   # a dead process does not block
    assert cfg.process_alive(os.getpid()) and not cfg.process_alive(0)


def test_data_dir_override(monkeypatch, tmp_path):
    monkeypatch.setenv('POSTQUEUE_DATA', str(tmp_path / 'x')); assert cfg.data_dir() == tmp_path / 'x'
    monkeypatch.delenv('POSTQUEUE_DATA'); assert cfg.data_dir().name == 'PostQueue'


def test_startup_command_points_at_pythonw_or_exe():
    assert cfg.startup_command().endswith('--minimized') and 'PostQueue' in cfg.startup_command()


class FakeWinreg:
    """Enough of winreg to test the Run and StartupApproved logic without touching the real registry."""
    HKEY_CURRENT_USER, REG_SZ, REG_BINARY = 'HKCU', 1, 3

    def __init__(self): self.keys = {}

    class Key:
        def __init__(self, store): self.store = store
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def CreateKey(self, root, path): return self.Key(self.keys.setdefault(path, {}))
    def OpenKey(self, root, path):
        if path not in self.keys: raise FileNotFoundError(path)
        return self.Key(self.keys[path])
    def SetValueEx(self, key, name, reserved, kind, value): key.store[name] = (value, kind)
    def QueryValueEx(self, key, name):
        if name not in key.store: raise FileNotFoundError(name)
        return key.store[name]


def test_start_at_login_windows_never_deletes_the_run_entry(monkeypatch):
    import sys
    fake = FakeWinreg(); monkeypatch.setitem(sys.modules, 'winreg', fake); monkeypatch.setattr(cfg, 'MAC', False)
    assert not cfg.starts_at_login()
    assert cfg.set_start_at_login(True, '"C:\\x\\PostQueue.exe" --minimized')
    assert fake.keys[cfg.RUN_KEY][cfg.RUN_VALUE][0] == '"C:\\x\\PostQueue.exe" --minimized'
    assert fake.keys[cfg.APPROVED_KEY][cfg.RUN_VALUE][0][0] == 2 and cfg.starts_at_login()
    cfg.set_start_at_login(False)
    assert fake.keys[cfg.RUN_KEY][cfg.RUN_VALUE][0] == '"C:\\x\\PostQueue.exe" --minimized'   # still there, as the MSI expects
    assert fake.keys[cfg.APPROVED_KEY][cfg.RUN_VALUE][0][0] == 3 and not cfg.starts_at_login()
    # The installer's own entry is kept as written when turning on again
    fake.keys[cfg.RUN_KEY][cfg.RUN_VALUE] = ('"C:\\Programs\\PostQueue\\PostQueue-1.0.0-x64.exe" --minimized', 1)
    cfg.set_start_at_login(True, 'other')
    assert fake.keys[cfg.RUN_KEY][cfg.RUN_VALUE][0].startswith('"C:\\Programs') and cfg.starts_at_login()


def test_start_at_login_macos_launch_agent(tmp_path):
    import plistlib
    assert not cfg.starts_at_login(home=tmp_path)
    assert cfg.set_start_at_login(True, home=tmp_path)
    plist = cfg.launch_agent_path(tmp_path); assert plist.exists() and cfg.starts_at_login(home=tmp_path)
    data = plistlib.loads(plist.read_bytes())
    assert data['Label'] == cfg.BUNDLE_ID and data['RunAtLoad'] and data['ProgramArguments'][-1] == '--minimized' and 'PostQueue' in ' '.join(data['ProgramArguments'])
    cfg.set_start_at_login(False, home=tmp_path); assert not plist.exists() and not cfg.starts_at_login(home=tmp_path)


def test_secrets_on_macos_go_to_the_keychain(monkeypatch):
    store, calls = {}, []
    def fake_security(args, secret=None):
        calls.append(args[0])
        if args[0] == 'add-generic-password': store[args[-1]] = secret; return ''
        if args[0] == 'find-generic-password': return store[args[-2]]
        if args[0] == 'delete-generic-password': store.pop(args[-1], None); return ''
    monkeypatch.setattr(cfg, 'WINDOWS', False); monkeypatch.setattr(cfg, 'MAC', True); monkeypatch.setattr(cfg, '_keychain', fake_security)
    marker = cfg.protect('EAAB-token', 'access_token')
    assert marker == 'keychain:access_token' and store == {'PostQueue access_token': 'EAAB-token'}
    assert cfg.unprotect(marker, 'access_token') == 'EAAB-token'
    cfg.forget_secret('access_token'); assert store == {}


def test_data_dir_on_macos(monkeypatch):
    monkeypatch.delenv('POSTQUEUE_DATA', raising=False); monkeypatch.setattr(cfg, 'MAC', True)
    assert cfg.data_dir().parts[-3:] == ('Library', 'Application Support', 'PostQueue')


# --- hosting -----------------------------------------------------------------------------------------------------
def test_cloudinary_signature_and_upload(monkeypatch, tmp_path):
    import hashlib
    c = hosting.Cloudinary('demo', 'key', 'abcd')
    assert c.sign({'public_id': 'a', 'timestamp': '1', 'api_key': 'key', 'file': 'x', 'resource_type': 'image'}) == hashlib.sha1(b'public_id=a&timestamp=1abcd').hexdigest()
    sent = []
    class Reply(io.BytesIO):
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): pass
    def fake_urlopen(req, timeout=0):
        sent.append(req); return Reply(b'{"secure_url": "https://res.cloudinary.com/demo/x.jpg", "public_id": "postqueue/x"}' if 'upload' in req.full_url else b'{"result": "ok"}')
    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    f = make_image(tmp_path / 'p.jpg')
    url, handle = c.upload(f, 'image')
    assert url.startswith('https://res.cloudinary.com/') and handle == 'postqueue/x'
    assert sent[0].full_url == 'https://api.cloudinary.com/v1_1/demo/image/upload' and b'filename="p.jpg"' in sent[0].data and b'name="signature"' in sent[0].data
    c.delete(handle, 'video'); assert sent[1].full_url.endswith('/video/destroy') and b'postqueue/x' in sent[1].data
    assert c.test().startswith('https://')


def test_cloudinary_chunked_upload(monkeypatch, tmp_path):
    monkeypatch.setattr(hosting, 'CHUNK_ABOVE', 100); monkeypatch.setattr(hosting, 'CHUNK', 64)
    ranges = []
    def fake(url, body, ctype, headers=None): ranges.append(headers['Content-Range']); return {'secure_url': 'https://r/x.mp4', 'public_id': 'p'}
    monkeypatch.setattr(hosting, 'http_json', fake)
    big = tmp_path / 'big.mp4'; big.write_bytes(b'\1' * 150)
    progress = []
    assert hosting.Cloudinary('c', 'k', 's').upload(big, 'video', progress.append)[0] == 'https://r/x.mp4'
    assert ranges == ['bytes 0-63/150', 'bytes 64-127/150', 'bytes 128-149/150'] and progress[-1] == 1.0


def test_hosting_errors(monkeypatch):
    def refused(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{"error": {"message": "Invalid Signature"}}'))
    monkeypatch.setattr(urllib.request, 'urlopen', refused)
    with pytest.raises(hosting.HostingError, match='Invalid Signature') as e: hosting.Cloudinary('c', 'k', 's').test()
    assert not e.value.transient
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(hosting.HostingError, match='Could not reach') as e: hosting.Cloudinary('c', 'k', 's').test()
    assert e.value.transient
    with pytest.raises(hosting.HostingError): hosting.Cloudinary('', 'k', 's')
    assert hosting.make_provider({'hosting': 'cloudinary', 'cloudinary_cloud': 'c'}) is None
    assert hosting.make_provider({'hosting': 'cloudinary', 'cloudinary_cloud': 'c', 'cloudinary_api_key': 'k', 'cloudinary_api_secret': 's'}).name == 'Cloudinary'


def test_postqueue_service_upload_and_delete(monkeypatch, tmp_path):
    sent = []
    def fake_urlopen(req, timeout=0):
        sent.append(req)
        if req.get_method() == 'PUT': return Reply(b'{"id": "abc.jpg", "url": "https://svc.example/m/abc.jpg"}')
        return Reply(b'{"ok": true}')
    monkeypatch.setattr(urllib.request, 'urlopen', fake_urlopen)
    svc = hosting.PostQueueService('https://svc.example/', 'TOK')
    url, handle = svc.upload(make_image(tmp_path / 'p.jpg'), 'image')
    assert url == 'https://svc.example/m/abc.jpg' and handle == 'abc.jpg'
    assert sent[0].full_url == 'https://svc.example/media' and sent[0].get_header('Authorization') == 'Bearer TOK' and sent[0].get_header('X-file-name') == 'p.jpg'
    assert sent[0].get_header('Content-type') == 'image/jpeg'
    svc.delete(handle, 'image'); assert sent[1].get_method() == 'DELETE' and sent[1].full_url == 'https://svc.example/media/abc.jpg'
    assert svc.test().startswith('https://')
    monkeypatch.setattr(hosting, 'SERVICE_LIMIT', 10)
    with pytest.raises(hosting.HostingError, match='up to 95 MB'): svc.upload(tmp_path / 'p.jpg', 'image')
    def refused(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 401, 'Unauthorized', {}, io.BytesIO(b'{"error": "Sign in to Instagram first."}'))
    monkeypatch.setattr(urllib.request, 'urlopen', refused)
    with pytest.raises(hosting.HostingError, match='did not accept the Instagram sign-in') as e: svc.test()
    assert not e.value.transient
    with pytest.raises(hosting.HostingError): hosting.PostQueueService('', 'TOK')


def test_make_provider_picks_the_service_when_configured():
    s = {'hosting': 'service', 'access_token': 'TOK'}
    assert hosting.make_provider(s, 'https://svc.example').name == 'PostQueue hosting'
    assert hosting.make_provider(s, '') is None and hosting.make_provider({'hosting': 'service'}, 'https://svc.example') is None
    assert hosting.make_provider({'hosting': 'cloudinary', 'cloudinary_cloud': 'c', 'cloudinary_api_key': 'k', 'cloudinary_api_secret': 's'}, 'https://svc.example').name == 'Cloudinary'


def test_settings_ready_with_service(tmp_path):
    s = cfg.Settings(tmp_path / 's.json'); s['access_token'] = 't'; s['ig_user_id'] = '1'
    assert s['hosting'] == 'service' and not s.hosting_ready() and s.hosting_ready('https://svc') and s.ready('https://svc') and not s.ready()


def test_service_exchange(monkeypatch):
    sent = []
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=0: (sent.append(req), Reply(b'{"access_token": "LONG", "expires_in": 5184000}'))[1])
    assert ig.service_exchange('https://svc.example/', 'facebook', token='SHORT') == ('LONG', 5184000)
    assert sent[0].full_url == 'https://svc.example/token' and json.loads(sent[0].data)['token'] == 'SHORT' and json.loads(sent[0].data)['kind'] == 'facebook'
    def bad(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 502, 'Bad', {}, io.BytesIO(b'{"error": "Meta replied 400"}'))
    monkeypatch.setattr(urllib.request, 'urlopen', bad)
    with pytest.raises(ig.InstagramError, match='could not finish the sign-in: Meta replied 400'): ig.service_exchange('https://svc.example', 'facebook', token='x')
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=0: (_ for _ in ()).throw(urllib.error.URLError('offline')))
    with pytest.raises(ig.InstagramError, match='Could not reach') as e: ig.service_exchange('https://svc.example', 'facebook', token='x')
    assert e.value.transient


def test_run_login_uses_the_service_for_the_exchange(monkeypatch):
    calls = []
    monkeypatch.setattr(ig, 'service_exchange', lambda url, kind, token=None, code=None, redirect=None: (calls.append((url, kind, token, code)), ('LONG', 5184000))[1])
    def opener(url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query)); port = urllib.parse.urlparse(q['redirect_uri']).port
        urllib.request.urlopen(f'http://127.0.0.1:{port}/done?access_token=SHORT&state={q["state"]}', timeout=5).read()
    assert ig.run_login('facebook', '123', '', opener=opener, service_url='https://svc.example')[:2] == ('LONG', 5184000)
    assert calls == [('https://svc.example', 'facebook', 'SHORT', None)]


# --- Instagram client --------------------------------------------------------------------------------------------
class Reply(io.BytesIO):
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): pass


def test_request_parses_graph_errors(monkeypatch):
    def fake(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 400, 'Bad', {}, io.BytesIO(b'{"error": {"message": "Invalid OAuth access token.", "code": 190, "error_subcode": 463}}'))
    monkeypatch.setattr(urllib.request, 'urlopen', fake)
    with pytest.raises(ig.InstagramError) as e: ig.Instagram('t', '1').profile()
    assert e.value.code == 190 and e.value.subcode == 463 and e.value.http_status == 400
    monkeypatch.setattr(urllib.request, 'urlopen', lambda req, timeout=0: Reply(b'<html>'))
    with pytest.raises(ig.InstagramError, match='Unexpected reply'): ig.Instagram('t', '1').profile()


def test_publish_calls(monkeypatch):
    sent = []
    def fake(req, timeout=0):
        sent.append((req.get_method(), req.full_url, req.data))
        if req.full_url.endswith('/C9?fields=status_code%2Cstatus&access_token=tok'): return Reply(b'{"status_code": "FINISHED"}')
        return Reply(b'{"id": "C9", "permalink": "https://www.instagram.com/p/abc/"}')
    monkeypatch.setattr(urllib.request, 'urlopen', fake)
    api = ig.Instagram('tok', '17841400000', ig.FB_HOST, 'v25.0')
    assert api.create_image('https://h/x.jpg', 'Hi #x') == 'C9'
    assert sent[0][0] == 'POST' and sent[0][1] == 'https://graph.facebook.com/v25.0/17841400000/media' and b'image_url=https%3A%2F%2Fh%2Fx.jpg' in sent[0][2] and b'caption=Hi+%23x' in sent[0][2]
    assert api.create_video('https://h/v.mp4') == 'C9' and b'media_type=REELS' in sent[1][2] and b'share_to_feed=true' in sent[1][2] and b'thumb_offset' not in sent[1][2]
    api.create_image('https://h/x.jpg', 'Hi', alt_text='A cat'); assert b'alt_text=A+cat' in sent[-1][2]
    api.create_image('https://h/x.jpg', carousel_item=True); assert b'is_carousel_item=true' in sent[-1][2] and b'caption' not in sent[-1][2]
    api.create_video('https://h/v.mp4', cover_offset_ms=2500); assert b'thumb_offset=2500' in sent[-1][2]
    api.create_video('https://h/v.mp4', carousel_item=True); assert b'media_type=VIDEO' in sent[-1][2] and b'is_carousel_item=true' in sent[-1][2]
    api.create_story('https://h/s.jpg'); assert b'media_type=STORIES' in sent[-1][2] and b'image_url=' in sent[-1][2]
    api.create_story('https://h/s.mp4', video=True); assert b'video_url=' in sent[-1][2]
    api.create_carousel(['A', 'B'], 'Cap'); assert b'media_type=CAROUSEL' in sent[-1][2] and b'children=A%2CB' in sent[-1][2]
    api.comment('17', 'First!'); assert sent[-1][1].endswith('/17/comments') and b'message=First%21' in sent[-1][2]
    api.wait_container('C9', sleep=lambda s: None)
    assert api.publish('C9') == 'C9' and b'creation_id=C9' in sent[-1][2]
    assert api.permalink('C9') == 'https://www.instagram.com/p/abc/'


def test_wait_container_outcomes():
    class Api(ig.Instagram):
        def __init__(self, seq): super().__init__('t', '1'); self.seq = list(seq); self.seen = []
        def container_status(self, cid): return self.seq.pop(0), 'detail'
    seen = []
    Api(['IN_PROGRESS', 'FINISHED']).wait_container('c', on_progress=seen.append, sleep=lambda s: None); assert seen == ['IN_PROGRESS']
    with pytest.raises(ig.InstagramError, match='could not process'): Api(['ERROR']).wait_container('c', sleep=lambda s: None)
    with pytest.raises(ig.InstagramError, match='too long') as e: Api(['IN_PROGRESS'] * 3).wait_container('c', timeout=0, sleep=lambda s: None)
    assert e.value.transient


def test_accounts_for_token_prefers_pages(monkeypatch):
    def fake(method, url, params=None):
        if 'me/accounts' in url: return {'data': [{'name': 'Page A'}, {'name': 'Page B', 'instagram_business_account': {'id': '178', 'username': 'bshop'}}]}
        raise AssertionError(url)
    monkeypatch.setattr(ig, 'request', fake)
    assert ig.accounts_for_token('t') == [{'id': '178', 'username': 'bshop', 'host': ig.FB_HOST, 'via': 'Page B'}]


def test_accounts_for_token_falls_back_to_instagram_login(monkeypatch):
    def fake(method, url, params=None):
        if 'graph.facebook.com' in url: raise ig.InstagramError('Invalid OAuth access token', code=190)
        return {'user_id': '999', 'username': 'solo'}
    monkeypatch.setattr(ig, 'request', fake)
    assert ig.accounts_for_token('t')[0] == {'id': '999', 'username': 'solo', 'host': ig.IG_HOST, 'via': 'Instagram Login'}
    monkeypatch.setattr(ig, 'request', lambda m, u, p=None: {'data': []} if 'facebook' in u else (_ for _ in ()).throw(ig.InstagramError('bad', code=190)))
    with pytest.raises(ig.InstagramError, match='linked to a Facebook Page'): ig.accounts_for_token('t')
    monkeypatch.setattr(ig, 'request', lambda m, u, p=None: (_ for _ in ()).throw(ig.InstagramError('bad', code=190)))
    with pytest.raises(ig.InstagramError, match='not valid'): ig.accounts_for_token('t')


def test_app_checks_and_signin_hints(monkeypatch):
    def fake(method, url, params=None):
        if url.endswith('/123'): return {'id': '123', 'name': 'My Shop App'}
        if url.endswith('/456'): raise ig.InstagramError('An access token is required to request this resource.', code=104)
        if 'client_credentials' in str(params):
            if params['client_id'] != '123': raise ig.InstagramError('Invalid Client ID', code=101)
            if params['client_secret'] != 'good': raise ig.InstagramError('Error validating client secret.', code=1)
            return {'access_token': '123|apptoken'}
        raise AssertionError(url)
    monkeypatch.setattr(ig, 'request', fake)
    assert ig.app_info('123')['name'] == 'My Shop App' and ig.app_info(' 456 ') is None
    with pytest.raises(ig.InstagramError, match='is a number'): ig.app_info('abc')
    assert ig.app_token('123', 'good') == '123|apptoken'
    with pytest.raises(ig.InstagramError, match='does not know this App ID'): ig.app_token('999', 'good')
    with pytest.raises(ig.InstagramError, match='secret does not match'): ig.app_token('123', 'bad')
    assert 'Valid OAuth Redirect URIs' in ig.signin_hint("Can't load URL: the domain of this URL isn't included in the app's domains. redirect_uri")
    assert 'App roles' in ig.signin_hint('App not active: This app is not accessible right now')
    assert 'cancelled' in ig.signin_hint('access_denied') and ig.signin_hint('Something odd') == 'Something odd'


def test_authorize_urls_and_exchanges(monkeypatch):
    fb = ig.authorize_url('facebook', '123', 'http://localhost:48215/', 'st')
    assert fb.startswith('https://www.facebook.com/v25.0/dialog/oauth?') and 'response_type=token' in fb and 'instagram_content_publish' in fb
    insta = ig.authorize_url('instagram', '123', 'http://localhost:48215/', 'st')
    assert insta.startswith('https://www.instagram.com/oauth/authorize?') and 'response_type=code' in insta and 'instagram_business_content_publish' in insta
    calls = []
    monkeypatch.setattr(ig, 'request', lambda m, u, p=None: (calls.append((m, u, p)), {'access_token': 'LL', 'expires_in': 5184000})[1])
    assert ig.exchange_code('instagram', '1', 's', 'r', 'CODE#_') == ('LL', 5184000) and calls[-1][1] == 'https://api.instagram.com/oauth/access_token' and calls[-1][2]['code'] == 'CODE'
    assert ig.long_lived('instagram', '1', 's', 'short') == ('LL', 5184000) and calls[-1][2]['grant_type'] == 'ig_exchange_token'
    assert ig.long_lived('facebook', '1', 's', 'short') == ('LL', 5184000) and calls[-1][2]['grant_type'] == 'fb_exchange_token'
    assert ig.refresh_instagram('LL') == ('LL', 5184000) and 'refresh_access_token' in calls[-1][1]


def test_login_server_relays_fragment_and_code():
    server = ig.LoginServer(port=0)
    try:
        page = urllib.request.urlopen(f'http://127.0.0.1:{server.port}/?state=x', timeout=5).read().decode()
        assert 'location.hash' in page and server.wait(0.1) is None
        urllib.request.urlopen(f'http://127.0.0.1:{server.port}/done?access_token=TOK&expires_in=5000&state=x', timeout=5).read()
        assert server.wait(2) == {'access_token': 'TOK', 'expires_in': '5000', 'state': 'x'}
    finally: server.close()
    server = ig.LoginServer(port=0)
    try:
        urllib.request.urlopen(f'http://127.0.0.1:{server.port}/?code=ABC&state=y', timeout=5).read()
        assert server.wait(2)['code'] == 'ABC'
    finally: server.close()


def test_run_login_flow(monkeypatch):
    opened = []
    def opener(url):
        opened.append(url); state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))['state']
        port = urllib.parse.urlparse(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))['redirect_uri']).port
        urllib.request.urlopen(f'http://127.0.0.1:{port}/done?access_token=SHORT&expires_in=7200&state={state}', timeout=5).read()
    monkeypatch.setattr(ig, 'long_lived', lambda *a, **k: ('LONG', 5184000))
    assert ig.run_login('facebook', '123', 'secret', opener=opener)[:2] == ('LONG', 5184000)
    assert ig.run_login('facebook', '123', '', opener=opener)[:2] == ('SHORT', 7200)
    with pytest.raises(ig.InstagramError, match='app ID'): ig.run_login('facebook', '', '')
    with pytest.raises(ig.InstagramError, match='cancelled'): ig.run_login('facebook', '1', '', opener=lambda u: None, cancelled=lambda: True)
    def wrong_state(url):
        port = urllib.parse.urlparse(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))['redirect_uri']).port
        urllib.request.urlopen(f'http://127.0.0.1:{port}/done?access_token=X&state=nope', timeout=5).read()
    with pytest.raises(ig.InstagramError, match='did not belong'): ig.run_login('facebook', '1', '', opener=wrong_state)
    def needs_secret(url):
        port = urllib.parse.urlparse(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))['redirect_uri']).port
        state = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))['state']
        urllib.request.urlopen(f'http://127.0.0.1:{port}/?code=C&state={state}', timeout=5).read()
    with pytest.raises(ig.InstagramError, match='app secret'): ig.run_login('instagram', '1', '', opener=needs_secret)


import urllib.parse  # noqa: E402
