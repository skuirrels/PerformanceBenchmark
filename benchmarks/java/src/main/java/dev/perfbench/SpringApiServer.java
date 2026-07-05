package dev.perfbench;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@SpringBootApplication
@RestController
public class SpringApiServer {
    private static final HttpClient CLIENT = HttpClient.newHttpClient();
    private static final byte[] HEALTH = "ok".getBytes(StandardCharsets.UTF_8);
    private static final byte[] PLAINTEXT = "hello, world".getBytes(StandardCharsets.UTF_8);
    private static final byte[] JSON = "{\"message\":\"hello, world\",\"value\":42,\"active\":true}".getBytes(StandardCharsets.UTF_8);
    private static final byte[] DOWNSTREAM = "{\"service\":\"downstream\",\"value\":42}".getBytes(StandardCharsets.UTF_8);
    private static final String SELF_URL = normalizedSelfUrl();
    private static final ConnectionPool DB_POOL = ConnectionPool.fromEnvironment();
    private static final RedisConnectionPool REDIS_POOL = redisPoolFromEnvironment();

    public static void main(String[] args) {
        int port = parsePort(args);
        SpringApplication application = new SpringApplication(SpringApiServer.class);
        application.setDefaultProperties(Map.of(
            "server.port", Integer.toString(port),
            "server.address", "0.0.0.0",
            "server.server-header", "",
            "spring.main.banner-mode", "off",
            "logging.level.root", "OFF",
            "server.tomcat.threads.max", Integer.toString(Math.max(256, Runtime.getRuntime().availableProcessors() * 16)),
            "server.tomcat.threads.min-spare", Integer.toString(Math.max(32, Runtime.getRuntime().availableProcessors() * 4))
        ));
        application.run(args);
        System.out.println("listening http://0.0.0.0:" + port + " spring=true");
    }

    @GetMapping(value = "/health", produces = MediaType.TEXT_PLAIN_VALUE)
    public ResponseEntity<byte[]> health() {
        return bytes(MediaType.TEXT_PLAIN, HEALTH);
    }

    @GetMapping(value = "/plaintext", produces = MediaType.TEXT_PLAIN_VALUE)
    public ResponseEntity<byte[]> plaintext() {
        return bytes(MediaType.TEXT_PLAIN, PLAINTEXT);
    }

