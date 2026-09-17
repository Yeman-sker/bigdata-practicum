package citibike.operations;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.time.Instant;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;

import static org.junit.jupiter.api.Assertions.*;

class OperationsWireTest {
    private static final Path FIXTURES = Path.of("../fixtures/day2");
    private static final String VERSION = "84fb15f78a673f21687fce423e223a2970f7276903398e6e0c19c502ab1ea41b";
    private final ObjectMapper mapper = new ObjectMapper();
    @TempDir Path metadataDir;
    private OperationsWire wire;

    @BeforeEach
    void setup() throws Exception {
        Files.copy(FIXTURES.resolve("metadata.json"), metadataDir.resolve(VERSION + ".json"));
        wire = new OperationsWire(mapper, metadataDir);
    }

    @Test
    void readsNativeFixtureRecordsAndOriginalMetadataBytes() throws Exception {
        OperationsWire.Decoded first = wire.decode(record(envelope(0)));
        assertEquals("4199.12", first.station().key());
        assertEquals(34, first.station().observation().bikes());
        assertEquals(6, first.station().observation().docks());
        assertEquals(Instant.parse("2025-02-05T12:59:40Z"), first.station().observation().lastReportedAt());
        assertEquals(Instant.parse("2025-02-05T13:00:02Z"), first.ingestedAt());
        assertEquals("2.3", first.value().get("source_version").textValue());
        assertNull(first.end());
        assertEquals("5484.09", wire.decode(record(envelope(1))).station().key());
        OperationsWire.Decoded end = wire.decode(record(envelope(2)));
        assertNull(end.station());
        assertEquals(2, end.end().stationCount());
        assertEquals(first.ingestedAt(), end.end().ingestedAt());
        Map<String, Operations.Metadata> metadata = wire.metadata(VERSION);
        assertEquals(List.of("4199.12", "5484.09"), List.copyOf(metadata.keySet()));
        assertEquals(new Operations.Metadata("4199.12", "样例乙站", 40.7, -74.001, 40), metadata.get("4199.12"));
        assertThrows(UnsupportedOperationException.class, () -> metadata.clear());
        assertNotSame(metadata, wire.metadata(VERSION));
    }

    @Test
    void requiresExactlyOneOfEachNativeHeaderAndValidContractValues() throws Exception {
        for (String name : List.of("contract_version", "record_type", "snapshot_id", "metadata_version", "data_origin")) {
            ConsumerRecord<byte[], byte[]> missing = record(envelope(0));
            missing.headers().remove(name);
            rejected(missing);
            ConsumerRecord<byte[], byte[]> duplicate = record(envelope(0));
            duplicate.headers().add(name, duplicate.headers().lastHeader(name).value());
            rejected(duplicate);
            ConsumerRecord<byte[], byte[]> nullable = record(envelope(0));
            nullable.headers().remove(name).add(name, null);
            rejected(nullable);
        }
        ConsumerRecord<byte[], byte[]> extra = record(envelope(0));
        extra.headers().add("extra", bytes("ignored"));
        rejected(extra);
        for (Map.Entry<String, String> invalid : Map.of("contract_version", "1.0", "record_type", "end",
                "snapshot_id", "A".repeat(64), "metadata_version", "b".repeat(63), "data_origin", "LIVE").entrySet()) {
            ObjectNode input = envelope(0);
            ((ObjectNode) input.get("headers")).put(invalid.getKey(), invalid.getValue());
            rejected(record(input));
        }
        for (String origin : List.of("GBFS_LIVE", "GBFS_REPLAY", "FIXTURE")) {
            ObjectNode input = envelope(0);
            ((ObjectNode) input.get("headers")).put("data_origin", origin);
            assertEquals(origin, wire.decode(record(input)).headers().dataOrigin());
        }
    }

