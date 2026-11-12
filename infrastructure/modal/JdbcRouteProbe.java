import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.util.Arrays;
import java.util.Locale;
import java.util.Properties;

public final class JdbcRouteProbe {
    private static final int SAMPLES = 100;

    private static double execute(PreparedStatement statement) throws Exception {
        long start = System.nanoTime();
        statement.execute();
        return (System.nanoTime() - start) / 1_000_000.0;
    }

    private static String summary(double[] samples) {
        Arrays.sort(samples);
        return String.format(Locale.ROOT, "{\"samples\":%d,\"p50_ms\":%.6f,\"p95_ms\":%.6f,\"p99_ms\":%.6f}",
            samples.length, samples[49], samples[94], samples[98]);
    }

    public static void main(String[] args) throws Exception {
        Properties options = new Properties();
        options.setProperty("user", "portal_runtime");
        options.setProperty("password", System.getenv("DB_RUNTIME_PASSWORD"));
        options.setProperty("connectTimeout", "3");
        options.setProperty("socketTimeout", "3");
        try (Connection connection = DriverManager.getConnection(args[0], options);
             PreparedStatement select = connection.prepareStatement("SELECT 1");
             PreparedStatement begin = connection.prepareStatement("BEGIN READ ONLY");
             PreparedStatement commit = connection.prepareStatement("COMMIT")) {
            double[] query = new double[SAMPLES], starts = new double[SAMPLES], commits = new double[SAMPLES];
            for (int i = 0; i < 20; i++) execute(select);
            for (int i = 0; i < SAMPLES; i++) query[i] = execute(select);
            for (int i = 0; i < SAMPLES; i++) {
                starts[i] = execute(begin);
                execute(select);
                commits[i] = execute(commit);
            }
            System.out.println("{\"select_1\":" + summary(query) + ",\"begin\":" + summary(starts) +
                ",\"read_only_commit\":" + summary(commits) + "}");
        }
    }
}
