// Change apiBaseUrl if the FastAPI server runs on a different host or port.
// Set it to '' when the frontend and /v1 API are served from the same origin.
window.SHORTS_CONFIG = {
  apiBaseUrl: 'http://127.0.0.1:8000',
  ...window.SHORTS_CONFIG,
};
