import React, { useState } from 'react';
import {
  Alert, Box, Button, Card, CardContent, Chip, Container, Divider, Grid, MenuItem, Stack,
  TextField, Typography,
} from '@mui/material';
import UploadFileIcon from '@mui/icons-material/UploadFile';
import GroupsIcon from '@mui/icons-material/Groups';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  createRideSharePost, digitizeSignboard, fetchRideSharePosts, joinRideSharePost, leaveRideSharePost,
  type DigitizedRouteDraft, type RideShareInput, type RideSharePost,
} from '../api/endpoints';
import { ErrorBox } from '../components/Feedback';
import { useAuthStore } from '../store/auth';

const RideCard: React.FC<{ post: RideSharePost; onJoin: () => void; onLeave: () => void; busy: boolean }> = ({ post, onJoin, onLeave, busy }) => {
  const price = post.suggested_safe_per_person_bdt == null ? 'Fare estimate unavailable' : `৳${post.suggested_safe_per_person_bdt} each`;
  return <Card variant="outlined" sx={{ height: '100%' }}>
    <CardContent>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Chip size="small" color={post.mode === 'cng' ? 'success' : 'warning'} label={post.mode.toUpperCase()} />
        <Typography variant="caption" color="text.secondary">{post.seats_remaining} seat{post.seats_remaining === 1 ? '' : 's'} free</Typography>
      </Stack>
      <Typography sx={{ fontWeight: 700 }}>{post.origin_hub || post.hub_id} → {post.destination_hub || 'nearby'}</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{post.notes || `Posted by ${post.created_by.name}`}</Typography>
      <Box sx={{ mt: 2, p: 1.25, bgcolor: 'action.hover', borderRadius: 1 }}>
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center' }}><SmartToyIcon color="primary" fontSize="small" /><Typography variant="body2" sx={{ fontWeight: 700 }}>{price}</Typography></Stack>
        <Typography variant="caption" color="text.secondary">Bot splits the estimated vehicle fare equally among {post.seats_taken} riders.</Typography>
      </Box>
      <Button fullWidth sx={{ mt: 1.5 }} variant={post.my_participation ? 'outlined' : 'contained'} disabled={busy || (!post.my_participation && post.seats_remaining === 0)} onClick={post.my_participation ? onLeave : onJoin}>
        {post.my_participation ? 'Leave ride' : post.seats_remaining === 0 ? 'Full' : 'Join & split fare'}
      </Button>
    </CardContent>
  </Card>;
};

