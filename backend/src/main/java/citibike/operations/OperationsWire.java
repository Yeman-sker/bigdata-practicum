package citibike.operations;

import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.json.JsonReadFeature;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.apache.kafka.common.header.Header;

import java.io.IOException;
import java.math.BigDecimal;
import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.time.DateTimeException;
import java.time.Instant;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** Validates native Kafka bytes and the collector's hash-addressed metadata files. */
public final class OperationsWire {
    private static final Set<String> HEADERS = Set.of(
            "contract_version", "record_type", "snapshot_id", "metadata_version", "data_origin");
    private static final Set<String> STATION_FIELDS = Set.of(
            "station_id", "snapshot_at_utc", "snapshot_at_local", "last_reported_at_utc", "ingested_at_utc",
            "num_bikes_available", "num_docks_available", "num_bikes_disabled", "num_docks_disabled",
            "is_installed", "is_renting", "is_returning", "source_version");
    private static final Set<String> END_FIELDS = Set.of("station_count", "snapshot_at_utc", "ingested_at_utc");
    private static final Set<String> METADATA_FIELDS = Set.of(
            "provider_station_id", "station_id", "mapping_status", "mapping_reason", "station_name",
            "lat", "lon", "capacity", "region_id", "is_current", "metadata_source", "metadata_updated_at");
    private static final Set<String> ORIGINS = Set.of("GBFS_LIVE", "GBFS_REPLAY", "FIXTURE");
    private static final Set<String> MAPPING_REASONS = Set.of(
            "MISSING_SHORT_NAME", "DUPLICATE_SHORT_NAME", "MISSING_METADATA");
    private static final Pattern HASH = Pattern.compile("[0-9a-f]{64}");
    private static final String DATE_TIME = "[0-9]{4}-[0-9]{2}-[0-9]{2}[Tt][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\\.[0-9]+)?";
    private static final Pattern UTC_TIME = Pattern.compile(DATE_TIME + "Z");
    private static final Pattern LOCAL_TIME = Pattern.compile(DATE_TIME + "[+-][0-9]{2}:[0-9]{2}");
    private static final ZoneId NEW_YORK = ZoneId.of("America/New_York");
    private final ObjectMapper mapper;
    private final Path metadataDir;

