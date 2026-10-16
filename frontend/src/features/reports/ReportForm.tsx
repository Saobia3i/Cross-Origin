import React, { useState, useRef } from 'react';
import {
  Box, Typography, Paper, TextField, Button, MenuItem,
  Chip, IconButton, Grid,
} from '@mui/material';
import CloudUploadIcon from '@mui/icons-material/CloudUpload';
import CloseIcon from '@mui/icons-material/Close';
import ReportIcon from '@mui/icons-material/Report';
import { useForm, Controller } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import { useTranslation } from 'react-i18next';
import { createReport } from '../../api/endpoints';
import { useAuthStore } from '../../store/auth';
import { useNavigate } from 'react-router-dom';

const schema = z.object({
  hub:         z.string().min(1, 'Hub is required'),
  type:        z.string().min(1, 'Type is required'),
  severity:    z.enum(['low', 'medium', 'high', 'critical']),
  description: z.string().min(10, 'At least 10 characters'),
  incident_time: z.string().min(1, 'Incident time required'),
});

type FormData = z.infer<typeof schema>;

const HUBS = [
  'Motijheel', 'Banasree', 'Malibagh', 'Shahbagh', 'Farmgate',
  'Science Lab', 'Dhanmondi', 'Mohammadpur', 'Mirpur 10', 'Agargaon',
  'Mohakhali', 'Badda', 'Uttara', 'Jatrabari', 'Kazipara',
];

const ALERT_TYPES = [
  'Waterlogging', 'Gridlock', 'Road closure', 'Construction',
  'Accident', 'Public event', 'Other',
];

const ReportForm: React.FC = () => {
  const { t } = useTranslation();
  const { user } = useAuthStore();
  const navigate = useNavigate();
  const [images, setImages] = useState<File[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const { control, handleSubmit, formState: { errors } } = useForm<FormData>({
    resolver: zodResolver(schema),
    defaultValues: { severity: 'medium', incident_time: new Date().toISOString().slice(0, 16) },
  });

  if (!user) {
    return (
      <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 3, textAlign: 'center' }}>
        <Typography variant="h6" gutterBottom>Login required</Typography>
        <Button variant="contained" onClick={() => navigate('/login')}>{t('auth.login')}</Button>
      </Paper>
    );
  }

  const onSubmit = async (data: FormData) => {
    if (images.length === 0) { alert('At least 1 image required'); return; }
    setSubmitting(true);
    try {
      const form = new FormData();
      Object.entries(data).forEach(([k, v]) => form.append(k, v as string));
      images.forEach(img => form.append('images', img));
      await createReport(form);
      setSuccess(true);
    } catch (e) {
      console.error(e);
    } finally {
      setSubmitting(false);
    }
  };

  const handleFileAdd = (files: FileList | null) => {
    if (!files) return;
    const valid = Array.from(files).filter(f => f.type.startsWith('image/') && f.size < 5 * 1024 * 1024);
    setImages(prev => [...prev, ...valid].slice(0, 5));
  };

  if (success) return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 4, textAlign: 'center' }}>
      <Typography sx={{ fontSize: 48, mb: 2 }}>✅</Typography>
      <Typography variant="h6" gutterBottom>Report submitted!</Typography>
      <Typography variant="body2" color="text.secondary" gutterBottom>It will be verified by the community and admins.</Typography>
      <Button variant="contained" sx={{ mt: 2 }} onClick={() => { setSuccess(false); navigate('/alerts'); }}>
        View alerts
      </Button>
    </Paper>
  );

  return (
    <Paper elevation={0} sx={{ border: '1px solid', borderColor: 'divider', borderRadius: 2, p: 2.5 }}>
      <Typography variant="h6" sx={{ fontFamily: 'var(--font-heading)', fontWeight: 700, mb: 2.5, display: 'flex', alignItems: 'center', gap: 1 }}>
        <ReportIcon sx={{ color: 'error.main' }} /> {t('alert.report')}
      </Typography>

      <Box component="form" onSubmit={handleSubmit(onSubmit)} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Controller name="hub" control={control} render={({ field }) => (
          <TextField {...field} select label="Hub / Location" size="small" error={!!errors.hub} helperText={errors.hub?.message}>
            {HUBS.map(h => <MenuItem key={h} value={h}>{h}</MenuItem>)}
          </TextField>
        )} />

        <Box sx={{ display: 'flex', gap: 1.5 }}>
          <Controller name="type" control={control} render={({ field }) => (
            <TextField {...field} select label="Type" size="small" error={!!errors.type} helperText={errors.type?.message} sx={{ flex: 1 }}>
              {ALERT_TYPES.map(a => <MenuItem key={a} value={a}>{a}</MenuItem>)}
            </TextField>
          )} />
          <Controller name="severity" control={control} render={({ field }) => (
            <TextField {...field} select label="Severity" size="small" sx={{ flex: 1 }}>
              {['low', 'medium', 'high', 'critical'].map(s => <MenuItem key={s} value={s} sx={{ textTransform: 'capitalize' }}>{s}</MenuItem>)}
            </TextField>
          )} />
        </Box>

        <Controller name="incident_time" control={control} render={({ field }) => (
          <TextField {...field} label="Incident time" size="small" type="datetime-local"
            error={!!errors.incident_time} helperText={errors.incident_time?.message}
            slotProps={{ inputLabel: { shrink: true } }}
          />
        )} />

        <Controller name="description" control={control} render={({ field }) => (
          <TextField {...field} label="Description" size="small" multiline rows={3}
            error={!!errors.description} helperText={errors.description?.message}
            placeholder="Describe the situation clearly…"
          />
        )} />

        {/* Image upload */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ mb: 1, display: 'block' }}>
            Photos (1–5, max 5MB each) *
          </Typography>
          <Box
            onClick={() => fileRef.current?.click()}
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => { e.preventDefault(); handleFileAdd(e.dataTransfer.files); }}
            sx={{
              border: '2px dashed', borderColor: 'divider', borderRadius: 1.5,
              p: 2.5, textAlign: 'center', cursor: 'pointer',
              '&:hover': { borderColor: 'primary.main', backgroundColor: 'primary.light' },
              transition: 'var(--tr)',
            }}
          >
            <CloudUploadIcon sx={{ color: 'text.muted', mb: 0.5 }} />
            <Typography variant="body2" color="text.secondary">Click or drag photos here</Typography>
          </Box>
          <input ref={fileRef} type="file" accept="image/*" multiple hidden
            onChange={(e) => handleFileAdd(e.target.files)} />

          {images.length > 0 && (
            <Box sx={{ display: 'flex', gap: 1, mt: 1.5, flexWrap: 'wrap' }}>
              {images.map((img, i) => (
                <Box key={i} sx={{ position: 'relative' }}>
                  <Box component="img" src={URL.createObjectURL(img)} alt=""
                    sx={{ width: 72, height: 60, objectFit: 'cover', borderRadius: 1 }} />
                  <IconButton size="small"
                    onClick={() => setImages(prev => prev.filter((_, j) => j !== i))}
                    sx={{ position: 'absolute', top: -6, right: -6, width: 18, height: 18, backgroundColor: 'error.main', color: '#fff', '&:hover': { backgroundColor: 'error.dark' } }}>
                    <CloseIcon sx={{ fontSize: 10 }} />
                  </IconButton>
                </Box>
              ))}
            </Box>
          )}
        </Box>

        <Button type="submit" variant="contained" color="error" disabled={submitting} sx={{ height: 44 }}>
          {submitting ? 'Submitting…' : t('alert.report')}
        </Button>
      </Box>
    </Paper>
  );
};

export default ReportForm;
