import type { MapResponse, Suggestion, View } from "./domain";
import {
  canDrawInventory,
  isExpiredAt,
  isMapExpired,
  sameReplaySelection,
} from "./model.mjs";

export type SceneStation = {
  id: string;
  name: string;
  coordinate: [number, number] | null;
  status: string;
  inventory: number | null;
};
export type SceneRoute = {
  id: string;
  from: string;
  to: string;
  quantity: number;
  label: string;
  top: boolean;
};
export type Scene =
  | {
      kind: "live";
      stations: Map<string, SceneStation>;
      dispatches: SceneRoute[];
    }
  | {
      kind: "replay";
      stations: Map<
        string,
        Omit<SceneStation, "inventory"> & { inventory: null }
      >;
      flows: SceneRoute[];
    };

export function buildScene(
  response: MapResponse | null,
  view: View,
  elapsed: number,
  suggestions: Suggestion[],
): Scene | null {
  if (!response) return null;
  const stations = new Map<string, SceneStation>();
  for (const station of response.stations) {
    const expired =
      isMapExpired(response, elapsed) ||
      isExpiredAt(response, station.expires_at_utc, elapsed);
    stations.set(station.station_id, {
      id: station.station_id,
      name: station.station_name || station.station_id,
      coordinate:
        station.lon !== null && station.lat !== null
          ? [station.lon, station.lat]
          : null,
      status:
        response.mode === "replay"
          ? "NOT_APPLICABLE"
          : expired
            ? "STALE_DATA"
            : view === "current"
              ? station.current_status
              : station.forecast_status,
      inventory:
        response.mode === "live" && canDrawInventory(station, expired)
          ? station.num_bikes_available
          : null,
    });
  }
  if (response.mode === "replay")
    return {
      kind: "replay",
      stations: new Map(
        [...stations].map(([id, station]) => [
          id,
          { ...station, inventory: null } as const,
        ]),
      ),
      flows: response.flows
        .filter((flow) => flow.ride_count > 0)
        .map((flow) => ({
          id: `${flow.from_station_id}:${flow.to_station_id}`,
          from: flow.from_station_id,
          to: flow.to_station_id,
          quantity: flow.ride_count,
          label: `${flow.ride_count} 次`,
          top: true,
        })),
    };
  return {
    kind: "live",
    stations,
    dispatches:
      view === "dispatch"
        ? suggestions.map((suggestion) => ({
            id: suggestion.suggestion_id,
            from: suggestion.from_station_id,
            to: suggestion.to_station_id,
            quantity: suggestion.move_bikes,
            label: `${suggestion.move_bikes} 辆 · ${suggestion.distance_meters} m`,
            top: suggestion.priority <= 5,
          }))
        : [],
  };
}

export function replayReady(
  response: MapResponse | null,
  selection: { serviceDate: string; hour: number },
  pending: boolean,
) {
  return (
    !pending && Boolean(response && sameReplaySelection(response, selection))
  );
}

export type RiskFilter = "all" | "shortage" | "full" | "issue";
export type FlowFilter = "top" | "all" | "selected" | "hidden";

export function visibleRoutes(
  scene: Scene | null,
  filter: FlowFilter,
  selectedStationId: string | null,
) {
  if (!scene) return [];
  if (scene.kind === "live") return scene.dispatches;
  if (filter === "top") {
    return [...scene.flows].sort((a, b) => b.quantity - a.quantity || a.id.localeCompare(b.id)).slice(0, 20);
  }
  return scene.flows.filter((route) =>
    filter === "all" || (filter === "selected" &&
      (route.from === selectedStationId || route.to === selectedStationId)),
  );
}

export function routeSummary(
  scene: Scene | null,
  filter: FlowFilter,
  selectedStationId: string | null,
) {
  const total = scene?.kind === "replay" ? scene.flows : [];
  const shown = visibleRoutes(scene, filter, selectedStationId).filter((route) =>
    scene?.stations.get(route.from)?.coordinate && scene?.stations.get(route.to)?.coordinate,
  );
  return {
    total: total.length,
    shown: shown.length,
    totalRides: total.reduce((sum, route) => sum + route.quantity, 0),
    shownRides: shown.reduce((sum, route) => sum + route.quantity, 0),
  };
}