    @Test
    void rejectsMalformedUtf8DuplicatePropertiesTrailingJsonAndEnvelopeValues() throws Exception {
        byte[] malformed = {(byte) 0xc3, 0x28};
        ConsumerRecord<byte[], byte[]> badHeader = record(envelope(0));
        badHeader.headers().remove("data_origin").add("data_origin", malformed);
        rejected(badHeader);
        ConsumerRecord<byte[], byte[]> valid = record(envelope(0));
        rejected(raw(valid, malformed, valid.value()));
        rejected(raw(valid, valid.key(), malformed));
        rejected(raw(valid, null, valid.value()));
        rejected(raw(valid, valid.key(), null));
        String value = new String(valid.value(), StandardCharsets.UTF_8);
        rejected(raw(valid, valid.key(), bytes(value + " {}")));
        rejected(raw(valid, valid.key(), bytes(value.replace("\"station_id\":", "\"station_id\":\"4199.12\",\"station_id\":"))));
        rejected(raw(valid, valid.key(), mapper.writeValueAsBytes(envelope(0))));
        rejected(raw(valid, valid.key(), bytes("[]")));
        rejected(raw(valid, valid.key(), bytes("null")));
        ObjectMapper lenient = new ObjectMapper().enable(JsonParser.Feature.ALLOW_COMMENTS);
        OperationsWire strict = new OperationsWire(lenient, metadataDir);
        assertThrows(IllegalArgumentException.class, () -> strict.decode(raw(valid, valid.key(), bytes("/* comment */" + value))));
        assertTrue(lenient.isEnabled(JsonParser.Feature.ALLOW_COMMENTS));
    }

    @Test
    void validatesEveryStationFieldWithoutCoercionAndRetainsQualityAnomalies() throws Exception {
        ObjectNode base = (ObjectNode) envelope(0).get("value");
        for (var fields = base.fieldNames(); fields.hasNext();) {
            String field = fields.next();
            rejectStation(value -> value.remove(field));
        }
        rejectStation(value -> value.put("extra", true));
        for (String field : List.of("num_bikes_available", "num_docks_available", "num_bikes_disabled", "num_docks_disabled")) {
            rejectStation(value -> value.put(field, "0"));
            rejectStation(value -> value.put(field, true));
            rejectStation(value -> value.put(field, 1.5));
            rejectStation(value -> value.put(field, Long.MAX_VALUE));
        }
        for (String field : List.of("is_installed", "is_renting", "is_returning")) {
            rejectStation(value -> value.put(field, 1));
            rejectStation(value -> value.putNull(field));
        }
        rejectStation(value -> value.putNull("num_bikes_available"));
        rejectStation(value -> value.put("source_version", "3.0"));
        rejectStation(value -> value.put("source_version", 2.3));
        rejectStation(value -> value.put("station_id", 4199));
        rejectStation(value -> value.put("station_id", ""));
        rejectStation(value -> value.put("station_id", "different"));
        rejectStation(value -> value.put("station_id", "a".repeat(129)));
        ObjectNode badKey = envelope(0);
        badKey.put("key", "5484.09");
        rejected(record(badKey));
        ObjectNode accepted = envelope(0);
        ObjectNode value = (ObjectNode) accepted.get("value");
        value.put("num_bikes_available", -2).put("num_docks_available", -3).put("num_bikes_disabled", -4);
        value.putNull("num_docks_disabled").putNull("last_reported_at_utc");
        OperationsWire.Decoded decoded = wire.decode(record(accepted));
        assertEquals(-2, decoded.station().observation().bikes());
        assertEquals(-3, decoded.station().observation().docks());
        assertEquals(-4, decoded.value().get("num_bikes_disabled").intValue());
        assertNull(decoded.station().observation().lastReportedAt());
        value.putNull("num_docks_available").putNull("num_bikes_disabled");
        assertNull(wire.decode(record(accepted)).station().observation().docks());
    }

