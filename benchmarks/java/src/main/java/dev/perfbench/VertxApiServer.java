package dev.perfbench;

import com.fasterxml.jackson.databind.ObjectMapper;
import io.vertx.core.Future;
import io.vertx.core.Vertx;
import io.vertx.core.VertxOptions;
import io.vertx.core.buffer.Buffer;
import io.vertx.core.http.HttpServerOptions;
import io.vertx.ext.web.client.HttpResponse;
import io.vertx.ext.web.client.WebClient;
import io.vertx.ext.web.client.WebClientOptions;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.util.ArrayList;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.atomic.AtomicBoolean;

public final class VertxApiServer {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final byte[] HEALTH = "ok".getBytes(StandardCharsets.UTF_8);
    private static final byte[] PLAINTEXT = "hello, world".getBytes(StandardCharsets.UTF_8);
    private static final byte[] JSON = "{\"message\":\"hello, world\",\"value\":42,\"active\":true}".getBytes(StandardCharsets.UTF_8);
    private static final byte[] DOWNSTREAM = "{\"service\":\"downstream\",\"value\":42}".getBytes(StandardCharsets.UTF_8);
    private static final Payload PAYLOAD = new Payload("hello, world", 42, true);

    private VertxApiServer() {
    }

    public static void main(String[] args) throws Exception {
        int port = parsePort(args);
        int processors = Runtime.getRuntime().availableProcessors();
        Vertx vertx = Vertx.vertx(new VertxOptions()
            .setWorkerPoolSize(Math.max(64, processors * 8)));
        WebClient client = WebClient.create(vertx, new WebClientOptions()
            .setKeepAlive(true)
            .setMaxPoolSize(512)
            .setConnectTimeout(1000));
        ConnectionPool dbPool = ConnectionPool.fromEnvironment();
        RedisConnectionPool redisPool = RedisConnectionPool.fromEnvironment();
        CountDownLatch started = new CountDownLatch(1);
        AtomicBoolean failedToStart = new AtomicBoolean(false);

        vertx.createHttpServer(new HttpServerOptions()
                .setTcpNoDelay(true)
                .setCompressionSupported(false))
            .requestHandler(request -> {
                String path = request.path();
                switch (path) {
                    case "/health" -> write(request.response(), "text/plain", HEALTH);
                    case "/plaintext" -> write(request.response(), "text/plain", PLAINTEXT);
                    case "/json" -> write(request.response(), "application/json", JSON);
                    case "/json-serde" -> writeJson(request.response(), PAYLOAD);
                    case "/orders/quote" -> quote(request);
                    case "/fanout" -> fanout(request, client, port);
                    case "/downstream", "/downstream/a", "/downstream/b", "/downstream/c" ->
                        write(request.response(), "application/json", DOWNSTREAM);
                    case "/serialize/json" ->
                        writeJson(request.response(), new FormatPayload(123456, "standard", 99.95, true));
                    case "/serialize/binary" ->
                        write(request.response(), "application/octet-stream", encodeBinaryPayload(123456, "standard", 99.95, true));
                    default -> {
                        if ("/db/orders".equals(path)) {
                            if ("POST".equalsIgnoreCase(request.method().name())) {
                                dbWrite(vertx, request, dbPool);
                            } else if ("GET".equalsIgnoreCase(request.method().name())) {
                                dbPage(vertx, request, dbPool);
                            } else {
                                request.response().setStatusCode(405).end();
                            }
                        } else if (path.startsWith("/db/orders/")) {
                            dbLookup(vertx, request, dbPool);
                        } else if (path.startsWith("/cache/orders/")) {
                            cacheOrder(vertx, request, redisPool, dbPool);
                        } else {
                            request.response().setStatusCode(404).end();
                        }
                    }
                }
            })
            .listen(port, "127.0.0.1")
            .onSuccess(server -> {
                System.out.println("listening http://127.0.0.1:" + port + " vertx=true");
                started.countDown();
            })
            .onFailure(error -> {
                error.printStackTrace(System.err);
                failedToStart.set(true);
                started.countDown();
                vertx.close();
            });

        started.await();
        if (failedToStart.get()) {
            System.exit(1);
        }
        Thread.currentThread().join();
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

    private static void write(io.vertx.core.http.HttpServerResponse response, String contentType, byte[] body) {
        response.putHeader("Content-Type", contentType).end(Buffer.buffer(body));
    }

    private static void writeJson(io.vertx.core.http.HttpServerResponse response, Object payload) {
        try {
            write(response, "application/json", MAPPER.writeValueAsBytes(payload));
        } catch (Exception ex) {
            response.setStatusCode(500).end();
        }
    }

    private static void quote(io.vertx.core.http.HttpServerRequest request) {
        if (!"POST".equalsIgnoreCase(request.method().name())) {
            request.response().setStatusCode(405).end();
            return;
        }
        request.body().onSuccess(body -> {
            try {
                QuoteRequest quote = MAPPER.readValue(body.getBytes(), QuoteRequest.class);
                double multiplier = quote.expedited() ? 1.2 : 1.0;
                double total = Math.round(quote.itemCount() * quote.unitPrice() * multiplier * 100.0) / 100.0;
                writeJson(request.response(), new QuoteResponse(quote.customerId(), total, quote.expedited(), true));
            } catch (Exception ex) {
                request.response().setStatusCode(400).end();
            }
        }).onFailure(error -> request.response().setStatusCode(400).end());
    }

    private static void fanout(io.vertx.core.http.HttpServerRequest request, WebClient client, int port) {
        var first = client.get(port, "127.0.0.1", "/downstream/a").send();
        var second = client.get(port, "127.0.0.1", "/downstream/b").send();
        var third = client.get(port, "127.0.0.1", "/downstream/c").send();
        Future.all(first, second, third).onSuccess(done -> {
            int bytes = 0;
            boolean complete = true;
            for (int i = 0; i < done.size(); i++) {
                HttpResponse<Buffer> response = done.resultAt(i);
                bytes += response.bodyAsBuffer().length();
                complete = complete && response.statusCode() == 200;
            }
            writeJson(request.response(), new FanoutResponse(done.size(), bytes, complete));
        }).onFailure(error -> request.response().setStatusCode(500).end());
    }

    private static void dbLookup(Vertx vertx, io.vertx.core.http.HttpServerRequest request, ConnectionPool pool) {
        if (pool == null) {
            request.response().setStatusCode(503).end();
            return;
        }
        int id;
        try {
            id = Integer.parseInt(request.path().substring("/db/orders/".length()));
        } catch (NumberFormatException ex) {
            request.response().setStatusCode(400).end();
            return;
        }

        vertx.executeBlocking(() -> lookupOrder(pool, id), false)
            .onSuccess(body -> write(request.response(), "application/json", body))
            .onFailure(error -> request.response().setStatusCode(error instanceof NoSuchElementException ? 404 : 500).end());
    }

    private static byte[] lookupOrder(ConnectionPool pool, int id) throws Exception {
        Connection connection = pool.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where id = ?")) {
            statement.setInt(1, id);
            try (ResultSet resultSet = statement.executeQuery()) {
                if (!resultSet.next()) {
                    throw new NoSuchElementException("order not found");
                }
                DbOrderResponse response = new DbOrderResponse(
                    resultSet.getInt(1),
                    resultSet.getString(2),
                    resultSet.getInt(3),
                    resultSet.getString(4));
                return MAPPER.writeValueAsBytes(response);
            }
        } finally {
            pool.release(connection);
        }
    }

