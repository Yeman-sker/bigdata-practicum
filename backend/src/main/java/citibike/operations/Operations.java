package citibike.operations;

import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.ZonedDateTime;
import java.util.*;

/** Pure risk, forecast and integer rebalance rules for Issue #29. */
public final class Operations {
    private Operations() {}

    public enum Status { SHORTAGE_RISK, LOW_INVENTORY, HEALTHY, HIGH_INVENTORY,
        OVERFLOW_RISK, INSUFFICIENT_DATA, SERVICE_UNAVAILABLE, STALE_DATA, INVALID_DATA }

    public record Profile(double avgInbound, double avgOutbound, double avgNetFlow, long sampleDays) {}
    public record Metadata(String stationId, String name, Double lat, Double lon, Integer capacity) {}
    public record Observation(String stationId, Instant snapshotAt, Instant lastReportedAt,
                              Integer bikes, Integer docks, Boolean installed, Boolean renting,
                              Boolean returning) {}
    public record Risk(String stationId, Metadata metadata, Observation observation,
                       Status currentStatus, String currentReason, Status forecastStatus,
                       String forecastReason, Double fillRatio, Double expectedInbound,
                       Double expectedOutbound, Double expectedNetFlow, Double projectedBikes,
                       Long sampleDays, Instant forecastFor, Instant expiresAt) {}
    public record Suggestion(String suggestionId, String snapshotId, String fromStationId,
                             String toStationId, int moveBikes, int fromSurplus, int toDeficit,
                             int distanceMeters, int priority, Instant generatedAt,
                             Instant expiresAt) {}

    public static final class RiskCalculator {
        private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
        private static final Duration FRESHNESS = Duration.ofSeconds(180);
        private static final Duration CLOCK_SKEW = Duration.ofSeconds(60);

        public Risk calculate(Observation o, Metadata m, Map<DayHour, Profile> profiles,
                              Instant asOf, String baselineDatasetId) {
            Objects.requireNonNull(o); Objects.requireNonNull(asOf);
            Instant observed = o.lastReportedAt() == null ? o.snapshotAt() :
                    min(o.snapshotAt(), o.lastReportedAt());
            Instant expires = observed == null ? asOf : observed.plus(FRESHNESS);
            String reason = null;
            Status current;
            if (o.snapshotAt() == null || o.snapshotAt().isAfter(asOf.plus(CLOCK_SKEW)) ||
                    (o.lastReportedAt() != null && o.lastReportedAt().isAfter(asOf.plus(CLOCK_SKEW)))) {
                current = Status.INVALID_DATA; reason = "INVALID_TIME";
            } else if (asOf.compareTo(expires) >= 0) {
                current = Status.STALE_DATA; reason = "STALE_OBSERVATION";
            } else if (o.installed() == null || o.renting() == null || o.returning() == null) {
                current = Status.INSUFFICIENT_DATA; reason = "MISSING_INVENTORY";
            } else if (!Boolean.TRUE.equals(o.installed()) || !Boolean.TRUE.equals(o.renting()) ||
                    !Boolean.TRUE.equals(o.returning())) {
                current = Status.SERVICE_UNAVAILABLE; reason = "SERVICE_FLAGS";
            } else if (o.bikes() != null && o.bikes() < 0 || o.docks() != null && o.docks() < 0) {
                current = Status.INVALID_DATA; reason = "NEGATIVE_INVENTORY";
            } else if (o.bikes() == null || o.docks() == null) {
                current = Status.INSUFFICIENT_DATA; reason = "MISSING_INVENTORY";
            } else {
                int serviceable = o.bikes() + o.docks();
                if (serviceable == 0) { current = Status.INSUFFICIENT_DATA; reason = "ZERO_SERVICEABLE_CAPACITY"; }
                else current = classify((double) o.bikes() / serviceable);
            }

            if (reason != null) return new Risk(o.stationId(), m, o, current, reason, current, reason,
                    null, null, null, null, null, null, null, expires);

            DayHour key = dayHour(o.snapshotAt());
            Profile p = profiles == null ? null : profiles.get(key);
            boolean usable = p != null && p.sampleDays() >= 1 && baselineDatasetId != null;
            Double projected = usable ? o.bikes() + p.avgNetFlow() : null;
            Status forecast = usable ? classify(projected / (o.bikes() + o.docks())) : Status.INSUFFICIENT_DATA;
            String forecastReason = usable ? null : "NO_BASELINE";
            return new Risk(o.stationId(), m, o, current, null, forecast, forecastReason,
                    (double) o.bikes() / (o.bikes() + o.docks()), usable ? p.avgInbound() : null,
                    usable ? p.avgOutbound() : null, usable ? p.avgNetFlow() : null, projected,
                    usable ? p.sampleDays() : null, usable ? o.snapshotAt().plusSeconds(3600) : null, expires);
        }

