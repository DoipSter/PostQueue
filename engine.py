"""PostQueue engine: the queue of posts, media preparation, due-time rules and the publishing steps.
No Qt in this module, so the tests drive it directly."""
import contextlib
import datetime as dt
import json
import logging
import logging.handlers
import re
import shutil
import urllib.error
import uuid
from dataclasses import dataclass, asdict, fields
from pathlib import Path

from hosting import HostingError
from instagram import InstagramError
from settings import atomic_json

IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff', '.gif'}
VIDEO_EXT = {'.mp4', '.mov', '.m4v'}
MIN_RATIO, MAX_RATIO = 0.8, 1.91        # Instagram feed photos: 4:5 portrait to 1.91:1 landscape
MAX_WIDTH, MIN_WIDTH = 1440, 320
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
CAPTION_LIMIT, HASHTAG_LIMIT = 2200, 30
RETRY_LIMIT, RETRY_MINUTES = 3, 5
STATUS_TEXT = {'queued': 'Scheduled', 'posting': 'Posting…', 'posted': 'Posted', 'failed': 'Failed', 'missed': 'Missed'}


class EngineError(Exception):
    pass


def now_local(): return dt.datetime.now().astimezone()
def parse_when(text): return dt.datetime.fromisoformat(text).astimezone()
def fmt_when(when): return when.astimezone().isoformat(timespec='minutes')


def media_kind(path):
    ext = Path(path).suffix.lower()
    if ext in IMAGE_EXT: return 'image'
    if ext in VIDEO_EXT: return 'video'
    raise EngineError(f'{Path(path).name}: not a supported photo or video. Photos: JPG, PNG, WEBP, BMP, TIFF, GIF. Videos: MP4, MOV.')


def caption_warnings(caption):
    """Things Instagram will reject, as short sentences; empty when the caption is fine."""
    out = []
    if len(caption) > CAPTION_LIMIT: out.append(f'The caption is {len(caption) - CAPTION_LIMIT} characters over Instagram\'s limit of {CAPTION_LIMIT}.')
    tags = len(re.findall(r'(?<!\w)#\w+', caption))
    if tags > HASHTAG_LIMIT: out.append(f'{tags} hashtags; Instagram allows {HASHTAG_LIMIT}.')
    return out


@dataclass
class Post:
    id: str
    media: str                 # the copy in the data folder
    source: str                # the file the user chose (for the name only)
    kind: str                  # image | video
    when: str                  # ISO 8601 with offset
    caption: str = ''
    status: str = 'queued'
    fit: str = 'crop'          # crop | pad (photos outside Instagram's aspect-ratio range)
    error: str = ''
    attempts: int = 0
    retry_at: str = ''
    media_id: str = ''
    permalink: str = ''
    created: str = ''
    posted_at: str = ''

    def when_dt(self): return parse_when(self.when)
    def name(self): return Path(self.source).name or Path(self.media).name
    def status_text(self): return STATUS_TEXT.get(self.status, self.status)


