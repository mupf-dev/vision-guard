# Dienst vlm (W3, A2 „Frag die Kamera“): lokales Bildmodell, Compose-Profil `vlm`, intern vlm:8200.
# Die erste pip-Schicht ist absichtlich identisch zu Dockerfile.bench (Docker-Cache, keine zweite große
# Installation). CUDA ≤ 12.4 (Host-Treiber 551.x).
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf HOME=/models/home

ARG TORCHAO=0.11.0
RUN pip install "transformers==4.57.6" "accelerate==1.10.1" "torchao==${TORCHAO}" "einops==0.8.1" \
      "bitsandbytes==0.45.5" "nvidia-ml-py==12.575.51" "pillow==10.4.0" "num2words==0.5.14" \
      "torch==2.5.1" "torchvision==0.20.1" \
 && python -c "import torch, transformers, torchao; print('ok', torch.__version__, transformers.__version__, torchao.__version__)"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt
COPY vg_vlm /app/vg_vlm

EXPOSE 8200
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
  CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8200/health',timeout=3)"
CMD ["python", "-m", "vg_vlm.server"]
