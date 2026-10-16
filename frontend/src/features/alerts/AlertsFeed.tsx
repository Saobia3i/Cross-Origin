import React, { useState } from 'react';
import {
  Box, Typography, Paper, Chip, Button, TextField, Divider,
  LinearProgress, Avatar, IconButton,
} from '@mui/material';
import ThumbUp from '@mui/icons-material/ThumbUp';
import ThumbDown from '@mui/icons-material/ThumbDown';
import ChatBubbleOutlineOutlinedIcon from '@mui/icons-material/ChatBubbleOutlineOutlined';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchAlerts, voteAlert, retractVote } from '../../api/endpoints';
import { StatusBadge } from '../../components/StatusBadge';
import { EmptyState, ErrorBox } from '../../components/Feedback';
import type { Alert } from '../../api/schemas';
import { useAuthStore } from '../../store/auth';
import { useNavigate } from 'react-router-dom';

const SEVERITY_COLOR: Record<string, string> = {
  low: '#94A3B8', medium: '#F59E0B', high: '#F97316', critical: '#E11D48',
};

const AlertCard: React.FC<{ alert: Alert }> = ({ alert }) => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [expanded, setExpanded] = useState(false);

  const voteMut = useMutation({
    mutationFn: ({ v }: { v: 1 | -1 }) => voteAlert(alert.alert_id, v),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });
  const retractMut = useMutation({
    mutationFn: () => retractVote(alert.alert_id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  });

  const handleVote = (v: 1 | -1) => {
    if (!user) { navigate('/login'); return; }
    if (alert.my_vote === v) retractMut.mutate();
    else voteMut.mutate({ v });
  };

  const timeSince = (dateStr: string) => {
    const mins = Math.floor((Date.now() - new Date(dateStr).getTime()) / 60000);
    if (mins < 60) return `${mins}m`;
    return `${Math.floor(mins / 60)}h`;
  };

  return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, overflow: 'hidden' }}>
      {/* Severity strip */}
      <Box sx={{ height: 3, backgroundColor: SEVERITY_COLOR[alert.severity] }} />

      <Box sx={{ p: 2 }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Box>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
              <StatusBadge status={alert.status} />
              <Chip label={alert.affected_hub} size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />
              {alert.edge_closed && (
                <Chip label="Road closed" size="small" sx={{ height: 20, fontSize: '0.65rem', backgroundColor: '#FFE4E6', color: '#E11D48' }} />
              )}
            </Box>
            <Typography variant="body2" fontWeight={500} sx={{ lineHeight: 1.4 }}>
              {alert.description}
            </Typography>
          </Box>
          <Typography variant="caption" color="text.muted" sx={{ flexShrink: 0, ml: 1 }}>
            {timeSince(alert.incident_time)}
          </Typography>
        </Box>

        {/* Confidence bar */}
        <Box sx={{ mb: 1.5 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 0.5 }}>
            <Typography variant="caption" color="text.secondary">{t('alert.confidence')}</Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontFamily: 'var(--font-mono)' }}>
              {Math.round(alert.confidence * 100)}%
            </Typography>
          </Box>
          <LinearProgress
            variant="determinate"
            value={alert.confidence * 100}
            sx={{
              '& .MuiLinearProgress-bar': {
                backgroundColor: alert.confidence > 0.6 ? '#16A34A' : alert.confidence > 0.3 ? '#F59E0B' : '#E11D48',
              }
            }}
          />
          {alert.last_verified_at && (
            <Typography variant="caption" color="text.muted" sx={{ mt: 0.25, display: 'block' }}>
              {t('alert.lastVerified', { time: timeSince(alert.last_verified_at) })}
            </Typography>
          )}
        </Box>

        {/* Images preview */}
        {alert.images.length > 0 && (
          <Box sx={{ display: 'flex', gap: 1, mb: 1.5, flexWrap: 'wrap' }}>
            {alert.images.slice(0, 3).map((img, i) => (
              <Box key={i} component="img" src={img} alt={`Alert image ${i + 1}`}
                sx={{ width: 72, height: 56, objectFit: 'cover', borderRadius: 1, border: '1px solid', borderColor: 'divider' }}
              />
            ))}
            {alert.images.length > 3 && (
              <Box sx={{ width: 72, height: 56, borderRadius: 1, border: '1px solid', borderColor: 'divider', display: 'flex', alignItems: 'center', justifyContent: 'center', backgroundColor: 'background.default' }}>
                <Typography variant="caption" color="text.secondary">+{alert.images.length - 3}</Typography>
              </Box>
            )}
          </Box>
        )}

        {/* Actions */}
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Button
            size="small" startIcon={<ThumbUp sx={{ fontSize: '14px !important' }} />}
            onClick={() => handleVote(1)}
            sx={{
              color: alert.my_vote === 1 ? 'success.main' : 'text.secondary',
              backgroundColor: alert.my_vote === 1 ? '#DCFCE7' : 'transparent',
              minWidth: 0, px: 1,
              '&:hover': { backgroundColor: '#DCFCE7', color: 'success.main' },
            }}
          >
            {alert.upvotes}
          </Button>
          <Button
            size="small" startIcon={<ThumbDown sx={{ fontSize: '14px !important' }} />}
            onClick={() => handleVote(-1)}
            sx={{
              color: alert.my_vote === -1 ? 'error.main' : 'text.secondary',
              backgroundColor: alert.my_vote === -1 ? '#FFE4E6' : 'transparent',
              minWidth: 0, px: 1,
              '&:hover': { backgroundColor: '#FFE4E6', color: 'error.main' },
            }}
          >
            {alert.downvotes}
          </Button>
          <Button
            size="small" startIcon={<ChatBubbleOutlineOutlinedIcon sx={{ fontSize: '14px !important' }} />}
            onClick={() => setExpanded(!expanded)}
            sx={{ color: 'text.secondary', minWidth: 0, px: 1, '&:hover': { color: 'text.primary' } }}
          >
            {alert.comments_count}
          </Button>
        </Box>
      </Box>
    </Paper>
  );
};

const AlertsFeed: React.FC = () => {
  const { t } = useTranslation();
  const [statusFilter, setStatusFilter] = useState<string>('all');

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ['alerts'],
    queryFn: fetchAlerts,
    refetchInterval: 12_000, // poll every 12s
  });

  const alerts = (data as Alert[] | undefined) ?? [];
  const filtered = statusFilter === 'all' ? alerts : alerts.filter(a => a.status === statusFilter);

  return (
    <Box>
      {/* Filter chips */}
      <Box sx={{ display: 'flex', gap: 1, mb: 2, flexWrap: 'wrap' }}>
        {['all', 'verified', 'pending', 'resolved'].map((s) => (
          <Chip
            key={s} label={s === 'all' ? 'All' : t(`alert.${s}`)}
            size="small" variant={statusFilter === s ? 'filled' : 'outlined'}
            color={statusFilter === s ? 'primary' : 'default'}
            onClick={() => setStatusFilter(s)}
          />
        ))}
      </Box>

      {isLoading && <Typography color="text.secondary">{t('common.loading')}</Typography>}
      {error && <ErrorBox message={(error as Error).message} onRetry={refetch} />}
      {!isLoading && filtered.length === 0 && (
        <EmptyState icon="🔔" title={t('alert.empty')} />
      )}

      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5 }}>
        {filtered.map((alert) => (
          <AlertCard key={alert.alert_id} alert={alert} />
        ))}
      </Box>
    </Box>
  );
};

export default AlertsFeed;
