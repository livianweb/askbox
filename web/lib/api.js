// Cliente fetch hacia el proxy /api (same-origin, cookie httpOnly).
export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`/api${path}`, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* sin cuerpo JSON */
  }
  if (!res.ok) {
    const e = data?.error;
    const err = new Error(e?.message || `Error HTTP ${res.status}`);
    err.status = res.status;
    err.code = e?.code;
    throw err;
  }
  return data;
}
