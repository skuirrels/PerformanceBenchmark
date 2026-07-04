package dev.perfbench;

import dev.perfbench.grpc.QuoteRequest;
import dev.perfbench.grpc.QuoteResponse;
import dev.perfbench.grpc.QuoteServiceGrpc;
import io.grpc.Server;
import io.grpc.ServerBuilder;
import io.grpc.stub.StreamObserver;

public final class GrpcApiServer {
    private GrpcApiServer() {
    }

    public static void main(String[] args) throws Exception {
        int port = parsePort(args);
        Server server = ServerBuilder
            .forPort(port)
            .addService(new QuoteRpcService())
            .build()
            .start();
        Runtime.getRuntime().addShutdownHook(new Thread(server::shutdown));
        System.out.println("listening grpc://127.0.0.1:" + port);
        server.awaitTermination();
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

    private static final class QuoteRpcService extends QuoteServiceGrpc.QuoteServiceImplBase {
        @Override
        public void quote(QuoteRequest request, StreamObserver<QuoteResponse> responseObserver) {
            double multiplier = request.getExpedited() ? 1.2 : 1.0;
            double total = Math.round(request.getItemCount() * request.getUnitPrice() * multiplier * 100.0) / 100.0;
            QuoteResponse response = QuoteResponse.newBuilder()
                .setCustomerId(request.getCustomerId())
                .setTotal(total)
                .setExpedited(request.getExpedited())
                .setAccepted(true)
                .build();
            responseObserver.onNext(response);
            responseObserver.onCompleted();
        }
    }
}
