import React from 'react';
import { Chip } from '@mui/material';
import { useTranslation } from 'react-i18next';

type Status = 'verified' | 'pending' | 'downvoted' | 'resolved' | 'expired' | 'active' | 'closed';

const STATUS_COLORS: Record<Status, { bg: string; color: string }> = {
  verified:  { bg: '#DCFCE7', color: '#16A34A' },
  pending:   { bg: '#FEF3C7', color: '#B45309' },
  downvoted: { bg: '#F1F5F9', color: '#64748B' },
  resolved:  { bg: '#DBEAFE', color: '#1D4ED8' },
  expired:   { bg: '#F1F5F9', color: '#94A3B8' },
  active:    { bg: '#DCFCE7', color: '#16A34A' },
  closed:    { bg: '#FFE4E6', color: '#E11D48' },
};

interface Props {
  status: Status;
  size?: 'small' | 'medium';
}

export const StatusBadge: React.FC<Props> = ({ status, size = 'small' }) => {
  const { t } = useTranslation();
  const colors = STATUS_COLORS[status] ?? STATUS_COLORS.pending;
  const label = t(`alert.${status}`, { defaultValue: status });

  return (
    <Chip
      label={label}
      size={size}
      sx={{
        backgroundColor: colors.bg,
        color:           colors.color,
        fontWeight:      600,
        fontSize:        '0.6875rem',
        height:          22,
        border:          'none',
        '& .MuiChip-label': { px: 1 },
      }}
    />
  );
};

export default StatusBadge;