    @Test
    void requiresUtcSpellingAndActualNewYorkTimeIncludingDst() throws Exception {
        for (String field : List.of("snapshot_at_utc", "last_reported_at_utc", "ingested_at_utc")) {
            rejectStation(value -> value.put(field, 1738760400));
            rejectStation(value -> value.put(field, "2025-02-05T13:00:00+00:00"));
            rejectStation(value -> value.put(field, "2025-02-30T13:00:00Z"));
            rejectStation(value -> value.put(field, "2025-02-05T13:00Z"));
        }
        rejectStation(value -> value.putNull("snapshot_at_utc"));
        rejectStation(value -> value.putNull("ingested_at_utc"));
        rejectStation(value -> value.putNull("snapshot_at_local"));
        rejectStation(value -> value.put("snapshot_at_local", "2025-02-05T09:00:00-04:00"));
        rejectStation(value -> value.put("snapshot_at_local", "2025-02-05T08:00:01-05:00"));
        rejectStation(value -> value.put("snapshot_at_local", "2025-02-05T13:00:00Z"));
        for (Map.Entry<String, String> time : Map.of(
                "2025-03-09T06:59:59Z", "2025-03-09T01:59:59-05:00",
                "2025-03-09T07:00:00Z", "2025-03-09T03:00:00-04:00",
                "2025-11-02T05:30:00Z", "2025-11-02T01:30:00-04:00",
                "2025-11-02T06:30:00Z", "2025-11-02T01:30:00-05:00").entrySet()) {
            ObjectNode input = envelope(0);
            ((ObjectNode) input.get("value")).put("snapshot_at_utc", time.getKey()).put("snapshot_at_local", time.getValue());
            assertEquals(Instant.parse(time.getKey()), wire.decode(record(input)).station().observation().snapshotAt());
        }
    }

    @Test
    void retainsFieldsOmittedFromObservationForDuplicateComparison() throws Exception {
        OperationsWire.Decoded original = wire.decode(record(envelope(0)));
        for (Consumer<ObjectNode> mutation : List.<Consumer<ObjectNode>>of(
                value -> value.put("num_bikes_disabled", 1),
                value -> value.put("num_docks_disabled", 1),
                value -> value.put("ingested_at_utc", "2025-02-05T13:00:03Z"),
                value -> value.put("snapshot_at_local", "2025-02-05T08:00:00.000-05:00"))) {
            ObjectNode input = envelope(0);
            mutation.accept((ObjectNode) input.get("value"));
            OperationsWire.Decoded changed = wire.decode(record(input));
            assertEquals(original.station().observation(), changed.station().observation());
            assertNotEquals(original.value(), changed.value());
        }
        assertEquals(original.value(), wire.decode(record(envelope(0))).value());
    }

    @Test
    void validatesEndFieldsAndUsesOnlyUnambiguousPoisonBoundaries() throws Exception {
        for (int count : List.of(0, 5000)) {
            ObjectNode input = envelope(2);
            ((ObjectNode) input.get("value")).put("station_count", count);
            assertEquals(count, wire.decode(record(input)).end().stationCount());
        }
        for (Consumer<ObjectNode> mutation : List.<Consumer<ObjectNode>>of(
                value -> value.put("station_count", -1), value -> value.put("station_count", 5001),
                value -> value.put("station_count", 2.0), value -> value.put("station_count", "2"),
                value -> value.putNull("station_count"), value -> value.remove("ingested_at_utc"),
                value -> value.put("extra", 1), value -> value.put("snapshot_at_utc", false))) {
            ObjectNode input = envelope(2);
            mutation.accept((ObjectNode) input.get("value"));
            rejected(record(input));
        }
        ConsumerRecord<byte[], byte[]> end = record(envelope(2));
        assertTrue(OperationsWire.isEnd(end));
        assertEquals(envelope(2).get("headers").get("snapshot_id").textValue(), OperationsWire.boundaryId(end));
        ConsumerRecord<byte[], byte[]> badJsonEnd = raw(end, end.key(), bytes("{"));
        assertTrue(OperationsWire.isEnd(badJsonEnd));
        assertNotNull(OperationsWire.boundaryId(badJsonEnd));
        rejected(badJsonEnd);
        ConsumerRecord<byte[], byte[]> badKey = raw(end, bytes("other"), end.value());
        assertFalse(OperationsWire.isEnd(badKey));
        rejected(badKey);
        end.headers().add("record_type", bytes("snapshot_end"));
        assertFalse(OperationsWire.isEnd(end));
        end.headers().add("snapshot_id", bytes("a".repeat(64)));
        assertNull(OperationsWire.boundaryId(end));
        ConsumerRecord<byte[], byte[]> invalidId = record(envelope(0));
        invalidId.headers().remove("snapshot_id").add("snapshot_id", bytes("A".repeat(64)));
        assertNull(OperationsWire.boundaryId(invalidId));
        invalidId.headers().remove("snapshot_id").add("snapshot_id", new byte[]{(byte) 0xff});
        assertNull(OperationsWire.boundaryId(invalidId));
        assertFalse(OperationsWire.isEnd(null));
        assertNull(OperationsWire.boundaryId(null));
    }