const CommunityPage: React.FC = () => {
  const client = useQueryClient();
  const user = useAuthStore(s => s.user);
  const [file, setFile] = useState<File | null>(null);
  const [draft, setDraft] = useState<DigitizedRouteDraft | null>(null);
  const [hub, setHub] = useState('');
  const [form, setForm] = useState<RideShareInput>({ mode: 'cng', origin_hub: '', destination_hub: '', seats_total: 3, notes: '', expires_in_minutes: 60 });
  const posts = useQuery({ queryKey: ['rideshare', hub], queryFn: () => fetchRideSharePosts(hub || undefined) });
  const refresh = () => client.invalidateQueries({ queryKey: ['rideshare'] });
  const digitize = useMutation({ mutationFn: digitizeSignboard, onSuccess: setDraft });
  const create = useMutation({ mutationFn: createRideSharePost, onSuccess: refresh });
  const join = useMutation({ mutationFn: joinRideSharePost, onSuccess: refresh });
  const leave = useMutation({ mutationFn: leaveRideSharePost, onSuccess: refresh });
  const error = digitize.error || create.error || join.error || leave.error || posts.error;

  const setField = <K extends keyof RideShareInput>(key: K, value: RideShareInput[K]) => setForm(current => ({ ...current, [key]: value }));
  const submitPost = (event: React.FormEvent) => {
    event.preventDefault();
    create.mutate(form);
  };

  return <Container maxWidth="lg" sx={{ py: 3 }}>
    <Box sx={{ mb: 3 }}>
      <Typography variant="h4" sx={{ fontWeight: 700 }}>Community tools</Typography>
      <Typography color="text.secondary">Turn local transit notices into structured route drafts and coordinate shared last-mile rides.</Typography>
    </Box>
    {error && <ErrorBox message={(error as Error).message} />}
    <Grid container spacing={3}>
      <Grid size={{ xs: 12, md: 5 }}>
        <Card variant="outlined"><CardContent>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>📷 Signboard & notice digitization</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>Upload a bus signboard, printed fare chart, or handwritten route note. The AI returns a structured draft for review.</Typography>
          <Button component="label" startIcon={<UploadFileIcon />} variant="outlined" sx={{ mt: 2 }}>
            Choose photo<input hidden type="file" accept="image/jpeg,image/png,image/webp" onChange={e => { setFile(e.target.files?.[0] || null); setDraft(null); }} />
          </Button>
          {file && <Typography variant="caption" sx={{ mt: 1, display: 'block' }}>{file.name} · {(file.size / 1024 / 1024).toFixed(1)} MB</Typography>}
          <Button fullWidth variant="contained" disabled={!file || digitize.isPending} onClick={() => file && digitize.mutate(file)} sx={{ mt: 2 }}>
            {digitize.isPending ? 'Reading photo…' : 'Extract route data'}
          </Button>
          {draft && <Box sx={{ mt: 2 }}>
            <Divider sx={{ mb: 1.5 }} />
            <Stack direction="row" sx={{ justifyContent: 'space-between' }}><Typography sx={{ fontWeight: 700 }}>Review draft</Typography><Chip size="small" label={`${draft.confidence}% confidence`} color={draft.confidence >= 70 ? 'success' : 'warning'} /></Stack>
            <Typography variant="body2" sx={{ mt: 1 }}><b>{draft.route_name || 'Unnamed route'}</b> · {draft.mode}</Typography>
            <Typography variant="body2">{draft.origin || 'Unknown'} → {draft.destination || 'Unknown'}</Typography>
            {!!draft.stops.length && <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>Stops: {draft.stops.join(' → ')}</Typography>}
            {!!draft.fare_rows.length && <Typography variant="body2" color="text.secondary">Fare entries: {draft.fare_rows.length}</Typography>}
            <Alert severity="info" sx={{ mt: 1.5 }}>{draft.review_note}</Alert>
          </Box>}
        </CardContent></Card>
      </Grid>
      <Grid size={{ xs: 12, md: 7 }}>
        <Card variant="outlined"><CardContent>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center' }}><GroupsIcon color="primary" /><Typography variant="h6" sx={{ fontWeight: 700 }}>Hub ride-share board</Typography></Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: .5 }}>Find nearby commuters for a rickshaw or CNG; the fare bot recalculates everyone’s equal share as people join.</Typography>
          <TextField label="Filter by hub" size="small" fullWidth value={hub} onChange={e => setHub(e.target.value)} sx={{ mt: 2 }} placeholder="e.g. Mirpur 10" />
          <Box component="form" onSubmit={submitPost} sx={{ mt: 2, display: 'grid', gap: 1 }}>
            <Typography variant="subtitle2">Post a ride {user?.is_verified ? '' : '(verified login required)'}</Typography>
            <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1}>
              <TextField select label="Vehicle" value={form.mode} onChange={e => setField('mode', e.target.value)} fullWidth><MenuItem value="cng">CNG</MenuItem><MenuItem value="rickshaw">Rickshaw</MenuItem></TextField>
              <TextField type="number" label="Total seats" value={form.seats_total} slotProps={{ htmlInput: { min: 2, max: 8 } }} onChange={e => setField('seats_total', Number(e.target.value))} fullWidth />
            </Stack>
            <TextField required label="Starting hub" value={form.origin_hub} onChange={e => setField('origin_hub', e.target.value)} />
            <TextField label="Destination hub" value={form.destination_hub} onChange={e => setField('destination_hub', e.target.value)} />
            <TextField label="Note (optional)" value={form.notes} onChange={e => setField('notes', e.target.value)} />
            <Button type="submit" variant="contained" disabled={create.isPending || !user?.is_verified}>Post to board</Button>
          </Box>
        </CardContent></Card>
      </Grid>
      <Grid size={{ xs: 12 }}><Typography variant="h6" sx={{ fontWeight: 700, mb: 1.5 }}>Open rides {posts.data ? `(${posts.data.count})` : ''}</Typography>
        <Grid container spacing={2}>{posts.data?.posts.map(post => <Grid key={post.post_id} size={{ xs: 12, sm: 6, md: 4 }}><RideCard post={post} busy={join.isPending || leave.isPending} onJoin={() => join.mutate(post.post_id)} onLeave={() => leave.mutate(post.post_id)} /></Grid>)}</Grid>
        {!posts.isLoading && posts.data?.posts.length === 0 && <Alert severity="info">No active ride shares here yet. Be the first to post one.</Alert>}
      </Grid>
    </Grid>
  </Container>;
};

export default CommunityPage;
