import React from 'react';
import {
  Container, Typography, Box, Grid, Paper, Button, Chip,
} from '@mui/material';
import BarChartIcon from '@mui/icons-material/BarChart';
import BlockIcon from '@mui/icons-material/Block';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import AssignmentIndIcon from '@mui/icons-material/AssignmentInd';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../store/auth';
import { fetchAlerts, moderateAlert, fetchApplications, reviewApplication } from '../api/endpoints';
import { StatusBadge } from '../components/StatusBadge';
import { ErrorBox } from '../components/Feedback';

// ── Stat card ───────────────────────────────────────────────────
const StatCard: React.FC<{ label: string; value: number; icon: React.ReactNode; color: string }> = ({ label, value, icon, color }) => (
  <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5 }}>
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
      <Box>
        <Typography variant="caption" color="text.secondary" fontWeight={600}>{label}</Typography>
        <Typography variant="h4" sx={{ fontFamily: 'var(--font-mono)', fontWeight: 700, color, mt: 0.5 }}>
          {value}
        </Typography>
      </Box>
      <Box sx={{ color, opacity: 0.7, fontSize: 32 }}>{icon}</Box>
    </Box>
  </Paper>
);

// ── Moderation queue ────────────────────────────────────────────
const ModerationQueue: React.FC = () => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['alerts'], queryFn: fetchAlerts });
  const pending = ((data as any[]) ?? []).filter((a: any) => a.status === 'pending');

  const mod = useMutation({
    mutationFn: ({ id, action }: { id: string; action: string }) => moderateAlert(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  if (isLoading) return <Typography color="text.secondary">{t('common.loading')}</Typography>;
  if (error) return <ErrorBox />;

  return (
    <Box>
      <Typography variant="subtitle1" fontWeight={700} sx={{ fontFamily: 'var(--font-heading)', mb: 1.5 }}>
        {t('admin.moderation')} ({pending.length})
      </Typography>
      {pending.length === 0 && <Typography variant="body2" color="text.secondary">No pending reports.</Typography>}
      {pending.map((a: any) => (
        <Paper key={a.alert_id} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1.5, p: 2, mb: 1.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
            <Box>
              <StatusBadge status={a.status} />
              <Typography variant="body2" fontWeight={500} sx={{ mt: 0.5 }}>{a.description}</Typography>
              <Typography variant="caption" color="text.secondary">{a.affected_hub} · ↑{a.upvotes} ↓{a.downvotes}</Typography>
            </Box>
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" color="success" onClick={() => mod.mutate({ id: a.alert_id, action: 'approve' })}>
              {t('admin.approve')}
            </Button>
            <Button size="small" variant="outlined" color="error" onClick={() => mod.mutate({ id: a.alert_id, action: 'reject' })}>
              {t('admin.reject')}
            </Button>
            <Button size="small" variant="outlined" onClick={() => mod.mutate({ id: a.alert_id, action: 'resolve' })}>
              {t('admin.resolve')}
            </Button>
          </Box>
        </Paper>
      ))}
    </Box>
  );
};

// ── Applications queue ──────────────────────────────────────────
const ApplicationsQueue: React.FC = () => {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({ queryKey: ['applications'], queryFn: fetchApplications });
  const apps = (data as any[]) ?? [];

  const review = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'approve' | 'reject' }) => reviewApplication(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['applications'] }),
  });

  if (isLoading) return <Typography color="text.secondary">{t('common.loading')}</Typography>;

  return (
    <Box>
      <Typography variant="subtitle1" fontWeight={700} sx={{ fontFamily: 'var(--font-heading)', mb: 1.5 }}>
        {t('admin.applications')} ({apps.filter((a: any) => a.status === 'pending').length} pending)
      </Typography>
      {apps.filter((a: any) => a.status === 'pending').map((a: any) => (
        <Paper key={a.id} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1.5, p: 2, mb: 1.5 }}>
          <Typography variant="body2" fontWeight={600}>{a.name}</Typography>
          <Typography variant="caption" color="text.secondary">{a.email} · {a.occupation}</Typography>
          <Typography variant="body2" sx={{ mt: 0.5, mb: 1 }}>{a.reason}</Typography>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" color="success" onClick={() => review.mutate({ id: a.id, action: 'approve' })}>
              {t('admin.approve')}
            </Button>
            <Button size="small" variant="outlined" color="error" onClick={() => review.mutate({ id: a.id, action: 'reject' })}>
              {t('admin.reject')}
            </Button>
          </Box>
        </Paper>
      ))}
    </Box>
  );
};

// ── Main admin dashboard ────────────────────────────────────────
const AdminPage: React.FC = () => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const { data } = useQuery({ queryKey: ['alerts'], queryFn: fetchAlerts });
  const alerts = (data as any[]) ?? [];

  if (!user || (user.role !== 'admin' && user.role !== 'superadmin')) {
    return (
      <Container maxWidth="sm" sx={{ py: 8, textAlign: 'center' }}>
        <Typography variant="h6">Access denied</Typography>
        <Button component={Link} to="/" variant="outlined" sx={{ mt: 2 }}>Go home</Button>
      </Container>
    );
  }

  return (
    <Container maxWidth="xl" sx={{ py: 3 }}>
      <Typography variant="h4" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, mb: 3 }}>
        🛡 {t('admin.dashboard')}
      </Typography>

      {/* Stats */}
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(4, 1fr)' }, gap: 2, mb: 4 }}>
        <StatCard label="Pending reports" value={alerts.filter((a: any) => a.status === 'pending').length}   icon={<FactCheckIcon />}       color="#F59E0B" />
        <StatCard label="Verified alerts" value={alerts.filter((a: any) => a.status === 'verified').length}  icon={<BarChartIcon />}        color="#16A34A" />
        <StatCard label="Closed roads"   value={alerts.filter((a: any) => a.edge_closed).length}             icon={<BlockIcon />}           color="#E11D48" />
        <StatCard label="Admin pending"  value={0}                                                            icon={<AssignmentIndIcon />}   color="#7C3AED" />
      </Box>

      {/* Action links */}
      <Box sx={{ display: 'flex', gap: 1.5, mb: 4, flexWrap: 'wrap' }}>
        {[
          { label: `🛣 ${t('admin.roads')}`,         to: '/admin/roads' },
          { label: `💰 ${t('admin.fares')}`,         to: '/admin/fares' },
          { label: `📋 ${t('admin.applications')}`,  to: '/admin/applications' },
        ].map(({ label, to }) => (
          <Button key={to} component={Link} to={to} variant="outlined" sx={{ borderRadius: 2 }}>
            {label}
          </Button>
        ))}
      </Box>

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' }, gap: 3 }}>
        <ModerationQueue />
        <ApplicationsQueue />
      </Box>
    </Container>
  );
};

export default AdminPage;
