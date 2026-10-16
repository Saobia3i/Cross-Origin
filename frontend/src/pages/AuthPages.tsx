import React, { useState } from 'react';
import {
  Container, Box, Typography, TextField, Button, Paper, Divider,
  Link as MuiLink, Alert, Chip,
} from '@mui/material';
import { Link, useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useTranslation } from 'react-i18next';
import { login, register } from '../api/endpoints';
import { useAuthStore } from '../store/auth';

const loginSchema = z.object({
  email:    z.string().email(),
  password: z.string().min(6),
});
const registerSchema = loginSchema.extend({
  name:  z.string().min(2),
  phone: z.string().optional(),
});

type LoginForm    = z.infer<typeof loginSchema>;
type RegisterForm = z.infer<typeof registerSchema>;

// ── Dev bypass users — works WITHOUT backend ──────────────────────
const DEV_USERS: Record<string, { name: string; role: 'admin' | 'user' }> = {
  'admin@dev':     { name: 'Dev Admin',      role: 'admin' },
  'super@dev':     { name: 'Dev Superadmin', role: 'admin' },  // backend roles are user|admin only
  'user@dev':      { name: 'Dev User',       role: 'user' },
};
const DEV_PASSWORD = 'devpass';

export const LoginPage: React.FC = () => {
  const { t } = useTranslation();
  const { setAuth } = useAuthStore();
  const navigate = useNavigate();
  const [error, setError] = useState('');

  const { register: reg, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
  });

  const onSubmit = async (data: LoginForm) => {
    setError('');

    // ── Dev bypass (no backend needed) ──────────────────────────
    const devUser = DEV_USERS[data.email];
    if (devUser && data.password === DEV_PASSWORD) {
      setAuth({ id: 1, name: devUser.name, email: data.email, role: devUser.role }, 'dev-token');
      navigate('/');
      return;
    }

    // ── Real backend login ───────────────────────────────────────
    try {
      const res = await login(data.email, data.password);
      setAuth(res.user, res.access_token);
      navigate('/');
    } catch (e) {
      setError((e as Error).message || 'Login failed. Backend may be offline.');
    }
  };

  const quickLogin = (email: string) => {
    setAuth(
      { id: 1, name: DEV_USERS[email].name, email, role: DEV_USERS[email].role },
      'dev-token'
    );
    navigate('/');
  };

  return (
    <Container maxWidth="xs" sx={{ py: 6 }}>
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, p: 4 }}>
        {/* Logo */}
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Box sx={{ fontSize: 48, mb: 1 }}>🛣️</Box>
          <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: "var(--font-heading)" }}>DhakaPath</Typography>
          <Typography variant="body2" color="text.secondary">{t('auth.login')}</Typography>
        </Box>

        {/* Dev quick-login panel */}
        <Alert
          severity="info"
          sx={{ mb: 2, borderRadius: 2, fontSize: '0.8rem' }}
          icon={false}
        >
          <Typography variant="caption" sx={{ fontWeight: 700, display: "block", mb: 0.75 }}>
            🔧 Dev Mode — 1-click login (backend not needed)
          </Typography>
          <Box sx={{ display: 'flex', gap: 1, flexWrap: 'wrap' }}>
            <Chip
              label="Admin" size="small" clickable
              onClick={() => quickLogin('admin@dev')}
              sx={{ backgroundColor: '#E6B800', color: '#1A1600', fontWeight: 700 }}
            />
            <Chip
              label="Superadmin" size="small" clickable
              onClick={() => quickLogin('super@dev')}
              sx={{ backgroundColor: '#2E7D4F', color: '#fff', fontWeight: 700 }}
            />
            <Chip
              label="User" size="small" clickable
              onClick={() => quickLogin('user@dev')}
              sx={{ backgroundColor: '#3A3930', color: '#F2F0EA', fontWeight: 700 }}
            />
          </Box>
          <Typography variant="caption" sx={{ display: "block", mt: 0.5, color: "text.secondary" }}>
            Or use email: <code>admin@dev</code> / password: <code>devpass</code>
          </Typography>
        </Alert>

        {/* Normal form */}
        <Box component="form" onSubmit={handleSubmit(onSubmit)} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField
            label={t('auth.email')} size="small" type="email"
            {...reg('email')} error={!!errors.email} helperText={errors.email?.message}
          />
          <TextField
            label={t('auth.password')} size="small" type="password"
            {...reg('password')} error={!!errors.password} helperText={errors.password?.message}
          />
          {error && <Typography color="error.main" variant="caption">{error}</Typography>}
          <Button type="submit" variant="contained" fullWidth disabled={isSubmitting} sx={{ height: 44 }}>
            {isSubmitting ? 'Signing in…' : t('auth.loginCta')}
          </Button>
        </Box>

        <Divider sx={{ my: 2 }} />
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center' }}>
          {t('auth.noAccount')}{' '}
          <MuiLink component={Link} to="/register" sx={{ fontWeight: 600 }}>{t('auth.register')}</MuiLink>
        </Typography>
      </Paper>
    </Container>
  );
};

export const RegisterPage: React.FC = () => {
  const { t } = useTranslation();
  const { setAuth } = useAuthStore();
  const navigate = useNavigate();
  const [error, setError] = useState('');

  const { register: reg, handleSubmit, formState: { errors, isSubmitting } } = useForm<RegisterForm>({
    resolver: zodResolver(registerSchema),
  });

  const onSubmit = async (data: RegisterForm) => {
    setError('');
    try {
      const res = await register(data.name, data.email, data.password);
      setAuth(res.user, res.access_token);
      navigate('/');
    } catch (e) {
      setError((e as Error).message || 'Registration failed. Backend may be offline.');
    }
  };

  return (
    <Container maxWidth="xs" sx={{ py: 6 }}>
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, p: 4 }}>
        <Typography variant="h5" sx={{ fontWeight: 700, fontFamily: "var(--font-heading)", mb: 3, textAlign: 'center' }}>
          {t('auth.register')}
        </Typography>

        <Box component="form" onSubmit={handleSubmit(onSubmit)} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <TextField label={t('auth.name')}  size="small" {...reg('name')}  error={!!errors.name}  helperText={errors.name?.message} />
          <TextField label={t('auth.email')} size="small" type="email" {...reg('email')} error={!!errors.email} helperText={errors.email?.message} />
          <TextField label={t('auth.password')} size="small" type="password" {...reg('password')} error={!!errors.password} helperText={errors.password?.message} />
          <TextField label={`${t('auth.phone')} (${t('common.optional')})`} size="small" {...reg('phone')} />
          {error && <Typography color="error.main" variant="caption">{error}</Typography>}
          <Button type="submit" variant="contained" fullWidth disabled={isSubmitting} sx={{ height: 44 }}>
            {isSubmitting ? 'Creating…' : t('auth.registerCta')}
          </Button>
        </Box>
        <Divider sx={{ my: 2 }} />
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center' }}>
          {t('auth.hasAccount')}{' '}
          <MuiLink component={Link} to="/login" sx={{ fontWeight: 600 }}>{t('auth.login')}</MuiLink>
        </Typography>
      </Paper>
    </Container>
  );
};
