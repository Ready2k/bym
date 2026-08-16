# Runtime image for the stdlib http.server UI + worker (see docker-compose.yml
# for how the two processes share this image). Deliberately not the
# FastAPI/Postgres shape SPEC.md originally sketched — the shipped app is
# stdlib http.server + SQLite/JSON (see CLAUDE.md), and this image packages
# exactly that, nothing more.
FROM python:3.12-slim

WORKDIR /app

# Only what `pip install .` needs — the dependency list resolves from
# pyproject.toml, so this layer caches until either changes.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && useradd --create-home --shell /usr/sbin/nologin bym \
    && chown -R bym:bym /app

USER bym

# data/, rounds/, config/, secrets/, results/ are bind-mounted by
# docker-compose.yml — nothing under them is baked into the image.
EXPOSE 8000

# No CMD: docker-compose.yml sets the command per service (web vs worker),
# since both run from this one image.
