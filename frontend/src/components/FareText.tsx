import React from 'react';
import { Typography } from '@mui/material';
import type { TypographyProps } from '@mui/material/Typography';
import { useTranslation } from 'react-i18next';

interface Props extends TypographyProps {
  amount: number;
  size?: 'sm' | 'md' | 'lg';
  showLabel?: boolean;
}

export const FareText: React.FC<Props> = ({ amount, size = 'md', showLabel = false, sx, ...rest }) => {
  const { t, i18n } = useTranslation();
  const isBn = i18n.language === 'bn';

  const fontSizeMap = { sm: '0.875rem', md: '1rem', lg: '1.25rem' };
  const formatted = isBn
    ? `৳${amount.toLocaleString('bn-BD')}`
    : `৳${amount.toLocaleString('en-BD')}`;

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
