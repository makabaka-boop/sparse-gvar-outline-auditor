// Thin client for the Python backend. No font math happens in the browser:
// every coordinate, weight and origin label is computed server-side directly
// from glyf/gvar. This UI only renders and explains that JSON.

async function jsonFetch(url, options) {
  const res = await fetch(url, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `request failed: ${res.status}`);
  }
  return data;
}

export const api = {
  info: () => jsonFetch("/api/info"),
  instance: (gid, location) =>
    jsonFetch("/api/instance", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gid, location }),
    }),
  load: async (file) => {
    const res = await fetch("/api/load", {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `upload failed: ${res.status}`);
    return data;
  },
};
