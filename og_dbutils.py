import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv
import os
import uuid
from datetime import date, time
from typing import Optional, Dict, Any

# Load environment variables from .env
load_dotenv()

# Fetch variables
USER = os.getenv("user")
PASSWORD = os.getenv("password")
HOST = os.getenv("host")
PORT = os.getenv("port")
DBNAME = os.getenv("dbname")

# Connect to the database
try:
    # A DSN (Data Source Name) is a more common way to pass all connection parameters
    dsn = f"dbname={DBNAME} user={USER} password={PASSWORD} host={HOST} port={PORT}"

    # Create a connection pool with a minimum of 1 connection and a maximum of 20
    # Adjust these numbers based on your expected traffic
    db_pool = ThreadedConnectionPool(1, 20, dsn)
    print("Database connection pool created successfully!")

except Exception as e:
    print(f"Failed to create connection pool: {e}")
    # You might want to exit the application if the connection fails at startup
    exit(1)


# --- Function to create tables ---
def create_tables():
    """
    Creates the 'users' and 'flights' tables in the database.
    It gets a connection from the pool, executes the SQL, and commits the transaction.
    """
    conn = None
    try:
        # Get a connection from the pool
        conn = db_pool.getconn()
        cursor = conn.cursor()

        # SQL statement to create the 'users' table
        create_users_table_sql = """
            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY,
                slack_id VARCHAR(20) UNIQUE NOT NULL,
                name TEXT NOT NULL,
                linkedin_url TEXT,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """
        cursor.execute(create_users_table_sql)
        print("Table 'users' created or already exists.")

        # SQL statement to create the 'flights' table
        create_flights_table_sql = """
            CREATE TABLE IF NOT EXISTS flights (
                id SERIAL PRIMARY KEY,
                user_id UUID REFERENCES users(id) ON DELETE CASCADE,
                flight_number VARCHAR(10) NOT NULL,
                date DATE NOT NULL,
                departure_time TIME NOT NULL, -- ← added this line
                dep_airport VARCHAR(5) NOT NULL,
                hours_early NUMERIC(3, 1) NOT NULL,
                created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
            );
        """
        cursor.execute(create_flights_table_sql)
        print("Table 'flights' created or already exists.")

        # Commit the transaction to save the changes to the database
        conn.commit()
        print("Tables created successfully.")

    except Exception as e:
        # Roll back the transaction if any error occurs
        if conn:
            conn.rollback()
        print(f"Failed to create tables: {e}")

    finally:
        # Return the connection to the pool
        if conn:
            db_pool.putconn(conn)


# --- Function to insert a new user ---
def insert_user(slack_id: str, name: str, linkedin_url: str = None) -> uuid.UUID:
    """
    Inserts a new user into the 'users' table and returns their new UUID.

    Returns:
        A uuid.UUID object of the newly created user's ID, or None on failure.
    """
    conn = None
    new_user_id = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        # Generate a UUID in Python. This is necessary because we set the id ourselves.
        user_uuid = uuid.uuid4()

        insert_user_sql = """
            INSERT INTO users (id, slack_id, name, linkedin_url)
            VALUES (%s, %s, %s, %s)
            RETURNING id;
        """

        # Pass the values as a tuple to the execute method
        cursor.execute(insert_user_sql, (str(user_uuid), slack_id, name, linkedin_url))

        # Fetch the ID of the newly inserted row
        new_user_id = cursor.fetchone()[0]

        conn.commit()
        print(f"Successfully inserted user with ID: {new_user_id}")
        return new_user_id

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Failed to insert user: {e}")
        return None

    finally:
        if conn:
            db_pool.putconn(conn)


