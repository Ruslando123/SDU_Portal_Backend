import java.sql.Connection;
import java.sql.DriverManager;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.Properties;

/** Protocol and transaction checks against the disposable portal database. */
class VerifyJdbc {
    public static void main(String[] args) throws Exception {
        String mode = args[0];
        Properties properties = new Properties();
        properties.setProperty("user", "portal_runtime");
        properties.setProperty("password", System.getenv("DB_RUNTIME_PASSWORD"));
        properties.setProperty("prepareThreshold", "1");
        String url = System.getenv("DB_URL");
        try (Connection first = DriverManager.getConnection(url, properties);
             Connection second = DriverManager.getConnection(url, properties);
             PreparedStatement named = first.prepareStatement("SELECT pg_backend_pid(), CAST(? AS integer)")) {
            first.setAutoCommit(false);
            second.setAutoCommit(false);
            int initialPid = preparedPid(named, 17);
            check(scalar(first, "SELECT pg_is_in_recovery()::text").equals("false"), "Expected a writable primary");
            execute(first, "SET LOCAL lock_timeout='137ms'");
            check(scalar(first, "SHOW lock_timeout").equals("137ms"), "Local timeout was not applied");
            first.commit();

            int otherPid = Integer.parseInt(scalar(second, "SELECT pg_backend_pid()"));
            int reusedPid = preparedPid(named, 29);
            if (mode.equals("transaction")) {
                check(otherPid == initialPid && reusedPid != initialPid,
                      "Expected a transaction to reuse the released server and the named statement to switch");
            } else {
                check(reusedPid == initialPid && otherPid != initialPid, "Expected stable session bindings");
            }
            check(scalar(first, "SHOW lock_timeout").equals("0"), "Transaction state leaked");
            String section = "'10000000-0000-4000-8000-000000000008'";
            String lock = "SELECT section_id FROM sections.course_sections WHERE section_id=" + section + " FOR UPDATE";
            scalar(first, lock);
            execute(second, "SET LOCAL lock_timeout='100ms'");
            try {
                scalar(second, lock);
                throw new IllegalStateException("The section lock was not held");
            } catch (SQLException failure) {
                check("55P03".equals(failure.getSQLState()), "Unexpected SQLSTATE while waiting for the section");
            } finally {
                second.rollback();
            }
            first.commit();
            scalar(second, lock);
            second.commit();

            first.setReadOnly(true);
            check(scalar(first, "SHOW transaction_read_only").equals("on"), "JDBC read-only transaction not enforced");
            first.commit();
        }
        System.out.println("Verified " + mode + " named JDBC statements, local settings, row lock and read-only transaction");
    }

    private static int preparedPid(PreparedStatement statement, int value) throws SQLException {
        statement.setInt(1, value);
        try (ResultSet rows = statement.executeQuery()) {
            check(rows.next() && rows.getInt(2) == value, "Incorrect prepared statement result");
            return rows.getInt(1);
        }
    }

    private static String scalar(Connection connection, String sql) throws SQLException {
        try (Statement statement = connection.createStatement(); ResultSet rows = statement.executeQuery(sql)) {
            check(rows.next(), "Expected a database row");
            return rows.getString(1);
        }
    }

    private static void execute(Connection connection, String sql) throws SQLException {
        try (Statement statement = connection.createStatement()) {
            statement.execute(sql);
        }
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new IllegalStateException(message);
    }
}