        public static Status classify(double ratio) {
            if (ratio <= .15) return Status.SHORTAGE_RISK;
            if (ratio <= .30) return Status.LOW_INVENTORY;
            if (ratio < .70) return Status.HEALTHY;
            if (ratio < .85) return Status.HIGH_INVENTORY;
            return Status.OVERFLOW_RISK;
        }
        private static DayHour dayHour(Instant instant) {
            ZonedDateTime z = instant.atZone(NEW_YORK);
            return new DayHour(z.getDayOfWeek().getValue(), z.getHour());
        }
        private static Instant min(Instant a, Instant b) { return a.isBefore(b) ? a : b; }
    }

    public record DayHour(int dayOfWeek, int hour) {}

    public static List<Suggestion> rebalance(String snapshotId, List<Risk> risks,
                                             Instant generatedAt) {
        List<Risk> targets = risks.stream().filter(r -> r.forecastStatus() == Status.SHORTAGE_RISK)
                .filter(r -> feasible(r) && r.projectedBikes() != null).sorted(Comparator
                        .comparingInt((Risk r) -> need(r)).reversed()
                        .thenComparing(r -> r.stationId())).toList();
        List<Risk> sources = risks.stream().filter(r -> r.forecastStatus() == Status.OVERFLOW_RISK)
                .filter(r -> feasible(r) && r.projectedBikes() != null).toList();
        Map<String, Integer> supplies = new HashMap<>();
        Map<String, Integer> needs = new HashMap<>();
        Map<String, Integer> sourceBikes = new HashMap<>();
        Map<String, Integer> targetDocks = new HashMap<>();
        Map<String, Integer> targetSafe = new HashMap<>();
        for (Risk r : sources) { int s = Math.max(0, (int)Math.floor(r.projectedBikes() - .70 * r.metadata().capacity()));
            int safe = Math.max(0, r.observation().bikes() - (int)Math.ceil(.30 * serviceable(r)));
            supplies.put(r.stationId(), Math.min(s, safe)); sourceBikes.put(r.stationId(), r.observation().bikes()); }
        for (Risk r : targets) { needs.put(r.stationId(), need(r));
            targetDocks.put(r.stationId(), r.observation().docks());
            targetSafe.put(r.stationId(), Math.max(0, (int)Math.floor(.70 * serviceable(r)) - r.observation().bikes())); }
        List<Suggestion> out = new ArrayList<>();
        for (Risk target : targets) {
            List<Risk> ordered = sources.stream().sorted(Comparator.comparingDouble((Risk s) -> distance(s.metadata(), target.metadata()))
                    .thenComparing(Risk::stationId)).toList();
            for (Risk source : ordered) {
                int move = Math.min(Math.min(supplies.get(source.stationId()), needs.get(target.stationId())),
                        Math.min(Math.min(sourceBikes.get(source.stationId()), targetDocks.get(target.stationId())),
                                targetSafe.get(target.stationId())));
                if (move <= 0) continue;
                int fromSurplus = supplies.get(source.stationId()), toDeficit = needs.get(target.stationId());
                supplies.put(source.stationId(), fromSurplus - move); needs.put(target.stationId(), toDeficit - move);
                sourceBikes.put(source.stationId(), sourceBikes.get(source.stationId()) - move);
                targetDocks.put(target.stationId(), targetDocks.get(target.stationId()) - move);
                targetSafe.put(target.stationId(), targetSafe.get(target.stationId()) - move);
                int priority = out.size() + 1;
                out.add(new Suggestion(snapshotId + ":" + priority, snapshotId, source.stationId(), target.stationId(),
                        move, fromSurplus, toDeficit, (int)Math.round(distance(source.metadata(), target.metadata())),
                        priority, generatedAt, min(source.expiresAt(), target.expiresAt())));
            }
        }
        return List.copyOf(out);
    }