    public OperationsWire(ObjectMapper mapper, Path metadataDir) {
        this.mapper = mapper.copy().enable(JsonParser.Feature.STRICT_DUPLICATE_DETECTION)
                .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS, DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);
        for (JsonReadFeature feature : JsonReadFeature.values()) this.mapper.disable(feature.mappedFeature());
        this.metadataDir = metadataDir;
    }

    /** value retains every wire field for whole-value duplicate detection. */
    public record Decoded(Operations.Headers headers, Operations.StationEvent station,
                          Operations.SnapshotEnd end, JsonNode value, Instant ingestedAt) {}

    public Decoded decode(ConsumerRecord<byte[], byte[]> record) {
        if (record == null) throw invalid("MISSING_RECORD");
        Map<String, String> values = new HashMap<>();
        for (Header header : record.headers()) {
            if (!HEADERS.contains(header.key()) || values.containsKey(header.key())) throw invalid("INVALID_HEADERS");
            values.put(header.key(), utf8(header.value(), "HEADER_UTF8"));
        }
        if (!values.keySet().equals(HEADERS) || !"1.1".equals(values.get("contract_version"))
                || !Set.of("station", "snapshot_end").contains(values.get("record_type"))
                || !ORIGINS.contains(values.get("data_origin"))) throw invalid("INVALID_HEADERS");
        requireHash(values.get("snapshot_id"));
        requireHash(values.get("metadata_version"));
        Operations.Headers headers = new Operations.Headers(values.get("contract_version"), values.get("record_type"),
                values.get("snapshot_id"), values.get("metadata_version"), values.get("data_origin"));
        String key = utf8(record.key(), "KEY_UTF8");
        JsonNode value = json(record.value());
        if (headers.recordType().equals("snapshot_end")) {
            fields(value, END_FIELDS);
            if (!key.equals("__snapshot_end__")) throw invalid("END_KEY");
            int count = integer(value, "station_count", false);
            if (count < 0 || count > 5000) throw invalid("STATION_COUNT");
            Instant snapshot = utc(value, "snapshot_at_utc", false);
            Instant ingested = utc(value, "ingested_at_utc", false);
            return new Decoded(headers, null, new Operations.SnapshotEnd(key, headers, count, snapshot, ingested), value, ingested);
        }
        fields(value, STATION_FIELDS);
        String stationId = id(value, "station_id");
        if (!key.equals(stationId)) throw invalid("STATION_KEY");
        if (!"2.3".equals(text(value, "source_version", false, 1, 128))) throw invalid("SOURCE_VERSION");
        Instant snapshot = utc(value, "snapshot_at_utc", false);
        String local = text(value, "snapshot_at_local", false, 1, 128);
        try {
            if (!LOCAL_TIME.matcher(local).matches()
                    || !OffsetDateTime.parse(local).equals(snapshot.atZone(NEW_YORK).toOffsetDateTime()))
                throw invalid("LOCAL_TIME");
        } catch (DateTimeException error) {
            throw invalid("LOCAL_TIME");
        }
        Instant lastReported = utc(value, "last_reported_at_utc", true);
        Instant ingested = utc(value, "ingested_at_utc", false);
        integer(value, "num_bikes_disabled", true);
        integer(value, "num_docks_disabled", true);
        Operations.Observation observation = new Operations.Observation(stationId, snapshot, lastReported,
                integer(value, "num_bikes_available", false), integer(value, "num_docks_available", true),
                bool(value, "is_installed"), bool(value, "is_renting"), bool(value, "is_returning"));
        return new Decoded(headers, new Operations.StationEvent(key, headers, observation), null, value, ingested);
    }

    public Map<String, Operations.Metadata> metadata(String version) {
        requireHash(version);
        byte[] bytes;
        try {
            bytes = Files.readAllBytes(metadataDir.resolve(version + ".json"));
        } catch (IOException error) {
            throw new IllegalArgumentException("METADATA_UNREADABLE", error);
        }
        try {
            String actual = HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
            if (!actual.equals(version)) throw invalid("METADATA_HASH");
        } catch (NoSuchAlgorithmException error) {
            throw new IllegalStateException(error);
        }
        JsonNode rows = json(bytes);
        if (!rows.isArray()) throw invalid("METADATA_ARRAY");
        Map<String, Operations.Metadata> result = new LinkedHashMap<>();
        Set<String> providers = new HashSet<>();
        String previous = null;
        for (JsonNode row : rows) {
            fields(row, METADATA_FIELDS);
            String provider = id(row, "provider_station_id");
            String station = id(row, "station_id");
            if (!providers.add(provider) || previous != null && previous.compareTo(station) >= 0)
                throw invalid("METADATA_ID_ORDER_OR_DUPLICATE");
            previous = station;
            String status = text(row, "mapping_status", false, 1, 128);
            String reason = text(row, "mapping_reason", true, 1, 128);
            if (status.equals("SHORT_NAME")) {
                if (reason != null) throw invalid("METADATA_MAPPING");
            } else if (!status.equals("ISOLATED") || reason == null || !MAPPING_REASONS.contains(reason)
                    || !station.equals("gbfs:" + provider)) throw invalid("METADATA_MAPPING");
            String name = text(row, "station_name", true, 0, 512);
            Double lat = coordinate(row, "lat", 90);
            Double lon = coordinate(row, "lon", 180);
            Integer capacity = integer(row, "capacity", true);
            if (capacity != null && capacity <= 0) throw invalid("METADATA_CAPACITY");
            text(row, "region_id", true, 1, 128);
            if (!bool(row, "is_current") || !"GBFS".equals(text(row, "metadata_source", false, 1, 128)))
                throw invalid("METADATA_SOURCE");
            utc(row, "metadata_updated_at", false);
            if ("MISSING_METADATA".equals(reason) && (name != null || lat != null || lon != null || capacity != null))
                throw invalid("METADATA_PLACEHOLDER");
            result.put(station, new Operations.Metadata(station, name, lat, lon, capacity));
        }
        return Collections.unmodifiableMap(result);
    }

    /** Identifies a rejection boundary only; this does not validate the record. */
    public static String boundaryId(ConsumerRecord<byte[], byte[]> record) {
        String id = uniqueHeader(record, "snapshot_id");
        return id != null && HASH.matcher(id).matches() ? id : null;
    }

    /** A malformed end can close a poisoned range only with one end header and the exact end key. */
    public static boolean isEnd(ConsumerRecord<byte[], byte[]> record) {
        if (!"snapshot_end".equals(uniqueHeader(record, "record_type"))) return false;
        try {
            return "__snapshot_end__".equals(utf8(record.key(), "KEY_UTF8"));
        } catch (IllegalArgumentException error) {
            return false;
        }
    }

    private static String uniqueHeader(ConsumerRecord<byte[], byte[]> record, String name) {
        if (record == null) return null;
        String value = null;
        try {
            for (Header header : record.headers().headers(name)) {
                if (value != null) return null;
                value = utf8(header.value(), "HEADER_UTF8");
            }
        } catch (IllegalArgumentException error) {
            return null;
        }
        return value;
    }

    private JsonNode json(byte[] bytes) {
        String value = utf8(bytes, "JSON_UTF8");
        try {
            JsonNode node = mapper.readTree(value);
            if (node == null) throw invalid("JSON_EMPTY");
            return node;
        } catch (IOException error) {
            throw new IllegalArgumentException("INVALID_JSON", error);
        }
    }

    private static String utf8(byte[] bytes, String reason) {
        if (bytes == null) throw invalid(reason);
        try {
            return StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
        } catch (CharacterCodingException error) {
            throw invalid(reason);
        }
    }

    private static void requireHash(String hash) {
        if (hash == null || !HASH.matcher(hash).matches()) throw invalid("INVALID_HASH");
    }

    private static void fields(JsonNode node, Set<String> expected) {
        if (!node.isObject() || node.size() != expected.size()) throw invalid("JSON_FIELDS");
        for (String field : expected) if (!node.has(field)) throw invalid("JSON_FIELDS");
    }

    private static String text(JsonNode row, String field, boolean nullable, int min, int max) {
        JsonNode value = row.get(field);
        if (nullable && value.isNull()) return null;
        if (!value.isTextual()) throw invalid("INVALID_FIELD:" + field);
        String string = value.textValue();
        int length = string.codePointCount(0, string.length());
        if (length < min || length > max) throw invalid("INVALID_FIELD:" + field);
        return string;
    }

    private static String id(JsonNode row, String field) {
        String value = text(row, field, false, 1, 128);
        if (value.codePoints().anyMatch(c -> Character.isWhitespace(c) || Character.isSpaceChar(c)))
            throw invalid("INVALID_FIELD:" + field);
        return value;
    }

    private static Integer integer(JsonNode row, String field, boolean nullable) {
        JsonNode value = row.get(field);
        if (nullable && value.isNull()) return null;
        if (!value.isIntegralNumber() || !value.canConvertToInt()) throw invalid("INVALID_FIELD:" + field);
        return value.intValue();
    }

    private static boolean bool(JsonNode row, String field) {
        JsonNode value = row.get(field);
        if (!value.isBoolean()) throw invalid("INVALID_FIELD:" + field);
        return value.booleanValue();
    }

    private static Double coordinate(JsonNode row, String field, int limit) {
        JsonNode value = row.get(field);
        if (value.isNull()) return null;
        if (!value.isNumber() || !Double.isFinite(value.doubleValue())
                || value.decimalValue().abs().compareTo(BigDecimal.valueOf(limit)) > 0)
            throw invalid("INVALID_FIELD:" + field);
        return value.doubleValue();
    }

    private static Instant utc(JsonNode row, String field, boolean nullable) {
        String value = text(row, field, nullable, 1, 128);
        if (value == null) return null;
        try {
            if (!UTC_TIME.matcher(value).matches()) throw invalid("INVALID_FIELD:" + field);
            return OffsetDateTime.parse(value).toInstant();
        } catch (DateTimeException error) {
            throw invalid("INVALID_FIELD:" + field);
        }
    }

    private static IllegalArgumentException invalid(String reason) { return new IllegalArgumentException(reason); }
}
