ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-python:3.12-alpine3.23
FROM $BUILD_FROM

# Install OS-level libraries required by onnxruntime and Pillow
RUN apk add --no-cache \
        gcc \
        g++ \
        libstdc++ \
        libgomp \
        libffi-dev \
        jpeg-dev \
        zlib-dev \
        freetype-dev \
        openblas-dev

# Copy and install Python dependencies
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --no-cache-dir -r /tmp/requirements.txt

# Copy server and entry-point
COPY server.py /server.py
COPY run.sh /run.sh
RUN chmod +x /run.sh

CMD ["/run.sh"]
