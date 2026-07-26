/**
 * Public read-only front end for the word-by-word audio bucket.
 *
 * Why not serve R2 directly: the r2.dev URL is rate limited and bypasses Cloudflare's
 * cache entirely, so every listener pays a full R2 read. Through a Worker the edge
 * caches each clip, which matters here more than usual -- a handful of words like
 * "Allah" account for thousands of positions, so cache hit rates are extremely high.
 *
 * Routes:
 *   GET /            -> tiny index describing the API
 *   GET /health      -> {"ok":true}
 *   GET /<key>       -> object from the bucket (e.g. en/v1/audio/ga08....opus)
 *
 * Only GET/HEAD are accepted. Uploads go through the S3 API with separate credentials.
 */

const IMMUTABLE = 'public, max-age=31536000, immutable';
// Indexes are regenerated in place (adding gloss text, fixing a mapping), so they must
// revalidate rather than sit in a browser cache for an hour. A stale index silently
// hides new data and looks like a bug in the consumer's code.
const SHORT = 'public, max-age=60, must-revalidate';

const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'GET, HEAD, OPTIONS',
  'Access-Control-Allow-Headers': 'Range, Content-Type',
  'Access-Control-Expose-Headers':
    'Content-Length, Content-Range, Accept-Ranges, ETag, Content-Type',
  'Access-Control-Max-Age': '86400',
};

function json(body, status = 200, extra = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8', ...CORS, ...extra },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === 'OPTIONS') {
      return new Response(null, { status: 204, headers: CORS });
    }
    if (request.method !== 'GET' && request.method !== 'HEAD') {
      return json({ error: 'method not allowed' }, 405, { Allow: 'GET, HEAD, OPTIONS' });
    }

    const key = decodeURIComponent(url.pathname.slice(1));

    if (!key) {
      return json({
        service: 'quran-wbw-audio',
        addressing: ['surah:ayah:word', 'qusx-global-id'],
        usage: {
          index: '/en/v1/index/001.json',
          audio: '/en/v1/audio/<glossId>.opus',
          manifest: '/en/v1/index.json',
        },
      });
    }
    if (key === 'health') return json({ ok: true });

    // Reject traversal and absolute keys outright rather than relying on R2 to 404.
    if (key.includes('..') || key.startsWith('/')) {
      return json({ error: 'bad key' }, 400);
    }

    // Only audio is cached at the edge. Audio bytes are immutable -- a corrected clip
    // ships under a new version prefix -- so a long-lived entry is always correct.
    // Indexes are regenerated in place, and an edge entry written with an old TTL
    // outlives any later header change: that is exactly what happened when gloss text
    // was added and consumers kept receiving the previous index for an hour.
    const cacheable = key.endsWith('.opus');
    const cache = caches.default;
    const cacheKey = new Request(url.toString(), { method: 'GET' });

    if (cacheable) {
      let response = await cache.match(cacheKey);
      if (response) {
        response = new Response(response.body, response);
        response.headers.set('X-Cache', 'HIT');
        return response;
      }
    }

    const range = request.headers.get('range');
    const object = range
      ? await env.AUDIO.get(key, { range: request.headers })
      : await env.AUDIO.get(key);

    if (!object) return json({ error: 'not found', key }, 404);

    const headers = new Headers(CORS);
    object.writeHttpMetadata(headers);
    headers.set('ETag', object.httpEtag);
    headers.set('Accept-Ranges', 'bytes');
    headers.set('X-Cache', 'MISS');
    // Audio bytes never change for a given key -- a corrected clip ships under a new
    // version prefix instead -- so it is safe to cache them for a year. JSON indexes
    // can be regenerated in place, so they get a short TTL.
    headers.set('Cache-Control', key.endsWith('.opus') ? IMMUTABLE : SHORT);

    let status = 200;
    if (object.range && range) {
      const size = object.size;
      const start = object.range.offset ?? 0;
      const len = object.range.length ?? size - start;
      headers.set('Content-Range', `bytes ${start}-${start + len - 1}/${size}`);
      status = 206;
    }

    const res = new Response(object.body, { status, headers });
    // Only full 200s are worth storing; partials would fragment the cache.
    // The put MUST be handed to waitUntil -- returning the response ends the request,
    // and an un-awaited cache write is cancelled with it, so every fetch stayed a MISS.
    if (cacheable && status === 200 && request.method === 'GET') {
      ctx.waitUntil(cache.put(cacheKey, res.clone()));
    }
    return res;
  },
};
