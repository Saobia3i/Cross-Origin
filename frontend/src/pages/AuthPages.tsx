import React, { useState } from 'react';
import {
  Container, Box, Typography, TextField, Button, Paper, Divider,
  Link as MuiLink,
} from '@mui/material';
import { Link, useNavigate } from 'react-router-dom';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useTranslation } from 'react-i18next';
import { useQueryClient } from '@tanstack/react-query';
import { login, register } from '../api/endpoints';
import { useAuthStore } from '../store/auth';

const loginSchema = z.object({
  email:    z.string().email(),
  password: z.string().min(1, 'Password is required'),
});
const registerSchema = loginSchema.extend({
  name:  z.string().min(2),
  phone: z.string().optional(),
});

type LoginForm    = z.infer<typeof loginSchema>;
type RegisterForm = z.infer<typeof registerSchema>;

export const LoginPage: React.FC = () => {
  const { t } = useTranslation();
  const { setAuth } = useAuthStore();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [error, setError] = useState('');

  const { register: reg, handleSubmit, formState: { errors, isSubmitting } } = useForm<LoginForm>({
    resolver: zodResolver(loginSchema),
  });

  const doLogin = async (email: string, password: string) => {
    setError('');
    try {
      const res = await login(email, password);
      setAuth(res.user, res.access_token);
      qc.invalidateQueries();
      navigate(res.user.role === 'admin' ? '/admin' : '/');
    } catch (e) {
      setError((e as Error).message || 'Login failed. Backend may be offline.');
    }
  };

  const onSubmit = (data: LoginForm) => doLogin(data.email, data.password);

  return (
    <Container maxWidth="xs" sx={{ py: 6 }}>
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, p: 4 }}>
        {/* Logo */}
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Box sx={{ fontSize: 48, mb: 1 }}>🛣️</Box>
          <Typography variant="h5" sx={{ fontFamily: "var(--font-heading)", fontWeight: 700 }}>DhakaPath</Typography>
          <Typography variant="body2" color="text.secondary">{t('auth.login')}</Typography>
        </Box>

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
        <Typography variant="h5" sx={{ fontFamily: "var(--font-heading)", fontWeight: 700, mb: 3, textAlign: 'center' }}>
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
