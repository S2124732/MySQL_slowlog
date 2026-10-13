CREATE TABLE mysql_slowlog (
    id INT UNSIGNED AUTO_INCREMENT,
    start_time DATETIME COMMENT 'When the query was run',
    query_time_seconds DECIMAL(10,2) UNSIGNED,
    lock_time_seconds DECIMAL(10,2) UNSIGNED,
    rows_sent INT UNSIGNED,
    rows_examined INT UNSIGNED,
    db_name VARCHAR(64),
    user_host VARCHAR(128) COMMENT 'Example: app_user[app_user] @ 10.0.x.x',
    sql_text TEXT,
    PRIMARY KEY (id)
);