# --- Function to insert a new flight registration ---
def insert_flight(
    user_id: uuid.UUID,
    flight_number: str,
    flight_date: date,
    departure_time: time,
    dep_airport: str,
    hours_early: float,
) -> int:
    """
    Inserts a new flight entry for a given user and returns the new flight ID.

    Returns:
        The integer ID of the newly created flight, or None on failure.
    """
    conn = None
    new_flight_id = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        insert_flight_sql = """
            INSERT INTO flights (user_id, flight_number, date, departure_time, dep_airport, hours_early)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id;
        """

        # Pass the user_id (UUID object) directly. psycopg2 will handle the type conversion.
        cursor.execute(
            insert_flight_sql,
            (
                user_id,
                flight_number,
                flight_date,
                departure_time,
                dep_airport,
                hours_early,
            ),
        )

        new_flight_id = cursor.fetchone()[0]

        conn.commit()
        print(
            f"Successfully inserted flight with ID: {new_flight_id} for user: {user_id}"
        )
        return new_flight_id

    except Exception as e:
        if conn:
            conn.rollback()
        print(f"Failed to insert flight: {e}")
        return None

    finally:
        if conn:
            db_pool.putconn(conn)


# --- Function 1: find_matches ---
def find_matches(slack_id: str) -> list[str]:
    """
    Finds and returns a list of Slack IDs of users who have registered
    for the exact same flight (same flight number and date) as the
    user with the given slack_id.

    Args:
        slack_id: The Slack ID of the user to find matches for.

    Returns:
        A list of other Slack IDs with the same flight registration.
        Returns an empty list on failure or if no matches are found.
    """
    conn = None
    matches = []
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        # Join the flights table to itself to find where the flight number
        # and date are the same, but the user ID is different.
        match_sql = """
SELECT DISTINCT u2.slack_id
FROM users u1
JOIN flights f1 ON u1.id = f1.user_id
JOIN flights f2 ON f1.flight_number = f2.flight_number AND f1.date = f2.date
JOIN users u2 ON f2.user_id = u2.id
WHERE u1.slack_id = %s
AND u1.id != u2.id;
        """

        cursor.execute(match_sql, (slack_id,))

        all_matches = cursor.fetchall()
        for match in all_matches:
            matches.append(match[0])

        return matches

    except Exception as e:
        print(f"Failed to find flight matches: {e}")
        return []

    finally:
        if conn:
            db_pool.putconn(conn)


def find_overlapping_airports(slack_id: str) -> list[tuple[str, float]]:
    """
    Finds and returns a list of users who have a flight departing from the
    same airport (but not the exact same flight) and the time overlap in minutes.

    The airport time window is now accurately calculated using the new
    `departure_time` column, which starts at `(date + departure_time - hours_early)`
    and ends at `(date + departure_time)`.

    Args:
        slack_id: The Slack ID of the user to find matches for.

    Returns:
        A list of tuples, where each tuple contains (slack_id, overlap_in_minutes).
        Returns an empty list on failure or if no matches are found.
    """
    conn = None
    overlaps = []
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        # The updated query uses the new departure_time column for more accurate calculations.
        overlap_sql = """
SELECT DISTINCT ON (u2.slack_id)
    u2.slack_id,
    -- This calculation remains the same
    EXTRACT(EPOCH FROM (
        LEAST(
            f1.date + f1.departure_time,
            f2.date + f2.departure_time
        ) - GREATEST(
            f1.date + f1.departure_time - (f1.hours_early * '1 hour'::interval),
            f2.date + f2.departure_time - (f2.hours_early * '1 hour'::interval)
        )
    )) / 60 AS overlap_minutes
FROM users u1
JOIN flights f1 ON u1.id = f1.user_id
JOIN flights f2 ON f1.dep_airport = f2.dep_airport
JOIN users u2 ON f2.user_id = u2.id
WHERE u1.slack_id = %s
AND u1.id != u2.id
AND (f1.flight_number, f1.date) IS DISTINCT FROM (f2.flight_number, f2.date)
AND LEAST(f1.date + f1.departure_time, f2.date + f2.departure_time) >
    GREATEST(f1.date + f1.departure_time - (f1.hours_early * '1 hour'::interval),
             f2.date + f2.departure_time - (f2.hours_early * '1 hour'::interval))
-- Add this ORDER BY clause
ORDER BY u2.slack_id, overlap_minutes DESC;
        """

        cursor.execute(overlap_sql, (slack_id,))

        all_overlaps = cursor.fetchall()
        for overlap in all_overlaps:
            if overlap[1] > 0:
                overlaps.append((overlap[0], overlap[1]))

        return overlaps

    except Exception as e:
        print(f"Failed to find airport overlaps: {e}")
        return []

    finally:
        if conn:
            db_pool.putconn(conn)