    private static void dbPage(Vertx vertx, io.vertx.core.http.HttpServerRequest request, ConnectionPool pool) {
        if (pool == null) {
            request.response().setStatusCode(503).end();
            return;
        }
        String customerId = request.getParam("customerId") == null ? "customer-42" : request.getParam("customerId");
        int limit = parseInt(request.getParam("limit"), 50);
        limit = Math.min(100, Math.max(1, limit));
        int finalLimit = limit;
        vertx.executeBlocking(() -> pageOrders(pool, customerId, finalLimit), false)
            .onSuccess(body -> write(request.response(), "application/json", body))
            .onFailure(error -> request.response().setStatusCode(500).end());
    }

    private static byte[] pageOrders(ConnectionPool pool, String customerId, int limit) throws Exception {
        List<DbOrderResponse> orders = new ArrayList<>(limit);
        Connection connection = pool.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where customer_id = ? order by id limit ?")) {
            statement.setString(1, customerId);
            statement.setInt(2, limit);
            try (ResultSet resultSet = statement.executeQuery()) {
                while (resultSet.next()) {
                    orders.add(new DbOrderResponse(
                        resultSet.getInt(1),
                        resultSet.getString(2),
                        resultSet.getInt(3),
                        resultSet.getString(4)));
                }
            }
            return MAPPER.writeValueAsBytes(new DbOrderPageResponse(customerId, orders.size(), orders));
        } finally {
            pool.release(connection);
        }
    }

    private static void dbWrite(Vertx vertx, io.vertx.core.http.HttpServerRequest request, ConnectionPool pool) {
        if (pool == null) {
            request.response().setStatusCode(503).end();
            return;
        }
        request.body().onSuccess(body -> {
            try {
                DbOrderWriteRequest writeRequest = MAPPER.readValue(body.getBytes(), DbOrderWriteRequest.class);
                vertx.executeBlocking(() -> writeOrder(pool, writeRequest), false)
                    .onSuccess(responseBody -> write(request.response(), "application/json", responseBody))
                    .onFailure(error -> request.response().setStatusCode(500).end());
            } catch (Exception ex) {
                request.response().setStatusCode(400).end();
            }
        }).onFailure(error -> request.response().setStatusCode(400).end());
    }

    private static byte[] writeOrder(ConnectionPool pool, DbOrderWriteRequest request) throws Exception {
        Connection connection = pool.borrow();
        try (PreparedStatement statement = connection.prepareStatement("insert into order_writes (customer_id, total_cents, status) values (?, ?, ?) returning id")) {
            statement.setString(1, request.customerId());
            statement.setInt(2, request.totalCents());
            statement.setString(3, request.status());
            try (ResultSet resultSet = statement.executeQuery()) {
                resultSet.next();
                return MAPPER.writeValueAsBytes(new DbOrderWriteResponse(resultSet.getLong(1), true));
            }
        } finally {
            pool.release(connection);
        }
    }

    private static void cacheOrder(Vertx vertx, io.vertx.core.http.HttpServerRequest request, RedisConnectionPool redisPool, ConnectionPool dbPool) {
        if (redisPool == null) {
            request.response().setStatusCode(503).end();
            return;
        }
        int id;
        try {
            id = Integer.parseInt(request.path().substring("/cache/orders/".length()));
        } catch (NumberFormatException ex) {
            request.response().setStatusCode(400).end();
            return;
        }
        vertx.executeBlocking(() -> cacheOrder(redisPool, dbPool, id), false)
            .onSuccess(body -> write(request.response(), "application/json", body))
            .onFailure(error -> request.response().setStatusCode(error instanceof NoSuchElementException ? 404 : 500).end());
    }

    private static byte[] cacheOrder(RedisConnectionPool redisPool, ConnectionPool dbPool, int id) throws Exception {
        String key = "order:" + id;
        byte[] cached = redisPool.get(key);
        if (cached != null) {
            return cached;
        }
        if (dbPool == null) {
            throw new NoSuchElementException("order not found");
        }
        byte[] body = lookupOrder(dbPool, id);
        redisPool.set(key, body);
        return body;
    }

    private static int parseInt(String value, int defaultValue) {
        if (value == null) {
            return defaultValue;
        }
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException ex) {
            return defaultValue;
        }
    }

    private static byte[] encodeBinaryPayload(int id, String category, double amount, boolean active) {
        byte[] categoryBytes = category.getBytes(StandardCharsets.UTF_8);
        ByteBuffer buffer = ByteBuffer.allocate(4 + 2 + categoryBytes.length + 8 + 1).order(ByteOrder.LITTLE_ENDIAN);
        buffer.putInt(id);
        buffer.putShort((short) categoryBytes.length);
        buffer.put(categoryBytes);
        buffer.putDouble(amount);
        buffer.put((byte) (active ? 1 : 0));
        return buffer.array();
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
            int size = Integer.parseInt(System.getenv().getOrDefault("PERFBENCH_DB_POOL", "16"));
            ArrayBlockingQueue<Connection> connections = new ArrayBlockingQueue<>(size);
            try {
                for (int i = 0; i < size; i++) {
                    connections.add(DriverManager.getConnection(url));
                }
            } catch (SQLException ex) {
                throw new IllegalStateException("failed to initialize database pool", ex);
            }
            return new ConnectionPool(connections);
        }

        Connection borrow() throws InterruptedException {
            return connections.take();
        }

        void release(Connection connection) {
            connections.offer(connection);
        }
    }
}
