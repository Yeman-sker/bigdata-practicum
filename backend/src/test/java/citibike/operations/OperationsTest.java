package citibike.operations;

import org.junit.jupiter.api.Test;

import java.time.Instant;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static citibike.operations.Operations.*;
import static org.junit.jupiter.api.Assertions.*;

class OperationsTest {
    @Test
    void completeBatchSurvivesDelayedPublicationRetryWithoutAdvancingRecordedClock() throws Exception {
        Instant now = Instant.parse("2025-02-05T13:00:02Z");
        String id = "a".repeat(64), version = "b".repeat(64);
        BatchConsumer gate = new BatchConsumer(Set.of("s"), "recorded", null, null, version);
        gate.acceptStation(new StationEvent("s", new Headers("1.1", "station", id, version, "FIXTURE"),
                new Observation("s", now.minusSeconds(2), now.minusSeconds(2), 5, 5, true, true, true)), now);
        SnapshotEnd end = new SnapshotEnd("__snapshot_end__",
                new Headers("1.1", "snapshot_end", id, version, "FIXTURE"), 1, now.minusSeconds(2), now);
        AtomicInteger attempts = new AtomicInteger();
        AtomicReference<Release> durable = new AtomicReference<>();
        BatchProcessor processor = new BatchProcessor(gate, (risks, suggestions, release) -> {
            if (attempts.incrementAndGet() == 1) throw new Exception("database unavailable");
            durable.set(release);
        }, version);
        Map<String, Metadata> metadata = Map.of("s", new Metadata("s", "Station", 40.7, -74.0, 10));
        assertThrows(Exception.class, () -> processor.finish(end, now, metadata, Map.of(), null, now));
        assertNull(durable.get());
        Instant retryAt = now.plusSeconds(121);
        assertEquals(BatchOutcome.BUFFERED, gate.timeout(retryAt).outcome());
        assertEquals(BatchOutcome.PUBLISH, processor.finish(end, retryAt, metadata, Map.of(), null, retryAt).outcome());
        assertEquals(2, attempts.get());
        assertEquals(now, durable.get().asOf());
        assertEquals(retryAt, durable.get().publishedAt());
        assertEquals(BatchOutcome.IGNORE_DUPLICATE, gate.acceptEnd(end, retryAt).outcome());
        assertEquals(now, durable.get().asOf());
    }

    @Test
    void contractSelfChecksRunInMaven() throws Exception {
        OperationsSelfTest.main(new String[0]);
        FixtureDrivenSelfTest.main(new String[]{"../fixtures/day2/cases.json"});
    }
}
