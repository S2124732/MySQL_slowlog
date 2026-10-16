# MySQL Slowlog_Generator
This project was created as a way to easily generate sample MySQL slowlog data to mimic a production environment.
Data is designed to be used for project work like analytics or building out sample pipelines.
Docker is used here so you don't need Python or Faker installed on your machine, and so everyone
gets the same Python and Faker versions (which matters for getting the same rows from the same seed).

## Sample Output

Example rows from a run with the default seed (`--seed 42`), copied straight from the generated files.
The full files contain 10,000 rows. Start times, and the date ranges inside some queries, are relative
to the day you run the script, so the dates in your output will differ from these.

CSV (`mysql_slowlog_fake.csv`):

```text
start_time,query_time_seconds,lock_time_seconds,rows_sent,rows_examined,db_name,user_host,sql_text
...
2026-09-16 00:46:54,1.39,0.02,200,33125,billing_prod,report_user[report_user] @ 10.0.3.9,"SELECT subscriptions.subscription_id, subscriptions.monthly_price, subscriptions.started_at, accounts.company_name, accounts.billing_email FROM subscriptions INNER JOIN accounts ON accounts.account_id = subscriptions.account_id WHERE subscriptions.status = 'canceled' AND subscriptions.renews_at >= NOW() - INTERVAL 14 DAY ORDER BY subscriptions.monthly_price DESC LIMIT 200"
2026-09-16 01:13:47,2.59,0.04,12,128241,shop_prod,app_user[app_user] @ 10.0.35.109,"SELECT DATE(created_at) AS day, COUNT(*) AS cnt, SUM(total_amount) AS total, ROUND(AVG(total_amount), 2) AS avg_value FROM orders WHERE created_at BETWEEN '2026-09-02' AND '2026-09-15 23:59:59' GROUP BY DATE(created_at) ORDER BY day"
2026-09-16 01:38:41,3.35,0.01,0,491192,shop_prod,app_user[app_user] @ 10.0.38.110,DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL 30 DAY LIMIT 5000
...
```

SQL (`mysql_slowlog_fake.sql`):

```sql
START TRANSACTION;
INSERT INTO mysql_slowlog (start_time, query_time_seconds, lock_time_seconds, rows_sent, rows_examined, db_name, user_host, sql_text) VALUES
...
('2026-09-16 00:46:54', 1.39, 0.02, 200, 33125, 'billing_prod', 'report_user[report_user] @ 10.0.3.9', 'SELECT subscriptions.subscription_id, subscriptions.monthly_price, subscriptions.started_at, accounts.company_name, accounts.billing_email FROM subscriptions INNER JOIN accounts ON accounts.account_id = subscriptions.account_id WHERE subscriptions.status = ''canceled'' AND subscriptions.renews_at >= NOW() - INTERVAL 14 DAY ORDER BY subscriptions.monthly_price DESC LIMIT 200'),
('2026-09-16 01:13:47', 2.59, 0.04, 12, 128241, 'shop_prod', 'app_user[app_user] @ 10.0.35.109', 'SELECT DATE(created_at) AS day, COUNT(*) AS cnt, SUM(total_amount) AS total, ROUND(AVG(total_amount), 2) AS avg_value FROM orders WHERE created_at BETWEEN ''2026-09-02'' AND ''2026-09-15 23:59:59'' GROUP BY DATE(created_at) ORDER BY day'),
('2026-09-16 01:38:41', 3.35, 0.01, 0, 491192, 'shop_prod', 'app_user[app_user] @ 10.0.38.110', 'DELETE FROM login_attempts WHERE attempted_at < NOW() - INTERVAL 30 DAY LIMIT 5000'),
...
COMMIT;
```

## What's in the data

Rows are spread across five fake databases (the `db_name` column), each with its own tables and application accounts:

| `db_name` | What it simulates | Approx. share of rows |
|---|---|---|
| `shop_prod` | Online store: customers, products, orders | 40% |
| `analytics_dw` | Marketing / web analytics warehouse | 20% |
| `billing_prod` | SaaS subscription billing | 15% |
| `inventory_prod` | Warehouse inventory and purchasing | 15% |
| `crm_prod` | Sales CRM and customer support | 10% |

## Run with Docker

The build step is identical in every shell. The run step differs only in how the current folder is written in the `-v` option: `${PWD}\output` in PowerShell, `$(pwd)/output` in bash.

### 1. Build the image

This image uses BuildKit's `RUN --mount` (see the `Dockerfile`), so you need Docker Engine 23.0 or later,
or a current Docker Desktop. Older Docker versions fail with an error about `--mount`.

Clone this repo, then run this from the project directory (the one that contains the `Docker` folder):

```
docker build -t slowlog-gen Docker
```

`slowlog-gen` is just a name; use whatever you like, as long as you use the same name when you run it.

### 2. Run the container

This produces a `.csv` and a `.sql` file in a local `output` folder. The `-v` option mounts that folder into the container at `/app`, which is how the files get out of the container.

#### PowerShell (Windows)

```powershell
docker run --rm -v "${PWD}\output:/app" slowlog-gen
```

#### bash (Linux, macOS, WSL)

```bash
docker run --rm -v "$(pwd)/output:/app" slowlog-gen
```

- On Linux and WSL the container runs as root, so the files in `output/` end up owned by root. To own them yourself, create the folder first and pass your user id:

  ```bash
  mkdir -p output
  docker run --rm --user "$(id -u):$(id -g)" -v "$(pwd)/output:/app" slowlog-gen
  ```

- In Git Bash on Windows, put `MSYS_NO_PATHCONV=1` in front of the command so `/app` isn't rewritten into a Windows path.

### 3. Pass options (optional)

Anything after the image name goes to the script (see [Options](#options)). Don't pass `--out-dir` here; the `-v` mount decides where the files land.

#### PowerShell (Windows)

```powershell
docker run --rm -v "${PWD}\output:/app" slowlog-gen --rows 5000 --seed 2
```

#### bash (Linux, macOS, WSL)

```bash
docker run --rm -v "$(pwd)/output:/app" slowlog-gen --rows 5000 --seed 2
```

## Run as a script (without Docker)

Use Python 3.12 (the version the Docker image uses). Install the dependencies once, then run the script from the project directory. The commands are the same in PowerShell and bash; on Linux and macOS use `python3` if `python` isn't found, and a virtual environment is a good idea.

```
pip install -r Docker/requirements.txt
python Docker/scripts/main.py --rows 5000 --seed 2 --out-dir output
```

## Options

| Option | What it does | Default |
|---|---|---|
| `--rows` | Total number of rows to produce | `10000` |
| `--seed` | Seed for the random generator. Reuse it to get the same queries again (dates are always relative to the day you run it) | `42` |
| `--out-dir` | Folder the files are written to. **Script only:** in Docker the image already sets this to `/app`, so pick the output folder with the `-v` mount instead | the folder containing `main.py` |

The output files are always named `mysql_slowlog_fake.csv` and `mysql_slowlog_fake.sql`.

## Other Notes

- This does not create an instance that runs on Docker to hold this slowlog data.
- `Docker/requirements.txt` pins Faker to an exact version because Faker's data changes between releases. Upgrading it changes what a given `--seed` produces.