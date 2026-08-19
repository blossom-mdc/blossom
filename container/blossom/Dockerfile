# Build with: `docker build -t blossom:0.2.0 -f container/blossom/Dockerfile .` when at blossom main dir
FROM ubuntu:24.04

ENV LANG="C.UTF-8" \
    LC_ALL="C.UTF-8" \
    PATH="/home/sentinel/.poetry/bin:/home/sentinel/.local/bin:$PATH" \
    PIP_NO_CACHE_DIR="false" \
    GDAL_CACHEMAX="100%" \
    GDAL_DISABLE_READDIR_ON_OPEN="true" \
    PYTHONUNBUFFERED="1"

RUN apt-get update && DEBIAN_FRONTEND=noninteractive \
    apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    pipx \
    python-is-python3 \
    python3-dev \
    python3-setuptools \
    python3-venv \
    curl \
    ca-certificates \
    wait-for-it \
    gdal-bin \
    libgdal-dev \
    python3-gdal \
    jq \
    bash-completion \
    build-essential \
    gcc \
    vim && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1001 sentinel && \
    useradd  --uid 1001 --gid sentinel --shell /bin/bash --create-home sentinel

RUN chown -R sentinel:sentinel /usr/local/

USER 1001
RUN mkdir /home/sentinel/app
WORKDIR /home/sentinel/app

# create the venv and activate it
# https://github.com/orgs/python-poetry/discussions/1879#discussioncomment-13151718
ENV VIRTUAL_ENV="/home/sentinel/venv"
RUN python -m venv $VIRTUAL_ENV
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

RUN pipx install poetry
RUN pipx run poetry --version
COPY --chown=sentinel:sentinel pyproject.toml poetry.lock ./
RUN pipx run poetry install --no-interaction --no-root --ansi --extras "dev" --extras "build"

# there was a problem with gdal not detecting numpy which resulted in an error when
# running `from osgeo import gdal_array` (or using `.ReadAsArray()`).
# "ImportError: cannot import name '_gdal_array' from 'osgeo'"
# An solution is provided here: https://github.com/OSGeo/gdal/issues/8069#issuecomment-2034022162
RUN pip uninstall -y gdal || true
RUN pip install numpy
RUN pip install -U setuptools wheel
RUN pip install --no-build-isolation --no-cache-dir --force-reinstall "gdal==$(gdal-config --version)"

# Test gdal to ensure proper installation
RUN python3 -c "from osgeo import gdal; print(gdal.__version__)"
RUN python3 -c "from osgeo import gdal_array"