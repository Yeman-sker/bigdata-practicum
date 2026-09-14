# Citi Bike Historical Trips — 2025-01 source handoff

This is the Day 1 source verification for Issue #6. It uses the official Citi
Bike Historical Trips object for January 2025:

Historical Trips page: `https://citibikenyc.com/system-data`
Direct object: `https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip`

The source is deliberately kept outside the repository. The checked-in
machine-readable record is [`citibike-202501-manifest.json`](citibike-202501-manifest.json).
The small fixture is [`../../fixtures/citibike/202501_sample.csv`](../../fixtures/citibike/202501_sample.csv).

## Reproduce

```bash
STAGING=/tmp/citibike-day1/202501
mkdir -p "$STAGING/raw" "$STAGING/extracted"
curl -fL --retry 3 \
  -o "$STAGING/raw/202501-citibike-tripdata.zip" \
  'https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip'

python3 -m citibike.historical_source \
  --zip "$STAGING/raw/202501-citibike-tripdata.zip" \
  --extract-dir "$STAGING/extracted" \
  --manifest docs/source/citibike-202501-manifest.json \
  --sample-output "$STAGING/verified_sample.csv" \
  --sample-rows 20 \
  --source-page-url 'https://citibikenyc.com/system-data' \
  --source-url 'https://s3.amazonaws.com/tripdata/202501-citibike-tripdata.zip' \
  --source-month 2025-01 \
  --source-name 'Citi Bike Historical Trips' \
  --downloaded-at '2026-09-11T07:34:34Z'
```

The verifier uses a streaming `csv.DictReader` count, enumerates every ZIP
member, validates every header against the 13-field source contract, and
records nulls and ratios, duplicate ride IDs, timestamp parse formats,
non-positive durations, coordinate anomalies, enum values, unknown enum counts,
and station-id examples. It keeps all original rows in staging; the tracked
fixture is not generated from the source ZIP.

To hand the verified CSVs to Track D without placing them in Git, use the
frozen RAW path:

```bash
hdfs dfs -mkdir -p /raw/citibike/trips/year=2025/month=01
hdfs dfs -put -f "$STAGING"/extracted/*.csv /raw/citibike/trips/year=2025/month=01/
hdfs dfs -count /raw/citibike/trips/year=2025/month=01
```

The tracked 20-row fixture is synthetic and hand-authored for schema/parser
tests. It deliberately contains no source ride IDs, station names, station IDs,
or source coordinates. The verifier's `--sample-output` is a raw source sample
for local inspection only and stays under `/tmp`.

## Handoff notes

- The ZIP contains three CSV files, not one.
- All three CSVs have the same 13-column header.
- `start_station_id` and `end_station_id` are intentionally strings. They are
  identifiers, may be blank, and must not be coerced to numeric values.
- Source `started_at` and `ended_at` are naive `America/New_York` wall-clock
  values and map to the internal `started_at_local` and `ended_at_local` fields.
- The manifest records the observed values and quality metrics needed by Tracks C–E;
  anomaly counts are recorded rather than silently dropping rows.
- The full ZIP and extracted CSVs are in `/tmp/citibike-day1/202501` on the
  verification machine and are not Git-tracked.

## Track D handoff evidence

On 2026-09-13, the three extracted source CSVs were uploaded to a local
Hadoop 3.3.6 single-node validation instance using the frozen RAW path. The
temporary NameNode/DataNode state was kept under `/tmp/citibike-hdfs`; no
source data was copied into Git.

The upload and directory verification returned:

```text
$ hdfs dfs -ls -h /raw/citibike/trips/year=2025/month=01
Found 3 items
-rw-r--r--  1 ohn supergroup  186.0 M  2026-09-13 20:16 /raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_1.csv
-rw-r--r--  1 ohn supergroup  186.0 M  2026-09-13 20:16 /raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_2.csv
-rw-r--r--  1 ohn supergroup   23.1 M  2026-09-13 20:16 /raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_3.csv

$ hdfs dfs -count /raw/citibike/trips/year=2025/month=01
1  3  414212882  /raw/citibike/trips/year=2025/month=01

$ hdfs fsck /raw/citibike/trips/year=2025/month=01 -files -blocks -locations
/raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_1.csv 195009639 bytes, replicated: replication=1, 2 block(s): OK
/raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_2.csv 194993655 bytes, replicated: replication=1, 2 block(s): OK
/raw/citibike/trips/year=2025/month=01/202501-citibike-tripdata_3.csv 24209588 bytes, replicated: replication=1, 1 block(s): OK
Status: HEALTHY
Total files: 3
Total blocks (validated): 5
Missing blocks: 0
Corrupt blocks: 0
```

The HDFS byte total (`414212882`) equals the sum of the three CSV sizes in
the manifest; the manifest's ZIP size and SHA-256 independently identify the
original download.
