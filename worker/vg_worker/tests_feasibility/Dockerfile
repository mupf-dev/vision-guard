# Test-Image für die Machbarkeitstests (W0-b). Baut auf dem fertigen Worker-Image auf und ergänzt nur, was die
# Tests zusätzlich brauchen (open_clip für histtest, transformers für den SigLIP-2-Tokenizer).
# Nicht für den Produktivbetrieb; der Worker bleibt unverändert.
#   docker build -t vg-w0-feasibility:test -f worker/vg_worker/tests_feasibility/Dockerfile worker/vg_worker/tests_feasibility
ARG BASE=vision-guard-worker:latest
FROM ${BASE}
RUN pip install "open_clip_torch==3.2.0" "transformers==4.57.6" "sentencepiece==0.2.0" "scikit-learn==1.6.1" \
      "numpy==2.1.2" "torch==2.5.1" "torchvision==0.20.1" "timm==1.0.20" \
 && python -c "import open_clip, transformers, sklearn, torch; print('ok', open_clip.__version__, transformers.__version__, torch.__version__)"