    private static int serviceable(Risk r) { return r.observation().bikes() + r.observation().docks(); }
    private static int need(Risk r) { return Math.max(0, (int)Math.ceil(.30 * r.metadata().capacity() - r.projectedBikes())); }
    private static boolean feasible(Risk r) { return r.metadata() != null && r.metadata().capacity() != null && r.metadata().capacity() > 0
            && r.metadata().lat() != null && r.metadata().lon() != null && r.observation().bikes() != null
            && r.observation().docks() != null && r.observation().bikes() >= 0 && r.observation().docks() >= 0
            && r.currentStatus() != Status.STALE_DATA && r.currentStatus() != Status.SERVICE_UNAVAILABLE
            && r.currentStatus() != Status.INVALID_DATA && r.currentStatus() != Status.INSUFFICIENT_DATA; }
    private static double distance(Metadata a, Metadata b) {
        double p1 = Math.toRadians(a.lat()), p2 = Math.toRadians(b.lat());
        double dp = Math.toRadians(b.lat() - a.lat()), dl = Math.toRadians(b.lon() - a.lon());
        double h = Math.sin(dp/2)*Math.sin(dp/2) + Math.cos(p1)*Math.cos(p2)*Math.sin(dl/2)*Math.sin(dl/2);
        return 6371000 * 2 * Math.atan2(Math.sqrt(h), Math.sqrt(1-h));
    }
    private static Instant min(Instant a, Instant b) { return a.isBefore(b) ? a : b; }

    public record Headers(String contractVersion, String recordType, String snapshotId,
                          String metadataVersion, String dataOrigin) {}
    public record StationEvent(String key, Headers headers, Observation observation) {}
    public record SnapshotEnd(String key, Headers headers, int stationCount,
                              Instant snapshotAt, Instant ingestedAt) {}
    public enum BatchOutcome { BUFFERED, PUBLISH, IGNORE_DUPLICATE, REJECT_KEEP_PREVIOUS }
    public record BatchResult(BatchOutcome outcome, String snapshotId, String reason,
                              List<StationEvent> stations) {}

    /**
     * Ordered, dependency-free batch gate. Kafka adapters translate records into these
     * values; a successful result is the only input allowed to an atomic ADS writer.
     */
    public static final class BatchConsumer {
        private static final String END_KEY = "__snapshot_end__";
        private static final Duration MAX_BATCH_AGE = Duration.ofSeconds(120);
        private final Set<String> metadataStationIds;
        private final Set<String> published = new HashSet<>();
        private final Map<String, StationEvent> stations = new LinkedHashMap<>();
        private String snapshotId;
        private Headers batchHeaders;
        private Instant startedAt;
        private Instant lastPublishedSnapshotAt;
        private final String mode;

        public BatchConsumer(Set<String> metadataStationIds) {
            this(metadataStationIds, "any");
        }
        public BatchConsumer(Set<String> metadataStationIds, String mode) {
            this.metadataStationIds = Set.copyOf(metadataStationIds);
            if (!Set.of("any", "live", "recorded").contains(mode)) throw new IllegalArgumentException("mode");
            this.mode = mode;
        }

