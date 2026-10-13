FROM python:3.11-slim

# 1. Copy your script outside of the mounted output folder
WORKDIR /src
COPY . .
RUN pip install --no-cache-dir -r requirements.txt

# 2. Set the WORKDIR to the folder you will mount your local folder to
WORKDIR /app

# 3. Tell Python to look back at the source folder to run the script
ENTRYPOINT ["python", "/src/scripts/main.py", "--out-dir", "/app"]