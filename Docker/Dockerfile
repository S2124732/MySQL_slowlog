FROM python:3.12-slim

# The script lives in /src, outside the folder you mount for output.
WORKDIR /src

# Install dependencies before copying the script, so this layer is only rebuilt when
# requirements.txt changes. The bind mount reads the file without copying it into the image.
RUN --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip install --no-cache-dir -r requirements.txt

# Copy the script (the only COPY in this file).
COPY scripts/ scripts/

# Mount your local output folder here, e.g. docker run -v <local folder>:/app ...
WORKDIR /app

# Run the script from /src; --out-dir points it at the mounted folder.
ENTRYPOINT ["python", "/src/scripts/main.py", "--out-dir", "/app"]
