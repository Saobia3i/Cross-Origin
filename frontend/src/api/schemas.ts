import { z } from 'zod';

// ── Hub ──────────────────────────────────────────────────────
export const HubSchema = z.object({
  hub_id:  z.string(),
  name:    z.string(),
  name_bn: z.string().optional(),
  aliases: z.array(z.string()).default([]),
  lat:     z.number(),
  lng:     z.number(),
  type:    z.string(),
});
export type Hub = z.infer<typeof HubSchema>;

// ── Route (edge) ─────────────────────────────────────────────
export const RouteEdgeSchema = z.object({
  route_id:            z.string(),
  mode:                z.enum(['bus', 'mrt', 'rickshaw', 'leguna', 'cng', 'walk']),
  origin_hub:          z.string(),
  destination_hub:     z.string(),
  base_fare_bdt:       z.number(),
  student_eligible:    z.boolean(),
  estimated_time_min:  z.number(),
  status:              z.enum(['active', 'closed']),
  closed_reason:       z.string().optional(),
  boarding_notes:      z.array(z.string()).default([]),
  alighting_notes:     z.array(z.string()).default([]),
  geometry:            z.unknown().optional(),
});
export type RouteEdge = z.infer<typeof RouteEdgeSchema>;

// ── Alert ─────────────────────────────────────────────────────
export const AlertSchema = z.object({
  alert_id:          z.string(),
  type:              z.string(),
  severity:          z.enum(['low', 'medium', 'high', 'critical']),
  affected_hub:      z.string(),
  affected_route_ids:z.array(z.string()).default([]),
  description:       z.string(),
  reported_by:       z.number().optional(),
  incident_time:     z.string(),
  created_at:        z.string(),
  last_verified_at:  z.string().optional(),
  upvotes:           z.number().default(0),
  downvotes:         z.number().default(0),
  my_vote:           z.number().nullable().default(null),
  confidence:        z.number().min(0).max(1).default(1),
  status:            z.enum(['pending', 'verified', 'downvoted', 'resolved', 'expired']),
  edge_closed:       z.boolean().default(false),
  extra_time_min:    z.number().default(0),
  images:            z.array(z.string()).default([]),
  comments_count:    z.number().default(0),
});
export type Alert = z.infer<typeof AlertSchema>;

// ── Trip search ───────────────────────────────────────────────
export const RouteLegSchema = z.object({
  mode:         z.enum(['bus', 'mrt', 'rickshaw', 'leguna', 'cng', 'walk']),
  from:         z.string(),
  to:           z.string(),
  fare:         z.number(),
  student_fare: z.number().optional(),
  duration_min: z.number(),
  boarding:     z.string().optional(),
  alighting:    z.string().optional(),
  route_id:     z.string().optional(),
});
export type RouteLeg = z.infer<typeof RouteLegSchema>;

export const RouteOptionSchema = z.object({
  id:             z.string(),
  legs:           z.array(RouteLegSchema),
  total_fare:     z.number(),
  total_duration: z.number(),
  transfers:      z.number(),
  within_budget:  z.boolean(),
  disrupted:      z.boolean(),
  grounding_ok:   z.boolean(),
});
export type RouteOption = z.infer<typeof RouteOptionSchema>;

// ── Fare estimate ─────────────────────────────────────────────
export const FareEstimateSchema = z.object({
  mode:          z.string(),
  baseline:      z.number(),
  rush:          z.number().optional(),
  rain:          z.number().optional(),
  student_fare:  z.number().optional(),
  student_discount_applies: z.boolean(),
});
export type FareEstimate = z.infer<typeof FareEstimateSchema>;

// ── Chat ──────────────────────────────────────────────────────
export const ChatResponseSchema = z.object({
  session_id:  z.string(),
  reply:       z.string(),
  intent:      z.string().optional(),
  grounded:    z.boolean().default(false),
  payload:     z.unknown().optional(),
  ui_blocks:   z.array(z.unknown()).default([]),
  suggestions: z.array(z.string()).default([]),
});
export type ChatResponse = z.infer<typeof ChatResponseSchema>;

// ── Auth ──────────────────────────────────────────────────────
export const UserSchema = z.object({
  id:    z.number(),
  name:  z.string(),
  email: z.string().email(),
  role:  z.enum(['user', 'admin', 'superadmin']),
  phone: z.string().optional(),
});
export type UserType = z.infer<typeof UserSchema>;

export const AuthResponseSchema = z.object({
  user:  UserSchema,
  token: z.string(),
});