        public BatchResult acceptStation(StationEvent event, Instant receivedAt) {
            if (!validHeaders(event.headers(), "station") || !allowedOrigin(event.headers().dataOrigin()) || !originAllowedForMode(event.headers().dataOrigin()))
                return reject(event.headers(), "INVALID_HEADERS_ORIGIN");
            if (!event.key().equals(event.observation().stationId())) return reject(event.headers(), "KEY_MISMATCH");
            if (snapshotId == null) { snapshotId = event.headers().snapshotId(); batchHeaders = event.headers(); startedAt = receivedAt; }
            if (!sameBatch(event.headers())) return reject(event.headers(), "MIXED_BATCH");
            if (published.contains(snapshotId)) { clearRejected(); return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, snapshotId, "ALREADY_PUBLISHED", List.of()); }
            if (stations.size() >= 5000 && !stations.containsKey(event.key())) return reject(event.headers(), "STATION_LIMIT");
            StationEvent previous = stations.putIfAbsent(event.key(), event);
            if (previous != null && !previous.observation().equals(event.observation())) return reject(event.headers(), "STATION_CONFLICT");
            return new BatchResult(BatchOutcome.BUFFERED, snapshotId, null, List.copyOf(stations.values()));
        }

        public BatchResult acceptEnd(SnapshotEnd end, Instant receivedAt) {
            if (!validHeaders(end.headers(), "snapshot_end") || !allowedOrigin(end.headers().dataOrigin()) || !originAllowedForMode(end.headers().dataOrigin()))
                return reject(end.headers(), "INVALID_HEADERS_ORIGIN");
            if (snapshotId == null && END_KEY.equals(end.key()) && end.stationCount() == 0) {
                snapshotId = end.headers().snapshotId(); batchHeaders = end.headers(); startedAt = receivedAt;
            }
            if (snapshotId == null || !sameBatch(end.headers()) || !END_KEY.equals(end.key()))
                return reject(end.headers(), "INCOMPLETE_OR_MIXED_BATCH");
            if (published.contains(snapshotId)) { clearRejected(); return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, snapshotId, "ALREADY_PUBLISHED", List.of()); }
            if (end.stationCount() != stations.size() || !metadataStationIds.containsAll(stations.keySet()))
                return reject(end.headers(), "COUNT_OR_METADATA_MISMATCH");
            if (lastPublishedSnapshotAt != null && !end.snapshotAt().isAfter(lastPublishedSnapshotAt))
                return reject(end.headers(), "OLDER_THAN_PUBLISHED");
            if (end.snapshotAt() == null || end.ingestedAt() == null || stations.values().stream()
                    .anyMatch(e -> !end.snapshotAt().equals(e.observation().snapshotAt())))
                return reject(end.headers(), "SNAPSHOT_TIME_MISMATCH");
            return new BatchResult(BatchOutcome.PUBLISH, snapshotId, null, List.copyOf(stations.values()));
        }

        public BatchResult timeout(Instant now) {
            if (snapshotId == null || startedAt == null || now.isBefore(startedAt.plus(MAX_BATCH_AGE)))
                return new BatchResult(BatchOutcome.BUFFERED, snapshotId, null, List.copyOf(stations.values()));
            return reject(batchHeaders, "BATCH_TIMEOUT");
        }

        /** Call only after the ADS/live_release transaction commits. */
        public void markPublished(Instant snapshotAt) {
            if (snapshotId == null) throw new IllegalStateException("no batch");
            published.add(snapshotId); lastPublishedSnapshotAt = snapshotAt;
            stations.clear(); snapshotId = null; batchHeaders = null; startedAt = null;
        }

        public void clearRejected() { stations.clear(); snapshotId = null; batchHeaders = null; startedAt = null; }
        private boolean sameBatch(Headers h) { return batchHeaders.snapshotId().equals(h.snapshotId()) &&
                batchHeaders.metadataVersion().equals(h.metadataVersion()) && batchHeaders.dataOrigin().equals(h.dataOrigin()) &&
                batchHeaders.contractVersion().equals(h.contractVersion()); }
        private static boolean validHeaders(Headers h, String type) { return h != null && "1.1".equals(h.contractVersion()) &&
                type.equals(h.recordType()) && h.snapshotId() != null && h.snapshotId().matches("[0-9a-f]{64}") &&
                h.metadataVersion() != null && h.metadataVersion().matches("[0-9a-f]{64}"); }
        private static boolean allowedOrigin(String origin) { return "FIXTURE".equals(origin) || "GBFS_LIVE".equals(origin) || "GBFS_REPLAY".equals(origin); }
        private boolean originAllowedForMode(String origin) { return "any".equals(mode) || ("live".equals(mode) && "GBFS_LIVE".equals(origin)) ||
                ("recorded".equals(mode) && ("GBFS_REPLAY".equals(origin) || "FIXTURE".equals(origin))); }
        private BatchResult reject(Headers h, String reason) { String id = h == null ? snapshotId : h.snapshotId(); clearRejected(); return new BatchResult(BatchOutcome.REJECT_KEEP_PREVIOUS, id, reason, List.of()); }
    }

    public record Release(String snapshotId, String metadataVersion, String baselineDatasetId,
                          Instant snapshotAt, Instant ingestedAt, Instant asOf,
                          Instant publishedAt, String dataOrigin, String clockMode) {}

    /**
     * Adapter boundary for the shared Spring process. Implementations must write both ADS
     * tables and live_release in one transaction and throw without changing durable state.
     */
    public interface AtomicPublisher {
        void publish(List<Risk> risks, List<Suggestion> suggestions, Release release) throws Exception;
    }

    public static final class BatchProcessor {
        private final BatchConsumer consumer;
        private final RiskCalculator calculator = new RiskCalculator();
        private final AtomicPublisher publisher;

        public BatchProcessor(BatchConsumer consumer, AtomicPublisher publisher) {
            this.consumer = Objects.requireNonNull(consumer); this.publisher = Objects.requireNonNull(publisher);
        }

        /**
         * Publishes only a complete, validated batch. A publisher failure deliberately leaves
         * the batch unconfirmed so the Kafka adapter can retry it; markPublished is never called.
         */
        public BatchResult finish(SnapshotEnd end, Instant asOf, Map<String, Metadata> metadata,
                                  Map<String, Map<DayHour, Profile>> profiles,
                                  String baselineDatasetId, Instant publishedAt) throws Exception {
            BatchResult checked = consumer.acceptEnd(end, asOf);
            if (checked.outcome() != BatchOutcome.PUBLISH) return checked;
            List<Risk> risks = new ArrayList<>();
            for (StationEvent event : checked.stations()) {
                Metadata m = metadata.get(event.observation().stationId());
                if (m == null) throw new IllegalArgumentException("missing metadata for " + event.observation().stationId());
                risks.add(calculator.calculate(event.observation(), m,
                        profiles.getOrDefault(event.observation().stationId(), Map.of()), asOf, baselineDatasetId));
            }
            List<Suggestion> suggestions = rebalance(end.headers().snapshotId(), risks, publishedAt);
            Release release = new Release(end.headers().snapshotId(), end.headers().metadataVersion(), baselineDatasetId,
                    end.snapshotAt(), end.ingestedAt(), asOf, publishedAt, end.headers().dataOrigin(),
                    "FIXTURE".equals(end.headers().dataOrigin()) || "GBFS_REPLAY".equals(end.headers().dataOrigin()) ? "recorded" : "wall");
            publisher.publish(List.copyOf(risks), suggestions, release);
            consumer.markPublished(end.snapshotAt());
            return new BatchResult(BatchOutcome.PUBLISH, release.snapshotId(), null, checked.stations());
        }
    }
}