def delete_tables():
    """
    Deletes the 'flights' and 'users' tables from the database.
    It handles foreign key dependencies by dropping 'flights' first.
    """
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        # IMPORTANT: Drop tables in the correct order due to foreign key constraints.
        # 'flights' depends on 'users', so 'flights' must be dropped first.
        # Alternatively, you could use 'DROP TABLE IF EXISTS users CASCADE;'
        # but explicitly dropping in order is clearer for beginners.
        sql_commands = """
            DROP TABLE IF EXISTS flights CASCADE;
            DROP TABLE IF EXISTS users CASCADE;
        """

        # Execute the SQL command
        cursor.execute(sql_commands)

        # Commit the transaction to make the changes permanent
        conn.commit()
        print("Tables 'flights' and 'users' deleted successfully.")

    except Exception as e:
        # Roll back the transaction if any error occurs
        if conn:
            conn.rollback()
        print(f"Failed to delete tables: {e}")
        # Re-raise the exception if you want the calling code to know about the failure
        raise

    finally:
        # ALWAYS return the connection to the pool
        if conn:
            db_pool.putconn(conn)


# --- Function to find a user by Slack ID ---
def find_user(slack_id: str) -> Optional[Dict[str, Any]]:
    """
    Finds a user by their Slack ID and returns their data in a dictionary (JSON-like format).

    Args:
        slack_id: The Slack ID of the user to find.

    Returns:
        A dictionary containing the user's data, or None if no user is found.
        The dictionary keys are 'id', 'slack_id', 'name', 'linkedin_url', and 'created_at'.
        UUID and datetime objects are converted to strings for JSON compatibility.
    """
    conn = None
    try:
        conn = db_pool.getconn()
        cursor = conn.cursor()

        find_user_sql = """
            SELECT id, slack_id, name, linkedin_url, created_at
            FROM users
            WHERE slack_id = %s;
        """
        cursor.execute(find_user_sql, (slack_id,))
        user_record = cursor.fetchone()

        # If no record is found, return None
        if not user_record:
            return None

        # Unpack the record into a dictionary, converting types for JSON serialization
        user_dict = {
            "id": str(user_record[0]),  # Convert UUID to string
            "slack_id": user_record[1],
            "name": user_record[2],
            "linkedin_url": user_record[3],
            "created_at": user_record[4].isoformat(),  # Convert datetime to ISO string
        }
        return user_dict

    except Exception as e:
        print(f"Failed to find user with slack_id '{slack_id}': {e}")
        return None

    finally:
        if conn:
            db_pool.putconn(conn)


