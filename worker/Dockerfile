# VisionGuard worker – GPU video analysis
# Base: CUDA 12.4 / cuDNN 9 (host driver supports CUDA <= 12.4)
FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
# torch/torchvision are pinned by the base image; keep them.
RUN pip install -r /tmp/requirements.txt \
 # resolve duplicate packages: one OpenCV (headless) and only the GPU build of onnxruntime
 && pip uninstall -y opencv-python opencv-python-headless onnxruntime onnxruntime-gpu \
 && pip install "opencv-python-headless==4.10.0.84" "onnxruntime-gpu==1.20.2" \
 && python -c "import torch, cv2, onnxruntime, ultralytics, supervision, insightface, fast_alpr, timm; print('ok', torch.__version__, cv2.__version__, onnxruntime.__version__, onnxruntime.get_available_providers())"

# YOLOE text prompts (Inventar): ultralytics.nn.text_model imports `clip` and would otherwise try
# `pip install git+https://github.com/ultralytics/CLIP.git` at runtime (no git in the image -> fails).
# Pinned commit as tarball, no deps (torch/torchvision/tqdm come from the base image).
# The MobileCLIP text encoder weights (mobileclip_blt.ts, ~600 MB) are loaded once into /models/yoloe.
RUN pip install --no-deps "ftfy==6.3.1" "regex==2024.11.6" \
      "clip @ https://github.com/ultralytics/CLIP/archive/7ffa84b3bfa40c42ecc1c77147a855e69cb2dd40.tar.gz" \
 && python -c "import clip, ultralytics.nn.text_model; print('clip ok')"

# B1 Zeitmaschine: SigLIP 2 über open_clip (messungen.md §5). Eigene Schicht, ohne Abhängigkeitsauflösung
# (torch/timm/huggingface_hub/ftfy kommen aus den Schichten davor); transformers/tokenizers/sentencepiece für den
# Gemma-Tokenizer des Textturms. Gewichte (~1,4 GB inkl. Textturm) einmalig nach /models/history.
RUN pip install --no-deps "open_clip_torch==3.2.0" "transformers==4.57.6" "tokenizers==0.22.2" \
      "sentencepiece==0.2.0" \
 && python -c "import open_clip, transformers, tokenizers; print('open_clip ok', open_clip.__version__, transformers.__version__)"

# cuDNN/cuBLAS from the torch wheels for onnxruntime-gpu
ENV LD_LIBRARY_PATH=/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cublas/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cuda_runtime/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cufft/lib:/opt/conda/lib/python3.11/site-packages/nvidia/curand/lib:/opt/conda/lib/python3.11/site-packages/nvidia/cuda_nvrtc/lib:${LD_LIBRARY_PATH}

# all model downloads/caches go to the /models volume
ENV VG_MODELS_DIR=/models \
    YOLO_CONFIG_DIR=/models/ultralytics \
    YOLO_OFFLINE=0 \
    HF_HOME=/models/hf \
    TORCH_HOME=/models/torch \
    INSIGHTFACE_HOME=/models/insightface \
    XDG_CACHE_HOME=/models/cache \
    HOME=/models/home

WORKDIR /app
COPY vg_worker /app/vg_worker

CMD ["python", "-m", "vg_worker"]
