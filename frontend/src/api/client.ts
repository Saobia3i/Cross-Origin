import axios, { AxiosError } from 'axios';
import { useAuthStore } from '../store/auth';

// VITE_API_URL is the legacy name, kept as a fallback.
const BASE_URL =
  import.meta.env.VITE_API_BASE_URL || import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface ApiErrorItem {
  code: string;
  message: string;
  field?: string | null;
  details?: unknown;
}

/** Thrown for every failed call; carries the backend `errors[]` when there was an envelope. */
export class ApiError extends Error {
  status?: number;
  errors: ApiErrorItem[];
  constructor(message: string, errors: ApiErrorItem[] = [], status?: number) {
    super(message);
    this.name = 'ApiError';
    this.errors = errors;
    this.status = status;
  }
  get code() {
    return this.errors[0]?.code;
  }
}

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 15_000,
});

apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

// Envelope: { ok, data, meta, errors }. Callers receive `res.data` === envelope.data.
apiClient.interceptors.response.use(
  (res) => {
    const env = res.data;
    if (env && typeof env === 'object' && 'ok' in env) {
      if (env.ok === false) {
        const errors: ApiErrorItem[] = env.errors ?? [];
        return Promise.reject(new ApiError(errors[0]?.message || 'API error', errors, res.status));
      }
      res.data = env.data;
    }
    return res;
  },
  (err: AxiosError<{ errors?: ApiErrorItem[] }>) => {
    const errors = err.response?.data?.errors ?? [];
    const msg = errors[0]?.message || err.message || 'Network error';
    return Promise.reject(new ApiError(msg, errors, err.response?.status));
  },
);

export default apiClient;