    @Test
    void hashesUnmodifiedMetadataBeforeParsingAndReloadsEachVersion() throws Exception {
        assertThrows(IllegalArgumentException.class, () -> wire.metadata("../metadata"));
        assertEquals("METADATA_UNREADABLE", assertThrows(IllegalArgumentException.class,
                () -> wire.metadata("a".repeat(64))).getMessage());
        Files.writeString(metadataDir.resolve(VERSION + ".json"), "{");
        assertEquals("METADATA_HASH", assertThrows(IllegalArgumentException.class,
                () -> wire.metadata(VERSION)).getMessage());
        byte[] original = Files.readAllBytes(FIXTURES.resolve("metadata.json"));
        String prettyVersion = save(original);
        byte[] compact = mapper.writeValueAsBytes(mapper.readTree(original));
        String compactVersion = save(compact);
        assertNotEquals(prettyVersion, compactVersion);
        assertEquals(wire.metadata(prettyVersion), wire.metadata(compactVersion));
        ArrayNode changed = metadataRows();
        ((ObjectNode) changed.get(0)).put("capacity", 60);
        String changedVersion = save(mapper.writeValueAsBytes(changed));
        assertEquals(60, wire.metadata(changedVersion).get("4199.12").capacity());
        assertEquals(40, wire.metadata(prettyVersion).get("4199.12").capacity());
        Files.writeString(metadataDir.resolve(changedVersion + ".json"), "[]");
        assertEquals("METADATA_HASH", assertThrows(IllegalArgumentException.class,
                () -> wire.metadata(changedVersion)).getMessage());
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(save(new byte[]{(byte) 0xff})));
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(save(bytes("[] []"))));
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(save(bytes("{}"))));
        String duplicate = new String(original, StandardCharsets.UTF_8).replace("\"capacity\": 40", "\"capacity\": 40, \"capacity\": 40");
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(save(bytes(duplicate))));
    }

    @Test
    void validatesMetadataSchemaOrderingMappingAndNullableFields() throws Exception {
        ObjectNode row = (ObjectNode) metadataRows().get(0);
        for (var fields = row.fieldNames(); fields.hasNext();) {
            String field = fields.next();
            rejectMetadata(rows -> ((ObjectNode) rows.get(0)).remove(field));
        }
        for (Consumer<ObjectNode> mutation : List.<Consumer<ObjectNode>>of(
                value -> value.put("extra", 1), value -> value.put("provider_station_id", ""),
                value -> value.put("station_id", "bad id"), value -> value.put("station_id", "a".repeat(129)),
                value -> value.put("station_name", "a".repeat(513)), value -> value.put("station_name", 1),
                value -> value.put("lat", 91), value -> value.put("lon", -181), value -> value.put("lat", "40.7"),
                value -> value.put("lat", new BigDecimal("90.00000000000000001")),
                value -> value.put("capacity", 0), value -> value.put("capacity", 1.5), value -> value.put("capacity", true),
                value -> value.put("region_id", ""), value -> value.put("region_id", 1),
                value -> value.put("is_current", "true"), value -> value.put("is_current", false),
                value -> value.put("metadata_source", "HISTORICAL"), value -> value.put("metadata_updated_at", "2025-02-05T12:59:00+00:00"),
                value -> value.put("mapping_status", "UNKNOWN"), value -> value.put("mapping_reason", "MISSING_SHORT_NAME"),
                value -> value.put("mapping_status", "ISOLATED").put("mapping_reason", "UNKNOWN"),
                value -> value.put("mapping_status", "ISOLATED").put("mapping_reason", "MISSING_SHORT_NAME")))
            rejectMetadata(rows -> mutation.accept((ObjectNode) rows.get(0)));
        rejectMetadata(rows -> ((ObjectNode) rows.get(1)).put("provider_station_id", rows.get(0).get("provider_station_id").textValue()));
        rejectMetadata(rows -> ((ObjectNode) rows.get(1)).put("station_id", rows.get(0).get("station_id").textValue()));
        rejectMetadata(rows -> { JsonNode first = rows.remove(0); rows.add(first); });
        ArrayNode nullable = metadataRows();
        ObjectNode station = (ObjectNode) nullable.get(0);
        station.putNull("station_name").putNull("lat").putNull("lon").putNull("capacity").putNull("region_id");
        Operations.Metadata metadata = wire.metadata(save(mapper.writeValueAsBytes(nullable))).get("4199.12");
        assertEquals(new Operations.Metadata("4199.12", null, null, null, null), metadata);
        nullable.remove(1);
        station.put("station_id", "gbfs:" + station.get("provider_station_id").textValue());
        station.put("mapping_status", "ISOLATED").put("mapping_reason", "MISSING_METADATA");
        assertEquals(1, wire.metadata(save(mapper.writeValueAsBytes(nullable))).size());
        station.put("station_name", "invented");
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(save(mapper.writeValueAsBytes(nullable))));
        assertTrue(wire.metadata(save(bytes("[]"))).isEmpty());
    }

    private ObjectNode envelope(int index) throws Exception {
        return (ObjectNode) mapper.readTree(Files.readAllLines(FIXTURES.resolve("events.ndjson")).get(index));
    }

    private ArrayNode metadataRows() throws Exception {
        return (ArrayNode) mapper.readTree(Files.readAllBytes(FIXTURES.resolve("metadata.json")));
    }

    private ConsumerRecord<byte[], byte[]> record(ObjectNode envelope) throws Exception {
        ConsumerRecord<byte[], byte[]> record = new ConsumerRecord<>("bike.station.status.v1", 0, 0,
                bytes(envelope.get("key").textValue()), mapper.writeValueAsBytes(envelope.get("value")));
        envelope.get("headers").fields().forEachRemaining(entry -> record.headers().add(entry.getKey(), bytes(entry.getValue().textValue())));
        return record;
    }

    private static ConsumerRecord<byte[], byte[]> raw(ConsumerRecord<byte[], byte[]> original, byte[] key, byte[] value) {
        ConsumerRecord<byte[], byte[]> record = new ConsumerRecord<>(original.topic(), original.partition(), original.offset(), key, value);
        for (var header : original.headers()) record.headers().add(header.key(), header.value());
        return record;
    }

    private void rejected(ConsumerRecord<byte[], byte[]> record) {
        assertThrows(IllegalArgumentException.class, () -> wire.decode(record));
    }

    private void rejectStation(Consumer<ObjectNode> mutation) throws Exception {
        ObjectNode input = envelope(0);
        mutation.accept((ObjectNode) input.get("value"));
        rejected(record(input));
    }

    private void rejectMetadata(Consumer<ArrayNode> mutation) throws Exception {
        ArrayNode rows = metadataRows();
        mutation.accept(rows);
        String version = save(mapper.writeValueAsBytes(rows));
        assertThrows(IllegalArgumentException.class, () -> wire.metadata(version));
    }

    private String save(byte[] bytes) throws Exception {
        String hash = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
        Files.write(metadataDir.resolve(hash + ".json"), bytes);
        return hash;
    }

    private static byte[] bytes(String value) { return value.getBytes(StandardCharsets.UTF_8); }
}
