FROM nousresearch/hermes-agent@sha256:adb7890916de63245523492be8f68f9d2095fecbec4e2a6dab7960381f0fc002
ENV PYTHONDONTWRITEBYTECODE=1
# Wheels from the reviewed plugin uv.lock. The pinned base supplies their other dependencies.
ADD --checksum=sha256:66d2759d1921838256a05a3f80ad7e724936f083e35be5abb5e16eed6be6dc54 https://files.pythonhosted.org/packages/1a/99/84ba7273339d0f3dfa57901b846489d2e5c2cd731470167757f1935fffbd/aiohttp_retry-2.9.1-py3-none-any.whl /tmp/hindsight-wheels/
ADD --checksum=sha256:690c728a17207a9e4df0b26c32abd1dfee627d1895615881f30ed709ab4126c3 https://files.pythonhosted.org/packages/6a/d0/848c7734d37215df53353dcea3de326ecc3bab6d908a12621530f5b81562/hindsight_client-1.2.0-py3-none-any.whl /tmp/hindsight-wheels/
ADD --checksum=sha256:b142112da4c28564b4104c0055da6f86e9338fdda47afc5052de4836a5ff2e06 https://files.pythonhosted.org/packages/7e/98/b58a47f65e2d10e57f3b4870f81022d625535a1339786de6ad800b9acbfd/hindsight_embed-1.2.0-py3-none-any.whl /tmp/hindsight-wheels/
RUN /opt/hermes/tools/uv-0.13.5-linux-x64/uv pip install --python /opt/hermes/.venv/bin/python --offline --no-index /tmp/hindsight-wheels/*.whl && \
    /opt/hermes/.venv/bin/python -c "from hindsight_client import Hindsight; from hindsight_embed import get_embed_manager" && \
    rm -rf /tmp/hindsight-wheels
