"""
AI Service - VLM integration for inspection analysis.
Uses Google Gemini as the VLM provider.
"""

import json
import re
import time

from google import genai
from google.genai import types
from pydantic import BaseModel, Field

import settings_service
from logger import get_logger

logger = get_logger("cloud_ai_service", "cloud_ai_service.log")


class InspectionResult(BaseModel):
    """Structured schema for inspection results."""
    is_NG: bool = Field(description="True if abnormal/NG, False if normal/OK")
    Description: str = Field(description="Issue description if NG, empty if OK")


def _extract_json_from_text(text):
    """Extract a JSON object from text that may contain markdown fences or surrounding text."""
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Extract from ```json ... ``` fence
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. Find first { ... } in text
    m = re.search(r'\{[^{}]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def parse_ai_response(response_obj):
    """
    Parse AI service response into standardized format.

    Args:
        response_obj: Response from generate_inspection or generate_report

    Returns:
        dict with keys:
            - result_text: JSON string or text result
            - is_ng: bool (True if NG)
            - description: str (issue description)
            - input_tokens: int
            - output_tokens: int
            - total_tokens: int
            - usage_json: str (JSON string of usage data)
    """
    result = {
        'result_text': '',
        'is_ng': False,
        'description': '',
        'input_tokens': 0,
        'output_tokens': 0,
        'total_tokens': 0,
        'usage_json': '{}'
    }

    if not response_obj:
        return result

    # Handle dict response from AIService
    if isinstance(response_obj, dict) and "result" in response_obj:
        result_data = response_obj["result"]
        usage_data = response_obj.get("usage", {})

        result['usage_json'] = json.dumps(usage_data)
        result['input_tokens'] = usage_data.get("prompt_token_count", 0)
        result['output_tokens'] = usage_data.get("candidates_token_count", 0)
        result['total_tokens'] = usage_data.get("total_token_count", 0)
    else:
        result_data = response_obj

    # Parse result data
    if isinstance(result_data, dict):
        result['is_ng'] = result_data.get("is_NG", False)
        result['description'] = result_data.get("Description", "")
        result['result_text'] = json.dumps(result_data, ensure_ascii=False)
    elif isinstance(result_data, str):
        result['result_text'] = result_data
        result['description'] = result_data
        # Simple heuristic for string responses
        result['is_ng'] = 'ng' in result_data.lower()
    else:
        result['result_text'] = str(result_data)
        result['description'] = result['result_text']

    return result


# ---------------------------------------------------------------------------
# Gemini Provider
# ---------------------------------------------------------------------------
class _GeminiProvider:
    """Google Gemini VLM provider."""

    def __init__(self):
        self.client = None
        self.api_key = None
        self.model_name = "gemini-2.0-flash"

    def configure(self, settings):
        new_api_key = settings.get("gemini_api_key")
        new_model_name = settings.get("gemini_model", "gemini-2.0-flash")

        if new_api_key != self.api_key or new_model_name != self.model_name or self.client is None:
            logger.info(f"Configuring Gemini with model: {new_model_name}")
            self.api_key = new_api_key
            self.model_name = new_model_name

            if self.api_key:
                try:
                    self.client = genai.Client(api_key=self.api_key)
                    logger.info("Gemini configured successfully.")
                except Exception as e:
                    logger.error(f"Gemini Configuration Error: {e}")
                    self.client = None
            else:
                logger.warning("Gemini configured without API Key.")
                self.client = None

    def get_model_name(self):
        return self.model_name

    def is_configured(self):
        return self.client is not None

    def _extract_usage(self, response):
        try:
            usage = response.usage_metadata
            return {
                "prompt_token_count": usage.prompt_token_count,
                "candidates_token_count": usage.candidates_token_count,
                "total_token_count": usage.total_token_count
            }
        except Exception as e:
            logger.warning(f"Could not extract token usage: {e}")
            return {}

    def generate_inspection(self, image, user_prompt, system_prompt=None):
        if not self.client:
            raise Exception("AI Model not configured. Check API Key in settings.")

        contents = []
        if system_prompt:
            contents.append(system_prompt)
        contents.append(user_prompt)
        contents.append(image)

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InspectionResult
        )

        try:
            logger.info(f"Gemini inspection request to {self.model_name}")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=contents,
                config=config
            )
            usage_data = self._extract_usage(response)
            logger.info(f"Token Usage: {usage_data}")
            result_data = json.loads(response.text) if response.text else {}
            return {"result": result_data, "usage": usage_data}
        except Exception as e:
            logger.error(f"Gemini Generation Error: {e}")
            raise

    def generate_report(self, report_prompt):
        if not self.client:
            raise Exception("AI Model not configured.")

        try:
            logger.info(f"Gemini report request to {self.model_name}")
            prompt = report_prompt or "Generate a summary report of the patrol."
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=prompt
            )
            usage_data = self._extract_usage(response)
            logger.info(f"Report Token Usage: {usage_data}")
            return {"result": response.text, "usage": usage_data}
        except Exception as e:
            logger.error(f"Gemini Report Error: {e}")
            raise

    def analyze_video(self, video_path, user_prompt):
        if not self.client:
            raise Exception("AI Model not configured.")

        try:
            logger.info(f"Uploading video {video_path}...")
            video_file = self.client.files.upload(file=video_path)

            while video_file.state.name == "PROCESSING":
                time.sleep(2)
                video_file = self.client.files.get(name=video_file.name)

            if video_file.state.name == "FAILED":
                raise Exception("Video processing failed.")

            logger.info(f"Video ready. Analyzing with prompt: {user_prompt}")
            response = self.client.models.generate_content(
                model=self.model_name,
                contents=[video_file, user_prompt]
            )
            usage_data = self._extract_usage(response)
            return {"result": response.text, "usage": usage_data}
        except Exception as e:
            logger.error(f"Video Analysis Error: {e}")
            raise


# ---------------------------------------------------------------------------
# AIService (Gemini only)
# ---------------------------------------------------------------------------
class AIService:
    """VLM service — uses Google Gemini for inspection analysis."""

    def __init__(self):
        self._gemini = _GeminiProvider()
        self._configure()

    def _configure(self):
        settings = settings_service.get_all()
        self._gemini.configure(settings)

    def get_model_name(self):
        self._configure()
        return self._gemini.get_model_name()

    def is_configured(self):
        self._configure()
        return self._gemini.is_configured()

    def generate_inspection(self, image, user_prompt, system_prompt=None):
        self._configure()
        return self._gemini.generate_inspection(image, user_prompt, system_prompt)

    def generate_report(self, report_prompt):
        self._configure()
        return self._gemini.generate_report(report_prompt)

    def analyze_video(self, video_path, user_prompt):
        self._configure()
        return self._gemini.analyze_video(video_path, user_prompt)


ai_service = AIService()
