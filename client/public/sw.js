const CACHE_NAME = "openpaper-app-shell-v5";

const APP_SHELL = [
  "/",
  "/login",
  "/openpaper.svg",
  "/icon.svg",
  // PDF.js worker — react-pdf-highlighter loads this at runtime to render
  // pages. Without it cached, offline PDF rendering can't even start no
  // matter what's in IDB.
  "/pdf.worker.mjs",
];

self.addEventListener("install", (event) => {
  // Use individual `cache.add` calls instead of `cache.addAll` so a single
  // 4xx/5xx (e.g. an SSR-protected route returning 401) doesn't fail the
  // entire install and leave us with no SW.
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE_NAME);
    await Promise.all(APP_SHELL.map(async (path) => {
      try {
        await cache.add(path);
      } catch (err) {
        console.warn("SW install: skipped pre-cache for", path, err);
      }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)));
    await self.clients.claim();

    // The page that registered this SW has already loaded — its navigation
    // request happened before we existed, so it's not in the cache yet.
    // Re-fetch each controlled client's current URL once so a "first visit
    // then immediately go offline" flow still has cached HTML to serve.
    try {
      const cache = await caches.open(CACHE_NAME);
      const clientList = await self.clients.matchAll({ type: "window" });
      await Promise.all(clientList.map(async (client) => {
        try {
          const url = new URL(client.url);
          if (url.origin !== self.location.origin) return;
          if (url.pathname.startsWith("/api/")) return;
          const response = await fetch(client.url, { credentials: "include" });
          if (response.ok && response.type === "basic") {
            await cache.put(client.url, response.clone());
          }
        } catch {
          // best effort — failure here just means no cache warmup for that tab
        }
      }));
    } catch {
      // best effort
    }
  })());
});

function isNavigationRequest(request) {
  if (request.mode === "navigate") return true;
  // Some browsers send `mode: "cors"` for prefetch/HTML; backstop with Accept.
  if (request.method === "GET") {
    const accept = request.headers.get("accept") || "";
    if (accept.includes("text/html")) return true;
  }
  return false;
}

// File extensions we'll cache as static assets. Covers Next.js chunks,
// fonts, images, and the PDF.js worker. Adding a new asset type means
// adding it here.
const STATIC_EXTENSIONS = [
  ".js", ".mjs", ".css", ".map",
  ".svg", ".png", ".jpg", ".jpeg", ".webp", ".avif", ".gif", ".ico",
  ".woff", ".woff2", ".ttf", ".otf",
  ".json", ".txt",
];

function shouldCacheStaticAsset(request) {
  if (request.method !== "GET") return false;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return false;
  // API responses are owned by the IDB-first hooks — SW caching would
  // poison freshness on reconnect.
  if (url.pathname.startsWith("/api/")) return false;
  // PDF blobs are already cached in IDB by `cachePdfBlob`; an SW Cache copy
  // would double storage and hit quota faster.
  if (url.pathname.startsWith("/s3/")) return false;
  // RSC payload prefetches: `?_rsc=...`. They're per-navigation data, not
  // static. Letting them through to the cache would also defeat freshness.
  if (url.searchParams.has("_rsc")) return false;
  // Next.js internal routes cover JS chunks, CSS, fonts, etc. Always cache.
  if (url.pathname.startsWith("/_next/")) return true;
  // Anything else with a static-asset extension (covers everything in
  // `public/` — pdf.worker.mjs, marketing images, etc.).
  const lower = url.pathname.toLowerCase();
  return STATIC_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

async function handleNavigation(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);

  // Stale-while-revalidate. Network-first looks correct ("always serve
  // fresh") until the network just hangs — captive portals, half-up VPNs,
  // or actual airplane mode where the radio is associated to a Wi-Fi that
  // isn't reachable. In all those cases `fetch` doesn't throw, it just
  // never resolves, and the user sees a pending request forever.
  //
  // Cache-first fixes that: if we have cached HTML, render immediately, and
  // kick off a network refresh in the background. After a deploy the user
  // sees the old shell once and the fresh version on the next navigation.
  // The activate handler also re-warms the cache for the URL that triggered
  // the SW upgrade, so that "stale" window is just one cycle.
  if (cached) {
    fetch(request)
      .then((response) => {
        if (response && response.ok && response.type === "basic") {
          cache.put(request, response.clone());
        }
      })
      .catch(() => {
        // Background refresh is best-effort. Offline = no-op.
      });
    return cached;
  }

  // No cached copy — we have to go to the network. If that throws, propagate
  // (browser shows its offline page).
  try {
    const response = await fetch(request);
    if (response && response.ok && response.type === "basic") {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    throw error;
  }
}

async function handleStaticAsset(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;

  try {
    const response = await fetch(request);
    if (response && response.ok) {
      cache.put(request, response.clone());
      return response;
    }
    if (cached) return cached;
    return response;
  } catch (error) {
    if (cached) return cached;
    throw error;
  }
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);

  // /api/* — never SW-cache (IDB-first hooks own offline reads, and a SW copy
  // would poison freshness on reconnect). But we *do* short-circuit when the
  // SW already knows we're offline. Otherwise the request flows out to the
  // network stack and just hangs: when Wi-Fi is off, the OS doesn't actively
  // reject, the request sits pending, and the only thing that ever frees it
  // is the page-side 30s AbortController timeout in fetchFromApi (the source
  // of "AbortError: signal is aborted without reason"). Returning
  // `Response.error()` makes the page-side fetch reject immediately with
  // TypeError — exactly like a native offline fetch — so existing
  // catch-and-fallback paths (auth cached user, usePapers cached list,
  // useSubscription error state) kick in right away instead of after 30s.
  if (url.origin === self.location.origin && url.pathname.startsWith("/api/")) {
    if (!self.navigator.onLine) {
      event.respondWith(Response.error());
    }
    return;
  }

  if (isNavigationRequest(request) && url.origin === self.location.origin) {
    event.respondWith(handleNavigation(request));
    return;
  }

  if (shouldCacheStaticAsset(request)) {
    event.respondWith(handleStaticAsset(request));
  }
});
