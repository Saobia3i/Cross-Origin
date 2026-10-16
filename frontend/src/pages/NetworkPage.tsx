import React, { useState } from 'react';
import {
  Container, Typography, Box, Tabs, Tab, TextField, InputAdornment,
  Table, TableBody, TableCell, TableHead, TableRow, Paper, Chip,
} from '@mui/material';
import SearchIcon from '@mui/icons-material/Search';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { fetchNetwork } from '../api/endpoints';
import { ModePill } from '../components/ModePill';
import { StatusBadge } from '../components/StatusBadge';
import { ErrorBox } from '../components/Feedback';
import type { Hub, RouteEdge } from '../api/schemas';

const HubsTab: React.FC<{ hubs: Hub[]; q: string }> = ({ hubs, q }) => {
  const filtered = hubs.filter(h => h.name.toLowerCase().includes(q.toLowerCase()));
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>Hub ID</TableCell>
          <TableCell>Name</TableCell>
          <TableCell>Type</TableCell>
          <TableCell>Coordinates</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {filtered.map(h => (
          <TableRow key={h.hub_id} hover>
            <TableCell><code style={{ fontSize: 12 }}>{h.hub_id}</code></TableCell>
            <TableCell sx={{ fontWeight: 500 }}>{h.name}{h.name_bn && <span style={{ marginLeft: 8, color: '#64748B', fontSize: 13 }}>{h.name_bn}</span>}</TableCell>
            <TableCell><Chip label={h.type} size="small" variant="outlined" /></TableCell>
            <TableCell sx={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{h.latitude != null && h.longitude != null ? `${h.latitude.toFixed(4)}, ${h.longitude.toFixed(4)}` : '—'}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

const RoutesTab: React.FC<{ routes: RouteEdge[]; q: string }> = ({ routes, q }) => {
  const { t } = useTranslation();
  const filtered = routes.filter(r =>
    r.origin_hub.toLowerCase().includes(q.toLowerCase()) ||
    r.destination_hub.toLowerCase().includes(q.toLowerCase()) ||
    r.route_id.toLowerCase().includes(q.toLowerCase())
  );
  return (
    <Table size="small">
      <TableHead>
        <TableRow>
          <TableCell>Route ID</TableCell>
          <TableCell>Mode</TableCell>
          <TableCell>From → To</TableCell>
          <TableCell>Distance</TableCell>
          <TableCell>Time</TableCell>
          <TableCell>Student</TableCell>
          <TableCell>Status</TableCell>
        </TableRow>
      </TableHead>
      <TableBody>
        {filtered.map(r => (
          <TableRow key={r.route_id} hover>
            <TableCell><code style={{ fontSize: 11 }}>{r.route_id}</code></TableCell>
            <TableCell><ModePill mode={r.mode} /></TableCell>
            <TableCell>{r.origin_hub} → {r.destination_hub}</TableCell>
            <TableCell sx={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.road_distance_km != null ? `${r.road_distance_km} km` : '—'}</TableCell>
            <TableCell sx={{ fontFamily: 'var(--font-mono)', fontSize: 12 }}>{r.time_normal_min ?? '—'}m</TableCell>
            <TableCell>{r.student_eligible ? '✅' : '—'}</TableCell>
            <TableCell><StatusBadge status={r.status} /></TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
};

const NetworkPage: React.FC = () => {
  const { t } = useTranslation();
  const [tab, setTab] = useState(0);
  const [q, setQ]     = useState('');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['network'],
    queryFn: fetchNetwork,
  });

  const hubs   = data?.hubs   ?? [];
  const routes = data?.routes ?? [];

  return (
    <Container maxWidth="xl" sx={{ py: 3 }}>
      <Typography variant="h4" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, mb: 3 }}>
        🗄 {t('network.title')}
      </Typography>

      <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', mb: 2, gap: 2, flexWrap: 'wrap' }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)}>
          <Tab label={`${t('network.hubs')} (${hubs.length})`} />
          <Tab label={`${t('network.routes')} (${routes.length})`} />
        </Tabs>
        <TextField
          size="small" value={q} onChange={(e) => setQ(e.target.value)}
          placeholder={t('network.search')}
          slotProps={{ input: { startAdornment: <InputAdornment position="start"><SearchIcon fontSize="small" /></InputAdornment> } }}
          sx={{ width: 280 }}
        />
      </Box>

      {error && <ErrorBox message={(error as Error).message} onRetry={refetch} />}
      {isLoading && <Typography color="text.secondary">{t('common.loading')}</Typography>}

      {!isLoading && (
        <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, overflow: 'auto' }}>
          {tab === 0 && <HubsTab hubs={hubs} q={q} />}
          {tab === 1 && <RoutesTab routes={routes} q={q} />}
        </Paper>
      )}
    </Container>
  );
};

export default NetworkPage;
