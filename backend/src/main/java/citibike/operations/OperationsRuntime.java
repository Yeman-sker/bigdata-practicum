package citibike.operations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.Consumer;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.WakeupException;
import org.apache.kafka.common.serialization.ByteArrayDeserializer;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.context.SmartLifecycle;
import org.springframework.context.annotation.Profile;
import org.springframework.core.env.Environment;
import org.springframework.stereotype.Component;

import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.function.LongSupplier;

import static citibike.operations.Operations.*;

@Component
@Profile("live | recorded")
public final class OperationsRuntime implements SmartLifecycle {
    private static final Logger LOG = LoggerFactory.getLogger(OperationsRuntime.class);
    private final Consumer<byte[], byte[]> kafka;
    private final TopicPartition partition;
    private final Session session;
    private volatile boolean running;
    private Thread thread;
    private long commitNext = -1;

    @Autowired
    public OperationsRuntime(JdbcOperationsStore store, ObjectMapper mapper, Clock clock, Environment env) {
        Set<String> profiles = Set.of(env.getActiveProfiles());
        if (profiles.contains("fixture") || profiles.contains("live") == profiles.contains("recorded"))
            throw new IllegalArgumentException("choose exactly one of live, recorded, fixture");
        String mode = profiles.contains("live") ? "live" : "recorded";
        String group = env.getProperty("SPRING_KAFKA_CONSUMER_GROUP_ID",
                mode.equals("live") ? "citibike-live-v1" : "");
        if (group.isBlank()) throw new IllegalArgumentException("recorded requires SPRING_KAFKA_CONSUMER_GROUP_ID");
        String dataDir = env.getProperty("DATA_DIR", "");
        if (dataDir.isBlank() || !Path.of(dataDir).isAbsolute())
            throw new IllegalArgumentException("DATA_DIR must be an absolute directory");
        partition = new TopicPartition(env.getProperty("KAFKA_TOPIC", "bike.station.status.v1"), 0);
        kafka = new KafkaConsumer<>(Map.of(
                "bootstrap.servers", env.getProperty("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
                "group.id", group,
                "enable.auto.commit", false,
                "auto.offset.reset", "earliest",
                "allow.auto.create.topics", false,
                "max.poll.records", 1,
                "default.api.timeout.ms", 10000,
                "key.deserializer", ByteArrayDeserializer.class,
                "value.deserializer", ByteArrayDeserializer.class));
        session = new Session(store, new OperationsWire(mapper, Path.of(dataDir).resolve("gbfs/metadata")),
                clock, System::nanoTime, mode);
    }

    OperationsRuntime(Consumer<byte[], byte[]> kafka, TopicPartition partition, Session session) {
        this.kafka = kafka;
        this.partition = partition;
        this.session = session;
    }

    @Override
    public synchronized void start() {
        if (running) return;
        try {
            if (kafka.partitionsFor(partition.topic()).size() != 1)
                throw new IllegalStateException("operations requires exactly one topic partition");
            // ponytail: one consumer/writer per database; use subscription plus fencing if that contract changes.
            kafka.assign(List.of(partition));
            session.restore();
        } catch (RuntimeException failure) {
            kafka.close(Duration.ofSeconds(5));
            throw failure;
        }
        running = true;
        thread = new Thread(this::run, "operations-consumer");
        thread.setDaemon(true);
        thread.start();
    }

    private void run() {
        try {
            while (running) {
                try {
                    cycle();
                } catch (WakeupException stopped) {
                    if (running) throw stopped;
                } catch (Exception failure) {
                    LOG.warn("operations retry snapshot={} commit_next={} cause={}",
                            session.snapshotId, commitNext, failure.toString());
                    try {
                        Thread.sleep(1000);
                    } catch (InterruptedException stopped) {
                        Thread.currentThread().interrupt();
                        break;
                    }
                }
            }
        } finally {
            running = false;
            kafka.close(Duration.ofSeconds(5));
        }
    }

    void cycle() throws Exception {
        if (commitNext >= 0) {
            kafka.commitSync(Map.of(partition, new OffsetAndMetadata(commitNext)));
            commitNext = -1;
            return;
        }
        if (session.ready()) {
            commitNext = session.publish();
            return;
        }
        var records = kafka.poll(Duration.ofMillis(200));
        if (records.isEmpty()) {
            session.expire();
            return;
        }
        if (records.count() != 1) throw new IllegalStateException("max.poll.records must be 1");
        commitNext = session.accept(records.iterator().next());
    }

    @Override
    public synchronized void stop() {
        running = false;
        kafka.wakeup();
        if (thread != null) {
            try {
                thread.join(15000);
            } catch (InterruptedException interrupted) {
                Thread.currentThread().interrupt();
            }
            if (thread.isAlive()) LOG.warn("operations consumer still stopping after JDBC timeout");
        }
    }

    @Override
    public boolean isRunning() { return running; }

    static final class Session {
        private final JdbcOperationsStore store;
        private final OperationsWire wire;
        private final Clock clock;
        private final LongSupplier elapsed;
        private final String mode;
        private final Map<String, JsonNode> stationValues = new HashMap<>();
        private Release published;
        private String snapshotId;
        private Headers headers;
        private boolean draining;
        private Map<String, Metadata> metadata;
        private BatchConsumer gate;
        private BatchProcessor processor;
        private SnapshotEnd completed;
        private JdbcOperationsStore.Baseline baseline;
        private Instant ingestedAt;
        private long lastOffset;
        private int rawCount;
        private int duplicates;

        Session(JdbcOperationsStore store, OperationsWire wire, Clock clock, LongSupplier elapsed, String mode) {
            this.store = store;
            this.wire = wire;
            this.clock = clock;
            this.elapsed = elapsed;
            this.mode = mode;
        }

        void restore() { published = store.latestRelease().orElse(null); }
        boolean ready() { return completed != null; }
        private Instant receivedAt() { return Instant.ofEpochSecond(0, elapsed.getAsLong()); }

        long accept(ConsumerRecord<byte[], byte[]> record) {
            if (ready()) throw new IllegalStateException("publication must finish before more input");
            expire();
            long safeNext = -1;
            String boundary = OperationsWire.boundaryId(record);
            if (snapshotId != null && boundary != null && !snapshotId.equals(boundary)) {
                if (!draining) reject("NEXT_BATCH_BEFORE_END");
                safeNext = record.offset();
                reset();
            }
            lastOffset = record.offset();
            if (draining) return drain(record, safeNext);
            if (snapshotId == null) snapshotId = boundary;
            try {
                OperationsWire.Decoded decoded = wire.decode(record);
                if (!allowed(decoded.headers().dataOrigin())) throw new IllegalArgumentException("INVALID_ORIGIN_FOR_MODE");
                if (published != null && published.snapshotId().equals(decoded.headers().snapshotId())) {
                    draining = true;
                    LOG.info("operations duplicate snapshot={}", snapshotId);
                    return drain(record, safeNext);
                }
                if (headers == null) {
                    headers = decoded.headers();
                    metadata = wire.metadata(headers.metadataVersion());
                    gate = new BatchConsumer(metadata.keySet(), mode,
                            published == null ? null : published.snapshotId(),
                            published == null ? null : published.snapshotAt(), headers.metadataVersion());
                    processor = new BatchProcessor(gate, (risks, suggestions, release) -> {
                        store.publish(risks, suggestions, release);
                        published = release;
                        Map<Status, Long> statuses = risks.stream().collect(java.util.stream.Collectors.groupingBy(
                                Risk::currentStatus, java.util.stream.Collectors.counting()));
                        LOG.info("operations published snapshot={} source={} metadata={} baseline={} raw={} stations={} duplicates={} suggestions={} statuses={}",
                                release.snapshotId(), release.snapshotAt(), release.metadataVersion(), release.baselineDatasetId(),
                                rawCount, risks.size(), duplicates, suggestions.size(), statuses);
                    }, headers.metadataVersion());
                    ingestedAt = decoded.ingestedAt();
                }
                if (!headers.snapshotId().equals(decoded.headers().snapshotId())
                        || !headers.metadataVersion().equals(decoded.headers().metadataVersion())
                        || !headers.dataOrigin().equals(decoded.headers().dataOrigin())
                        || !Objects.equals(ingestedAt, decoded.ingestedAt()))
                    throw new IllegalArgumentException("MIXED_BATCH");
                BatchResult result;
                if (decoded.station() != null) {
                    rawCount++;
                    JsonNode previous = stationValues.putIfAbsent(decoded.station().key(), decoded.value());
                    if (previous != null) {
                        if (!previous.equals(decoded.value())) throw new IllegalArgumentException("STATION_CONFLICT");
                        duplicates++;
                    }
                    result = gate.acceptStation(decoded.station(), receivedAt());
                } else {
                    result = gate.acceptEnd(decoded.end(), receivedAt());
                }
                if (result.outcome() == BatchOutcome.PUBLISH) completed = decoded.end();
                else if (result.outcome() != BatchOutcome.BUFFERED) {
                    reject(result.reason());
                    return drain(record, safeNext);
                }
                return safeNext;
            } catch (IllegalArgumentException invalid) {
                reject(invalid.getMessage());
                return drain(record, safeNext);
            }
        }

        private boolean allowed(String origin) {
            return mode.equals("live") ? origin.equals("GBFS_LIVE")
                    : origin.equals("GBFS_REPLAY") || origin.equals("FIXTURE");
        }

        private long drain(ConsumerRecord<byte[], byte[]> record, long safeNext) {
            if (OperationsWire.isEnd(record)) {
                reset();
                return record.offset() + 1;
            }
            // Keep the poisoned prefix replayable until a boundary; restarting must not accept its suffix.
            return safeNext;
        }

        void expire() {
            if (gate != null && !draining && !ready()
                    && gate.timeout(receivedAt()).outcome() == BatchOutcome.REJECT_KEEP_PREVIOUS)
                reject("BATCH_TIMEOUT");
        }

        private void reject(String reason) {
            LOG.warn("operations rejected snapshot={} reason={} received={} offset={}",
                    snapshotId, reason, stationValues.size(), lastOffset);
            draining = true;
            stationValues.clear();
            if (gate != null) gate.clearRejected();
        }

        long publish() throws Exception {
            long next = lastOffset + 1;
            try {
                if (baseline == null) baseline = store.baseline(completed.snapshotAt());
                Instant now = clock.instant();
                processor.finish(completed, now, metadata, baseline.profiles(), baseline.datasetId(), now);
            } catch (JdbcOperationsStore.StalePublication old) {
                LOG.warn("operations rejected snapshot={} reason=OLDER_THAN_DURABLE_RELEASE", snapshotId);
                restore();
            }
            reset();
            return next;
        }

        private void reset() {
            snapshotId = null;
            headers = null;
            draining = false;
            metadata = null;
            gate = null;
            processor = null;
            completed = null;
            baseline = null;
            ingestedAt = null;
            stationValues.clear();
            rawCount = 0;
            duplicates = 0;
        }
    }
}
