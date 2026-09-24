// Pure helpers and a glow-sprite cache for the canvas effects layer in PrismMap.
// Everything here is decorative geometry or a direct transform of API values;
// it never invents inventory, risk or ride counts.

export type Point = { x: number; y: number };

/** Comet pulses drawn on each dispatch / OD arc. */
export const PULSES_PER_ROUTE = 4;
/** One building scan sweep crosses the viewport every this many ms. */
export const SCAN_PERIOD_MS = 8000;
/** Ground radius of a station ring, in meters, before selection. */
export const RING_METERS = 70;
/** Inventory particle offset units → meters (matches the old degree scaling). */
export const PARTICLE_METERS_X = 1.6;
export const PARTICLE_METERS_Y = 1.55;

/** Share of docks filled with bikes, or null when either side is unknown. */
export function gaugeRatio(
  inventory: number | null,
  capacity: number | null,
): number | null {
  if (
    inventory === null ||
    capacity === null ||
    !Number.isFinite(inventory) ||
    !Number.isFinite(capacity) ||
    capacity <= 0 ||
    inventory < 0
  )
    return null;
  return Math.min(1, inventory / capacity);
}

/** Points `meters` east and north of `center`, for a local ground basis. */
export function groundAxes(
  center: [number, number],
  meters: number,
): { east: [number, number]; north: [number, number] } {
  const lat = (center[1] * Math.PI) / 180;
  return {
    east: [center[0] + meters / (111320 * Math.cos(lat)), center[1]],
    north: [center[0], center[1] + meters / 110540],
  };
}

/** Size multiplier so marks shrink when zoomed out and grow (bounded) when zoomed in. */
export function zoomScale(zoom: number) {
  return Math.min(1.7, Math.max(0.5, 2 ** ((zoom - 14.8) * 0.5)));
}

/**
 * Brightness of a building vertex under the periodic scan band.
 * `position` is 0 (south-west of the screen) → 1 (north-east).
 */
export function scanIntensity(position: number, time: number) {
  const band = ((time % SCAN_PERIOD_MS) / SCAN_PERIOD_MS) * 1.5 - 0.25;
  const behind = band - position;
  if (behind < -0.035) return 0;
  if (behind < 0) return 1 + behind / 0.035;
  return Math.exp(-behind * 7);
}

/** Angular speed (rad/ms) for a particle orbiting at `radius` offset units. */
export function orbitSpeed(radius: number) {
  return 0.00055 / Math.sqrt(1 + radius / 4);
}

export function smoothstep(edge0: number, edge1: number, value: number) {
  const t = Math.min(1, Math.max(0, (value - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

export function rgba(hex: string, alpha: number) {
  const value = Number.parseInt(hex.slice(1), 16);
  return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${alpha})`;
}

export function cubicPoint(
  a: Point,
  c: Point,
  d: Point,
  b: Point,
  t: number,
): Point {
  const u = 1 - t;
  return {
    x: u ** 3 * a.x + 3 * u * u * t * c.x + 3 * u * t * t * d.x + t ** 3 * b.x,
    y: u ** 3 * a.y + 3 * u * u * t * c.y + 3 * u * t * t * d.y + t ** 3 * b.y,
  };
}

/** Log-scaled stroke width for a route quantity. */
export function routeWidth(quantity: number) {
  return 1 + Math.min(3, Math.log2(1 + Math.max(0, quantity)) * 0.6);
}

type Rect = { x: number; y: number; w: number; h: number };
/** Nudge a label rect up in steps until it clears already placed labels. */
export function placeLabel(rect: Rect, placed: Rect[], step = 26, tries = 4) {
  let candidate = rect;
  for (let i = 0; i < tries; i++) {
    const hit = placed.some(
      (other) =>
        candidate.x < other.x + other.w &&
        candidate.x + candidate.w > other.x &&
        candidate.y < other.y + other.h &&
        candidate.y + candidate.h > other.y,
    );
    if (!hit) break;
    candidate = { ...rect, y: rect.y - step * (i + 1) * (i % 2 ? -1 : 1) };
  }
  placed.push(candidate);
  return candidate;
}

/**
 * Pre-rendered radial glow sprites, bucketed by color and radius, so the
 * frame loop uses drawImage instead of building gradients per dot.
 */
export function createGlowCache(ratio: number) {
  const cache = new Map<string, HTMLCanvasElement>();
  return function glow(color: string, radius: number) {
    const bucket = Math.max(1, Math.round(radius * 2) / 2);
    const key = `${color}:${bucket}`;
    let sprite = cache.get(key);
    if (!sprite) {
      const size = Math.ceil(bucket * 2 * ratio);
      sprite = document.createElement("canvas");
      sprite.width = sprite.height = size;
      const g = sprite.getContext("2d")!;
      const gradient = g.createRadialGradient(
        size / 2,
        size / 2,
        0,
        size / 2,
        size / 2,
        size / 2,
      );
      gradient.addColorStop(0, "rgba(255,255,255,1)");
      gradient.addColorStop(0.14, rgba(color, 1));
      gradient.addColorStop(0.38, rgba(color, 0.32));
      gradient.addColorStop(1, rgba(color, 0));
      g.fillStyle = gradient;
      g.fillRect(0, 0, size, size);
      cache.set(key, sprite);
    }
    return { sprite, radius: bucket };
  };
}
