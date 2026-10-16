import React from 'react';
import {
  Container, Typography, Box, Paper, Button, Chip, TextField, Autocomplete,
} from '@mui/material';
import BarChartIcon from '@mui/icons-material/BarChart';
import BlockIcon from '@mui/icons-material/Block';
import FactCheckIcon from '@mui/icons-material/FactCheck';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuthStore } from '../store/auth';
import {
  fetchReports, verifyReport, rejectReport, resolveReport, fetchNetwork,
  adminListUsers, adminUserAction, adminBlockRoute, adminUnblockRoute,
} from '../api/endpoints';
import type { Report } from '../api/schemas';
import { StatusBadge } from '../components/StatusBadge';
import { ErrorBox } from '../components/Feedback';

// ── Stat card ───────────────────────────────────────────────────
const StatCard: React.FC<{ label: string; value: number; icon: React.ReactNode; color: string }> = ({ label, value, icon, color }) => (
  <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5 }}>
    <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
      <Box>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>{label}</Typography>
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

// ── Users (verification) ────────────────────────────────────────
const UsersPanel: React.FC = () => {
  const qc = useQueryClient();
  const { data, isLoading, error } = useQuery({ queryKey: ['admin-users'], queryFn: adminListUsers });
  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: 'verify' | 'disable' | 'enable' }) => adminUserAction(id, action),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-users'] }),
  });
  if (isLoading) return <Typography color="text.secondary">Loading users…</Typography>;
  if (error) return <ErrorBox message={(error as Error).message} />;
  return (
    <Box>
      <Typography variant="subtitle1" sx={{ fontWeight: 700, fontFamily: 'var(--font-heading)', mb: 1.5 }}>
        Users ({(data ?? []).length})
      </Typography>
      {(data ?? []).map((u) => (
        <Paper key={u.id} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1.5, p: 1.5, mb: 1, display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
          <Box sx={{ flex: 1, minWidth: 160 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{u.name}</Typography>
            <Typography variant="caption" color="text.secondary">{u.email}</Typography>
          </Box>
          <Chip size="small" label={u.role} />
          <Chip size="small" color={u.is_verified ? 'success' : 'warning'} label={u.is_verified ? 'verified' : 'unverified'} />
          {u.status !== 'active' && <Chip size="small" color="error" label={u.status} />}
          {!u.is_verified && (
            <Button size="small" variant="contained" onClick={() => act.mutate({ id: u.id, action: 'verify' })}>Verify</Button>
          )}
          {u.role !== 'admin' && (u.status === 'active'
            ? <Button size="small" color="error" variant="outlined" onClick={() => act.mutate({ id: u.id, action: 'disable' })}>Disable</Button>
            : <Button size="small" variant="outlined" onClick={() => act.mutate({ id: u.id, action: 'enable' })}>Enable</Button>)}
        </Paper>
      ))}
      {act.error && <Typography color="error.main" variant="caption">{(act.error as Error).message}</Typography>}
    </Box>
  );
};

// ── Road closures ───────────────────────────────────────────────
const RoadsPanel: React.FC = () => {
  const qc = useQueryClient();
  const { data: net } = useQuery({ queryKey: ['network'], queryFn: fetchNetwork });
  const [routeId, setRouteId] = React.useState<string | null>(null);
  const [reason, setReason] = React.useState('');
  const refresh = () => qc.invalidateQueries({ queryKey: ['network'] });
  const block = useMutation({ mutationFn: () => adminBlockRoute(routeId!, reason || undefined), onSuccess: () => { setReason(''); refresh(); } });
  const unblock = useMutation({ mutationFn: (id: string) => adminUnblockRoute(id), onSuccess: refresh });
  const routes = net?.routes ?? [];
  const closed = routes.filter((r) => r.status === 'closed');
  return (
    <Box>
      <Typography variant="subtitle1" sx={{ fontWeight: 700, fontFamily: 'var(--font-heading)', mb: 1.5 }}>
        Road closures ({closed.length})
      </Typography>
      <Box sx={{ display: 'flex', gap: 1, mb: 1.5, flexDirection: { xs: 'column', sm: 'row' } }}>
        <Autocomplete size="small" sx={{ flex: 2 }} options={routes.filter((r) => r.status !== 'closed').map((r) => r.route_id)}
          value={routeId} onChange={(_, v) => setRouteId(v)} renderInput={(p) => <TextField {...p} label="Route to block" />} />
        <TextField size="small" sx={{ flex: 1 }} label="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
        <Button variant="contained" color="error" disabled={!routeId || block.isPending} onClick={() => block.mutate()}>Block</Button>
      </Box>
      {closed.map((r) => (
        <Paper key={r.route_id} elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 1.5, p: 1.5, mb: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
          <Box sx={{ flex: 1 }}>
            <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.route_id}</Typography>
            <Typography variant="caption" color="text.secondary">{r.closed_reason}</Typography>
          </Box>
          <Button size="small" variant="outlined" onClick={() => unblock.mutate(r.route_id)}>Unblock</Button>
        </Paper>
      ))}
      {(block.error || unblock.error) && <Typography color="error.main" variant="caption">{((block.error || unblock.error) as Error).message}</Typography>}
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

      <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' }, gap: 3 }}>
        <ModerationQueue />
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
          <UsersPanel />
          <RoadsPanel />
        </Box>
      </Box>
    </Container>
  );
};

export default AdminPage;
