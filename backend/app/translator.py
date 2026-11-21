"""Machine translation with Meta's NLLB-200 model (runs locally, on the CPU or a GPU)."""

import os
import threading
from functools import lru_cache

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

MODEL = os.getenv("MODEL_ID", "facebook/nllb-200-distilled-600M")

# Beam-search width: 5 = best quality but slowest, 1 = fastest (greedy).
NUM_BEAMS = int(os.getenv("NUM_BEAMS", "2"))

# Dynamic int8 quantisation of the Linear layers: lower memory use on x86 CPUs.
QUANTIZE = os.getenv("QUANTIZE_INT8", "0") == "1"


class Translator:
    def __init__(self):
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(MODEL)

        if self.device == "cpu" and QUANTIZE:
            self.model = torch.quantization.quantize_dynamic(
                self.model, {torch.nn.Linear}, dtype=torch.qint8
            )

        self.model = self.model.to(self.device)
        self.model.eval()

    def translate(self, text, source, target):
        """Translate `text` from the `source` language code to the `target` language code."""
        if source == target:
            return text

        self.tokenizer.src_lang = source
        inputs = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        inputs = inputs.to(self.device)

        with torch.inference_mode():
            output = self.model.generate(
                **inputs,
                forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(target),
                max_new_tokens=512,
                num_beams=NUM_BEAMS,
                no_repeat_ngram_size=3,
                repetition_penalty=1.1,
            )

        return self.tokenizer.batch_decode(output, skip_special_tokens=True)[0]


@lru_cache(maxsize=1)
def get_translator():
    """The model is large, so it is loaded once and reused for every request."""
    return Translator()


def preload():
    """Load the model in a background thread at server start so the first message is not delayed."""

    def load_and_warm_up():
        translator = get_translator()
        translator.translate("Hello", "eng_Latn", "zul_Latn")  # one warm-up pass

    threading.Thread(target=load_and_warm_up, daemon=True, name="model-preload").start()
