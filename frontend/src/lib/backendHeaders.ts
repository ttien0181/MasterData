/** Headers sent only to the backend API, never to presigned storage URLs. */
export function backendHeaders(existing?: HeadersInit): Headers {
  const headers = new Headers(existing);
  if (process.env.NEXT_PUBLIC_NGROK_BYPASS_WARNING === "true") {
    headers.set("ngrok-skip-browser-warning", "1");
  }
  return headers;
}
