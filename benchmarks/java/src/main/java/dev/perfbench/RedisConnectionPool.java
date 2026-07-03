package dev.perfbench;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.ArrayBlockingQueue;

final class RedisConnectionPool {
    private final ArrayBlockingQueue<RedisConnection> connections;

    private RedisConnectionPool(ArrayBlockingQueue<RedisConnection> connections) {
        this.connections = connections;
    }

    static RedisConnectionPool fromEnvironment() throws IOException {
        String endpoint = System.getenv("PERFBENCH_REDIS");
        if (endpoint == null || endpoint.isBlank()) {
            return null;
        }
        String[] parts = endpoint.split(":", 2);
        String host = parts[0];
        int port = parts.length == 2 ? Integer.parseInt(parts[1]) : 6379;
        int size = Integer.parseInt(System.getenv().getOrDefault("PERFBENCH_REDIS_POOL", "64"));
        ArrayBlockingQueue<RedisConnection> connections = new ArrayBlockingQueue<>(size);
        for (int i = 0; i < size; i++) {
            connections.add(RedisConnection.connect(host, port));
        }
        return new RedisConnectionPool(connections);
    }

    byte[] get(String key) throws IOException, InterruptedException {
        RedisConnection connection = connections.take();
        try {
            return connection.get(key);
        } finally {
            connections.offer(connection);
        }
    }

    void set(String key, byte[] value) throws IOException, InterruptedException {
        RedisConnection connection = connections.take();
        try {
            connection.set(key, value);
        } finally {
            connections.offer(connection);
        }
    }

    private static final class RedisConnection {
        private final Socket socket;

        private RedisConnection(Socket socket) {
            this.socket = socket;
        }

        static RedisConnection connect(String host, int port) throws IOException {
            Socket socket = new Socket(host, port);
            socket.setTcpNoDelay(true);
            return new RedisConnection(socket);
        }

        byte[] get(String key) throws IOException {
            writeCommand("GET", key);
            return readBulk();
        }

        void set(String key, byte[] value) throws IOException {
            writeCommand("SET", key, new String(value, StandardCharsets.UTF_8));
            readSimple();
        }

        private void writeCommand(String... parts) throws IOException {
            StringBuilder builder = new StringBuilder();
            builder.append('*').append(parts.length).append("\r\n");
            for (String part : parts) {
                builder.append('$').append(part.getBytes(StandardCharsets.UTF_8).length).append("\r\n");
                builder.append(part).append("\r\n");
            }
            socket.getOutputStream().write(builder.toString().getBytes(StandardCharsets.UTF_8));
            socket.getOutputStream().flush();
        }

        private byte[] readBulk() throws IOException {
            int prefix = socket.getInputStream().read();
            if (prefix != '$') {
                throw new IOException("unexpected redis response");
            }
            int length = Integer.parseInt(readLine());
            if (length < 0) {
                return null;
            }
            byte[] body = socket.getInputStream().readNBytes(length);
            socket.getInputStream().readNBytes(2);
            return body;
        }

        private void readSimple() throws IOException {
            int prefix = socket.getInputStream().read();
            if (prefix != '+') {
                throw new IOException("unexpected redis response");
            }
            readLine();
        }

        private String readLine() throws IOException {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream(32);
            while (true) {
                int value = socket.getInputStream().read();
                if (value == '\r') {
                    socket.getInputStream().read();
                    return buffer.toString(StandardCharsets.UTF_8);
                }
                if (value < 0) {
                    throw new IOException("redis connection closed");
                }
                buffer.write(value);
            }
        }
    }
}
