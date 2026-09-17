package citibike.operations;

import citibike.CitibikeApplication;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.apache.kafka.clients.admin.Admin;
import org.apache.kafka.clients.admin.NewTopic;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.apache.kafka.common.TopicPartition;
import org.apache.kafka.common.errors.GroupIdNotFoundException;
import org.apache.kafka.common.serialization.ByteArraySerializer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.Timeout;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.junit.jupiter.api.extension.ExtendWith;
import org.junit.jupiter.api.io.TempDir;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.test.system.CapturedOutput;
import org.springframework.boot.test.system.OutputCaptureExtension;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.core.io.FileSystemResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.DriverManagerDataSource;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;

import javax.management.ObjectName;
import java.lang.management.ManagementFactory;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;

import static org.junit.jupiter.api.Assertions.*;

/** Synthetic FIXTURE records through real Kafka, Spring, MySQL and HTTP; rebuilds only citibike_kafka_it. */
@EnabledIfEnvironmentVariable(named = "CITIBIKE_KAFKA_IT", matches = "true")
@ExtendWith(OutputCaptureExtension.class)
class OperationsKafkaIntegrationTest {
    private static final String SNAPSHOT = "144dc6c63f679f72b02b9ada5129d5393976fdb04be00534eda8ebe73c3fb3c3";
    private static final String DATASET = "542344d497656e88af7552acd0944c8be3026e026242282a630e55765dc73b7e";
    private static final String FAILURE = "KAFKA_IT_INJECTED_PUBLICATION_FAILURE";
    private final ObjectMapper json = new ObjectMapper();
    private final HttpClient http = HttpClient.newBuilder().connectTimeout(Duration.ofSeconds(5)).build();
    private final String topic = "citibike-kafka-it-" + UUID.randomUUID();
    private final String group = topic + "-consumer";
    private final TopicPartition partition = new TopicPartition(topic, 0);
    @TempDir Path dataDir;
    private List<JsonNode> fixture;
    private JdbcTemplate jdbc;
    private Admin admin;
    private KafkaProducer<byte[], byte[]> producer;
    private ServletWebServerApplicationContext app;

    @BeforeEach
    void prepareDedicatedInfrastructure() throws Exception {
        String bootstrap = requiredEnvironment("CITIBIKE_KAFKA_IT_BOOTSTRAP");
        var datasource = new DriverManagerDataSource(requiredEnvironment("CITIBIKE_KAFKA_IT_URL"),
                requiredEnvironment("CITIBIKE_KAFKA_IT_USERNAME"), requiredEnvironment("CITIBIKE_KAFKA_IT_PASSWORD"));
        var candidate = new JdbcTemplate(datasource);
        assertEquals("citibike_kafka_it", candidate.queryForObject("SELECT DATABASE()", String.class),
                "Destructive integration tests require the explicit dedicated citibike_kafka_it database");
        jdbc = candidate;
        jdbc.setQueryTimeout(10);
        jdbc.execute("DROP TRIGGER IF EXISTS kafka_it_reject_publication");
        new ResourceDatabasePopulator(new FileSystemResource("../sql/serving.sql")).execute(datasource);
        for (String table : List.of("ads_rebalance_suggestion", "ads_station_current_risk", "live_release",
                "historical_release", "dim_station_v1", "dws_station_hourly_flow_v1",
                "dws_station_hour_profile_v1", "dws_station_od_hourly_v1")) {
            jdbc.update("DELETE FROM " + table);
        }
        new ResourceDatabasePopulator(new FileSystemResource("../fixtures/day2/seed.sql")).execute(datasource);
        // The baseline is seeded; every live result must be produced by Kafka during this test.
        for (String table : List.of("ads_rebalance_suggestion", "ads_station_current_risk", "live_release")) {
            jdbc.update("DELETE FROM " + table);
        }
        fixture = new ArrayList<>();
        for (String line : Files.readAllLines(Path.of("../fixtures/day2/events.ndjson"))) fixture.add(json.readTree(line));
        Path metadata = Files.createDirectories(dataDir.resolve("gbfs/metadata"));
        Files.copy(Path.of("../fixtures/day2/metadata.json"),
                metadata.resolve(fixture.get(0).path("headers").path("metadata_version").asText() + ".json"));
        admin = Admin.create(Map.of("bootstrap.servers", bootstrap,
                "request.timeout.ms", 5000, "default.api.timeout.ms", 10000));
        admin.createTopics(List.of(new NewTopic(topic, 1, (short) 1))).all().get(15, TimeUnit.SECONDS);
        producer = new KafkaProducer<>(Map.of("bootstrap.servers", bootstrap,
                "key.serializer", ByteArraySerializer.class, "value.serializer", ByteArraySerializer.class,
                "acks", "all", "max.block.ms", 10000, "request.timeout.ms", 5000, "delivery.timeout.ms", 10000));
        startApplication();
    }

