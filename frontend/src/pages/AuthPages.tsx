import React, { useState } from 'react';
import {
  Container, Box, Typography, TextField, Button, Paper, Divider, Link as MuiLink,
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
    try {
      const res = await login(data.email, data.password);
      setAuth(res.user, res.access_token);
      navigate('/');
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <Container maxWidth="xs" sx={{ py: 8 }}>
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, p: 4 }}>
        <Box sx={{ textAlign: 'center', mb: 3 }}>
          <Box sx={{ fontSize: 48, mb: 1 }}>🗺️</Box>
          <Typography variant="h5" fontFamily="var(--font-heading)" fontWeight={700}>DhakaPath</Typography>
          <Typography variant="body2" color="text.secondary">{t('auth.login')}</Typography>
        </Box>

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
        <Typography variant="body2" color="text.secondary" textAlign="center">
          {t('auth.noAccount')}{' '}
          <MuiLink component={Link} to="/register" fontWeight={600}>{t('auth.register')}</MuiLink>
        </Typography>
        <Typography variant="body2" color="text.secondary" textAlign="center" sx={{ mt: 1 }}>
          Want to be an admin?{' '}
          <MuiLink component={Link} to="/join-admin" fontWeight={600}>{t('auth.joinAdmin')}</MuiLink>
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
      setError((e as Error).message);
    }
  };

  return (
    <Container maxWidth="xs" sx={{ py: 8 }}>
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 3, p: 4 }}>
        <Typography variant="h5" fontFamily="var(--font-heading)" fontWeight={700} textAlign="center" mb={3}>
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
        <Typography variant="body2" color="text.secondary" textAlign="center">
          {t('auth.hasAccount')}{' '}
          <MuiLink component={Link} to="/login" fontWeight={600}>{t('auth.login')}</MuiLink>
        </Typography>
      </Paper>
    </Container>
  );
};
