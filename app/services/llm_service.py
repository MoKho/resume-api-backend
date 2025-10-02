# app/services/llm_service.py

import os
import logging
import json
from openai import OpenAI, APIError
from dotenv import load_dotenv
from app import system_prompts
from typing import List


# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Data From Colab Notebook ---

provider_urls = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "openai": "https://api.openai.com/v1"
}

model_mapping = {
    "resume-match-agent": [
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "cerebras", "model": "gpt-oss-120b"},
        {"provider":"sambanova", "model": "gpt-oss-120b"},
        {"provider": "gemini", "model": "models/gemini-2.5-pro"}
    ],
    "resume-rewrite-agent": [
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "cerebras", "model": "gpt-oss-120b"},
        {"provider":"sambanova", "model": "DeepSeek-V3.1-Terminus"},
        {"provider":"sambanova", "model": "gpt-oss-120b"},
        {"provider": "gemini", "model": "models/gemini-2.5-pro"}
    ],
    "professional_summary_rewrite_agent": [
        {"provider": "groq", "model": "openai/gpt-oss-120b"},
        {"provider": "cerebras", "model": "gpt-oss-120b"},
        {"provider":"sambanova", "model": "gpt-oss-120b"},
        {"provider": "gemini", "model": "models/gemini-2.5-pro"}
    ],
    "resume-history-jobs-extractor": [
        {"provider": "groq", "model": "openai/gpt-oss-20b"},

        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
        {"provider": "groq", "model": "llama-3.1-8b-instant"},
        {"provider": "cerebras", "model": "llama3.1-8b"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ],
    "resume-summary-extractor": [
        {"provider": "groq", "model": "openai/gpt-oss-20b"},

        #{"provider": "groq", "model": "llama-3.1-8b-instant"},
        {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
        {"provider":"sambanova", "model": "gpt-oss-120b"},

        #{"provider": "cerebras", "model": "llama3.1-8b"},
        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ],
    "job-description-extractor-agent": [
        {"provider":"sambanova", "model": "gpt-oss-120b"},
        {"provider": "groq", "model": "openai/gpt-oss-20b"},
        {"provider": "groq", "model": "llama-3.1-8b-instant"},
        {"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ]
}

# --- Core LLM Caller Function ---

def call_llm_provider(provider_name, workload_difficulty, system_prompt, user_prompt, custom_settings=None):
    logger.info(f"Calling LLM provider: {provider_name} for workload: {workload_difficulty}")
    """
    Calls an OpenAI-compatible LLM provider and returns the results based on workload difficulty.

    Args:
        provider_name (str): The name of the LLM provider ('groq' or 'cerebras').
        workload_difficulty (str): The difficulty of the workload, used to select an LLM model.
        system_prompt (str): The system prompt for the LLM.
        user_prompt (str): The user prompt for the LLM.
        custom_settings (dict, optional): Custom settings for the API call. Defaults to None.

    Returns:
        str: The response from the LLM.
    """

    if provider_name.lower() not in provider_urls:
        logger.error(f"Unknown provider: {provider_name}")
        raise ValueError(f"Error: Unknown provider '{provider_name}'.")

    # Add check for workload difficulty existence
    if workload_difficulty not in model_mapping:
        logger.error(f"Unsupported workload difficulty: {workload_difficulty}")
        raise ValueError(f"Error: Unsupported workload difficulty '{workload_difficulty}'.")

    # Retrieve the list of provider-model pairs for the given workload_difficulty
    available_models = model_mapping.get(workload_difficulty)
    
    selected_model = None
    # Prioritize the provider that matches the provider_name argument
    selected_model = next((m for m in available_models if m["provider"].lower() == provider_name.lower()), None)

    if selected_model is None:
        selected_model = available_models[0]
        logger.warning(f"No model found for provider '{provider_name}'. Using first available: {selected_model}")

    # If no matching provider is found, select the first available provider and model
    selected_model_name = selected_model["model"]
    selected_provider_name_for_url = selected_model["provider"]
    logger.info(f"Selected model: {selected_model_name} from provider: {selected_provider_name_for_url}")

    base_url = provider_urls[selected_provider_name_for_url.lower()]
    api_key = os.environ.get(f"{selected_provider_name_for_url.upper()}_API_KEY")
    if not api_key:
        logger.error(f"API key for {selected_provider_name_for_url.upper()} not found in environment variables.")
        raise ValueError(f"Error: API key for {selected_provider_name_for_url.upper()} not found.")

    client = OpenAI(base_url=base_url, api_key=api_key)

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    # Merge custom_settings with default parameters
    params = {
        "model": selected_model_name,
        "messages": messages,
        "stream": False,
        "temperature": 1,
        "max_tokens": 60000,
    }
    if custom_settings:
        params.update(custom_settings)

    try:
        response = client.chat.completions.create(**params)
        logger.info("API call successful.")
        return response.choices[0].message.content
    except APIError as e:
        logger.error(f"An API error occurred: {e}")
        raise
    except Exception as e:
        logger.error(f"An unexpected error occurred during API call: {e}")
        raise

# --- Real LLM Functions  ---

def analyze_job_description(job_description: str) -> str:
    logger.info("LLM Service: Analyzing job description...")
    prompt = f"<Job Description>\n{job_description}\n</Job Description>"
    return call_llm_provider(
        provider_name='gemini',
        workload_difficulty='job-description-extractor-agent',
        system_prompt=system_prompts.job_summarizer_agent_system_prompt,
        user_prompt=prompt
    )

def rewrite_job_history(job_history_background: str, summarized_job_description: str) -> str:
    logger.info("LLM Service: Rewriting job history...")
    # Add the background to the system prompt as per the notebook's logic
    custom_settings = {"reasoning_effort": "high"}
    prompt = f"<Job Description>\n\n"+summarized_job_description+f"\n\n</Job Description>" + f"\n\n<background>\n{job_history_background}\n</background>"
    return call_llm_provider(
        provider_name='gemini',
        workload_difficulty='resume-rewrite-agent',
        system_prompt=system_prompts.resume_rewriter_agent_system_prompt,
        user_prompt=prompt,
        custom_settings = custom_settings
    )

def generate_professional_summary(updated_resume: str, summarized_job_description: str) -> str:
    logger.info("LLM Service: Generating new professional summary...")
    user_prompt = f"<Job Description>\n{summarized_job_description}\n</Job Description>\n\n<Resume>\n{updated_resume}\n</Resume>"
    return call_llm_provider(
        provider_name='gemini',
        workload_difficulty='professional_summary_rewrite_agent',
        system_prompt=system_prompts.professional_summary_rewriter_agent_system_prompt,
        user_prompt=user_prompt
    )


def parse_resume_to_json(resume_text: str) -> List[dict]:
    logger.info("LLM Service: Parsing resume text to JSON...")
    try:
        response_str = call_llm_provider(
            provider_name='cerebras',
            workload_difficulty='resume-history-jobs-extractor',
            system_prompt=system_prompts.resume_history_company_extractor_agent_system_prompt,
            user_prompt=resume_text
        )
        # The LLM returns a string, we need to parse it into a Python list
        return json.loads(response_str)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode LLM response into JSON: {e}")
        raise ValueError("The AI failed to return valid JSON. Please try again.")
    except Exception as e:
        logger.error(f"An error occurred during resume parsing: {e}")
        raise