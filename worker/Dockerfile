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