    @GetMapping(value = "/json", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<byte[]> json() {
        return bytes(MediaType.APPLICATION_JSON, JSON);
    }

    @GetMapping(value = "/json-serde", produces = MediaType.APPLICATION_JSON_VALUE)
    public Payload jsonSerde() {
        return new Payload("hello, world", 42, true);
    }

    @PostMapping(value = "/orders/quote", consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public QuoteResponse quote(@RequestBody QuoteRequest request) {
        double multiplier = request.expedited() ? 1.2 : 1.0;
        double total = Math.round(request.itemCount() * request.unitPrice() * multiplier * 100.0) / 100.0;
        return new QuoteResponse(request.customerId(), total, request.expedited(), true);
    }

    @GetMapping(value = "/fanout", produces = MediaType.APPLICATION_JSON_VALUE)
    public FanoutResponse fanout() {
        String baseUrl = SELF_URL == null ? "http://127.0.0.1:" + parsePort(new String[0]) : SELF_URL;
        CompletableFuture<HttpResponse<String>> first = CLIENT.sendAsync(HttpRequest.newBuilder(URI.create(baseUrl + "/downstream/a")).GET().build(), HttpResponse.BodyHandlers.ofString());
        CompletableFuture<HttpResponse<String>> second = CLIENT.sendAsync(HttpRequest.newBuilder(URI.create(baseUrl + "/downstream/b")).GET().build(), HttpResponse.BodyHandlers.ofString());
        CompletableFuture<HttpResponse<String>> third = CLIENT.sendAsync(HttpRequest.newBuilder(URI.create(baseUrl + "/downstream/c")).GET().build(), HttpResponse.BodyHandlers.ofString());
        CompletableFuture.allOf(first, second, third).join();
        HttpResponse<String>[] responses = new HttpResponse[] { first.join(), second.join(), third.join() };
        int bytes = 0;
        boolean complete = true;
        for (HttpResponse<String> response : responses) {
            bytes += response.body().length();
            complete = complete && response.statusCode() == 200;
        }
        return new FanoutResponse(responses.length, bytes, complete);
    }

    @GetMapping(value = {"/downstream", "/downstream/{name}"}, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<byte[]> downstream() {
        return bytes(MediaType.APPLICATION_JSON, DOWNSTREAM);
    }

    @GetMapping(value = "/serialize/json", produces = MediaType.APPLICATION_JSON_VALUE)
    public FormatPayload serializeJson() {
        return new FormatPayload(123456, "standard", 99.95, true);
    }

    @GetMapping(value = "/serialize/binary", produces = MediaType.APPLICATION_OCTET_STREAM_VALUE)
    public ResponseEntity<byte[]> serializeBinary() {
        return bytes(MediaType.APPLICATION_OCTET_STREAM, encodeBinaryPayload(123456, "standard", 99.95, true));
    }

    @GetMapping(value = "/db/orders/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<DbOrderResponse> dbLookup(@PathVariable int id) throws Exception {
        if (DB_POOL == null) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
        }
        Connection connection = DB_POOL.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where id = ?")) {
            statement.setInt(1, id);
            try (ResultSet resultSet = statement.executeQuery()) {
                if (!resultSet.next()) {
                    return ResponseEntity.notFound().build();
                }
                return ResponseEntity.ok(new DbOrderResponse(
                    resultSet.getInt(1),
                    resultSet.getString(2),
                    resultSet.getInt(3),
                    resultSet.getString(4)));
            }
        } finally {
            DB_POOL.release(connection);
        }
    }

    @GetMapping(value = "/db/orders", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<DbOrderPageResponse> dbPage(
        @RequestParam(defaultValue = "customer-42") String customerId,
        @RequestParam(defaultValue = "50") int limit) throws Exception {
        if (DB_POOL == null) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
        }
        int boundedLimit = Math.min(100, Math.max(1, limit));
        List<DbOrderResponse> orders = new ArrayList<>(boundedLimit);
        Connection connection = DB_POOL.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where customer_id = ? order by id limit ?")) {
            statement.setString(1, customerId);
            statement.setInt(2, boundedLimit);
            try (ResultSet resultSet = statement.executeQuery()) {
                while (resultSet.next()) {
                    orders.add(new DbOrderResponse(
                        resultSet.getInt(1),
                        resultSet.getString(2),
                        resultSet.getInt(3),
                        resultSet.getString(4)));
                }
            }
            return ResponseEntity.ok(new DbOrderPageResponse(customerId, orders.size(), orders));
        } finally {
            DB_POOL.release(connection);
        }
    }

    @PostMapping(value = "/db/orders", consumes = MediaType.APPLICATION_JSON_VALUE, produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<DbOrderWriteResponse> dbWrite(@RequestBody DbOrderWriteRequest request) throws Exception {
        if (DB_POOL == null) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
        }
        Connection connection = DB_POOL.borrow();
        try (PreparedStatement statement = connection.prepareStatement("insert into order_writes (customer_id, total_cents, status) values (?, ?, ?) returning id")) {
            statement.setString(1, request.customerId());
            statement.setInt(2, request.totalCents());
            statement.setString(3, request.status());
            try (ResultSet resultSet = statement.executeQuery()) {
                resultSet.next();
                return ResponseEntity.ok(new DbOrderWriteResponse(resultSet.getLong(1), true));
            }
        } finally {
            DB_POOL.release(connection);
        }
    }