    @AfterEach
    void cleanUp() throws Exception {
        try {
            if (app != null) app.close();
        } finally {
            try {
                if (jdbc != null) jdbc.execute("DROP TRIGGER IF EXISTS kafka_it_reject_publication");
            } finally {
                if (producer != null) producer.close(Duration.ofSeconds(5));
                if (admin != null) {
                    try {
                        if (committed() >= 0) admin.deleteConsumerGroups(List.of(group)).all().get(15, TimeUnit.SECONDS);
                        admin.deleteTopics(List.of(topic)).all().get(15, TimeUnit.SECONDS);
                    } finally {
                        admin.close(Duration.ofSeconds(5));
                    }
                }
            }
        }
    }

    @Test
    @Timeout(180)
    void publishesAtomicallyAndReplaysSafelyAcrossRealApplicationRestarts(CapturedOutput output) throws Exception {
        assertEquals("DATA_UNAVAILABLE", map(503).path("error").path("code").asText());
        assertEquals(0, send(fixture.get(0)));
        await("Kafka delivered the incomplete prefix", () -> consumed() >= 1);
        for (int sample = 0; sample < 5; sample++) {
            assertEquals(-1, committed(), "An incomplete prefix must remain replayable");
            assertTrue(publication().get("release").isEmpty());
            assertEquals("DATA_UNAVAILABLE", map(503).path("error").path("code").asText());
            Thread.sleep(100);
        }
        assertEquals(1, send(fixture.get(1)));
        assertEquals(2, send(fixture.get(2)));
        await("First complete snapshot committed exactly through its end", () -> committed() == 3);
        assertFixtureMap(SNAPSHOT, "2025-02-05T13:00:02Z");
        var firstPublication = publication();
        assertEquals(2, firstPublication.get("risks").size());
        assertEquals(1, firstPublication.get("suggestions").size());

        List<JsonNode> poisoned = batch("a".repeat(64), 60);
        ObjectNode conflicting = poisoned.get(0).deepCopy();
        ((ObjectNode) conflicting.path("value")).put("num_bikes_disabled", 1);
        assertEquals(3, send(poisoned.get(0)));
        assertEquals(4, send(conflicting));
        await("Conflicting wire field poisoned the batch", () -> output.getAll().contains("reason=STATION_CONFLICT"));
        assertEquals(3, committed());
        assertEquals(firstPublication, publication());

        restartApplication();
        await("Restart replayed the uncommitted poisoned prefix", () -> consumed() >= 2);
        assertEquals(3, committed());
        assertEquals(5, send(poisoned.get(1)));
        assertEquals(6, send(poisoned.get(2)));
        await("Poisoned suffix drained through its end", () -> committed() == 7);
        assertEquals(firstPublication, publication(), "Rejected batches must preserve rows and both release clocks");
        assertFixtureMap(SNAPSHOT, "2025-02-05T13:00:02Z");

        restartApplication();
        assertEquals(7, committed(), "Restart must retain the original group offsets");
        for (int i = 0; i < fixture.size(); i++) assertEquals(7 + i, send(fixture.get(i)));
        await("Restarted application drained the durable duplicate", () -> committed() == 10);
        assertEquals(firstPublication, publication(), "Duplicate replay must not refresh published_at or as_of");
        assertFixtureMap(SNAPSHOT, "2025-02-05T13:00:02Z");

        jdbc.execute("CREATE TRIGGER kafka_it_reject_publication BEFORE INSERT ON ads_rebalance_suggestion "
                + "FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '" + FAILURE + "'");
        List<JsonNode> retry = batch("b".repeat(64), 120);
        for (int i = 0; i < retry.size(); i++) assertEquals(10 + i, send(retry.get(i)));
        await("MySQL rejected an actual publication attempt", () -> output.getAll().contains(FAILURE));
        assertEquals(10, committed(), "A failed MySQL transaction must not commit its Kafka end");
        assertEquals(firstPublication, publication(), "The deleted rows and inserted risks must roll back together");
        assertFixtureMap(SNAPSHOT, "2025-02-05T13:00:02Z");
        jdbc.execute("DROP TRIGGER kafka_it_reject_publication");
        await("The same buffered snapshot retried after MySQL recovered", () -> committed() == 13);
        assertFixtureMap("b".repeat(64), "2025-02-05T13:02:02Z");

        var recovered = publication();
        // Advance only this isolated recorded release to exercise the HTTP expiry boundary without waiting.
        jdbc.update("UPDATE live_release SET as_of_utc = '2025-02-05 13:04:40' WHERE singleton = 1");
        JsonNode expired = map(200);
        assertEquals("2025-02-05T13:04:40Z", expired.path("as_of_utc").asText());
        assertEquals(2, expired.path("stations").size());
        assertEquals(0, expired.path("suggestions").size());
        for (JsonNode station : expired.path("stations")) {
            assertEquals("STALE_DATA", station.path("current_status").asText());
            assertEquals("STALE_DATA", station.path("forecast_status").asText());
            assertTrue(station.path("projected_bikes_1h").isNull());
        }
        assertEquals(recovered.get("risks"), publication().get("risks"));
        assertEquals(recovered.get("suggestions"), publication().get("suggestions"));

        ObjectNode empty = batch("c".repeat(64), 300).get(2).deepCopy();
        ((ObjectNode) empty.path("value")).put("station_count", 0);
        assertEquals(13, send(empty));
        await("A complete empty snapshot was committed", () -> committed() == 14);
        JsonNode emptyMap = map(200);
        assertEquals("c".repeat(64), emptyMap.path("snapshot_id").asText());
        assertEquals("2025-02-05T13:05:02Z", emptyMap.path("as_of_utc").asText());
        assertEquals(0, emptyMap.path("stations").size());
        assertEquals(0, emptyMap.path("suggestions").size());
        assertTrue(publication().get("risks").isEmpty());
        assertTrue(publication().get("suggestions").isEmpty());
        System.out.println("Kafka integration verified: synthetic FIXTURE, commits 3/7/10/13/14, "
                + "two app restarts, MySQL rollback/retry, HTTP expiry and empty 200; topic=" + topic);
    }

