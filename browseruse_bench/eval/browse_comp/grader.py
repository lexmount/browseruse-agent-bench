"""BrowseComp evaluation utilities"""
import os
import re
import time

from openai import APIConnectionError, APIError, OpenAI, RateLimitError

from browseruse_bench.utils import load_eval_config, load_prompt, make_template_prompt

# Load eval configuration (returns {} if file missing — all .get() have fallbacks)
_EVAL_CFG = load_eval_config("BrowseComp")
_MODEL_CFG = _EVAL_CFG
_PROMPT_DIR = _EVAL_CFG.get("prompt_dir", "browseruse_bench/eval/browse_comp/prompts")


class GraderModel:
    def __init__(self, model="gpt-4.1", api_key=None, base_url=None, temperature=None):
        kwargs = {}
        if api_key:
            kwargs['api_key'] = api_key
        if base_url:
            kwargs['base_url'] = base_url
        self.client = OpenAI(**kwargs)
        self.model = model
        self.temperature = temperature
        self.last_usage = None

    def __call__(self, prompt: str) -> str:
        self.last_usage = None
        _max_tries = _MODEL_CFG.get("max_tries", 5)
        for trial in range(_max_tries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature if self.temperature is not None else _MODEL_CFG.get("temperature", 0),
                    max_tokens=_MODEL_CFG.get("max_tokens", 1024),
                )
                self.last_usage = getattr(response, "usage", None)
                return response.choices[0].message.content or ""
            except (APIError, APIConnectionError, RateLimitError):
                if trial < _max_tries - 1:
                    time.sleep(2 ** trial)
                else:
                    self.last_usage = None
                    raise
        raise RuntimeError("Unreachable: max_tries exhausted without return")

def load_grader_model(model=None, api_key=None, base_url=None, temperature=None):
    model = model or os.getenv("EVAL_MODEL_NAME", _MODEL_CFG.get("model", "gpt-4.1"))
    api_key = api_key or os.getenv("OPENAI_API_KEY")
    base_url = base_url or os.getenv("EVAL_MODEL_BASE_URL")
    if not api_key:
        raise ValueError("OPENAI_API_KEY required")
    return GraderModel(model, api_key, base_url, temperature=temperature)

def grade_response(question: str, correct_answer: str, agent_response: str, grader_fn) -> dict:
    """Grade agent response using LLM grader.

    Returns a dict with grading results plus a ``user_prompt``
    ``TemplatePrompt`` for snapshot traceability.
    """
    grader_template, prompt_ref = load_prompt(
        f"{_PROMPT_DIR}/grader_user.txt"
    )
    prompt_params = {
        "question": question,
        "correct_answer": correct_answer,
        "response": agent_response,
    }
    grader_prompt = grader_template.format(**prompt_params)
    grading_response = grader_fn(grader_prompt)
    match = re.search(r"correct: (yes|no)", grading_response, re.IGNORECASE)
    match_result = match.group(1).lower() if match else "no"

    user_prompt_snapshot = make_template_prompt(
        grader_template, prompt_ref, prompt_params,
    )

    return {
        "is_correct": (match_result == "yes"),
        "grader_response": grading_response,
        "match_result": match_result,
        "usage": getattr(grader_fn, "last_usage", None),
        "user_prompt": user_prompt_snapshot,
    }