# --- Function to run tests ---
def run_tests():
    """
    Orchestrates a series of tests to validate the matching logic.
    It creates tables, seeds data, runs find functions, and then cleans up.
    """
    print("--- 🚀 STARTING TEST SEQUENCE 🚀 ---")
    try:
        # 1. SETUP: Create clean tables
        print("\n[Step 1] Creating tables...")
        create_tables()
        print("Tables created successfully.")

        # 2. SEED DATA: Insert users and flights for our test cases
        print("\n[Step 2] Seeding test data...")
        # Our main user
        alice_id = insert_user("alice_slack", "Alice")
        # A user on the exact same flight
        bob_id = insert_user("bob_slack", "Bob")
        # A user with an overlapping flight at the same airport
        charlie_id = insert_user("charlie_slack", "Charlie")
        # A user at the same airport, but with no time overlap
        david_id = insert_user("david_slack", "David")
        # A user at a different airport
        eve_id = insert_user("eve_slack", "Eve")

        # Flight data for Alice (our reference)
        # Window: Aug 10, 08:00 - 10:00 at IAH
        insert_flight(alice_id, "UA100", date(2025, 8, 10), time(10, 0), "IAH", 2.0)

        # Bob has the exact same flight
        insert_flight(bob_id, "UA100", date(2025, 8, 10), time(10, 0), "IAH", 3.0)

        # Charlie has a flight from IAH with a 60-minute overlap
        # Window: Aug 10, 08:30 - 09:30 at IAH (Overlaps 08:30-09:30)
        insert_flight(charlie_id, "DL200", date(2025, 8, 10), time(9, 30), "IAH", 1.0)
        # Charlie also has a second flight with a 30-minute overlap.
        # The test will confirm we only get the LARGEST overlap (60 mins).
        # Window: Aug 10, 09:30 - 11:00 at IAH (Overlaps 09:30-10:00)
        insert_flight(charlie_id, "AA300", date(2025, 8, 10), time(11, 0), "IAH", 1.5)

        # David is at IAH, but his flight is too late to overlap
        # Window: Aug 10, 11:00 - 13:00 at IAH
        insert_flight(david_id, "SW400", date(2025, 8, 10), time(13, 0), "IAH", 2.0)

        # Eve is at a completely different airport
        insert_flight(eve_id, "FR500", date(2025, 8, 10), time(10, 0), "HOU", 2.0)
        print("Test data seeded successfully.")

        # 3. EXECUTE & VERIFY
        print("\n[Step 3] Running matching functions for Alice...")

        # Test find_user
        print("\n--- Testing find_user ---")
        alice_data = find_user("alice_slack")
        non_existent_user = find_user("ghost_slack")
        print(f"Expected name for 'alice_slack': Alice")
        print(f"Actual name: {alice_data.get('name') if alice_data else 'Not Found'}")
        assert (
            alice_data and alice_data["name"] == "Alice"
        ), "find_user failed to find Alice"
        print("Expected for 'ghost_slack': None")
        print(f"Actual: {non_existent_user}")
        assert non_existent_user is None, "find_user found a non-existent user"
        print("✅ Test PASSED")

        # Test find_matches
        print("\n--- Testing find_matches ---")
        matches = find_matches("alice_slack")
        print(f"Expected: ['bob_slack']")
        print(f"Actual:   {matches}")
        assert matches == ["bob_slack"], "find_matches test failed!"
        print("✅ Test PASSED")

        # Test find_overlapping_airports
        print("\n--- Testing find_overlapping_airports ---")
        overlaps = find_overlapping_airports("alice_slack")
        # We convert to a set for order-independent comparison
        overlaps_set = set(overlaps)
        expected_set = {("charlie_slack", 60.0)}
        print(f"Expected: {expected_set}")
        print(f"Actual:   {overlaps_set}")
        assert overlaps_set == expected_set, "find_overlapping_airports test failed!"
        print("✅ Test PASSED")

    except Exception as e:
        print(f"\n--- ❌ TEST FAILED ❌ ---")
        print(f"An error occurred during the test: {e}")

    finally:
        # 4. TEARDOWN: Delete the tables to clean up the environment
        print("\n[Step 4] Cleaning up...")
        try:
            delete_tables()
        except Exception as e:
            print(f"Could not delete tables during cleanup: {e}")
        print("--- ✅ TEST SEQUENCE COMPLETE ✅ ---")


# --- Main execution block ---
if __name__ == "__main__":
    # Run the full test suite
    delete_tables()
    create_tables()

    # Close the connection pool when the application is done
    db_pool.closeall()
    print("Connection pool closed.")