    @GetMapping(value = "/cache/orders/{id}", produces = MediaType.APPLICATION_JSON_VALUE)
    public ResponseEntity<byte[]> cacheOrder(@PathVariable int id) throws Exception {
        if (REDIS_POOL == null) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).build();
        }
        String key = "order:" + id;
        byte[] cached = REDIS_POOL.get(key);
        if (cached != null) {
            return bytes(MediaType.APPLICATION_JSON, cached);
        }
        if (DB_POOL == null) {
            return ResponseEntity.notFound().build();
        }
        Connection connection = DB_POOL.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where id = ?")) {
            statement.setInt(1, id);
            try (ResultSet resultSet = statement.executeQuery()) {
                if (!resultSet.next()) {
                    return ResponseEntity.notFound().build();
                }
                String payload = "{\"id\":" + resultSet.getInt(1)
                    + ",\"customerId\":\"" + resultSet.getString(2)
                    + "\",\"totalCents\":" + resultSet.getInt(3)
                    + ",\"status\":\"" + resultSet.getString(4) + "\"}";
                byte[] body = payload.getBytes(StandardCharsets.UTF_8);
                REDIS_POOL.set(key, body);
                return bytes(MediaType.APPLICATION_JSON, body);
            }
        } finally {
            DB_POOL.release(connection);
        }
    }

    private static ResponseEntity<byte[]> bytes(MediaType contentType, byte[] body) {
        return ResponseEntity.ok()
            .header(HttpHeaders.CONTENT_LENGTH, Integer.toString(body.length))
            .contentType(contentType)
            .body(body);
    }

    private static int parsePort(String[] args) {
        for (int i = 0; i < args.length - 1; i++) {
            if ("--port".equals(args[i])) {
                return Integer.parseInt(args[i + 1]);
            }
        }
        String envPort = System.getenv("PORT");
        return envPort == null || envPort.isBlank() ? 8080 : Integer.parseInt(envPort);
    }

    private static String normalizedSelfUrl() {
        String value = System.getenv("PERFBENCH_SELF_URL");
        if (value == null || value.isBlank()) {
            return null;
        }
        while (value.endsWith("/")) {
            value = value.substring(0, value.length() - 1);
        }
        return value;
    }

    private static RedisConnectionPool redisPoolFromEnvironment() {
        try {
            return RedisConnectionPool.fromEnvironment();
        } catch (IOException ex) {
            throw new IllegalStateException("failed to initialize redis pool", ex);
        }
    }

    private static byte[] encodeBinaryPayload(int id, String category, double amount, boolean active) {
        try {
            ByteArrayOutputStream stream = new ByteArrayOutputStream();
            DataOutputStream writer = new DataOutputStream(stream);
            writer.writeInt(Integer.reverseBytes(id));
            writer.writeUTF(category);
            writer.writeLong(Long.reverseBytes(Double.doubleToRawLongBits(amount)));
            writer.writeBoolean(active);
            writer.flush();
            return stream.toByteArray();
        } catch (IOException ex) {
            throw new IllegalStateException(ex);
        }
    }

    public record Payload(String message, int value, boolean active) {
    }

    public record QuoteRequest(String customerId, int itemCount, double unitPrice, boolean expedited) {
    }

    public record QuoteResponse(String customerId, double total, boolean expedited, boolean accepted) {
    }

    public record FanoutResponse(int services, int bytes, boolean complete) {
    }

    public record FormatPayload(int id, String category, double amount, boolean active) {
    }

    public record DbOrderResponse(int id, String customerId, int totalCents, String status) {
    }

    public record DbOrderPageResponse(String customerId, int count, List<DbOrderResponse> orders) {
    }

    public record DbOrderWriteRequest(String customerId, int totalCents, String status) {
    }

    public record DbOrderWriteResponse(long id, boolean accepted) {
    }

    private static final class ConnectionPool {
        private final ArrayBlockingQueue<Connection> connections;

        private ConnectionPool(ArrayBlockingQueue<Connection> connections) {
            this.connections = connections;
        }

        static ConnectionPool fromEnvironment() {
            String url = System.getenv("PERFBENCH_DB");
            if (url == null || url.isBlank()) {
                return null;
            }
            try {
                ArrayBlockingQueue<Connection> connections = new ArrayBlockingQueue<>(64);
                for (int i = 0; i < 64; i++) {
                    connections.add(DriverManager.getConnection(url));
                }
                return new ConnectionPool(connections);
            } catch (SQLException ex) {
                throw new IllegalStateException("failed to initialize db pool", ex);
            }
        }

        Connection borrow() throws InterruptedException {
            return connections.take();
        }

        void release(Connection connection) {
            connections.offer(connection);
        }
    }
}
