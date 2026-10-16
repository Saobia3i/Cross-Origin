import React from 'react';
import {
  Container, Typography, Box, Paper, Button,
} from '@mui/material';
import BarChartIcon from '@mui/icons-material/BarChart';
import BlockIcon from '@mui/icons-material/Block';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../store/auth';
import { fetchReports, verifyReport, rejectReport, resolveReport } from '../api/endpoints';
import type { Report } from '../api/schemas';
import { StatusBadge } from '../components/StatusBadge';
import { ErrorBox } from '../components/Feedback';

// ── Stat card ───────────────────────────────────────────────────
const StatCard: React.FC<{ label: string; value: number; icon: React.ReactNode; color: string }> = ({ label, value, icon, color }) => (
  <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5 }}>
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
      <Box>
        <Typography variant="caption" sx={{ fontWeight: 600, color: "text.secondary" }}>{label}</Typography>
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
  const { data, isLoading, error } = useQuery({ queryKey: ['alerts'], queryFn: () => fetchReports({ limit: 200 }) });
  const pending = (data ?? []).filter((a) => a.status === 'pending');
  const [actionError, setActionError] = React.useState('');

  // Admin verify/reject is the final authority on report status (votes never change it).
  const mod = useMutation({
    mutationFn: ({ id, action }: { id: string; action: 'verify' | 'reject' | 'resolve' }) => {
      if (action === 'verify') return verifyReport(id);
      if (action === 'resolve') return resolveReport(id);
      return rejectReport(id, window.prompt('Reason for rejection?') || 'Rejected by admin');
    },
    onSuccess: () => { setActionError(''); qc.invalidateQueries({ queryKey: ['alerts'] }); },
    onError: (e: Error) => setActionError(e.message),
  });

  if (isLoading) return <Typography color="text.secondary">{t('common.loading')}</Typography>;
  if (error) return <ErrorBox />;

  return (
    <Box>
      <Typography variant="subtitle1" sx={{ fontWeight: 700, fontFamily: 'var(--font-heading)', mb: 1.5 }}>
        {t('admin.moderation')} ({pending.length})
      </Typography>
      {pending.length === 0 && <Typography variant="body2" color="text.secondary">No pending reports.</Typography>}
      {actionError && <Typography color="error.main" variant="caption">{actionError}</Typography>}
      {pending.map((a: Report) => (
        <Paper key={a.report_id} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1.5, p: 2, mb: 1.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
            <Box>
              <StatusBadge status={a.status} />
              <Typography variant="body2" sx={{ fontWeight: 500, mt: 0.5 }}>{a.description}</Typography>
              <Typography variant="caption" color="text.secondary">{a.affected_hub} · ↑{a.upvotes} ↓{a.downvotes}</Typography>
            </Box>
          </Box>
          <Box sx={{ display: 'flex', gap: 1 }}>
            <Button size="small" variant="contained" color="success" onClick={() => mod.mutate({ id: a.report_id, action: 'verify' })}>
              {t('admin.approve')}
            </Button>
            <Button size="small" variant="outlined" color="error" onClick={() => mod.mutate({ id: a.report_id, action: 'reject' })}>
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
  const { data } = useQuery({ queryKey: ['alerts'], queryFn: () => fetchReports({ limit: 200 }) });
  const alerts = data ?? [];

  if (!user || user.role !== 'admin') {
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
      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(3, 1fr)' }, gap: 2, mb: 4 }}>
        <StatCard label="Pending reports" value={alerts.filter((a) => a.status === 'pending').length}   icon={<FactCheckIcon />}       color="#F59E0B" />
        <StatCard label="Verified alerts" value={alerts.filter((a) => a.status === 'verified').length}  icon={<BarChartIcon />}        color="#16A34A" />
        <StatCard label="Closed roads"   value={alerts.filter((a) => a.edge_closed).length}             icon={<BlockIcon />}           color="#E11D48" />
      </Box>

      {/* TODO: road block/unblock and fare-rule editing endpoints are 501 NOT_IMPLEMENTED in the backend catalog. */}
      <Box sx={{ display: 'grid', gridTemplateColumns: '1fr', gap: 3 }}>
        <ModerationQueue />
      </Box>
    </Container>
  );
};

export default AdminPage;
