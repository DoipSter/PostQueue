/**
 * The PostQueue service: a Cloudflare Worker that does the two things a desktop app cannot do on its own.
 *
 *   POST /token        finish a Meta sign-in: holds the app secret and returns a long-lived token
 *   PUT  /media        host a photo or video for the moment it takes Instagram to fetch it (R2 storage)
 *   GET  /m/<id>       the public link Instagram fetches
 *   DELETE /media/<id> remove the file right after posting (an hourly job removes anything older than a day)
 *
 * Only holders of a valid token for *this* Meta app may upload or delete, so the storage cannot be used by anyone
 * else. Secrets: META_APP_ID and META_APP_SECRET (wrangler secret put). Binding: MEDIA (an R2 bucket).
 */
const GRAPH = 'https://graph.facebook.com';
const IG_GRAPH = 'https://graph.instagram.com';
const MAX_BYTES = 100 * 1024 * 1024;
const KEEP_MS = 24 * 60 * 60 * 1000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (request.method === 'OPTIONS') return new Response(null, { status: 204, headers: cors() });
      if (path === '/' && request.method === 'GET') return json({ service: 'PostQueue', ok: true });
      if (path === '/token' && request.method === 'POST') return await token(request, env);
      if (path === '/media' && request.method === 'PUT') return await upload(request, env, url);
      if (path.startsWith('/m/') && request.method === 'GET') return await serve(path.slice(3), env);
      if (path.startsWith('/media/') && request.method === 'DELETE') return await remove(request, decodeURIComponent(path.slice(7)), env);
      return json({ error: 'Not found' }, 404);
    } catch (e) {
      return json({ error: e.message || String(e) }, e.status || 500);
    }
  },

  // Hourly: anything a client failed to delete goes after a day.
  async scheduled(event, env) {
    const cutoff = Date.now() - KEEP_MS;
    let cursor;
    do {
      const page = await env.MEDIA.list({ cursor, limit: 500 });
      const old = page.objects.filter(o => o.uploaded.getTime() < cutoff).map(o => o.key);
      if (old.length) await env.MEDIA.delete(old);
      cursor = page.truncated ? page.cursor : undefined;
    } while (cursor);
  },
};

const version = env => env.GRAPH_VERSION || 'v25.0';
const cors = () => ({ 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,PUT,POST,DELETE,OPTIONS', 'Access-Control-Allow-Headers': 'Authorization,Content-Type,X-File-Name' });
const json = (data, status = 200) => new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json', ...cors() } });
const fail = (message, status) => { const e = new Error(message); e.status = status; return e; };

async function graph(url, init) {
  const r = await fetch(url, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.error) throw fail((data.error && data.error.message) || `Meta replied ${r.status}`, 502);
  return data;
}

/** Short-lived token or authorization code -> long-lived token, for Facebook Login or Instagram Login. */
async function token(request, env) {
  if (!env.META_APP_ID || !env.META_APP_SECRET) throw fail('The service has no Meta app secret configured.', 500);
  const { kind, token, code, redirect } = await request.json().catch(() => ({}));
  const v = version(env);
  let short = token, result;
  if (kind === 'instagram') {
    if (!short) {
      if (!code || !redirect) throw fail('Instagram Login needs the authorization code and redirect address.', 400);
      const form = new URLSearchParams({ client_id: env.META_APP_ID, client_secret: env.META_APP_SECRET, grant_type: 'authorization_code', redirect_uri: redirect, code });
      short = (await graph('https://api.instagram.com/oauth/access_token', { method: 'POST', body: form })).access_token;
    }
    const q = new URLSearchParams({ grant_type: 'ig_exchange_token', client_secret: env.META_APP_SECRET, access_token: short });
    result = await graph(`${IG_GRAPH}/access_token?${q}`);
  } else {
    if (!short) {
      if (!code || !redirect) throw fail('Facebook Login needs the token or the authorization code and redirect address.', 400);
      const q0 = new URLSearchParams({ client_id: env.META_APP_ID, client_secret: env.META_APP_SECRET, redirect_uri: redirect, code });
      short = (await graph(`${GRAPH}/${v}/oauth/access_token?${q0}`)).access_token;
    }
    const q = new URLSearchParams({ grant_type: 'fb_exchange_token', client_id: env.META_APP_ID, client_secret: env.META_APP_SECRET, fb_exchange_token: short });
    result = await graph(`${GRAPH}/${v}/oauth/access_token?${q}`);
  }
  return json({ access_token: result.access_token, expires_in: result.expires_in || null });
}

/** The bearer token must be a valid user token issued to this Meta app (Facebook Login), or a valid Instagram Login token. */
async function authorize(request, env) {
  const auth = request.headers.get('Authorization') || '';
  const bearer = auth.startsWith('Bearer ') ? auth.slice(7).trim() : '';
  if (!bearer) throw fail('Sign in to Instagram first.', 401);
  const v = version(env);
  const debug = await fetch(`${GRAPH}/${v}/debug_token?input_token=${encodeURIComponent(bearer)}&access_token=${env.META_APP_ID}|${env.META_APP_SECRET}`)
    .then(r => r.json()).catch(() => ({}));
  if (debug.data && debug.data.is_valid && String(debug.data.app_id) === String(env.META_APP_ID)) return debug.data.user_id;
  const me = await fetch(`${IG_GRAPH}/${v}/me?fields=user_id&access_token=${encodeURIComponent(bearer)}`).then(r => r.json()).catch(() => ({}));
  if (me.user_id) return me.user_id;
  throw fail('The Instagram sign-in is not valid for this service. Sign in again.', 401);
}

async function upload(request, env, url) {
  await authorize(request, env);
  const length = Number(request.headers.get('Content-Length') || 0);
  if (length > MAX_BYTES) throw fail(`Files up to ${MAX_BYTES / 1024 / 1024} MB only.`, 413);
  const name = decodeURIComponent(request.headers.get('X-File-Name') || 'file');
  const ext = (name.match(/\.[A-Za-z0-9]{1,5}$/) || ['.bin'])[0].toLowerCase();
  const type = request.headers.get('Content-Type') || 'application/octet-stream';
  const key = `${crypto.randomUUID()}${ext}`;
  await env.MEDIA.put(key, request.body, { httpMetadata: { contentType: type } });
  return json({ id: key, url: `${url.origin}/m/${key}` });
}

async function serve(key, env) {
  const object = await env.MEDIA.get(key);
  if (!object) return new Response('Not found', { status: 404 });
  const headers = new Headers();
  object.writeHttpMetadata(headers);
  headers.set('Content-Length', String(object.size));
  headers.set('Cache-Control', 'public, max-age=300');
  headers.set('Accept-Ranges', 'bytes');
  return new Response(object.body, { headers });
}

async function remove(request, key, env) {
  await authorize(request, env);
  await env.MEDIA.delete(key);
  return json({ ok: true });
}
