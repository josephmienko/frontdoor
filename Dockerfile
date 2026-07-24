FROM python:3.11-slim

RUN pip install --no-cache-dir uv
WORKDIR /opt/access-control
COPY pyproject.toml README.md ./
COPY src ./src
COPY configs ./configs
RUN uv sync --no-dev

USER 65532:65532
ENTRYPOINT ["uv", "run", "--no-sync", "access-control"]
CMD ["check-config", "--config", "configs/local.yaml"]

