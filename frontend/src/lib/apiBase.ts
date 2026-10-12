export function apiBase() {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
  if (configured) return configured === "/" ? "" : configured.replace(/\/+$/, "");
  if (process.env.NODE_ENV === "production") return "";
  const host = typeof window !== "undefined" ? window.location.hostname : "localhost";
  return `http://${host === "127.0.0.1" ? host : "localhost"}:8001`;
}
