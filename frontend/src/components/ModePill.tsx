import React from 'react';
import { Box, Typography } from '@mui/material';
import { useTranslation } from 'react-i18next';

type Mode = string;

const MODE_STYLE: Record<string, { bg: string; color: string; emoji: string }> = {
  bus:      { bg: '#DBEAFE', color: '#1D4ED8', emoji: '🚌' },
  local_bus:{ bg: '#DBEAFE', color: '#1D4ED8', emoji: '🚌' },
  car:      { bg: '#E0E7FF', color: '#3730A3', emoji: '🚗' },
  pathao_car: { bg: '#E0E7FF', color: '#3730A3', emoji: '🚗' },
  uber_car: { bg: '#E0E7FF', color: '#3730A3', emoji: '🚗' },
  motorbike:{ bg: '#FCE7F3', color: '#9D174D', emoji: '🏍️' },
  pathao_bike: { bg: '#FCE7F3', color: '#9D174D', emoji: '🏍️' },
  uber_moto:{ bg: '#FCE7F3', color: '#9D174D', emoji: '🏍️' },
  mrt:      { bg: '#EDE9FE', color: '#6D28D9', emoji: '🚇' },
  rickshaw: { bg: '#FEF3C7', color: '#B45309', emoji: '🛺' },
  leguna:   { bg: '#CCFBF1', color: '#0D9488', emoji: '🚐' },
  cng:      { bg: '#DCFCE7', color: '#16A34A', emoji: '🛺' },
  walk:     { bg: '#F1F5F9', color: '#475569', emoji: '🚶' },
};

interface Props {
  mode: Mode;
  label?: string;
  size?: 'sm' | 'md';
}

export const ModePill: React.FC<Props> = ({ mode, label, size = 'sm' }) => {
  const { t } = useTranslation();
  const style = MODE_STYLE[mode] ?? MODE_STYLE.bus;
  const text  = label ?? t(`mode.${mode}`, { defaultValue: mode.replace(/_/g, ' ') });
  const isSmall = size === 'sm';

  return (
    <Box
      component="span"
      sx={{
        display:        'inline-flex',
        alignItems:     'center',
        gap:            0.5,
        px:             isSmall ? 1 : 1.5,
        py:             isSmall ? 0.25 : 0.5,
        borderRadius:   9999,
        backgroundColor: style.bg,
        color:          style.color,
        fontSize:       isSmall ? '0.6875rem' : '0.8125rem',
        fontWeight:     600,
        whiteSpace:     'nowrap',
        lineHeight:     1.4,
        userSelect:     'none',
      }}
    >
      <span>{style.emoji}</span>
      <Typography component="span" sx={{ fontSize: 'inherit', fontWeight: 'inherit', color: 'inherit', lineHeight: 'inherit' }}>
        {text}
      </Typography>
    </Box>
  );
};

export default ModePill;
