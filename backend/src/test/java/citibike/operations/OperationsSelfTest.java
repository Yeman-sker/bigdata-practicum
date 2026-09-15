package citibike.operations;

import java.time.Instant;
import java.util.*;

import static citibike.operations.Operations.*;

/** Dependency-free Checkpoint A executable: java -ea ...OperationsSelfTest. */
public final class OperationsSelfTest {
    private static final Instant AS_OF = Instant.parse("2025-02-05T13:00:20Z");
    public static void main(String[] args) {
        boundaries();
        forecastAndRebalance();
        batchGate();
        expiryView();
        System.out.println("OperationsSelfTest: OK");
    }
    private static void boundaries() {
        for (double[] x : new double[][]{{0,.15},{.15,.15},{.16,.30},{.30,.30},{.31,.31},{.69,.69},{.70,.70},{.84,.84},{.85,.85},{1,1}})
            assert RiskCalculator.classify(x[0]) == expected(x[1]);
    }
    private static Status expected(double r) { return r <= .15 ? Status.SHORTAGE_RISK : r <= .30 ? Status.LOW_INVENTORY : r < .70 ? Status.HEALTHY : r < .85 ? Status.HIGH_INVENTORY : Status.OVERFLOW_RISK; }
    private static void forecastAndRebalance() {
        RiskCalculator c = new RiskCalculator();
        Metadata source = new Metadata("source", "S", 40.7, -74.001, 40);
        Metadata target = new Metadata("target", "T", 40.7, -74.0, 40);
        Observation so = new Observation("source", AS_OF.minusSeconds(20), AS_OF.minusSeconds(20), 34, 6, true,true,true);
        Observation to = new Observation("target", AS_OF.minusSeconds(20), AS_OF.minusSeconds(20), 6, 34, true,true,true);
        Map<DayHour, Profile> ps = Map.of(new DayHour(3, 8), new Profile(2, 0, 2, 2));
        Risk sr = c.calculate(so, source, ps, AS_OF, "dataset");
        Risk tr = c.calculate(to, target, Map.of(new DayHour(3, 8), new Profile(0, 2, -2, 2)), AS_OF, "dataset");
        assert sr.forecastStatus() == Status.OVERFLOW_RISK;
        assert tr.forecastStatus() == Status.SHORTAGE_RISK;
        List<Suggestion> s = rebalance("snapshot", List.of(sr,tr), AS_OF);
        assert s.size() == 1 && s.get(0).moveBikes() == 8 && s.get(0).distanceMeters() == 84;
    }
    private static void batchGate() {
        String id = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        String mv = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";
        Headers h = new Headers("1.1", "station", id, mv, "FIXTURE");
        Observation o = new Observation("s", AS_OF.minusSeconds(20), AS_OF.minusSeconds(20), 1, 9, true,true,true);
        BatchConsumer gate = new BatchConsumer(Set.of("s"), "recorded");
        assert gate.acceptStation(new StationEvent("s", h, o), AS_OF).outcome() == BatchOutcome.BUFFERED;
        Headers eh = new Headers("1.1", "snapshot_end", id, mv, "FIXTURE");
        BatchResult result = gate.acceptEnd(new SnapshotEnd("__snapshot_end__", eh, 1, o.snapshotAt(), AS_OF), AS_OF);
        assert result.outcome() == BatchOutcome.PUBLISH;
        gate.markPublished(o.snapshotAt());
        assert gate.acceptStation(new StationEvent("s", h, o), AS_OF).outcome() == BatchOutcome.IGNORE_DUPLICATE;
        assert new BatchConsumer(Set.of("s"), "live").acceptStation(new StationEvent("s", h, o), AS_OF).outcome()
                == BatchOutcome.REJECT_KEEP_PREVIOUS;
        final boolean[] attempted = {false};
        BatchConsumer retryGate = new BatchConsumer(Set.of("s"), "recorded");
        retryGate.acceptStation(new StationEvent("s", h, o), AS_OF);
        BatchProcessor processor = new BatchProcessor(retryGate, (risks, suggestions, release) -> {
            attempted[0] = true; throw new Exception("simulated transaction failure");
        });
        try {
            processor.finish(new SnapshotEnd("__snapshot_end__", eh, 1, o.snapshotAt(), AS_OF), AS_OF,
                    Map.of("s", new Metadata("s", "S", 40.7, -74.0, 10)), Map.of(), "dataset", AS_OF);
            throw new AssertionError("publisher failure was swallowed");
        } catch (Exception expected) { assert attempted[0]; }
        assert retryGate.acceptEnd(new SnapshotEnd("__snapshot_end__", eh, 1, o.snapshotAt(), AS_OF), AS_OF).outcome()
                == BatchOutcome.PUBLISH;
        String emptyId = "fedcba9876543210fedcba9876543210fedcba9876543210fedcba9876543210";
        Headers emptyHeaders = new Headers("1.1", "snapshot_end", emptyId, mv, "FIXTURE");
        assert new BatchConsumer(Set.of(), "recorded").acceptEnd(new SnapshotEnd("__snapshot_end__", emptyHeaders, 0,
                o.snapshotAt(), AS_OF), AS_OF).outcome() == BatchOutcome.PUBLISH;
    }

    private static void expiryView() {
        RiskCalculator c = new RiskCalculator();
        Instant observed = AS_OF.minusSeconds(20);
        Observation o = new Observation("s", observed, observed, 5, 5, true, true, true);
        Metadata m = new Metadata("s", "S", 40.7, -74.0, 10);
        Risk risk = c.calculate(o, m, Map.of(new DayHour(3, 8), new Profile(0, 0, 0, 1)), AS_OF, "dataset");
        Risk expired = forRead(risk, risk.expiresAt());
        assert expired.currentStatus() == Status.STALE_DATA && expired.projectedBikes() == null;
        Suggestion suggestion = new Suggestion("x", "snap", "s", "s", 1, 1, 1, 0, 1, AS_OF, risk.expiresAt());
        assert suggestionsForRead(List.of(suggestion), Map.of("s", risk), risk.expiresAt()).isEmpty();
    }
}