class Engine:
    def __init__(self, data_dir, settings=None):
        self.dir = Path(data_dir); self.media_dir = self.dir / 'media'; self.tmp_dir = self.dir / 'tmp'
        self.posts_file = self.dir / 'posts.json'
        for d in (self.dir, self.media_dir, self.tmp_dir): d.mkdir(parents=True, exist_ok=True)
        self.settings = settings if settings is not None else {}
        self.log = logging.getLogger('postqueue')
        if not self.log.handlers:
            handler = logging.handlers.RotatingFileHandler(self.dir / 'postqueue.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8')
            handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
            self.log.addHandler(handler); self.log.setLevel(logging.INFO)
        self.posts = []
        self.load()

    # --- storage --------------------------------------------------------------------------------------------------
    def load(self):
        self.posts = []
        if not self.posts_file.exists(): return
        try: data = json.loads(self.posts_file.read_text(encoding='utf-8'))
        except (OSError, ValueError): self.log.error('posts.json could not be read; starting with an empty queue'); return
        names = {f.name for f in fields(Post)}
        for entry in data:
            with contextlib.suppress(TypeError, ValueError):
                post = Post(**{k: v for k, v in entry.items() if k in names})
                if post.status == 'posting': post.status = 'queued'   # interrupted by a crash or shutdown: try again
                self.posts.append(post)

    def save(self):
        atomic_json(self.posts_file, [asdict(p) for p in sorted(self.posts, key=lambda p: p.when)])

    def get(self, post_id): return next((p for p in self.posts if p.id == post_id), None)

    def add(self, path, when, caption='', fit=None):
        """Copy the file into the data folder and queue it. `when` is an aware datetime."""
        src = Path(path)
        if not src.is_file(): raise EngineError(f'{src.name}: file not found.')
        kind = media_kind(src)
        size = src.stat().st_size
        if kind == 'video' and size > MAX_VIDEO_BYTES: raise EngineError(f'{src.name}: videos must be under 1 GB.')
        if size == 0: raise EngineError(f'{src.name}: the file is empty.')
        post_id = uuid.uuid4().hex[:12]
        dest = self.media_dir / f'{post_id}{src.suffix.lower()}'
        shutil.copy2(src, dest)
        post = Post(id=post_id, media=str(dest), source=str(src), kind=kind, when=fmt_when(when), caption=caption,
                    fit=fit or self.settings.get('fit_mode', 'crop'), created=fmt_when(now_local()))
        self.posts.append(post); self.save()
        self.log.info('queued %s for %s', post.name(), post.when)
        return post

    def update(self, post, **changes):
        for key, value in changes.items(): setattr(post, key, value)
        self.save()
        return post

    def reschedule(self, post, when):
        return self.update(post, when=fmt_when(when), status='queued', error='', attempts=0, retry_at='')

    def remove(self, post):
        self.posts = [p for p in self.posts if p.id != post.id]
        with contextlib.suppress(OSError): Path(post.media).unlink()
        self.save()

    # --- timing ---------------------------------------------------------------------------------------------------
    def next_slot(self, now=None):
        """The next free default posting time: today's default time if it is still ahead, otherwise the next day
        that has nothing scheduled at that time."""
        now = now or now_local()
        hour, minute = int(self.settings.get('default_hour', 9)), int(self.settings.get('default_minute', 0))
        taken = {p.when for p in self.posts if p.status == 'queued'}
        slot = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot <= now + dt.timedelta(minutes=5): slot += dt.timedelta(days=1)
        while fmt_when(slot) in taken: slot += dt.timedelta(days=1)
        return slot

    def check(self, now=None, grace_hours=None):
        """Mark posts that were due too long ago as missed and return the post to publish now, if any."""
        now = now or now_local()
        grace = dt.timedelta(hours=float(grace_hours if grace_hours is not None else self.settings.get('grace_hours', 12)))
        due, changed = [], False
        for post in self.posts:
            if post.status != 'queued': continue
            when = post.when_dt()
            if when > now: continue
            if now - when > grace and not post.attempts:
                post.status, post.error, changed = 'missed', f'Missed: PostQueue was not running at the scheduled time ({when:%a %d %b %H:%M}).', True
                self.log.warning('missed %s (%s)', post.name(), post.when); continue
            if post.retry_at and parse_when(post.retry_at) > now: continue
            due.append(post)
        if changed: self.save()
        return min(due, key=lambda p: p.when) if due else None

    def next_scheduled(self, now=None):
        now = now or now_local()
        ahead = [p for p in self.posts if p.status == 'queued']
        return min(ahead, key=lambda p: p.when) if ahead else None

    # --- media ----------------------------------------------------------------------------------------------------
    @staticmethod
    def prepare_image(src, dest, fit='crop'):
        """Make a JPEG Instagram accepts: orientation applied, transparency on white, aspect ratio within 4:5 to
        1.91:1 (cropped to the nearest allowed ratio, or padded with white), width 320 to 1440, under 8 MB."""
        from PIL import Image, ImageOps
        dest = Path(dest); dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as opened:
            im = ImageOps.exif_transpose(opened)
            if im.mode in ('RGBA', 'LA', 'PA') or (im.mode == 'P' and 'transparency' in im.info):
                im = im.convert('RGBA'); background = Image.new('RGB', im.size, 'white'); background.paste(im, mask=im.split()[-1]); im = background
            else: im = im.convert('RGB')
            w, h = im.size; ratio = w / h
            target = min(max(ratio, MIN_RATIO), MAX_RATIO)
            if abs(target - ratio) > 1e-3:
                if fit == 'crop':
                    if ratio > target: nw = round(h * target); left = (w - nw) // 2; im = im.crop((left, 0, left + nw, h))
                    else: nh = round(w / target); top = (h - nh) // 2; im = im.crop((0, top, w, top + nh))
                else:
                    canvas = (w, round(w / target)) if ratio > target else (round(h * target), h)
                    background = Image.new('RGB', canvas, 'white'); background.paste(im, ((canvas[0] - w) // 2, (canvas[1] - h) // 2)); im = background
            w, h = im.size
            if w > MAX_WIDTH: im = im.resize((MAX_WIDTH, max(1, round(h * MAX_WIDTH / w))), Image.LANCZOS)
            elif w < MIN_WIDTH: im = im.resize((MIN_WIDTH, max(1, round(h * MIN_WIDTH / w))), Image.LANCZOS)
            while True:   # lower the quality first; if that is not enough, shrink the picture
                for quality in (92, 88, 84, 80, 75, 70, 60):
                    im.save(dest, 'JPEG', quality=quality, optimize=True)
                    if dest.stat().st_size <= MAX_IMAGE_BYTES: return dest
                w, h = im.size
                if w <= MIN_WIDTH: return dest
                im = im.resize((max(MIN_WIDTH, int(w * 0.8)), max(1, int(h * 0.8))), Image.LANCZOS)

    # --- publishing -----------------------------------------------------------------------------------------------
    @staticmethod
    def describe(exc):
        if isinstance(exc, InstagramError): return exc.friendly()
        if isinstance(exc, (HostingError, EngineError)): return str(exc)
        if isinstance(exc, urllib.error.URLError): return f'No connection: {exc.reason}.'
        if isinstance(exc, (TimeoutError, ConnectionError, OSError)): return f'No connection: {exc}.'
        return f'{type(exc).__name__}: {exc}'

    @staticmethod
    def transient(exc):
        if isinstance(exc, InstagramError): return exc.transient
        if isinstance(exc, HostingError): return exc.transient
        return isinstance(exc, (urllib.error.URLError, TimeoutError, ConnectionError))

    def publish(self, post, instagram, provider, on_progress=None):
        """Prepare, host, create the Instagram container, publish, clean up. Updates the post's status and returns it.
        Transient failures put the post back in the queue for a retry; others mark it failed."""
        step = on_progress or (lambda fraction, text: None)
        self.update(post, status='posting', error='')
        prepared = handle = None
        try:
            if instagram is None: raise EngineError('No Instagram account is connected. Open Settings > Instagram.')
            if provider is None: raise EngineError('No media hosting is set up. Open Settings > Media hosting.')
            if not Path(post.media).is_file(): raise EngineError('The media file is missing from the data folder.')
            if post.kind == 'image':
                step(0.05, 'Preparing the photo')
                prepared = self.prepare_image(post.media, self.tmp_dir / f'{post.id}.jpg', post.fit); upload = prepared
            else: upload = Path(post.media)
            step(0.15, f'Uploading to {provider.name}')
            url, handle = provider.upload(upload, post.kind, lambda f: step(0.15 + 0.35 * f, f'Uploading to {provider.name}'))
            step(0.55, 'Creating the Instagram post')
            if post.kind == 'image': container = instagram.create_image(url, post.caption)
            else:
                container = instagram.create_video(url, post.caption)
                step(0.65, 'Instagram is processing the video')
                instagram.wait_container(container, on_progress=lambda s: step(0.7, f'Instagram is processing the video ({s.lower().replace("_", " ")})'))
            step(0.85, 'Publishing')
            media_id = instagram.publish(container)
            link = ''
            with contextlib.suppress(Exception): link = instagram.permalink(media_id)
            self.update(post, status='posted', media_id=str(media_id), permalink=link, posted_at=fmt_when(now_local()), retry_at='', error='')
            self.log.info('posted %s as %s %s', post.name(), media_id, link)
            step(1.0, 'Posted')
        except Exception as exc:
            attempts, message = post.attempts + 1, self.describe(exc)
            if self.transient(exc) and attempts < RETRY_LIMIT:
                retry = now_local() + dt.timedelta(minutes=RETRY_MINUTES)
                self.update(post, status='queued', attempts=attempts, retry_at=fmt_when(retry), error=f'{message} Trying again at {retry:%H:%M}.')
            else:
                self.update(post, status='failed', attempts=attempts, retry_at='', error=message)
            self.log.warning('publish failed for %s: %s', post.name(), message)
        finally:
            if handle is not None:
                try: provider.delete(handle, post.kind)
                except Exception as exc: self.log.warning('could not remove hosted copy %s: %s', handle, exc)
            if prepared is not None:
                with contextlib.suppress(OSError): Path(prepared).unlink()
        return post
