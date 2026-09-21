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

export function sortStations(stations, view = "current", resolveStatus) {
  const field = view === "current" ? "current_status" : "forecast_status";
  const statusOf = resolveStatus ?? ((station) => station[field]);
  return [...stations].sort(
    (a, b) =>
      (riskOrder.get(statusOf(a)) ?? 99) - (riskOrder.get(statusOf(b)) ?? 99) ||
      String(a.station_id).localeCompare(String(b.station_id)),
  );
}

export function isTraversalStatus(status, mode = "live") {
  return mode === "replay" || status !== "HEALTHY";
}

export function sameReplaySelection(map, selection) {
  return Boolean(
    map?.mode === "replay" &&
    selection &&
    map.service_date === selection.serviceDate &&
    map.hour === selection.hour,
  );
}

export function isExpiredAt(map, expiresAt, elapsedMs = 0) {
  if (!map || !expiresAt) return false;
  const reference =
    Date.parse(map.as_of_utc) + (map.clock_mode === "recorded" ? 0 : Math.max(0, elapsedMs));
  return reference >= Date.parse(expiresAt);
}

export function isMapExpired(map, elapsedMs = 0) {
  return isExpiredAt(map, map?.expires_at_utc, elapsedMs);
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

export function canDrawForecast(station, mapExpired = false) {
  return (
    canDrawInventory(station, mapExpired) &&
    Number.isFinite(station.projected_bikes_1h) &&
    !["SERVICE_UNAVAILABLE", "STALE_DATA", "INVALID_DATA", "INSUFFICIENT_DATA"].includes(station.forecast_status)
  );
}

export function canDispatchStation(station, mapExpired = false) {
  return (
    canDrawForecast(station, mapExpired) &&
    Number.isSafeInteger(station.capacity) &&
    station.capacity > 0 &&
    Number.isFinite(station.lat) &&
    Number.isFinite(station.lon)
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

export function matchesRisk(status, filter = "all") {
  if (filter === "all") return true;
  if (filter === "shortage") return ["SHORTAGE_RISK", "LOW_INVENTORY"].includes(status);
  if (filter === "full") return ["OVERFLOW_RISK", "HIGH_INVENTORY"].includes(status);
  return !["SHORTAGE_RISK", "LOW_INVENTORY", "OVERFLOW_RISK", "HIGH_INVENTORY", "HEALTHY", "NOT_APPLICABLE"].includes(status);
}

// Cancellation also invalidates completed/cached requests and failures awaiting metadata.
export function latestRequestGate() {
  let controller;
  return {
    cancel() {
      controller?.abort();
    },
    start() {
      controller?.abort();
      const current = new AbortController();
      controller = current;
      return {
        signal: current.signal,
        isCurrent: () => controller === current && !current.signal.aborted,
      };
    },
  };
}

export function availableSelection(dates, serviceDate) {
  const date = dates.find((entry) => entry.service_date === serviceDate);
  return date?.hours.length ? { serviceDate, hour: Math.min(...date.hours) } : null;
}

export function hourAtIndex(hours, index) {
  return hours[Math.max(0, Math.min(hours.length - 1, index))] ?? null;
}
