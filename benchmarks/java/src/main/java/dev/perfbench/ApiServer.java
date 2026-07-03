package dev.perfbench;

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
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
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executors;

public final class ApiServer {
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final HttpClient CLIENT = HttpClient.newHttpClient();
    private static final byte[] HEALTH = "ok".getBytes(StandardCharsets.UTF_8);
    private static final byte[] PLAINTEXT = "hello, world".getBytes(StandardCharsets.UTF_8);
    private static final byte[] JSON = "{\"message\":\"hello, world\",\"value\":42,\"active\":true}".getBytes(StandardCharsets.UTF_8);
    private static final byte[] DOWNSTREAM = "{\"service\":\"downstream\",\"value\":42}".getBytes(StandardCharsets.UTF_8);
    private static final Payload PAYLOAD = new Payload("hello, world", 42, true);

    private ApiServer() {
    }

    public static void main(String[] args) throws IOException {
        int port = parsePort(args);
        boolean virtualThreads = hasFlag(args, "--virtual-threads");
        ConnectionPool dbPool = ConnectionPool.fromEnvironment();
        RedisConnectionPool redisPool = RedisConnectionPool.fromEnvironment();
        HttpServer server = HttpServer.create(new InetSocketAddress(port), 0);
        server.createContext("/health", exchange -> write(exchange, "text/plain", HEALTH));
        server.createContext("/plaintext", exchange -> write(exchange, "text/plain", PLAINTEXT));
        server.createContext("/json", exchange -> write(exchange, "application/json", JSON));
        server.createContext("/json-serde", exchange -> write(exchange, "application/json", MAPPER.writeValueAsBytes(PAYLOAD)));
        server.createContext("/orders/quote", ApiServer::quote);
        server.createContext("/fanout", ApiServer::fanout);
        server.createContext("/downstream", exchange -> write(exchange, "application/json", DOWNSTREAM));
        server.createContext("/serialize/json", exchange -> write(exchange, "application/json", MAPPER.writeValueAsBytes(new FormatPayload(123456, "standard", 99.95, true))));
        server.createContext("/serialize/binary", exchange -> write(exchange, "application/octet-stream", encodeBinaryPayload(123456, "standard", 99.95, true)));
        server.createContext("/db/orders", exchange -> dbOrders(exchange, dbPool));
        server.createContext("/cache/orders", exchange -> cacheOrder(exchange, redisPool, dbPool));
        server.setExecutor(virtualThreads
            ? Executors.newVirtualThreadPerTaskExecutor()
            : Executors.newFixedThreadPool(Math.max(256, Runtime.getRuntime().availableProcessors() * 16)));
        server.start();
        System.out.println("listening http://127.0.0.1:" + port + " virtualThreads=" + virtualThreads);
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

    private static boolean hasFlag(String[] args, String flag) {
        for (String arg : args) {
            if (flag.equals(arg)) {
                return true;
            }
        }
        return false;
    }

    private static void write(HttpExchange exchange, String contentType, byte[] body) throws IOException {
        exchange.getResponseHeaders().set("Content-Type", contentType);
        exchange.sendResponseHeaders(200, body.length);
        try (var stream = exchange.getResponseBody()) {
            stream.write(body);
        }
    }

    private static void quote(HttpExchange exchange) throws IOException {
        if (!"POST".equalsIgnoreCase(exchange.getRequestMethod())) {
            exchange.sendResponseHeaders(405, -1);
            return;
        }
        QuoteRequest request = MAPPER.readValue(exchange.getRequestBody(), QuoteRequest.class);
        double multiplier = request.expedited() ? 1.2 : 1.0;
        double total = Math.round(request.itemCount() * request.unitPrice() * multiplier * 100.0) / 100.0;
        write(exchange, "application/json", MAPPER.writeValueAsBytes(new QuoteResponse(request.customerId(), total, request.expedited(), true)));
    }

    private static void fanout(HttpExchange exchange) throws IOException {
        String baseUrl = "http://" + exchange.getRequestHeaders().getFirst("Host");
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
        write(exchange, "application/json", MAPPER.writeValueAsBytes(new FanoutResponse(responses.length, bytes, complete)));
    }

    private static void dbOrders(HttpExchange exchange, ConnectionPool pool) throws IOException {
        if (pool == null) {
            exchange.sendResponseHeaders(503, -1);
            return;
        }
        String path = exchange.getRequestURI().getPath();
        if ("/db/orders".equals(path)) {
            if ("POST".equalsIgnoreCase(exchange.getRequestMethod())) {
                dbWrite(exchange, pool);
                return;
            }
            if ("GET".equalsIgnoreCase(exchange.getRequestMethod())) {
                dbPage(exchange, pool);
                return;
            }
            exchange.sendResponseHeaders(405, -1);
            return;
        }
        dbLookup(exchange, pool);
    }

    private static void dbLookup(HttpExchange exchange, ConnectionPool pool) throws IOException {
        String prefix = "/db/orders/";
        String path = exchange.getRequestURI().getPath();
        if (!path.startsWith(prefix)) {
            exchange.sendResponseHeaders(400, -1);
            return;
        }
        int id = Integer.parseInt(path.substring(prefix.length()));
        try {
            Connection connection = pool.borrow();
            try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where id = ?")) {
                statement.setInt(1, id);
                try (ResultSet resultSet = statement.executeQuery()) {
                    if (!resultSet.next()) {
                        exchange.sendResponseHeaders(404, -1);
                        return;
                    }
                    DbOrderResponse response = new DbOrderResponse(
                        resultSet.getInt(1),
                        resultSet.getString(2),
                        resultSet.getInt(3),
                        resultSet.getString(4));
                    write(exchange, "application/json", MAPPER.writeValueAsBytes(response));
                }
            } finally {
                pool.release(connection);
            }
        } catch (SQLException | InterruptedException ex) {
            exchange.sendResponseHeaders(500, -1);
        }
    }

