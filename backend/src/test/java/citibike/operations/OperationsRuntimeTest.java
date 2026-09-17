package citibike.operations;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.kafka.clients.consumer.Consumer;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.clients.consumer.ConsumerRecords;
import org.apache.kafka.clients.consumer.OffsetAndMetadata;
import org.apache.kafka.clients.consumer.RetriableCommitFailedException;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.header.internals.RecordHeaders;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;
import org.mockito.ArgumentCaptor;
import org.springframework.dao.DataAccessResourceFailureException;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Clock;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;

import static citibike.operations.Operations.*;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class OperationsRuntimeTest {
    private static final TopicPartition PARTITION = new TopicPartition("operations-test", 0);
    private final ObjectMapper json = new ObjectMapper();
    private final AtomicLong nanos = new AtomicLong();
    private final AtomicReference<Release> durable = new AtomicReference<>();
    private final Clock clock = Clock.fixed(Instant.parse("2026-09-17T00:00:00Z"), ZoneOffset.UTC);
    private JdbcOperationsStore store;
    private OperationsWire wire;
    private List<JsonNode> records;
    @TempDir Path metadataDir;

    @BeforeEach
    void setup() throws Exception {
        records = Files.readAllLines(Path.of("../fixtures/day2/events.ndjson")).stream().map(line -> {
            try { return json.readTree(line); } catch (Exception invalid) { throw new AssertionError(invalid); }
        }).toList();
        String version = records.get(0).path("headers").path("metadata_version").asText();
        Files.copy(Path.of("../fixtures/day2/metadata.json"), metadataDir.resolve(version + ".json"));
        wire = new OperationsWire(json, metadataDir);
        store = mock(JdbcOperationsStore.class);
        when(store.latestRelease()).thenAnswer(call -> Optional.ofNullable(durable.get()));
        when(store.baseline(any())).thenReturn(new JdbcOperationsStore.Baseline(null, Map.of()));
        doAnswer(call -> { durable.set(call.getArgument(2)); return null; }).when(store).publish(anyList(), anyList(), any());
    }

    private OperationsRuntime.Session session(String mode) {
        var session = new OperationsRuntime.Session(store, wire, clock, nanos::get, mode);
        session.restore();
        return session;
    }

    private ConsumerRecord<byte[], byte[]> record(long offset, JsonNode envelope) throws Exception {
        RecordHeaders headers = new RecordHeaders();
        envelope.path("headers").fields().forEachRemaining(field ->
                headers.add(field.getKey(), field.getValue().asText().getBytes(StandardCharsets.UTF_8)));
        return new ConsumerRecord<>(PARTITION.topic(), 0, offset, 0,
                org.apache.kafka.common.record.TimestampType.CREATE_TIME, 0, 0,
                envelope.path("key").asText().getBytes(StandardCharsets.UTF_8),
                json.writeValueAsBytes(envelope.path("value")), headers, Optional.empty());
    }

    @Test
    void conflictingDiscardedWireFieldPoisonsSuffixAcrossRestart() throws Exception {
        ObjectNode changed = records.get(0).deepCopy();
        ((ObjectNode) changed.path("value")).put("num_bikes_disabled", 1);
        for (int restart = 0; restart < 2; restart++) {
            var session = session("recorded");
            assertEquals(-1, session.accept(record(0, records.get(0))));
            assertEquals(-1, session.accept(record(1, changed)));
            assertEquals(-1, session.accept(record(2, records.get(1))));
            assertEquals(4, session.accept(record(3, records.get(2))));
            assertFalse(session.ready());
        }
        verify(store, never()).publish(anyList(), anyList(), any());
        assertNull(durable.get());
    }

    @Test
    void newBoundaryCommitsOnlyThroughPriorBatchAndReprocessesTheNewRecord() throws Exception {
        var session = session("recorded");
        assertEquals(-1, session.accept(record(0, records.get(0))));
        ObjectNode next = records.get(0).deepCopy();
        ((ObjectNode) next.path("headers")).put("snapshot_id", "b".repeat(64));
        assertEquals(5, session.accept(record(5, next)));
        assertFalse(session.ready());
        ObjectNode end = records.get(2).deepCopy();
        ((ObjectNode) end.path("headers")).put("snapshot_id", "b".repeat(64));
        ((ObjectNode) end.path("value")).put("station_count", 1);
        assertEquals(-1, session.accept(record(6, end)));
        assertTrue(session.ready());
        assertEquals(7, session.publish());
        assertEquals("b".repeat(64), durable.get().snapshotId());
    }

    @Test
    void idleTimeoutDoesNotCommitARecoverableSuffix() throws Exception {
        var session = session("recorded");
        assertEquals(-1, session.accept(record(0, records.get(0))));
        nanos.set(Duration.ofSeconds(120).toNanos());
        session.expire();
        assertEquals(-1, session.accept(record(1, records.get(1))));
        assertEquals(3, session.accept(record(2, records.get(2))));
        assertFalse(session.ready());
        assertNull(durable.get());
    }

    @Test
    void completePublicationRetriesAfterDeadlineWithOneBaselineAndRestoresDuplicateClock() throws Exception {
        var session = session("recorded");
        for (int i = 0; i < 3; i++) assertEquals(-1, session.accept(record(i, records.get(i))));
        doThrow(new DataAccessResourceFailureException("offline"))
                .doAnswer(call -> { durable.set(call.getArgument(2)); return null; })
                .when(store).publish(anyList(), anyList(), any());
        assertThrows(DataAccessResourceFailureException.class, session::publish);
        assertNull(durable.get());
        nanos.set(Duration.ofSeconds(121).toNanos());
        session.expire();
        assertTrue(session.ready());
        assertEquals(3, session.publish());
        Release previous = durable.get();
        assertEquals(Instant.parse("2025-02-05T13:00:02Z"), previous.asOf());
        verify(store, times(1)).baseline(any());
        var restarted = session("recorded");
        for (int i = 0; i < 2; i++) assertEquals(-1, restarted.accept(record(i + 3, records.get(i))));
        assertEquals(6, restarted.accept(record(5, records.get(2))));
        assertFalse(restarted.ready());
        assertEquals(previous, durable.get());
        verify(store, times(2)).publish(anyList(), anyList(), any());
    }

    @Test
    void liveRejectsRecordedOriginWithoutPublishing() throws Exception {
        var session = session("live");
        for (int i = 0; i < 2; i++) assertEquals(-1, session.accept(record(i, records.get(i))));
        assertEquals(3, session.accept(record(2, records.get(2))));
        assertFalse(session.ready());
        assertNull(durable.get());
    }

    @Test
    @SuppressWarnings("unchecked")
    void nextSnapshotLoadsItsOwnMetadataVersion() throws Exception {
        var session = session("recorded");
        for (int i = 0; i < 3; i++) session.accept(record(i, records.get(i)));
        session.publish();
        JsonNode metadata = json.readTree(Files.readAllBytes(Path.of("../fixtures/day2/metadata.json")));
        ((ObjectNode) metadata.get(0)).put("station_name", "更新后的站名");
        byte[] bytes = json.writeValueAsBytes(metadata);
        String version = java.util.HexFormat.of().formatHex(java.security.MessageDigest.getInstance("SHA-256").digest(bytes));
        Files.write(metadataDir.resolve(version + ".json"), bytes);
        for (int i = 0; i < 3; i++) {
            ObjectNode next = records.get(i).deepCopy();
            ((ObjectNode) next.path("headers")).put("snapshot_id", "c".repeat(64)).put("metadata_version", version);
            ObjectNode value = (ObjectNode) next.path("value");
            value.put("snapshot_at_utc", "2025-02-05T13:01:00Z").put("ingested_at_utc", "2025-02-05T13:01:02Z");
            if (i < 2) value.put("snapshot_at_local", "2025-02-05T08:01:00-05:00");
            assertEquals(-1, session.accept(record(i + 3, next)));
        }
        assertEquals(6, session.publish());
        ArgumentCaptor<List<Risk>> rows = ArgumentCaptor.forClass(List.class);
        verify(store, times(2)).publish(rows.capture(), anyList(), any());
        assertEquals("样例乙站", rows.getAllValues().get(0).get(0).metadata().name());
        assertEquals("更新后的站名", rows.getAllValues().get(1).get(0).metadata().name());
        assertEquals(version, durable.get().metadataVersion());
    }

    @Test
    @SuppressWarnings("unchecked")
    void offsetFailureRetriesTheSameCommitWithoutPollingOrRepublishing() throws Exception {
        Consumer<byte[], byte[]> kafka = mock(Consumer.class);
        when(kafka.poll(any(Duration.class))).thenReturn(
                new ConsumerRecords<>(Map.of(PARTITION, List.of(record(0, records.get(0))))),
                new ConsumerRecords<>(Map.of(PARTITION, List.of(record(1, records.get(1))))),
                new ConsumerRecords<>(Map.of(PARTITION, List.of(record(2, records.get(2))))));
        doThrow(new RetriableCommitFailedException("offset temporarily unavailable")).doNothing()
                .when(kafka).commitSync(anyMap());
        var runtime = new OperationsRuntime(kafka, PARTITION, session("recorded"));
        for (int i = 0; i < 4; i++) runtime.cycle();
        assertNotNull(durable.get());
        assertThrows(RetriableCommitFailedException.class, runtime::cycle);
        runtime.cycle();
        verify(kafka, times(3)).poll(any(Duration.class));
        verify(store, times(1)).publish(anyList(), anyList(), any());
        ArgumentCaptor<Map<TopicPartition, OffsetAndMetadata>> commits = ArgumentCaptor.forClass(Map.class);
        verify(kafka, times(2)).commitSync(commits.capture());
        for (var commit : commits.getAllValues()) assertEquals(3, commit.get(PARTITION).offset());
    }

}
