const DEFAULT_BACKEND = 'https://hackdavis26-production.up.railway.app';

export function getBackendBaseUrl() {
  const raw = process.env.BACKEND_URL ?? DEFAULT_BACKEND;
  const trimmed = raw.trim().replace(/\/+$/, '');
  // Accept either https://host or https://host/api from env.
  return trimmed.replace(/\/api$/, '');
}
