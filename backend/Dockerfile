FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (layer-cached)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . .

# Copy schema.sql so db.py can find it at /app/data/schema.sql
# (db.py resolves Path(__file__).parent.parent / "data" / "schema.sql")
# We bake it in during image build; the path is satisfied by the COPY below.
# The actual data/ dir is one level above backend/ in the repo, so we
# rely on the Docker build context being set to the project root (see deploy.ps1).
# If schema.sql is missing the schema-init step is skipped gracefully.

EXPOSE 8000

# Use 0.0.0.0 so ECS can route traffic from the ALB
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
