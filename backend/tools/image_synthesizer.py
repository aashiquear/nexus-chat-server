"""
Image Synthesizer tool — SmolVLM-powered image-to-text.

Uses HuggingFace's SmolVLM-256M-Instruct (a tiny 0.3B-param vision-language
model) to convert an uploaded image into a rich textual description that any
LLM can reason over.  The model and processor are loaded lazily on first use
so they consume no resources until the tool is actually invoked.
"""

import json
import logging
import threading
from pathlib import Path

from . import BaseTool, register_tool

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tiff", ".tif",
}

MODEL_ID = "HuggingFaceTB/SmolVLM-256M-Instruct"

# Module-level singleton — shared across requests, loaded once.
_model = None
_processor = None
_device = None
_lock = threading.Lock()


def _ensure_model_loaded(cache_dir: str | None = None):
    """Load model + processor once (thread-safe)."""
    global _model, _processor, _device

    if _model is not None:
        return

    with _lock:
        # Double-check after acquiring lock
        if _model is not None:
            return

        try:
            import torch
            from transformers import AutoProcessor, AutoModelForVision2Seq
        except ImportError as e:
            raise RuntimeError(
                "SmolVLM requires 'transformers' and 'torch'. "
                "Install them with: pip install transformers torch"
            ) from e

        _device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading SmolVLM-256M-Instruct on %s …", _device)

        load_kwargs = {}
        if cache_dir:
            load_kwargs["cache_dir"] = cache_dir

        _processor = AutoProcessor.from_pretrained(MODEL_ID, **load_kwargs)
        _model = AutoModelForVision2Seq.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.bfloat16 if _device == "cuda" else torch.float32,
            _attn_implementation="eager",
            **load_kwargs,
        ).to(_device)
        _model.eval()

        logger.info("SmolVLM model ready on %s", _device)


def _describe_image(image, prompt: str, max_tokens: int = 500) -> str:
    """Run SmolVLM inference on a single PIL image and return the text."""
    import torch

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image"},
                {"type": "text", "text": prompt},
            ],
        },
    ]

    text_prompt = _processor.apply_chat_template(
        messages, add_generation_prompt=True
    )
    inputs = _processor(
        text=text_prompt, images=[image], return_tensors="pt"
    )
    inputs = {
        k: v.to(_device) if hasattr(v, "to") else v
        for k, v in inputs.items()
    }

    with torch.no_grad():
        generated_ids = _model.generate(**inputs, max_new_tokens=max_tokens)

    decoded = _processor.batch_decode(generated_ids, skip_special_tokens=True)
    raw = decoded[0] if decoded else ""

    # The decoded output includes the prompt; strip it.
    # The assistant reply follows the last "Assistant:" marker.
    if "Assistant:" in raw:
        raw = raw.split("Assistant:")[-1].strip()

    return raw


@register_tool("image_synthesizer")
class ImageSynthesizerTool(BaseTool):
    name = "image_synthesizer"
    description = (
        "Analyze an uploaded image using a local vision model (SmolVLM) and "
        "return a detailed textual description the LLM can reason over. "
        "Extracts visual content, text, layout, colours, objects, and context. "
        "Use this tool when the user wants to discuss or analyse an image."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": (
                    "Name of the uploaded image file to analyze "
                    "(e.g. 'photo.jpg', 'chart.png')."
                ),
            },
            "prompt": {
                "type": "string",
                "description": (
                    "Optional custom prompt/question about the image. "
                    "Defaults to a comprehensive description request."
                ),
            },
        },
        "required": ["filename"],
    }

    async def execute(self, **kwargs) -> str:
        filename = kwargs.get("filename", "")
        user_prompt = kwargs.get("prompt", "").strip()

        upload_dir = self.upload_dir()
        filepath = upload_dir / Path(filename).name

        # ── Validate file ──────────────────────────────────────────
        if not filepath.exists():
            return json.dumps({"error": f"Image file not found: {filename}"})

        ext = filepath.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return json.dumps({
                "error": f"Unsupported image format '{ext}'. "
                         f"Supported: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
            })

        # ── Open image ─────────────────────────────────────────────
        try:
            from PIL import Image
        except ImportError:
            return json.dumps({
                "error": "Pillow (PIL) is not installed. "
                         "Install with: pip install Pillow"
            })

        try:
            img = Image.open(filepath).convert("RGB")
        except Exception as e:
            return json.dumps({"error": f"Failed to open image: {e}"})

        # ── Load model (lazy, first-call only) ─────────────────────
        cache_dir = self.config.get("model_cache_dir")
        try:
            _ensure_model_loaded(cache_dir)
        except RuntimeError as e:
            return json.dumps({"error": str(e)})

        # ── Build prompts and run inference ────────────────────────
        # Always get a comprehensive description; optionally answer a
        # user-specific question too.
        default_prompt = (
            "Describe this image in detail. Include: what the image shows, "
            "any text visible, colors, objects, people, layout, and context. "
            "Be thorough but concise."
        )

        try:
            description = _describe_image(img, default_prompt, max_tokens=500)
        except Exception as e:
            logger.exception("SmolVLM inference failed")
            return json.dumps({"error": f"Vision model inference failed: {e}"})

        result = {
            "filename": filename,
            "size": {"width": img.width, "height": img.height},
            "description": description,
        }

        # If the user asked a specific question, answer that too.
        if user_prompt and user_prompt.lower() != default_prompt.lower():
            try:
                answer = _describe_image(img, user_prompt, max_tokens=300)
                result["answer"] = answer
                result["question"] = user_prompt
            except Exception as e:
                result["answer_error"] = str(e)

        # Build a single summary string the LLM can consume directly.
        summary_parts = [
            f"Image '{filename}' ({img.width}x{img.height}):",
            description,
        ]
        if "answer" in result:
            summary_parts.append(f"\nUser question: {user_prompt}")
            summary_parts.append(f"Answer: {result['answer']}")

        result["summary"] = "\n".join(summary_parts)

        return json.dumps(result, indent=2)
