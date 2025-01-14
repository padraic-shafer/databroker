# FROM ghcr.io/bluesky/tiled:v0.1.0b10 as base
FROM tiled:local as base

FROM base as builder

# We need git at build time in order for versioneer to work. This
# does not leak out of the API (at this time) but it seems useful
# to have it correctly reported in the build logs.
RUN apt-get -y update && apt-get install -y git

WORKDIR /code
COPY ./databroker .
COPY ./suitcase-mongo ./suitcase-mongo
RUN pip install -e ./suitcase-mongo
RUN pip install -e .[back-compat,server]

FROM base as runner

ENV VIRTUAL_ENV=/opt/venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"
COPY --from=builder $VIRTUAL_ENV $VIRTUAL_ENV
