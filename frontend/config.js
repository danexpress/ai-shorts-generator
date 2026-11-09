// Change apiBaseUrl if the FastAPI server runs on a different host or port.
// Set it to '' when the frontend and /v1 API are served from the same origin.
// Locally, match the page's hostname: session cookies are SameSite=Lax, so a page on
// localhost calling an API on 127.0.0.1 (or the reverse) would lose them after sign-in.
window.SHORTS_CONFIG = {
  apiBaseUrl: `http://${window.location?.hostname === 'localhost' ? 'localhost' : '127.0.0.1'}:8000`,
  ...window.SHORTS_CONFIG,
};
