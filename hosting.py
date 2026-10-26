"""Public hosting for media. Instagram's API does not accept file uploads for photos: it fetches each photo (and, in
this app, each video) from a public URL. PostQueue uploads the file to the user's hosting account, publishes the post,
and then removes the file again. Cloudinary is supported; its free plan is enough for a personal queue."""
import hashlib
import json
import mimetypes
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

CHUNK = 6 * 1024 * 1024          # Cloudinary accepts single requests up to 100 MB; larger files go in 6 MB chunks
CHUNK_ABOVE = 60 * 1024 * 1024
TIMEOUT = 300
TEST_PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8ffff3f0300050001'
                         '2d5c7c2ec20000000049454e44ae426082')


class HostingError(Exception):
    def __init__(self, message, transient=False):
        super().__init__(message); self.transient = transient


def multipart(fields, file_field=None, file_name=None, file_bytes=None):
    """Encode a multipart/form-data body without third-party libraries."""
    boundary = uuid.uuid4().hex; parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf-8'))
    if file_field:
        ctype = mimetypes.guess_type(file_name)[0] or 'application/octet-stream'
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{file_name}"\r\n'
                     f'Content-Type: {ctype}\r\n\r\n'.encode('utf-8') + file_bytes + b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode('utf-8'))
    return b''.join(parts), f'multipart/form-data; boundary={boundary}'


def http_json(url, body, content_type, headers=None):
    req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': content_type, 'User-Agent': 'PostQueue', **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r: text, status = r.read().decode('utf-8', 'replace'), r.status
    except urllib.error.HTTPError as e:
        text, status = e.read().decode('utf-8', 'replace'), e.code
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise HostingError(f'Could not reach the media hosting service: {getattr(e, "reason", e)}', transient=True)
    try: payload = json.loads(text) if text else {}
    except ValueError: payload = {}
    if status >= 400:
        message = (payload.get('error') or {}).get('message') if isinstance(payload, dict) else None
        raise HostingError(f'Media hosting refused the request (HTTP {status}): {message or text[:200]}', transient=status >= 500)
    return payload


class Cloudinary:
    """Signed uploads to a Cloudinary account (cloud name, API key and API secret from the Cloudinary console)."""
    name = 'Cloudinary'

    def __init__(self, cloud, api_key, api_secret):
        self.cloud, self.api_key, self.api_secret = cloud.strip(), api_key.strip(), api_secret.strip()
        if not (self.cloud and self.api_key and self.api_secret): raise HostingError('Cloudinary needs the cloud name, API key and API secret.')

    def sign(self, params):
        """Cloudinary's signature: the sorted parameters (without file, api_key and resource_type), then the secret, SHA-1."""
        skip = {'file', 'api_key', 'resource_type', 'cloud_name', 'signature'}
        text = '&'.join(f'{k}={v}' for k, v in sorted(params.items()) if k not in skip and v not in (None, ''))
        return hashlib.sha1((text + self.api_secret).encode('utf-8')).hexdigest()

    def _url(self, kind, action): return f'https://api.cloudinary.com/v1_1/{self.cloud}/{"video" if kind == "video" else "image"}/{action}'

    def _params(self, **extra):
        params = {'timestamp': str(int(time.time())), **extra}
        params['signature'] = self.sign(params); params['api_key'] = self.api_key
        return params

    def upload(self, path, kind, on_progress=None):
        """Upload a file. Returns (public URL, handle for delete)."""
        path = Path(path); public_id = f'postqueue/{uuid.uuid4().hex}'
        params = self._params(public_id=public_id)
        size = path.stat().st_size
        if size <= CHUNK_ABOVE:
            body, ctype = multipart(params, 'file', path.name, path.read_bytes())
            result = http_json(self._url(kind, 'upload'), body, ctype)
        else:
            upload_id = uuid.uuid4().hex; result = {}
            with path.open('rb') as f:
                start = 0
                while start < size:
                    data = f.read(CHUNK); end = start + len(data) - 1
                    body, ctype = multipart(params, 'file', path.name, data)
                    result = http_json(self._url(kind, 'upload'), body, ctype,
                                       {'X-Unique-Upload-Id': upload_id, 'Content-Range': f'bytes {start}-{end}/{size}'})
                    start = end + 1
                    if on_progress: on_progress(start / size)
        url = result.get('secure_url')
        if not url: raise HostingError('Media hosting did not return a URL for the upload.')
        if on_progress: on_progress(1.0)
        return url, result.get('public_id', public_id)

    def delete(self, handle, kind):
        params = self._params(public_id=handle, invalidate='true')
        body, ctype = multipart(params)
        http_json(self._url(kind, 'destroy'), body, ctype)

    def test(self):
        """Upload a 1x1 image and remove it again. Raises HostingError when the account details are wrong."""
        params = self._params(public_id=f'postqueue/test-{uuid.uuid4().hex}')
        body, ctype = multipart(params, 'file', 'test.png', TEST_PNG)
        result = http_json(self._url('image', 'upload'), body, ctype)
        if not result.get('secure_url'): raise HostingError('The test upload did not return a URL.')
        self.delete(result.get('public_id'), 'image')
        return result['secure_url']


def make_provider(settings):
    """The configured hosting provider, or None when hosting is not set up yet."""
    if settings.get('hosting') == 'cloudinary' and all(settings.get(k) for k in ('cloudinary_cloud', 'cloudinary_api_key', 'cloudinary_api_secret')):
        return Cloudinary(settings['cloudinary_cloud'], settings['cloudinary_api_key'], settings['cloudinary_api_secret'])
    return None
