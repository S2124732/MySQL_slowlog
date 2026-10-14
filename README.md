# MySQL Slowlog_Generator
This project was created as a way to easily generate sample MySQL slwolog data to mimic a production environment.
Data is designed to be used for project work like analytics or building out sample pipelines. 
Docker is used here as a way to isolate resources in the event you want to produce lots of rows.

## Sample Output: 
CSV: 
start_time	    query_time_seconds	lock_time_seconds	rows_sent	rows_examined	db_name	        user_host	                          sql_text
9/13/2026 0:21	0.81	            0.04	            50	        55105	        billing_prod	billing_svc[billing_svc] @ 10.0.3.9	  SELECT account_id, plan_tier, status FROM accounts WHERE status = 'suspended' ORDER BY created_at DESC LIMIT 50 OFFSET 55050
9/13/2026 0:45	0.89	            0.02	            5	        46312	        shop_prod	    app_user[app_user] @ 10.0.3.9	      SELECT o.order_status, COUNT(*) AS num_rows, SUM(oi.unit_price) AS total FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id WHERE oi.created_at BETWEEN '2026-09-06' AND '2026-09-12 23:59:59' AND p.category = 'apparel' GROUP BY o.order_status HAVING COUNT(*) > 10 ORDER BY total DESC LIMIT 20
9/13/2026 1:00	2.54	            0.04	            58	        170554	        shop_prod	    app_user[app_user] @ 10.0.6.57	      SELECT DATE(created_at) AS day, COUNT(*) AS cnt, SUM(unit_price) AS total, ROUND(AVG(unit_price), 2) AS avg_value FROM order_items WHERE created_at BETWEEN '2026-07-15' AND '2026-09-12 23:59:59' GROUP BY DATE(created_at) ORDER BY day
...

SQL: 
START TRANSACTION;
INSERT INTO mysql_slowlog (start_time, query_time_seconds, lock_time_seconds, rows_sent, rows_examined, db_name, user_host, sql_text) VALUES
('2026-09-13 00:21:21', 0.81, 0.04, 50, 55105, 'billing_prod', 'billing_svc[billing_svc] @ 10.0.3.9', 'SELECT account_id, plan_tier, status FROM accounts WHERE status = ''suspended'' ORDER BY created_at DESC LIMIT 50 OFFSET 55050'),
('2026-09-13 00:45:45', 0.89, 0.02, 5, 46312, 'shop_prod', 'app_user[app_user] @ 10.0.3.9', 'SELECT o.order_status, COUNT(*) AS num_rows, SUM(oi.unit_price) AS total FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id WHERE oi.created_at BETWEEN ''2026-09-06'' AND ''2026-09-12 23:59:59'' AND p.category = ''apparel'' GROUP BY o.order_status HAVING COUNT(*) > 10 ORDER BY total DESC LIMIT 20'),
('2026-09-13 01:00:52', 2.54, 0.04, 58, 170554, 'shop_prod', 'app_user[app_user] @ 10.0.6.57', 'SELECT DATE(created_at) AS day, COUNT(*) AS cnt, SUM(unit_price) AS total, ROUND(AVG(unit_price), 2) AS avg_value FROM order_items WHERE created_at BETWEEN ''2026-07-15'' AND ''2026-09-12 23:59:59'' GROUP BY DATE(created_at) ORDER BY day'),
...
COMMIT;

# How to run with Docker

1. Clone this repo
	
2. Within the project directory run the following:
	"docker build -t slowlog-gen Docker"  # or whatever name you want instead of 'slowlog-gen'

3. Run the following to produce a csv and a .sql file: 
	"docker run --rm -v "${PWD}\output:/app" slowlog-gen" 


# Modify your output
The following are arguments that can be used to produce different outputs: 

--rows     # determines the total number of rows produced 
--seed     # code made of numbers used to generate the slowlog output (use the same seed if you want the same data)

--out-dir  # *should only be configured when running as a script* changes where the output is directed

Defaults:
rows = 10000
seed = 42
out-dir = Path(__file__).resolve().parent  # only applicable when running outside of Docker

# Running Docker with arguments?

Use a command like the following:
	"docker run --rm -v "${PWD}\output:/app" slowlog-gen --rows 5000 --seed 2"
	
# Other Notes
This does not create an instance that runs on Docker to hold this slowlog data.
