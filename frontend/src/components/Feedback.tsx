import React from 'react';
import { Box, Typography, Button } from '@mui/material';
import { useTranslation } from 'react-i18next';

interface Props {
  icon?: React.ReactNode;
  title: string;
  subtitle?: string;
  action?: { label: string; onClick: () => void };
}

export const EmptyState: React.FC<Props> = ({ icon, title, subtitle, action }) => (
  <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, py: 8, px: 3, textAlign: 'center' }}>
    {icon && (
      <Box sx={{ fontSize: 48, opacity: 0.5 }}>{icon}</Box>
    )}
    <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', color: 'text.secondary' }}>
      {title}
    </Typography>
    {subtitle && (
      <Typography variant="body2" color="text.disabled">{subtitle}</Typography>
    )}
    {action && (
      <Button variant="outlined" size="small" onClick={action.onClick}>{action.label}</Button>
    )}
  </Box>
);

export const ErrorBox: React.FC<{ message?: string; onRetry?: () => void }> = ({ message, onRetry }) => {
  const { t } = useTranslation();
  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, py: 6, px: 3, textAlign: 'center' }}>
      <Typography sx={{ fontSize: 36 }}>⚠️</Typography>
      <Typography color="error.main" sx={{ fontWeight: 500 }}>{message || t('common.error')}</Typography>
      {onRetry && (
        <Button variant="outlined" size="small" color="error" onClick={onRetry}>{t('common.retry')}</Button>
      )}
    </Box>
  );
};

export const GroundingBadge: React.FC<{ ok: boolean }> = ({ ok }) => {
  const { t } = useTranslation();
  return (
    <Box
      component="span"
      sx={{
        display: 'inline-flex', alignItems: 'center', gap: 0.5,
        px: 1, py: 0.25, borderRadius: 9999,
        backgroundColor: ok ? '#DCFCE7' : '#FFE4E6',
        color:           ok ? '#16A34A'  : '#E11D48',
        fontSize: '0.6875rem', fontWeight: 600,
      }}
    >
      {ok ? '✓' : '⚠'} {t(ok ? 'grounding.badge' : 'grounding.fake')}
    </Box>
  );
};
