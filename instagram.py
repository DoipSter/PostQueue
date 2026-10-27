"""Instagram publishing through Meta's Graph API, and the sign-in helpers.

Two kinds of sign-in are supported, and both end in a long-lived user token (60 days):
- Facebook Login: an Instagram professional account linked to a Facebook Page. Calls go to graph.facebook.com.
- Instagram Login (Business Login for Instagram): the Instagram account alone. Calls go to graph.instagram.com.
Publishing itself is the same on both hosts: create a media container from a public URL, wait for it, publish it."""
import contextlib
import http.server
import json
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

FB_HOST, IG_HOST = 'graph.facebook.com', 'graph.instagram.com'
DEFAULT_VERSION = 'v25.0'
FB_SCOPES = 'instagram_basic,instagram_content_publish,pages_show_list,pages_read_engagement'   # exactly what App Review is asked for
IG_SCOPES = 'instagram_business_basic,instagram_business_content_publish'
LOGIN_PORT = 48215                      # add http://localhost:48215/ to the Meta app's redirect URIs
TRANSIENT_CODES = {1, 2, 4, 17, 32, 613}  # unknown, service, rate limits
TIMEOUT = 60
FRIENDLY = {
    190: 'The Instagram sign-in has expired or was revoked. Open Settings > Instagram and sign in again.',
    10: 'The signed-in account is not allowed to publish. Check the permissions granted to the app (content publishing).',
    200: 'The signed-in account is not allowed to publish. Check the permissions granted to the app (content publishing).',
    9004: 'Instagram could not fetch the media from the hosting URL. Check Settings > Media hosting.',
    9007: 'Instagram rejected the media: it may not meet the size or format rules.',
    36003: 'Instagram rejected the photo because of its aspect ratio.',
    2207026: 'Instagram rejected the video: unsupported format. Use an MP4 (H.264, AAC audio).',
    2207053: 'Instagram rejected the video. Check its length, size and format.',
    9008: 'Instagram declined the post because the caption has too many hashtags or mentions.',
}


class InstagramError(Exception):
    def __init__(self, message, code=None, subcode=None, http_status=None, user_msg=''):
        super().__init__(message)
        self.code, self.subcode, self.http_status, self.user_msg = code, subcode, http_status, user_msg

    @property
    def transient(self):
        return self.code in TRANSIENT_CODES or (self.http_status or 0) >= 500

    def friendly(self):
        for key in (self.subcode, self.code):
            if key in FRIENDLY: return FRIENDLY[key]
        text = self.user_msg or str(self)
        return text if self.code is None else f'{text} (Instagram error {self.code})'


