const riskOrder = new Map([
  ["SHORTAGE_RISK", 0],
  ["OVERFLOW_RISK", 1],
  ["LOW_INVENTORY", 2],
  ["HIGH_INVENTORY", 3],
  ["SERVICE_UNAVAILABLE", 4],
  ["STALE_DATA", 5],
  ["INVALID_DATA", 6],
  ["INSUFFICIENT_DATA", 7],
  ["HEALTHY", 8],
  ["NOT_APPLICABLE", 9],
]);

export function stableHash(value) {
  let hash = 2166136261;
  for (const char of String(value)) {
    hash ^= char.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

export function particleOffsets(stationId, count) {
  if (!Number.isSafeInteger(count) || count < 0) return [];
  const start = (stableHash(stationId) % 360) * (Math.PI / 180);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));
  return Array.from({ length: count }, (_, index) => {
    const radius = index === 0 ? 0 : 7 + Math.sqrt(index) * 3.2;
    const angle = start + index * goldenAngle;
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });
}

export function sortStations(stations, view = "current") {
  const field = view === "forecast" ? "forecast_status" : "current_status";
  return [...stations].sort(
    (a, b) =>
      (riskOrder.get(a[field]) ?? 99) - (riskOrder.get(b[field]) ?? 99) ||
      String(a.station_id).localeCompare(String(b.station_id)),
  );
}

export function isMapExpired(map, now = Date.now()) {
  if (!map?.expires_at_utc) return false;
  const reference =
    map.clock_mode === "recorded" ? Date.parse(map.as_of_utc) : now;
  return reference >= Date.parse(map.expires_at_utc);
}

export function canDrawInventory(station, mapExpired = false) {
  return (
    !mapExpired &&
    Number.isSafeInteger(station.num_bikes_available) &&
    station.num_bikes_available >= 0 &&
    ![
      "SERVICE_UNAVAILABLE",
      "STALE_DATA",
      "INVALID_DATA",
      "INSUFFICIENT_DATA",
      "NOT_APPLICABLE",
    ].includes(station.current_status)
  );
}

export function nextAvailableHour(hours, current, direction = 1) {
  const ordered = [...hours].sort((a, b) => a - b);
  const index = ordered.indexOf(current);
  if (index < 0) return ordered[0] ?? null;
  return ordered[index + direction] ?? null;
}

export function playbackDelay(speed) {
  return 2000 / speed;
}
