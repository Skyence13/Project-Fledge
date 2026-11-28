const key = "lab-summary-v1.json";
const response = (body, status = 200, type = "application/json") => new Response(body, {
  status, headers: {"Content-Type":type, "Cache-Control":"no-store", "X-Content-Type-Options":"nosniff",
  "Content-Security-Policy":"default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self' https://chatgpt.com"}
});

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path !== "/api/summary") {
      const asset = files[path === "/" ? "index.html" : path.slice(1)];
      if (request.method !== "GET" || !Array.isArray(asset)) return response("Not found",404,"text/plain");
      return response(asset[1],200,asset[0]);
    }
    if (request.method === "GET") {
      const saved = env.BUCKET ? await env.BUCKET.get(key) : null;
      return response(saved ? saved.body : JSON.stringify(initial));
    }
    // Owner-private dispatch authenticates service calls. Browser visitors are read-only.
    if (request.method !== "PUT" || request.headers.has("Origin") || request.headers.has("oai-authenticated-user-id"))
      return response(null,405);
    if (!request.headers.get("content-type")?.startsWith("application/json")) return response(null,415);
    if (!env.BUCKET) return response(null,503);
    const raw = await request.text();
    if (raw.length > 256000) return response(null,413);
    let payload;
    try { payload = JSON.parse(raw); } catch { return response(null,400); }
    const stamp = Date.parse(payload?.heartbeat_at || payload?.observed_at);
    if (!Number.isFinite(stamp) || stamp > Date.now()+300000 || !payload.activity?.label ||
        !Array.isArray(payload.campaign?.arms) || !Array.isArray(payload.history)) return response(null,422);
    const previous = await env.BUCKET.get(key);
    const last = previous ? await previous.json() : initial;
    if (Date.parse(last.heartbeat_at || last.observed_at) > stamp) return response(null,409);
    const written = await env.BUCKET.put(key,raw,{onlyIf:previous ? {etagMatches:previous.etag} : {etagDoesNotMatch:"*"}});
    return response(null,written ? 204 : 409);
  }
};