def request(method, url, params=None):
    """One Graph API call. Returns the decoded JSON, or raises InstagramError with Meta's error details."""
    params = {k: v for k, v in (params or {}).items() if v not in (None, '')}
    body = None
    if method == 'GET' and params: url += ('&' if '?' in url else '?') + urllib.parse.urlencode(params)
    elif method == 'POST': body = urllib.parse.urlencode(params).encode('utf-8')
    req = urllib.request.Request(url, data=body, method=method, headers={'User-Agent': 'PostQueue'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r: text, status = r.read().decode('utf-8', 'replace'), r.status
    except urllib.error.HTTPError as e:
        text, status = e.read().decode('utf-8', 'replace'), e.code
    try: payload = json.loads(text) if text else {}
    except ValueError:
        raise InstagramError(f'Unexpected reply from {urllib.parse.urlparse(url).netloc} (HTTP {status}).', http_status=status)
    if isinstance(payload, dict) and 'error' in payload:
        err = payload['error'] if isinstance(payload['error'], dict) else {'message': str(payload['error'])}
        raise InstagramError(err.get('message', 'Unknown error'), err.get('code'), err.get('error_subcode'), status, err.get('error_user_msg', ''))
    if status >= 400: raise InstagramError(f'HTTP {status} from {urllib.parse.urlparse(url).netloc}.', http_status=status)
    return payload


class Instagram:
    """Publishing for one Instagram professional account."""
    def __init__(self, token, user_id, host=FB_HOST, version=DEFAULT_VERSION):
        self.token, self.user_id, self.host, self.version = token, str(user_id), host or FB_HOST, version or DEFAULT_VERSION

    def url(self, path): return f'https://{self.host}/{self.version}/{path.lstrip("/")}'
    def get(self, path, **params): return request('GET', self.url(path), {**params, 'access_token': self.token})
    def post(self, path, **params): return request('POST', self.url(path), {**params, 'access_token': self.token})

    def profile(self): return self.get(self.user_id, fields='username')

    def create_image(self, image_url, caption=''):
        return self.post(f'{self.user_id}/media', image_url=image_url, caption=caption)['id']

    def create_video(self, video_url, caption='', share_to_feed=True):
        """Feed videos are published as Reels, which is what the API offers for single videos."""
        return self.post(f'{self.user_id}/media', media_type='REELS', video_url=video_url, caption=caption,
                         share_to_feed='true' if share_to_feed else 'false')['id']

    def container_status(self, container_id):
        r = self.get(container_id, fields='status_code,status')
        return r.get('status_code', 'IN_PROGRESS'), r.get('status', '')

    def wait_container(self, container_id, timeout=900, interval=5, on_progress=None, sleep=time.sleep):
        """Poll until Instagram has processed the media (videos take a while). Raises when it fails or expires."""
        end = time.monotonic() + timeout
        while True:
            code, detail = self.container_status(container_id)
            if code == 'FINISHED': return
            if code in ('ERROR', 'EXPIRED'): raise InstagramError(f'Instagram could not process the media: {detail or code}')
            if on_progress: on_progress(code)
            if time.monotonic() >= end: raise InstagramError('Instagram took too long to process the media. Try again later.', code=4)
            sleep(interval)

    def publish(self, container_id): return self.post(f'{self.user_id}/media_publish', creation_id=container_id)['id']

    def permalink(self, media_id): return self.get(media_id, fields='permalink').get('permalink', '')

    def quota(self):
        """(used, total) posts in the rolling 24-hour publishing limit, or (None, None) when not reported."""
        r = self.get(f'{self.user_id}/content_publishing_limit', fields='quota_usage,config')
        entry = (r.get('data') or [{}])[0]
        return entry.get('quota_usage'), (entry.get('config') or {}).get('quota_total')


def accounts_for_token(token, version=DEFAULT_VERSION):
    """The Instagram professional accounts a token can publish to. Facebook Login tokens list the Pages' linked
    accounts; Instagram Login tokens name one account. Each entry: id, username, host, via."""
    fb_error = None
    try:
        found, url = [], f'https://{FB_HOST}/{version}/me/accounts'
        params = {'fields': 'name,instagram_business_account{id,username}', 'access_token': token, 'limit': 100}
        while url:
            page = request('GET', url, params); params = None
            for p in page.get('data', []):
                ig = p.get('instagram_business_account')
                if ig: found.append({'id': str(ig['id']), 'username': ig.get('username', ''), 'host': FB_HOST, 'via': p.get('name', '')})
            url = (page.get('paging') or {}).get('next')
        if found: return found
    except InstagramError as e:
        fb_error = e
    try:
        me = request('GET', f'https://{IG_HOST}/{version}/me', {'fields': 'user_id,username', 'access_token': token})
        if me.get('user_id'): return [{'id': str(me['user_id']), 'username': me.get('username', ''), 'host': IG_HOST, 'via': 'Instagram Login'}]
    except InstagramError as e:
        if fb_error is None:
            raise InstagramError('The token works, but no Instagram professional account is linked to a Facebook Page it can see. '
                                 'Link the Instagram account to a Page (Instagram > Settings > Business tools > Connect a Facebook Page), '
                                 'or use Instagram Login.', code=fb_error and fb_error.code)
        raise fb_error if fb_error.code != 190 or e.code != 190 else InstagramError(
            'The token is not valid for Facebook Login or Instagram Login. Copy it again, or sign in with the browser.', code=190)
    raise fb_error or InstagramError('No Instagram professional account was found for this token.')


# --- Sign-in -----------------------------------------------------------------------------------------------------
def authorize_url(kind, app_id, redirect, state, version=DEFAULT_VERSION):
    if kind == 'instagram':
        return 'https://www.instagram.com/oauth/authorize?' + urllib.parse.urlencode(
            {'client_id': app_id, 'redirect_uri': redirect, 'scope': IG_SCOPES, 'response_type': 'code', 'state': state, 'force_reauth': 'true'})
    return f'https://www.facebook.com/{version}/dialog/oauth?' + urllib.parse.urlencode(
        {'client_id': app_id, 'redirect_uri': redirect, 'scope': FB_SCOPES, 'response_type': 'token', 'state': state})


def exchange_code(kind, app_id, app_secret, redirect, code, version=DEFAULT_VERSION):
    """Authorization code -> short-lived token. Both logins need the app secret for this step."""
    code = code.strip().split('#')[0]
    if kind == 'instagram':
        r = request('POST', 'https://api.instagram.com/oauth/access_token',
                    {'client_id': app_id, 'client_secret': app_secret, 'grant_type': 'authorization_code', 'redirect_uri': redirect, 'code': code})
        return r['access_token'], r.get('expires_in')
    r = request('GET', f'https://{FB_HOST}/{version}/oauth/access_token',
                {'client_id': app_id, 'client_secret': app_secret, 'redirect_uri': redirect, 'code': code})
    return r['access_token'], r.get('expires_in')


def long_lived(kind, app_id, app_secret, token, version=DEFAULT_VERSION):
    """Short-lived token -> long-lived token (about 60 days)."""
    if kind == 'instagram':
        r = request('GET', f'https://{IG_HOST}/access_token', {'grant_type': 'ig_exchange_token', 'client_secret': app_secret, 'access_token': token})
    else:
        r = request('GET', f'https://{FB_HOST}/{version}/oauth/access_token',
                    {'grant_type': 'fb_exchange_token', 'client_id': app_id, 'client_secret': app_secret, 'fb_exchange_token': token})
    return r['access_token'], r.get('expires_in')


def service_exchange(service_url, kind, token=None, code=None, redirect=None):
    """Long-lived token through the PostQueue service, which holds the app secret (service/ in the repository)."""
    body = json.dumps({'kind': kind, 'token': token, 'code': code, 'redirect': redirect}).encode('utf-8')
    req = urllib.request.Request(service_url.rstrip('/') + '/token', data=body, method='POST', headers={'Content-Type': 'application/json', 'User-Agent': 'PostQueue'})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r: text, status = r.read().decode('utf-8', 'replace'), r.status
    except urllib.error.HTTPError as e:
        text, status = e.read().decode('utf-8', 'replace'), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise InstagramError(f'Could not reach the PostQueue service to finish the sign-in: {getattr(e, "reason", e)}', code=2)
    try: payload = json.loads(text) if text else {}
    except ValueError: raise InstagramError(f'Unexpected reply from the PostQueue service (HTTP {status}).', http_status=status)
    if status >= 400 or 'access_token' not in payload:
        raise InstagramError(f'The PostQueue service could not finish the sign-in: {payload.get("error") or text[:200]}', http_status=status)
    return payload['access_token'], payload.get('expires_in')


def refresh_instagram(token):
    """Extend an Instagram Login long-lived token by another 60 days (it must be at least a day old and still valid)."""
    r = request('GET', f'https://{IG_HOST}/refresh_access_token', {'grant_type': 'ig_refresh_token', 'access_token': token})
    return r['access_token'], r.get('expires_in')


RELAY_HTML = """<!doctype html><meta charset="utf-8"><title>PostQueue sign-in</title>
<body style="font-family:Segoe UI,sans-serif;padding:2em"><p id="m">Finishing sign-in…</p>
<script>if (location.hash.length > 1) { location.replace('/done?' + location.hash.substring(1)); }
else { document.getElementById('m').textContent = 'Waiting for the sign-in reply…'; }</script></body>"""
DONE_HTML = """<!doctype html><meta charset="utf-8"><title>PostQueue sign-in</title>
<body style="font-family:Segoe UI,sans-serif;padding:2em"><h2>Signed in</h2><p>You can close this tab and return to PostQueue.</p></body>"""


class LoginServer:
    """Receives the browser's redirect on localhost. Facebook returns the token in the URL fragment, which the served
    page forwards to /done; Instagram Login returns a code in the query string."""
    def __init__(self, port=LOGIN_PORT):
        self.result, self.event, outer = None, threading.Event(), self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a): pass
            def do_GET(self):
                parts = urllib.parse.urlparse(self.path); query = dict(urllib.parse.parse_qsl(parts.query))
                if parts.path == '/done' or 'code' in query or 'error' in query:
                    outer.result = query; outer.event.set(); body = DONE_HTML
                else: body = RELAY_HTML
                data = body.encode('utf-8')
                self.send_response(200); self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data)

        try: self.server = http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler)
        except OSError: self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.redirect = f'http://localhost:{self.port}/'
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait(self, timeout):
        return self.result if self.event.wait(timeout) else None

    def close(self):
        self.server.shutdown(); self.server.server_close()