    private static void dbPage(HttpExchange exchange, ConnectionPool pool) throws IOException {
        String customerId = queryParam(exchange.getRequestURI().getRawQuery(), "customerId", "customer-42");
        int limit = Math.min(100, Math.max(1, parseInt(queryParam(exchange.getRequestURI().getRawQuery(), "limit", "50"), 50)));
        List<DbOrderResponse> orders = new ArrayList<>(limit);
        try {
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
            } finally {
                pool.release(connection);
            }
            write(exchange, "application/json", MAPPER.writeValueAsBytes(new DbOrderPageResponse(customerId, orders.size(), orders)));
        } catch (SQLException | InterruptedException ex) {
            exchange.sendResponseHeaders(500, -1);
        }
    }

    private static void dbWrite(HttpExchange exchange, ConnectionPool pool) throws IOException {
        DbOrderWriteRequest request = MAPPER.readValue(exchange.getRequestBody(), DbOrderWriteRequest.class);
        try {
            Connection connection = pool.borrow();
            try (PreparedStatement statement = connection.prepareStatement("insert into order_writes (customer_id, total_cents, status) values (?, ?, ?) returning id")) {
                statement.setString(1, request.customerId());
                statement.setInt(2, request.totalCents());
                statement.setString(3, request.status());
                try (ResultSet resultSet = statement.executeQuery()) {
                    resultSet.next();
                    write(exchange, "application/json", MAPPER.writeValueAsBytes(new DbOrderWriteResponse(resultSet.getLong(1), true)));
                }
            } finally {
                pool.release(connection);
            }
        } catch (SQLException | InterruptedException ex) {
            exchange.sendResponseHeaders(500, -1);
        }
    }

    private static void cacheOrder(HttpExchange exchange, RedisConnectionPool redisPool, ConnectionPool dbPool) throws IOException {
        if (redisPool == null) {
            exchange.sendResponseHeaders(503, -1);
            return;
        }
        String prefix = "/cache/orders/";
        String path = exchange.getRequestURI().getPath();
        if (!path.startsWith(prefix)) {
            exchange.sendResponseHeaders(400, -1);
            return;
        }
        int id = Integer.parseInt(path.substring(prefix.length()));
        String key = "order:" + id;
        try {
            byte[] cached = redisPool.get(key);
            if (cached != null) {
                write(exchange, "application/json", cached);
                return;
            }
            if (dbPool == null) {
                exchange.sendResponseHeaders(404, -1);
                return;
            }
            byte[] payload = lookupOrderJson(dbPool, id);
            redisPool.set(key, payload);
            write(exchange, "application/json", payload);
        } catch (SQLException | InterruptedException ex) {
            exchange.sendResponseHeaders(500, -1);
        }
    }

    private static byte[] lookupOrderJson(ConnectionPool pool, int id) throws SQLException, InterruptedException, IOException {
        Connection connection = pool.borrow();
        try (PreparedStatement statement = connection.prepareStatement("select id, customer_id, total_cents, status from orders where id = ?")) {
            statement.setInt(1, id);
            try (ResultSet resultSet = statement.executeQuery()) {
                if (!resultSet.next()) {
                    throw new SQLException("order not found");
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

    private static String queryParam(String rawQuery, String key, String defaultValue) {
        if (rawQuery == null || rawQuery.isBlank()) {
            return defaultValue;
        }
        for (String part : rawQuery.split("&")) {
            String[] pair = part.split("=", 2);
            if (pair.length == 2 && key.equals(pair[0])) {
                return pair[1];
            }
        }
        return defaultValue;
    }

    private static int parseInt(String value, int defaultValue) {
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
