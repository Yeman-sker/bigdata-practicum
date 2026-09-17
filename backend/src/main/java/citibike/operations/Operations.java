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
            if (o.snapshotAt() == null || o.snapshotAt().isAfter(asOf.plus(CLOCK_SKEW)) ||
                    (o.lastReportedAt() != null && o.lastReportedAt().isAfter(asOf.plus(CLOCK_SKEW)))) {
                Instant expires = o.snapshotAt() == null ? asOf : effectiveObservation(o).plus(FRESHNESS);
                return invalid(o, m, Status.INVALID_DATA, "INVALID_TIME", expires);
            }
            Instant expires = effectiveObservation(o).plus(FRESHNESS);
            if (asOf.compareTo(expires) >= 0) return invalid(o, m, Status.STALE_DATA, "STALE_OBSERVATION", expires);
            if (Boolean.FALSE.equals(o.installed()) || Boolean.FALSE.equals(o.renting()) || Boolean.FALSE.equals(o.returning()))
                return invalid(o, m, Status.SERVICE_UNAVAILABLE, "SERVICE_FLAGS", expires);
            if (o.bikes() != null && o.bikes() < 0 || o.docks() != null && o.docks() < 0)
                return invalid(o, m, Status.INVALID_DATA, "NEGATIVE_INVENTORY", expires);
            if (o.installed() == null || o.renting() == null || o.returning() == null)
                return invalid(o, m, Status.INSUFFICIENT_DATA, "MISSING_INVENTORY", expires);
            if (o.bikes() == null || o.docks() == null)
                return invalid(o, m, Status.INSUFFICIENT_DATA, "MISSING_INVENTORY", expires);
            int serviceable = o.bikes() + o.docks();
            if (serviceable == 0) return invalid(o, m, Status.INSUFFICIENT_DATA, "ZERO_SERVICEABLE_CAPACITY", expires);
            Status current = classify((double) o.bikes() / serviceable);
            Profile p = profiles == null ? null : profiles.get(dayHour(o.snapshotAt()));
            boolean usable = p != null && p.sampleDays() >= 1 && baselineDatasetId != null;
            Double projected = usable ? o.bikes() + p.avgNetFlow() : null;
            Status forecast = usable ? classify(projected / serviceable) : Status.INSUFFICIENT_DATA;
            return new Risk(o.stationId(), m, o, current, null, forecast, usable ? null : "NO_BASELINE",
                    (double) o.bikes() / serviceable, usable ? p.avgInbound() : null, usable ? p.avgOutbound() : null,
                    usable ? p.avgNetFlow() : null, projected, usable ? p.sampleDays() : null,
                    usable ? o.snapshotAt().plusSeconds(3600) : null, expires);
        }

        private static Instant effectiveObservation(Observation o) {
            return o.lastReportedAt() == null || o.snapshotAt().isBefore(o.lastReportedAt()) ? o.snapshotAt() : o.lastReportedAt();
        }
        private static Risk invalid(Observation o, Metadata m, Status status, String reason, Instant expires) {
            return new Risk(o.stationId(), m, o, status, reason, status, reason, null, null, null, null, null, null, null, expires);
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
        Map<String, Integer> sourceSafe = new HashMap<>();
        Map<String, Integer> needs = new HashMap<>();
        Map<String, Integer> sourceBikes = new HashMap<>();
        Map<String, Integer> targetDocks = new HashMap<>();
        Map<String, Integer> targetSafe = new HashMap<>();
        for (Risk r : sources) { int s = Math.max(0, (int)Math.floor(r.projectedBikes() - .70 * r.metadata().capacity()));
            int safe = Math.max(0, r.observation().bikes() - (int)Math.ceil(.30 * serviceable(r)));
            supplies.put(r.stationId(), s); sourceSafe.put(r.stationId(), safe); sourceBikes.put(r.stationId(), r.observation().bikes()); }
        for (Risk r : targets) { needs.put(r.stationId(), need(r));
            targetDocks.put(r.stationId(), r.observation().docks());
            targetSafe.put(r.stationId(), Math.max(0, (int)Math.floor(.70 * serviceable(r)) - r.observation().bikes())); }
        List<Suggestion> out = new ArrayList<>();
        for (Risk target : targets) {
            List<Risk> ordered = sources.stream().sorted(Comparator.comparingDouble((Risk s) -> distance(s.metadata(), target.metadata()))
                    .thenComparing(Risk::stationId)).toList();
            for (Risk source : ordered) {
                int move = Math.min(Math.min(Math.min(supplies.get(source.stationId()), sourceSafe.get(source.stationId())), needs.get(target.stationId())),
                        Math.min(Math.min(sourceBikes.get(source.stationId()), targetDocks.get(target.stationId())),
                                targetSafe.get(target.stationId())));
                if (move <= 0) continue;
                int fromSurplus = supplies.get(source.stationId()), toDeficit = needs.get(target.stationId());
                supplies.put(source.stationId(), fromSurplus - move); needs.put(target.stationId(), toDeficit - move);
                sourceSafe.put(source.stationId(), sourceSafe.get(source.stationId()) - move);
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

    /** Apply request-time expiry to a row loaded from ADS. */
    public static Risk forRead(Risk risk, Instant asOf) {
        if (asOf.isBefore(risk.expiresAt())) return risk;
        return new Risk(risk.stationId(), risk.metadata(), risk.observation(), Status.STALE_DATA,
                "STALE_OBSERVATION", Status.STALE_DATA, "STALE_OBSERVATION", null, null, null,
                null, null, null, null, risk.expiresAt());
    }

    /** Keep suggestions only while both endpoint observations are still usable. */
    public static List<Suggestion> suggestionsForRead(List<Suggestion> suggestions,
                                                       Map<String, Risk> risks, Instant asOf) {
        return suggestions.stream().filter(s -> asOf.isBefore(s.expiresAt()))
                .filter(s -> {
                    Risk from = risks.get(s.fromStationId()), to = risks.get(s.toStationId());
                    return from != null && to != null && usableForSuggestion(forRead(from, asOf))
                            && usableForSuggestion(forRead(to, asOf));
                }).toList();
    }

    private static boolean usableForSuggestion(Risk risk) {
        return risk.currentStatus() != Status.STALE_DATA && risk.currentStatus() != Status.SERVICE_UNAVAILABLE
                && risk.currentStatus() != Status.INVALID_DATA && risk.currentStatus() != Status.INSUFFICIENT_DATA;
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
    public enum BatchOutcome { BUFFERED, PUBLISH, IGNORE_DUPLICATE, REJECT_KEEP_PREVIOUS, UNCONSUMED }
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
        private final String expectedMetadataVersion;
        private final Map<String, StationEvent> stations = new LinkedHashMap<>();
        private String snapshotId;
        private Headers batchHeaders;
        private Instant startedAt;
        private SnapshotEnd completedEnd;
        private String skippingSnapshotId;
        private Instant lastPublishedSnapshotAt;
        private final String mode;

        public BatchConsumer(Set<String> metadataStationIds) {
            this(metadataStationIds, "any", null, null, null);
        }
        public BatchConsumer(Set<String> metadataStationIds, String mode) {
            this(metadataStationIds, mode, null, null, null);
        }
        public BatchConsumer(Set<String> metadataStationIds, String mode, String lastPublishedSnapshotId,
                             Instant lastPublishedSnapshotAt) {
            this(metadataStationIds, mode, lastPublishedSnapshotId, lastPublishedSnapshotAt, null);
        }
        public BatchConsumer(Set<String> metadataStationIds, String mode, String lastPublishedSnapshotId,
                             Instant lastPublishedSnapshotAt, String expectedMetadataVersion) {
            this.metadataStationIds = Set.copyOf(metadataStationIds);
            if (!Set.of("any", "live", "recorded").contains(mode)) throw new IllegalArgumentException("mode");
            if (expectedMetadataVersion != null && !expectedMetadataVersion.matches("[0-9a-f]{64}"))
                throw new IllegalArgumentException("expectedMetadataVersion");
            this.mode = mode;
            this.skippingSnapshotId = null;
            this.lastPublishedSnapshotAt = lastPublishedSnapshotAt;
            this.lastPublishedSnapshotId = lastPublishedSnapshotId;
            this.expectedMetadataVersion = expectedMetadataVersion;
        }
        private String lastPublishedSnapshotId;

        public BatchResult acceptStation(StationEvent event, Instant receivedAt) {
            if (completedEnd != null) throw new IllegalStateException("complete batch awaits publication");
            if (!validHeaders(event.headers(), "station") || !metadataVersionAllowed(event.headers()) ||
                    !allowedOrigin(event.headers().dataOrigin()) || !originAllowedForMode(event.headers().dataOrigin()))
                return reject(event.headers(), "INVALID_HEADERS_ORIGIN");
            if (!event.key().equals(event.observation().stationId())) return reject(event.headers(), "KEY_MISMATCH");
            if (snapshotId != null && isTimedOut(receivedAt)) {
                if (!snapshotId.equals(event.headers().snapshotId())) {
                    clearRejected();
                    return new BatchResult(BatchOutcome.UNCONSUMED, event.headers().snapshotId(), "PREVIOUS_BATCH_TIMEOUT", List.of(event));
                }
                return reject(event.headers(), "BATCH_TIMEOUT");
            }
            if (snapshotId == null && skippingSnapshotId == null) {
                if (event.headers().snapshotId().equals(lastPublishedSnapshotId)) {
                    skippingSnapshotId = event.headers().snapshotId(); batchHeaders = event.headers();
                    return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, skippingSnapshotId, "ALREADY_PUBLISHED", List.of());
                }
                if (event.observation().snapshotAt() == null) return reject(event.headers(), "INVALID_TIME");
                if (lastPublishedSnapshotAt != null && !event.observation().snapshotAt().isAfter(lastPublishedSnapshotAt))
                    return reject(event.headers(), "OLDER_THAN_PUBLISHED");
                snapshotId = event.headers().snapshotId(); batchHeaders = event.headers(); startedAt = receivedAt;
            }
            if (skippingSnapshotId != null) {
                if (skippingSnapshotId.equals(event.headers().snapshotId()))
                    return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, skippingSnapshotId, "ALREADY_PUBLISHED", List.of());
                clearRejected();
                return new BatchResult(BatchOutcome.UNCONSUMED, event.headers().snapshotId(), "DUPLICATE_END_EXPECTED", List.of(event));
            }
            if (!sameBatch(event.headers())) {
                String unconsumed = event.headers().snapshotId();
                clearRejected();
                return new BatchResult(BatchOutcome.UNCONSUMED, unconsumed, "PREVIOUS_BATCH_REJECTED", List.of(event));
            }
            if (stations.size() >= 5000 && !stations.containsKey(event.key())) return reject(event.headers(), "STATION_LIMIT");
            StationEvent previous = stations.putIfAbsent(event.key(), event);
            if (previous != null && !previous.observation().equals(event.observation())) return reject(event.headers(), "STATION_CONFLICT");
            return new BatchResult(BatchOutcome.BUFFERED, snapshotId, null, List.copyOf(stations.values()));
        }

        public BatchResult acceptEnd(SnapshotEnd end, Instant receivedAt) {
            if (completedEnd != null) {
                if (!completedEnd.equals(end)) throw new IllegalStateException("complete batch awaits publication");
                return new BatchResult(BatchOutcome.PUBLISH, snapshotId, null, List.copyOf(stations.values()));
            }
            if (!validHeaders(end.headers(), "snapshot_end") || !metadataVersionAllowed(end.headers()) ||
                    !allowedOrigin(end.headers().dataOrigin()) || !originAllowedForMode(end.headers().dataOrigin()))
                return reject(end.headers(), "INVALID_HEADERS_ORIGIN");
            if (snapshotId == null && skippingSnapshotId == null && END_KEY.equals(end.key())
                    && end.headers().snapshotId().equals(lastPublishedSnapshotId))
                return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, end.headers().snapshotId(), "ALREADY_PUBLISHED", List.of());
            if (snapshotId != null && isTimedOut(receivedAt)) {
                if (!snapshotId.equals(end.headers().snapshotId())) {
                    clearRejected();
                    return new BatchResult(BatchOutcome.UNCONSUMED, end.headers().snapshotId(), "PREVIOUS_BATCH_TIMEOUT", List.of());
                }
                return reject(end.headers(), "BATCH_TIMEOUT");
            }
            if (snapshotId == null && skippingSnapshotId == null && END_KEY.equals(end.key()) && end.stationCount() == 0) {
                snapshotId = end.headers().snapshotId(); batchHeaders = end.headers(); startedAt = receivedAt;
            }
            if (skippingSnapshotId != null) {
                if (skippingSnapshotId.equals(end.headers().snapshotId())) {
                    String duplicate = skippingSnapshotId; clearRejected();
                    return new BatchResult(BatchOutcome.IGNORE_DUPLICATE, duplicate, "ALREADY_PUBLISHED", List.of());
                }
                clearRejected();
                return new BatchResult(BatchOutcome.UNCONSUMED, end.headers().snapshotId(), "DUPLICATE_END_EXPECTED", List.of());
            }
            if (snapshotId != null && !snapshotId.equals(end.headers().snapshotId())) {
                clearRejected();
                return new BatchResult(BatchOutcome.UNCONSUMED, end.headers().snapshotId(), "PREVIOUS_BATCH_REJECTED", List.of());
            }
            if (snapshotId == null || !sameBatch(end.headers()) || !END_KEY.equals(end.key()))
                return reject(end.headers(), "INCOMPLETE_OR_MIXED_BATCH");
            if (end.stationCount() != stations.size() || !metadataStationIds.containsAll(stations.keySet()))
                return reject(end.headers(), "COUNT_OR_METADATA_MISMATCH");
            if (end.snapshotAt() == null || end.ingestedAt() == null || stations.values().stream()
                    .anyMatch(e -> !end.snapshotAt().equals(e.observation().snapshotAt())))
                return reject(end.headers(), "SNAPSHOT_TIME_MISMATCH");
            if (lastPublishedSnapshotAt != null && !end.snapshotAt().isAfter(lastPublishedSnapshotAt))
                return reject(end.headers(), "OLDER_THAN_PUBLISHED");
            completedEnd = end;
            return new BatchResult(BatchOutcome.PUBLISH, snapshotId, null, List.copyOf(stations.values()));
        }

        public BatchResult timeout(Instant now) {
            if (completedEnd != null || snapshotId == null || startedAt == null || now.isBefore(startedAt.plus(MAX_BATCH_AGE)))
                return new BatchResult(BatchOutcome.BUFFERED, snapshotId, null, List.copyOf(stations.values()));
            return reject(batchHeaders, "BATCH_TIMEOUT");
        }

        /** Call only after the ADS/live_release transaction commits. */
        public void markPublished(Instant snapshotAt) {
            if (snapshotId == null) throw new IllegalStateException("no batch");
            lastPublishedSnapshotId = snapshotId; lastPublishedSnapshotAt = snapshotAt;
            clearRejected();
        }

        public void clearRejected() { stations.clear(); snapshotId = null; batchHeaders = null; startedAt = null; completedEnd = null; skippingSnapshotId = null; }
        private boolean sameBatch(Headers h) { return batchHeaders.snapshotId().equals(h.snapshotId()) &&
                batchHeaders.metadataVersion().equals(h.metadataVersion()) && batchHeaders.dataOrigin().equals(h.dataOrigin()) &&
                batchHeaders.contractVersion().equals(h.contractVersion()); }
        private boolean metadataVersionAllowed(Headers h) {
            return expectedMetadataVersion == null || expectedMetadataVersion.equals(h.metadataVersion());
        }
        private boolean isTimedOut(Instant receivedAt) {
            return receivedAt != null && startedAt != null && !receivedAt.isBefore(startedAt.plus(MAX_BATCH_AGE));
        }
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
        private final String metadataVersion;

        public BatchProcessor(BatchConsumer consumer, AtomicPublisher publisher, String metadataVersion) {
            this.consumer = Objects.requireNonNull(consumer); this.publisher = Objects.requireNonNull(publisher);
            if (metadataVersion == null || !metadataVersion.matches("[0-9a-f]{64}"))
                throw new IllegalArgumentException("metadataVersion");
            this.metadataVersion = metadataVersion;
        }

        /**
         * Publishes only a complete, validated batch. A publisher failure deliberately leaves
         * the batch unconfirmed so the Kafka adapter can retry it; markPublished is never called.
         */
        public BatchResult finish(SnapshotEnd end, Instant asOf, Map<String, Metadata> metadata,
                                  Map<String, Map<DayHour, Profile>> profiles,
                                  String baselineDatasetId, Instant publishedAt) throws Exception {
            if (!metadataVersion.equals(end.headers().metadataVersion())) {
                consumer.clearRejected();
                return new BatchResult(BatchOutcome.REJECT_KEEP_PREVIOUS, end.headers().snapshotId(),
                        "METADATA_VERSION_MISMATCH", List.of());
            }
            BatchResult checked = consumer.acceptEnd(end, asOf);
            if (checked.outcome() != BatchOutcome.PUBLISH) return checked;
            List<Risk> risks = new ArrayList<>();
            Instant calculationAsOf = recorded(end.headers().dataOrigin()) ? end.ingestedAt() : asOf;
            for (StationEvent event : checked.stations()) {
                Metadata m = metadata.get(event.observation().stationId());
                if (m == null) throw new IllegalArgumentException("missing metadata for " + event.observation().stationId());
                risks.add(calculator.calculate(event.observation(), m,
                        profiles.getOrDefault(event.observation().stationId(), Map.of()), calculationAsOf, baselineDatasetId));
            }
            List<Suggestion> suggestions = rebalance(end.headers().snapshotId(), risks, publishedAt);
            Release release = new Release(end.headers().snapshotId(), end.headers().metadataVersion(), baselineDatasetId,
                    end.snapshotAt(), end.ingestedAt(), calculationAsOf, publishedAt, end.headers().dataOrigin(),
                    recorded(end.headers().dataOrigin()) ? "recorded" : "wall");
            publisher.publish(List.copyOf(risks), suggestions, release);
            consumer.markPublished(end.snapshotAt());
            return new BatchResult(BatchOutcome.PUBLISH, release.snapshotId(), null, checked.stations());
        }

        private static boolean recorded(String origin) {
            return "FIXTURE".equals(origin) || "GBFS_REPLAY".equals(origin);
        }
    }
}
