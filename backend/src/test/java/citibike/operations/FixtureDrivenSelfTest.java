package citibike.operations;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.*;

import static citibike.operations.Operations.*;

/** Reads the shared day-2 JSON without a third-party test dependency. */
public final class FixtureDrivenSelfTest {
    private static final Instant AS_OF = Instant.parse("2025-02-05T13:00:20Z");
    private FixtureDrivenSelfTest() {}

    public static void main(String[] args) throws Exception {
        Map<String, Object> root = castMap(new Json(Files.readString(Path.of(args.length == 0 ?
                "fixtures/day2/cases.json" : args[0])).trim()).parse());
        testRisk(castList(root.get("risk")));
        testRebalance(castList(root.get("rebalance")));
        testBatches(castList(root.get("batches")));
        System.out.println("FixtureDrivenSelfTest: 22 risk, 9 rebalance, 12 batches OK");
    }

    private static void testRisk(List<Object> cases) {
        assert cases.size() == 22;
        RiskCalculator calculator = new RiskCalculator();
        for (Object raw : cases) {
            Map<String,Object> c = castMap(raw), in = castMap(c.get("input")), expected = castMap(c.get("expected"));
            boolean flags = bool(in.get("flags"));
            Integer bikes = integer(in.get("bikes")), docks = integer(in.get("docks"));
            int age = integer(in.get("age_seconds"));
            Instant observed = AS_OF.minusSeconds(age);
            Observation o = new Observation("fixture", observed, observed, bikes, docks, flags, flags, flags);
            Integer capacity = integer(in.get("capacity"));
            Metadata m = new Metadata("fixture", "fixture", 40.7, -74.0, capacity);
            Integer net = integer(in.get("net"));
            Map<DayHour, Profile> profiles = net == null ? Map.of() : Map.of(new DayHour(3, 8),
                    new Profile(0, 0, net, integer(in.get("sample_days"))));
            Risk result = calculator.calculate(o, m, profiles, AS_OF, "dataset");
            check(result.currentStatus().name().equals(expected.get("current")), c, "current");
            check(result.forecastStatus().name().equals(expected.get("forecast")), c, "forecast");
            Object projected = expected.get("projected");
            check(projected == null ? result.projectedBikes() == null :
                    Math.abs(result.projectedBikes() - number(projected)) < 1e-9, c, "projected");
        }
    }

    private static void testRebalance(List<Object> cases) {
        assert cases.size() == 9;
        RiskCalculator calculator = new RiskCalculator();
        for (Object raw : cases) {
            Map<String,Object> c = castMap(raw); List<Object> rawStations = castList(c.get("stations"));
            List<Risk> risks = new ArrayList<>();
            for (Object stationRaw : rawStations) {
                Map<String,Object> s = castMap(stationRaw); String id = string(s.get("station_id"));
                Integer bikes = integer(s.get("bikes")), docks = integer(s.get("docks"));
                Metadata m = new Metadata(id, id, nullableNumber(s.get("lat")), nullableNumber(s.get("lon")), integer(s.get("capacity")));
                Observation o = new Observation(id, AS_OF.minusSeconds(20), AS_OF.minusSeconds(20), bikes, docks, true, true, true);
                double projected = number(s.get("projected"));
                risks.add(calculator.calculate(o, m, Map.of(new DayHour(3, 8),
                        new Profile(0, 0, projected - bikes, 1)), AS_OF, "dataset"));
            }
            List<Object> expected = castList(c.get("expected"));
            List<Suggestion> actual = Operations.rebalance("fixture", risks, AS_OF);
            check(actual.size() == expected.size(), c, "suggestion count");
            for (int i = 0; i < actual.size(); i++) {
                Suggestion a = actual.get(i); Map<String,Object> e = castMap(expected.get(i));
                check(a.fromStationId().equals(e.get("source")) && a.toStationId().equals(e.get("target"))
                        && a.moveBikes() == integer(e.get("move")) && a.fromSurplus() == integer(e.get("from_surplus"))
                        && a.toDeficit() == integer(e.get("to_deficit")), c, "suggestion " + i);
            }
        }
    }

