# GBFS 2.3 PoC

This directory documents the Citi Bike GBFS collector delivered for Issue #7.
The feed is a station inventory snapshot source, not a stream of individual
ride events.

## Verified source

- Discovery: `https://gbfs.citibikenyc.com/gbfs/2.3/gbfs.json`
- Provider version observed: `2.3`
- Discovery currently advertises English feeds through `gbfs.lyft.com`:
  - `station_information`
  - `station_status`
  - `vehicle_types`
- The collector resolves these URLs from discovery and does not hard-code the
  provider's redirected host/path.
- The observed `vehicle_types` records contain `vehicle_type_id`,
  `form_factor`, and `propulsion_type`, but no `name` field. The fixture keeps
  that provider shape; a name must not be invented downstream.

The checked-in files under `fixtures/gbfs/` are small samples captured from the
live feed. Full snapshots must stay outside Git. The collector ignores neither
the raw response nor its provider metadata: it writes both locally so the
normalization can be audited.

## Run a three-snapshot collection

```bash
python3 scripts/gbfs_collector.py \
  --output-dir data/gbfs/citibike/$(date -u +%F) \
  --snapshots 3 \
  --interval-seconds 60 \
  --sample-size 5 \
  --sample-station-id 0c923abb-298a-4a47-b132-9fae73cc59e6
```

The output contains `discovery.json`, `feed_manifest.json`, three timestamped
snapshot directories, `station_status_event_v1.sample.json` in each snapshot,
and `collection_log.json`. `data/gbfs/` is ignored because full raw payloads
must not be committed.

Each collection-log snapshot records the provider `last_updated`, local
`ingested_at_utc`, station count, and a small set of sample station states.
Use repeatable `--sample-station-id` options when the same station must be
tracked across snapshots.

The checked-in collection log tracks station
`0c923abb-298a-4a47-b132-9fae73cc59e6` across three real responses. Provider
`last_updated` advanced from `1789118479` to `1789118539` to `1789118600`;
the station changed from 1 bike / 37 docks to 2 bikes / 36 docks in the third
snapshot. This is evidence of both feed refresh and inventory change, not a
copy of one response.

In the observed `station_information` response, `region_id` was absent or
null for 13 of 2,507 stations. `capacity` was populated in that capture, but
the validator and downstream contract still allow it to be nullable.

## Validate the normalized fixture

```bash
PYTHONPATH=scripts python3 scripts/validate_gbfs_fixture.py \
  fixtures/gbfs/station_status_event_v1.sample.json
```

## Provider-to-contract mapping

| GBFS provider field | Internal field | Rule |
| --- | --- | --- |
| `data.stations[].station_id` | `station_id` | Preserve as string, including numeric-looking IDs |
| `station_status.last_updated` | `snapshot_at_utc` | POSIX seconds converted to UTC RFC 3339 |
| `snapshot_at_utc` | `snapshot_at_local` | Convert with `America/New_York` zone rules |
| `num_bikes_available` | `num_bikes_available` | Required integer |
| `num_bikes_disabled` | `num_bikes_disabled` | Nullable integer |
| `num_docks_available` | `num_docks_available` | Nullable integer |
| `num_docks_disabled` | `num_docks_disabled` | Nullable integer |
| `is_installed`, `is_renting`, `is_returning` | same names | Accept GBFS `0/1`, emit booleans |
| `last_reported` | `last_reported_at_utc` | Nullable POSIX seconds converted to UTC |
| local request time | `ingested_at_utc` | Collector clock, always UTC |
| response `version` | `source_version` | Required; Issue #4 expects `2.3` |

`capacity` and `region_id` are station-information metadata and are not part of
`station_status_event_v1`; both remain nullable in the station dimension.
