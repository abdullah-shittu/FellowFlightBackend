# db_utils.py

import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
import os
import uuid
from datetime import date, time
from typing import Optional, Dict, Any, List

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


# --- NEW: Helper function to get a dict cursor connection ---
def get_db_conn():
    """Gets a connection from the pool with a dictionary cursor."""
    conn = db_pool.getconn()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    return conn, cursor


def return_db_conn(conn):
    """Returns a connection to the pool."""
    db_pool.putconn(conn)


# --- User Functions (Adapted and New) ---


def find_or_create_user(slack_id: str, name: str) -> Dict[str, Any]:
    """Finds a user by Slack ID, or creates them if they don't exist."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute("SELECT * FROM users WHERE slack_id = %s", (slack_id,))
        user = cursor.fetchone()
        if user:
            return user

        # User not found, create them
        user_uuid = str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO users (id, slack_id, name) VALUES (%s, %s, %s) RETURNING *",
            (user_uuid, slack_id, name),
        )
        new_user = cursor.fetchone()
        conn.commit()
        return new_user
    finally:
        return_db_conn(conn)


def get_user_by_id(user_id: uuid.UUID) -> Optional[Dict[str, Any]]:
    """Fetches a single user by their internal UUID."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        return cursor.fetchone()
    finally:
        return_db_conn(conn)


def get_user_by_slack_id(slack_id: str) -> Optional[Dict[str, Any]]:
    """Fetches a single user by their internal UUID."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute("SELECT * FROM users WHERE slack_id = %s", (slack_id,))
        return cursor.fetchone()
    finally:
        return_db_conn(conn)


def update_user_linkedin(
    user_id: uuid.UUID, linkedin_url: str
) -> Optional[Dict[str, Any]]:
    """Updates the LinkedIn URL for a given user."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute(
            "UPDATE users SET linkedin_url = %s WHERE id = %s RETURNING *",
            (linkedin_url, user_id),
        )
        updated_user = cursor.fetchone()
        conn.commit()
        return updated_user
    finally:
        return_db_conn(conn)


def delete_user(user_id: uuid.UUID):
    """Deletes a user and all their data (cascades to flights)."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
    finally:
        return_db_conn(conn)


# --- Flight Functions (Adapted and New) ---


def insert_flight(user_id: uuid.UUID, flight: dict) -> Dict[str, Any]:
    """Inserts a new flight entry for a given user."""
    conn, cursor = get_db_conn()
    try:
        insert_sql = """
            INSERT INTO flights (user_id, flight_number, date, departure_time, dep_airport, hours_early)
            VALUES (%s, %s, %s, %s, %s, %s) RETURNING *;
        """
        cursor.execute(
            insert_sql,
            (
                user_id,
                flight["flight_number"],
                flight["date"],
                flight["departure_time"],
                flight["dep_airport"],
                flight["hours_early"],
            ),
        )
        new_flight = cursor.fetchone()
        conn.commit()
        return new_flight
    finally:
        return_db_conn(conn)


def check_flight_ownership(flight_id: int, user_id: uuid.UUID) -> bool:
    """Checks if a given flight belongs to the specified user."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute(
            "SELECT 1 FROM flights WHERE id = %s AND user_id = %s", (flight_id, user_id)
        )
        return cursor.fetchone() is not None
    finally:
        return_db_conn(conn)


def delete_flight(flight_id: int):
    """Deletes a single flight entry."""
    conn, cursor = get_db_conn()
    try:
        cursor.execute("DELETE FROM flights WHERE id = %s", (flight_id,))
        conn.commit()
    finally:
        return_db_conn(conn)


# --- Match Functions (MODIFIED for API spec) ---


def find_matches_for_flight(flight_id: int, user_id: uuid.UUID) -> List[Dict[str, Any]]:
    """Finds users on the exact same flight."""
    conn, cursor = get_db_conn()
    try:
        sql = """
            SELECT u2.name, u2.linkedin_url, u2.slack_id
            FROM flights f1
            JOIN flights f2 ON f1.flight_number = f2.flight_number AND f1.date = f2.date
            JOIN users u2 ON f2.user_id = u2.id
            WHERE f1.id = %s AND f2.user_id != %s;
        """
        cursor.execute(sql, (flight_id, user_id))
        return cursor.fetchall()
    finally:
        return_db_conn(conn)


def find_overlaps_for_flight(
    flight_id: int, user_id: uuid.UUID
) -> List[Dict[str, Any]]:
    """Finds users with overlapping airport times."""
    conn, cursor = get_db_conn()
    try:
        sql = """
            SELECT DISTINCT ON (u2.id)
                u2.name, u2.linkedin_url, u2.slack_id,
                EXTRACT(EPOCH FROM (
                    LEAST(f1.date + f1.departure_time, f2.date + f2.departure_time) - 
                    GREATEST(
                        f1.date + f1.departure_time - (f1.hours_early * '1 hour'::interval),
                        f2.date + f2.departure_time - (f2.hours_early * '1 hour'::interval)
                    )
                )) / 60 AS overlap_minutes
            FROM flights f1
            JOIN flights f2 ON f1.dep_airport = f2.dep_airport
            JOIN users u2 ON f2.user_id = u2.id
            WHERE f1.id = %s 
              AND f2.user_id != %s
              AND (f1.flight_number, f1.date) IS DISTINCT FROM (f2.flight_number, f2.date)
              AND (
                  tstzrange(
                      f1.date + f1.departure_time - (f1.hours_early * '1 hour'::interval),
                      f1.date + f1.departure_time, '[]'
                  ) && 
                  tstzrange(
                      f2.date + f2.departure_time - (f2.hours_early * '1 hour'::interval),
                      f2.date + f2.departure_time, '[]'
                  )
              )
            ORDER BY u2.id, overlap_minutes DESC;
        """
        cursor.execute(sql, (flight_id, user_id))
        return cursor.fetchall()
    finally:
        return_db_conn(conn)
