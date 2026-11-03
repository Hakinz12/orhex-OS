from sqlalchemy import create_engine, inspect, text

from app.core.database import _migrate_conversations


def test_existing_conversations_receive_memory_retrieval_metadata():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE users (id VARCHAR(64) PRIMARY KEY)")
        connection.exec_driver_sql(
            "CREATE TABLE conversations ("
            "id VARCHAR(64) PRIMARY KEY, "
            "user_id VARCHAR(64) NOT NULL, "
            "visitor_identity_id VARCHAR(64), "
            "version INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP NOT NULL, "
            "updated_at TIMESTAMP NOT NULL)"
        )
        connection.exec_driver_sql("INSERT INTO users (id) VALUES ('owner')")
        connection.exec_driver_sql(
            "INSERT INTO conversations "
            "(id, user_id, version, created_at, updated_at) "
            "VALUES ('existing-conversation', 'owner', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        )

        _migrate_conversations(connection)

        columns = {
            column["name"]
            for column in inspect(connection).get_columns("conversations")
        }
        status, count = connection.execute(
            text(
                "SELECT memory_context_status, memory_context_count "
                "FROM conversations WHERE id = 'existing-conversation'"
            )
        ).one()

    assert "memory_context_status" in columns
    assert "memory_context_count" in columns
    assert status == "not_retrieved"
    assert count == 0
    engine.dispose()
