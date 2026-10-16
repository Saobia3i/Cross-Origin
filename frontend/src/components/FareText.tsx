import React from 'react';
import { Typography } from '@mui/material';
import type { TypographyProps } from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';

interface Props extends TypographyProps {
  amount?: number;
  /** Fare range in BDT. When min !== max it renders as a range (rickshaw/CNG/estimates). */
  min?: number;
  max?: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

export const FareText: React.FC<Props> = ({ amount, min, max, size = 'md', showLabel = false, sx, ...rest }) => {
  const { i18n } = useTranslation();
  const isBn = i18n.language === 'bn';

  const fontSizeMap = { sm: '0.875rem', md: '1rem', lg: '1.25rem' };
  const loc = isBn ? 'bn-BD' : 'en-BD';
  const fmt = (n: number) => Math.round(n).toLocaleString(loc);
  const lo = min ?? amount ?? 0;
  const hi = max ?? amount ?? lo;
  const formatted = Math.round(lo) === Math.round(hi) ? `৳${fmt(lo)}` : `৳${fmt(lo)}–${fmt(hi)}`;

  return (
    <Typography
      component="span"
      sx={{
        fontFamily: "'JetBrains Mono', monospace",
        fontWeight: 600,
        fontSize:   fontSizeMap[size],
        color:      'inherit',
        ...sx,
      }}
      {...rest}
    >
      {formatted}
      {showLabel && (
        <Typography component="span" sx={{ fontSize: '0.75rem', fontFamily: 'inherit', fontWeight: 400, ml: 0.5, color: 'text.secondary' }}>
          BDT
        </Typography>
      )}
    </Typography>
  );
};

export default FareText;
