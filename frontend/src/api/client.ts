import axios from 'axios';
import { useAuthStore } from '../store/auth';

const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export const apiClient = axios.create({
  baseURL: BASE_URL,
  headers: { 'Content-Type': 'application/json' },
  timeout: 15_000,
});

// Attach JWT token on every request
apiClient.interceptors.request.use((config) => {
  const token = useAuthStore.getState().token;
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// Unwrap envelope  { ok, data, meta, errors }
apiClient.interceptors.response.use(
  (res) => {
    if (res.data?.ok === false) {
      return Promise.reject(new Error(res.data.errors?.[0]?.message || 'API error'));
    }
    // return the inner data directly so callers get res.data directly
    res.data = res.data?.data ?? res.data;
    return res;
  },
  (err) => {
    const msg =
      err.response?.data?.errors?.[0]?.message ||
      err.response?.data?.message ||
      err.message ||
      'Network error';
    return Promise.reject(new Error(msg));
  }
);

export default apiClient;
