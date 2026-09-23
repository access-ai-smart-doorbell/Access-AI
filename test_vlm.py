import cv2
import os
from dotenv import load_dotenv
load_dotenv()
from accessai.vlm_module import VLMModule
from config import VLM_FALLBACK_MODELS
keys = os.environ.get("GEMINI_API_KEY", "").split(",")
v = VLMModule(keys=keys, model="gemini-3.6-flash", extra_providers=VLM_FALLBACK_MODELS)
import numpy as np
img = np.zeros((480, 640, 3), dtype=np.uint8)
print(v.describe_and_read(img, facts="2 people detected. Person: unknown person (in the center). Person: unknown person (on the right)"))