    private static void testBatches(List<Object> cases) throws Exception {
        assert cases.size() == 12;
        for (Object raw : cases) {
            Map<String,Object> c = castMap(raw); List<Object> records = castList(c.get("records"));
            Set<String> ids = new HashSet<>();
            for (Object x : records) { Map<String,Object> r = castMap(x); Map<String,Object> v = castMap(r.get("value"));
                if ("station".equals(castMap(r.get("headers")).get("record_type"))) ids.add(string(v.get("station_id"))); }
            String mode = "live".equals(c.get("backend_profile")) ? "live" : "recorded";
            String published = (String)c.get("published_snapshot_id");
            Instant publishedAt = c.get("published_snapshot_at_utc") == null ? null : Instant.parse(string(c.get("published_snapshot_at_utc")));
            Instant publishedAsOf = c.get("published_as_of_utc") == null ? null : Instant.parse(string(c.get("published_as_of_utc")));
            String metadataVersion = string(castMap(castMap(records.get(0)).get("headers")).get("metadata_version"));
            BatchConsumer gate = new BatchConsumer(ids, mode, published, publishedAt, metadataVersion);
            java.util.concurrent.atomic.AtomicReference<Instant> durableAsOf =
                    new java.util.concurrent.atomic.AtomicReference<>(publishedAsOf);
            Map<String, Metadata> metadata = new HashMap<>();
            ids.forEach(id -> metadata.put(id, new Metadata(id, id, 40.7, -74.0, 40)));
            BatchProcessor processor = new BatchProcessor(gate,
                    (risks, suggestions, release) -> durableAsOf.set(release.asOf()), metadataVersion);
            BatchResult last = null;
            for (Object x : records) {
                Map<String,Object> r = castMap(x), h = castMap(r.get("headers"));
                Instant received = AS_OF;
                if ("station".equals(h.get("record_type"))) {
                    Map<String,Object> v = castMap(r.get("value"));
                    Instant at = Instant.parse(string(v.get("snapshot_at_utc")));
                    Observation o = new Observation(string(v.get("station_id")), at, at, integer(v.get("num_bikes_available")), integer(v.get("num_docks_available")), true,true,true);
                    last = gate.acceptStation(new StationEvent(string(r.get("key")), headers(h), o), received);
                } else {
                    Map<String,Object> v = castMap(r.get("value"));
                    last = processor.finish(new SnapshotEnd(string(r.get("key")), headers(h), integer(v.get("station_count")),
                            Instant.parse(string(v.get("snapshot_at_utc"))), Instant.parse(string(v.get("ingested_at_utc")))),
                            received, metadata, Map.of(), null, received);
                }
            }
            if ("timeout".equals(c.get("name"))) last = gate.timeout(AS_OF.plusSeconds(integer(c.get("advance_wall_seconds"))));
            String expected = string(c.get("expected"));
            BatchOutcome wanted = "PUBLISH".equals(expected) ? BatchOutcome.PUBLISH : "IGNORE_DUPLICATE".equals(expected)
                    ? BatchOutcome.IGNORE_DUPLICATE : BatchOutcome.REJECT_KEEP_PREVIOUS;
            check(last != null && last.outcome() == wanted, c, "batch outcome");
            if (c.get("expected_as_of_utc") != null) {
                Instant expectedAsOf = Instant.parse(string(c.get("expected_as_of_utc")));
                check(expectedAsOf.equals(durableAsOf.get()), c, "failed batch preserves published as_of");
            }
        }
    }

    private static Headers headers(Map<String,Object> h) { return new Headers(string(h.get("contract_version")), string(h.get("record_type")),
            string(h.get("snapshot_id")), string(h.get("metadata_version")), string(h.get("data_origin"))); }
    private static void check(boolean ok, Map<String,Object> c, String what) { if (!ok) throw new AssertionError(c.get("name") + ": " + what); }
    private static String string(Object x) { return x == null ? null : (String)x; }
    private static boolean bool(Object x) { return (Boolean)x; }
    private static Integer integer(Object x) { return x == null ? null : ((Number)x).intValue(); }
    private static double number(Object x) { return ((Number)x).doubleValue(); }
    private static Double nullableNumber(Object x) { return x == null ? null : number(x); }
    @SuppressWarnings("unchecked") private static Map<String,Object> castMap(Object x) { return (Map<String,Object>)x; }
    @SuppressWarnings("unchecked") private static List<Object> castList(Object x) { return (List<Object>)x; }

    private static final class Json {
        private final String s; private int i;
        Json(String s) { this.s = s; }
        Object parse() { ws(); Object v = value(); ws(); if (i != s.length()) throw error(); return v; }
        private Object value() { ws(); char c=s.charAt(i); if(c=='{')return object(); if(c=='[')return array(); if(c=='"')return str(); if(c=='t'){lit("true");return true;} if(c=='f'){lit("false");return false;} if(c=='n'){lit("null");return null;} return num(); }
        private Map<String,Object> object(){ Map<String,Object> m=new LinkedHashMap<>(); i++; ws(); if(s.charAt(i)=='}'){i++;return m;} while(true){ws();String k=str();ws();if(s.charAt(i++)!=':')throw error();m.put(k,value());ws();if(s.charAt(i)=='}'){i++;return m;}if(s.charAt(i++)!=',')throw error();} }
        private List<Object> array(){ List<Object> a=new ArrayList<>(); i++;ws();if(s.charAt(i)==']'){i++;return a;}while(true){a.add(value());ws();if(s.charAt(i)==']'){i++;return a;}if(s.charAt(i++)!=',')throw error();} }
        private String str(){if(s.charAt(i++)!='"')throw error();StringBuilder b=new StringBuilder();while(i<s.length()){char c=s.charAt(i++);if(c=='"')return b.toString();if(c=='\\'){char e=s.charAt(i++);b.append(e=='n'?'\n':e=='r'?'\r':e=='t'?'\t':e);}else b.append(c);}throw error();}
        private Number num(){int st=i;while(i<s.length()&&"-+0123456789.eE".indexOf(s.charAt(i))>=0)i++;String x=s.substring(st,i);return x.indexOf('.')>=0||x.indexOf('e')>=0||x.indexOf('E')>=0?Double.valueOf(x):Long.valueOf(x);}
        private void lit(String x){if(!s.startsWith(x,i))throw error();i+=x.length();} private void ws(){while(i<s.length()&&Character.isWhitespace(s.charAt(i)))i++;} private IllegalArgumentException error(){return new IllegalArgumentException("invalid JSON at "+i);}
    }
}