def run_login(kind, app_id, app_secret='', version=DEFAULT_VERSION, opener=webbrowser.open, timeout=300, cancelled=lambda: False, service_url=''):
    """Open the browser for Meta's sign-in and wait for the reply. Returns (token, expires_in seconds or None,
    redirect URL used). The long-lived exchange goes through the PostQueue service when `service_url` is set,
    otherwise through the app secret when given. Raises InstagramError when the sign-in fails or is cancelled."""
    if not app_id: raise InstagramError('Enter the Meta app ID first.')
    server, state = LoginServer(), secrets.token_urlsafe(16)
    try:
        opener(authorize_url(kind, app_id, server.redirect, state, version))
        started = time.monotonic(); result = None
        while result is None:
            result = server.wait(0.5)
            if result is None and cancelled(): raise InstagramError('Sign-in cancelled.')
            if result is None and time.monotonic() - started > timeout: raise InstagramError('Sign-in timed out. Try again.')
    finally:
        server.close()
    if result.get('state') != state: raise InstagramError('The sign-in reply did not belong to this request. Try again.')
    if 'error' in result: raise InstagramError(result.get('error_description') or result.get('error_reason') or result['error'])
    if service_url:
        token, expires = service_exchange(service_url, kind, token=result.get('access_token'), code=result.get('code', '').split('#')[0] or None, redirect=server.redirect)
        return token, (int(expires) if expires else None), server.redirect
    if 'access_token' in result:
        token, expires = result['access_token'], result.get('expires_in')
    else:
        if not app_secret: raise InstagramError('This sign-in needs the app secret to finish. Enter it under Meta app, or paste an access token instead.')
        token, expires = exchange_code(kind, app_id, app_secret, server.redirect, result.get('code', ''), version)
    if app_secret:
        with contextlib.suppress(InstagramError): token, expires = long_lived(kind, app_id, app_secret, token, version)
    return token, (int(expires) if expires else None), server.redirect