    private void startApplication() {
        var application = new SpringApplication(CitibikeApplication.class);
        app = (ServletWebServerApplicationContext) application.run(
                "--spring.profiles.active=recorded", "--server.port=0", "--spring.main.banner-mode=off",
                "--spring.datasource.url=" + requiredEnvironment("CITIBIKE_KAFKA_IT_URL"),
                "--spring.datasource.username=" + requiredEnvironment("CITIBIKE_KAFKA_IT_USERNAME"),
                "--spring.datasource.password=" + requiredEnvironment("CITIBIKE_KAFKA_IT_PASSWORD"),
                "--DATA_DIR=" + dataDir.toAbsolutePath(), "--KAFKA_TOPIC=" + topic,
                "--KAFKA_BOOTSTRAP_SERVERS=" + requiredEnvironment("CITIBIKE_KAFKA_IT_BOOTSTRAP"),
                "--SPRING_KAFKA_CONSUMER_GROUP_ID=" + group,
                "--logging.level.org.apache.kafka=WARN");
    }

    private void restartApplication() {
        app.close();
        app = null;
        startApplication();
    }

    private long send(JsonNode envelope) throws Exception {
        var record = new ProducerRecord<byte[], byte[]>(topic, 0,
                envelope.path("key").asText().getBytes(StandardCharsets.UTF_8),
                json.writeValueAsBytes(envelope.path("value")));
        envelope.path("headers").fields().forEachRemaining(field ->
                record.headers().add(field.getKey(), field.getValue().asText().getBytes(StandardCharsets.UTF_8)));
        return producer.send(record).get(15, TimeUnit.SECONDS).offset();
    }

