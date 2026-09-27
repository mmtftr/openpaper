/**
 * Self-destructing service worker, served at every path a service worker was
 * ever registered from on this origin (the old offline layer registered
 * `/sw.js` with the default `/` scope, 2026-05-08 .. 2026-05-10; removed in
 * d047ca4). Browsers that still have it installed byte-compare the script on
 * their next update check (any navigation, capped at 24h), install this one,
 * and it then deletes every Cache Storage entry (`openpaper-app-shell-v*` held
 * stale HTML + /_next chunks served cache-first), drops the old offline
 * IndexedDB, unregisters itself and reloads open tabs onto the network.
 * It has no fetch handler, so it never intercepts anything.
 */
const KILL_SW_SOURCE = `// openpaper: service worker kill switch (2026-09-27)
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch {}
    try { indexedDB.deleteDatabase("openpaper-offline"); } catch {}
    try { await self.registration.unregister(); } catch {}
    try {
      const clients = await self.clients.matchAll({ type: "window" });
      await Promise.all(clients.map((client) => client.navigate(client.url).catch(() => {})));
    } catch {}
  })());
});
`;

export function killServiceWorkerResponse() {
	return new Response(KILL_SW_SOURCE, {
		headers: {
			"Content-Type": "application/javascript; charset=utf-8",
			"Cache-Control": "no-store, max-age=0",
			"Clear-Site-Data": '"cache"',
		},
	});
}

/**
 * Inline page-side counterpart (root layout): unregister every registration
 * and empty Cache Storage once per tab session; reload once if a service
 * worker was controlling the page. Never touches cookies or localStorage.
 */
export const UNREGISTER_SW_INLINE_SCRIPT = `(function(){try{
if(!("serviceWorker" in navigator))return;
var k="op-sw-purged";if(sessionStorage.getItem(k))return;sessionStorage.setItem(k,"1");
var had=!!navigator.serviceWorker.controller;
navigator.serviceWorker.getRegistrations().then(function(rs){return Promise.all(rs.map(function(r){return r.unregister();}));})
.then(function(){return self.caches?caches.keys().then(function(ks){return Promise.all(ks.map(function(c){return caches.delete(c);}));}):0;})
.then(function(){try{indexedDB.deleteDatabase("openpaper-offline");}catch(e){}})
.catch(function(){}).then(function(){if(had)location.reload();});
}catch(e){}})();`;
