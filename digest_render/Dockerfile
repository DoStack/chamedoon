# Renders the courier daily board (render.py). Chrome comes with the Playwright image; fonts, flags
# and photos are local, so a render needs no network.
FROM mcr.microsoft.com/playwright/python:v1.48.0-noble
WORKDIR /render
RUN pip install --no-cache-dir playwright==1.48.0
COPY render.py ./
COPY assets ./assets
CMD ["python", "render.py"]