    private long committed() throws Exception {
        try {
            var offset = admin.listConsumerGroupOffsets(group).partitionsToOffsetAndMetadata()
                    .get(15, TimeUnit.SECONDS).get(partition);
            return offset == null ? -1 : offset.offset();
        } catch (ExecutionException failure) {
            if (failure.getCause() instanceof GroupIdNotFoundException) return -1;
            throw failure;
        }
    }

    private double consumed() throws Exception {
        var mbeans = ManagementFactory.getPlatformMBeanServer();
        var names = mbeans.queryNames(new ObjectName(
                "kafka.consumer:type=consumer-fetch-manager-metrics,client-id=consumer-" + group + "-*"), null);
        assertEquals(1, names.size(), "Exactly one real Kafka consumer must be running for this test group");
        return ((Number) mbeans.getAttribute(names.iterator().next(), "records-consumed-total")).doubleValue();
    }

    private JsonNode map(int expectedStatus) throws Exception {
        var response = http.send(HttpRequest.newBuilder(URI.create("http://127.0.0.1:"
                        + app.getWebServer().getPort() + "/api/v1/map?mode=live"))
                .timeout(Duration.ofSeconds(10)).GET().build(), HttpResponse.BodyHandlers.ofString());
        assertEquals(expectedStatus, response.statusCode(), response.body());
        return json.readTree(response.body());
    }

    private void assertFixtureMap(String snapshot, String asOf) throws Exception {
        JsonNode response = map(200);
        assertEquals(snapshot, response.path("snapshot_id").asText());
        assertEquals(DATASET, response.path("baseline_dataset_id").asText());
        assertEquals("FIXTURE", response.path("data_origin").asText());
        assertEquals("recorded", response.path("clock_mode").asText());
        assertEquals(asOf, response.path("as_of_utc").asText());
        assertEquals(2, response.path("stations").size());
        assertEquals("4199.12", response.at("/stations/0/station_id").asText());
        assertEquals("OVERFLOW_RISK", response.at("/stations/0/current_status").asText());
        assertEquals("5484.09", response.at("/stations/1/station_id").asText());
        assertEquals("SHORTAGE_RISK", response.at("/stations/1/current_status").asText());
        assertEquals(1, response.path("suggestions").size());
        assertEquals("4199.12", response.at("/suggestions/0/from_station_id").asText());
        assertEquals("5484.09", response.at("/suggestions/0/to_station_id").asText());
        assertEquals(8, response.at("/suggestions/0/move_bikes").asInt());
        assertEquals(84, response.at("/suggestions/0/distance_meters").asInt());
    }

    private List<JsonNode> batch(String snapshot, long seconds) {
        List<JsonNode> batch = new ArrayList<>();
        for (JsonNode event : fixture) {
            ObjectNode changed = event.deepCopy();
            ((ObjectNode) changed.path("headers")).put("snapshot_id", snapshot);
            ObjectNode value = (ObjectNode) changed.path("value");
            for (String field : List.of("snapshot_at_utc", "ingested_at_utc", "last_reported_at_utc")) {
                if (value.hasNonNull(field)) value.put(field, Instant.parse(value.path(field).asText()).plusSeconds(seconds).toString());
            }
            if (value.has("snapshot_at_local")) value.put("snapshot_at_local", DateTimeFormatter.ISO_OFFSET_DATE_TIME
                    .format(Instant.parse(value.path("snapshot_at_utc").asText()).atZone(ZoneId.of("America/New_York"))));
            batch.add(changed);
        }
        return batch;
    }

    private Map<String, List<Map<String, Object>>> publication() {
        return Map.of("risks", jdbc.queryForList("SELECT * FROM ads_station_current_risk ORDER BY station_id"),
                "suggestions", jdbc.queryForList("SELECT * FROM ads_rebalance_suggestion ORDER BY priority"),
                "release", jdbc.queryForList("SELECT * FROM live_release"));
    }

    private static void await(String message, Callable<Boolean> condition) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(30).toNanos();
        while (System.nanoTime() < deadline) {
            if (condition.call()) return;
            Thread.sleep(100);
        }
        fail(message);
    }

    private static String requiredEnvironment(String name) {
        String value = System.getenv(name);
        if (value == null || value.isBlank()) throw new IllegalStateException(name + " must be set for Kafka integration tests");
        return value;
    }
}
