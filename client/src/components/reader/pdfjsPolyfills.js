// Map / WeakMap `getOrInsert` + `getOrInsertComputed` (TC39 "upsert").
//
// pdfjs-dist 5.x's modern build calls `getOrInsertComputed` unguarded, on the
// main thread and in the worker. Safari 18.x (and anything else without the
// upsert proposal) lacks it, so every PDF failed with "Could not load PDF ...
// getOrInsertComputed is not a function".
//
// Plain script on purpose (no import/export): `pdfjs.ts` imports it before
// pdfjs-dist, and `scripts/sync-pdfjs-assets.mjs` prepends it verbatim to the
// worker it copies into public/.
(function () {
	for (const C of [Map, WeakMap]) {
		const proto = C.prototype;
		if (typeof proto.getOrInsert !== "function") {
			Object.defineProperty(proto, "getOrInsert", {
				configurable: true,
				writable: true,
				value: function getOrInsert(key, value) {
					if (this.has(key)) return this.get(key);
					this.set(key, value);
					return value;
				},
			});
		}
		if (typeof proto.getOrInsertComputed !== "function") {
			Object.defineProperty(proto, "getOrInsertComputed", {
				configurable: true,
				writable: true,
				value: function getOrInsertComputed(key, callbackfn) {
					if (typeof callbackfn !== "function") {
						throw new TypeError("callbackfn is not a function");
					}
					if (this.has(key)) return this.get(key);
					const value = callbackfn(key);
					this.set(key, value);
					return value;
				},
			});
		}
	}
})();
