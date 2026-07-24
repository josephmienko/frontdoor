FROM python:3.13-slim AS builder

RUN pip install --no-cache-dir poetry==2.4.1
WORKDIR /opt/bridgewire
COPY pyproject.toml poetry.lock README.md ./
COPY src ./src
RUN poetry build --format wheel

FROM python:3.13-slim
WORKDIR /opt/bridgewire
COPY --from=builder /opt/bridgewire/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl
COPY configs ./configs
COPY schemas ./schemas

USER 65532:65532
ENTRYPOINT ["bridgewire"]
CMD ["simulate", "--config", "configs/simulation.toml"]
