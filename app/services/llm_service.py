# app/services/llm_service.py

import os
import logging
import json
from openai import OpenAI, APIError
from dotenv import load_dotenv
from app import system_prompts
from typing import List
from app.logging_config import get_logger, bind_logger, configure_logging
from app.utils.text_cleaning import normalize_to_ascii

configure_logging()

# Load environment variables from .env file
load_dotenv()

logger = get_logger(__name__)
# Bind a module-level LoggerAdapter so logs from this module include the agent name
log = bind_logger(logger, {"agent_name": "llm_service"})

# --- Provider and Model Configuration ---
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
    "resume-professional-summary-extractor": [
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
        #{"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
        {"provider":"sambanova", "model": "gpt-oss-120b"},

        #{"provider": "cerebras", "model": "llama3.1-8b"},
        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ],
    "job-description-extractor-agent": [
        {"provider":"sambanova", "model": "gpt-oss-120b"},
        {"provider": "groq", "model": "openai/gpt-oss-20b"},
       # {"provider": "groq", "model": "llama-3.1-8b-instant"},
        #{"provider": "groq", "model": "meta-llama/llama-4-scout-17b-16e-instruct"},
        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ]

    ,"job-qualifications-extractor-agent": [
        {"provider": "groq", "model": "openai/gpt-oss-20b"},
        {"provider": "cerebras", "model": "llama-4-scout-17b-16e-instruct"},
        {"provider": "gemini", "model": "models/gemini-flash-latest"}
    ]
}

# --- Core LLM Caller Function ---

def call_llm_provider(provider_name, workload_difficulty, system_prompt, user_prompt, custom_settings=None):
    log = bind_logger(logger, {"agent_name": "call llm provider"})

    log.info("Calling LLM provider", extra={"provider": provider_name, "workload": workload_difficulty})
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
        log.error(f"Unknown provider: {provider_name}")
        raise ValueError(f"Error: Unknown provider '{provider_name}'.")

    # Add check for workload difficulty existence
    if workload_difficulty not in model_mapping:
        log.error(f"Unsupported workload difficulty: {workload_difficulty}")
        raise ValueError(f"Error: Unsupported workload difficulty '{workload_difficulty}'.")

    # Retrieve the list of provider-model pairs for the given workload_difficulty
    available_models = model_mapping.get(workload_difficulty)
    # Sanity check for static type-checkers: ensure we actually got a list
    if not available_models:
        log.error(f"No models configured for workload_difficulty: {workload_difficulty}")
        raise ValueError(f"No models available for workload_difficulty '{workload_difficulty}'")
    
    selected_model = None
    # Prioritize the provider that matches the provider_name argument
    selected_model = next((m for m in available_models if m["provider"].lower() == provider_name.lower()), None)

    if selected_model is None:
        selected_model = available_models[0]
        log.warning(f"No model found for provider '{provider_name}'. Using first available: {selected_model}")

    # If no matching provider is found, select the first available provider and model
    selected_model_name = selected_model["model"]
    selected_provider_name_for_url = selected_model["provider"]
    log.info(f"Selected model: {selected_model_name} from provider: {selected_provider_name_for_url}")

    base_url = provider_urls[selected_provider_name_for_url.lower()]
    api_key = os.environ.get(f"{selected_provider_name_for_url.upper()}_API_KEY")
    if not api_key:
        log.error(f"API key for {selected_provider_name_for_url.upper()} not found in environment variables.")
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
        log.info("API call successful", extra={"model": selected_model_name})

        raw_text = response.choices[0].message.content
        # Sanitize to ASCII and log any replacements
        found, cleaned_text, replacements = normalize_to_ascii(raw_text)
        if found:
            log.info("Non-ASCII characters found and sanitized", extra={
                "model": selected_model_name,
                "replacements": replacements
            })
        return cleaned_text
    except APIError as e:
        log.exception("An API error occurred during LLM call in call_llm", exc_info=True, extra={"model": selected_model_name})
        raise
    except Exception as e:
        log.exception("An unexpected error occurred during API call in call_llm", exc_info=True, extra={"model": selected_model_name})
        raise

# --- Real LLM Functions  ---

def analyze_job_description(job_description: str) -> str:
    log = bind_logger(logger, {"agent_name": "analyze_job_description"})

    log.info("LLM Service: Analyzing job description")
    prompt = f"<Job Description>\n{job_description}\n</Job Description>"
    return call_llm_provider(
        provider_name='groq',
        workload_difficulty='job-description-extractor-agent',
        system_prompt=system_prompts.job_summarizer_agent_system_prompt,
        user_prompt=prompt
    )

def rewrite_job_history(job_history_background: str, summarized_job_description: str, current_resume: str) -> str:
    log = bind_logger(logger, {"agent_name": "rewrite_job_history"})

    log.info("LLM Service: Rewriting job history")
    # Add the background to the system prompt as per the notebook's logic
    custom_settings = {"reasoning_effort": "high"}
    prompt = (f"<JobDescription>\n\n" +
              summarized_job_description +
              f"\n\n</JobDescription>" +
              f"\n\n<background>\n" +
              job_history_background +
              "\n</background>" +
              "\n\n<current_resume>\n" +
                current_resume +
              "\n</current_resume>"
    )
    return call_llm_provider(
        provider_name='gemini',
        workload_difficulty='resume-rewrite-agent',
        system_prompt=system_prompts.resume_rewriter_agent_system_prompt,
        user_prompt=prompt,
        custom_settings = custom_settings
    )

def generate_professional_summary(updated_resume: str, summarized_job_description: str) -> str:
    log = bind_logger(logger, {"agent_name": "generate_professional_summary"})

    log.info("LLM Service: Generating new professional summary")
    custom_settings = {"reasoning_effort": "high"}
    user_prompt = f"<JobDescription>\n{summarized_job_description}\n</JobDescription>\n\n<Resume>\n{updated_resume}\n</Resume>"
    return call_llm_provider(
        provider_name='gemini',
        workload_difficulty='professional_summary_rewrite_agent',
        system_prompt=system_prompts.professional_summary_rewriter_agent_system_prompt,
        user_prompt=user_prompt,
        custom_settings=custom_settings
    )

def score_resume(resume: str, qualifications: str) -> str:
    """
    Analyze a resume against a list of qualifications and return a csv textual comparison.

    This function builds a structured prompt, calls the configured `resume-match-agent`
    via `call_llm_provider`, and returns the agent's text output. Logging is intentionally
    concise to avoid recording PII; only short previews of inputs are logged.
    """
    log = bind_logger(logger, {"agent_name": "score_resume"})

    try:
        log.info("LLM Service: Checking resume against qualifications")
        # Log short previews (first 200 chars) to help debugging without leaking full PII
        preview_len = 100
        qualifications_preview = (qualifications[:preview_len].replace("\n", " ") + ("..." if len(qualifications) > preview_len else "")) if qualifications else ""
        resume_preview = (resume[:preview_len].replace("\n", " ") + ("..." if len(resume) > preview_len else "")) if resume else ""
        log.debug("Qualifications preview", extra={"preview": qualifications_preview})
        log.debug("Resume preview", extra={"preview": resume_preview})

        # Build the user prompt. The agent is expected to return a comprehensive analysis,
        # including strengths, gaps, suggested improvements, keyword matches, and sample bullets.
        user_prompt = (
            f"<Resume>\n{resume}\n</Resume>\n\n"
            f"<Qualifications>\n{qualifications}\n</Qualifications>"
        )

        # Call the generic provider wrapper
        score = call_llm_provider(
            provider_name='groq',
            workload_difficulty='resume-match-agent',
            system_prompt=system_prompts.resume_score_agent_system_prompt,
            user_prompt=user_prompt,
            custom_settings={"temperature": 0.2, "max_tokens": 12000}
        )

        log.info("Received the score from LLM provider")
        return score

    except Exception:
        # Do not reference an undefined user_id here; this is a generic LLM service method.
        log.exception("Error during resume check process", exc_info=True)
        # Re-raise so callers can handle the failure; caller may want to mark status elsewhere.
        raise

def check_resume(resume: str, job_post: str) -> str:
    """
    Analyze a resume against a job description and return a detailed textual comparison.

    This function builds a structured prompt, calls the configured `resume-match-agent`
    via `call_llm_provider`, and returns the agent's text output. Logging is intentionally
    concise to avoid recording PII; only short previews of inputs are logged.
    """
    log = bind_logger(logger, {"agent_name": "check_resume"})

    try:
        log.info("LLM Service: Checking resume against job description")
        # Log short previews (first 200 chars) to help debugging without leaking full PII
        #preview_len = 100
        #job_preview = (job_post[:preview_len].replace("\n", " ") + ("..." if len(job_post) > preview_len else "")) if job_post else ""
        #resume_preview = (resume[:preview_len].replace("\n", " ") + ("..." if len(resume) > preview_len else "")) if resume else ""
        #log.debug("Job preview", extra={"preview": job_preview})
        #log.debug("Resume preview", extra={"preview": resume_preview})

        # Build the user prompt. The agent is expected to return a comprehensive analysis,
        # including strengths, gaps, suggested improvements, keyword matches, and sample bullets.
        user_prompt = (
            f"<Resume>\n{resume}\n</Resume>\n\n"
            f"<Jobpost>\n{job_post}\n</Jobpost>"
        )

        # Call the generic provider wrapper
        analysis = call_llm_provider(
            provider_name='groq',
            workload_difficulty='resume-match-agent',
            system_prompt=system_prompts.resume_match_analyzer_agent_system_prompt,
            user_prompt=user_prompt,
            custom_settings={"temperature": 0.2, "max_tokens": 12000}
        )

        log.info("Received analysis from LLM provider")
        return analysis

    except Exception:
        # Do not reference an undefined user_id here; this is a generic LLM service method.
        log.exception("Error during resume check process", exc_info=True)
        # Re-raise so callers can handle the failure; caller may want to mark status elsewhere.
        raise

def parse_resume_to_json(resume_text: str) -> List[dict]:
    log = bind_logger(logger, {"agent_name": "parse_resume_to_json"})

    log.info("LLM Service: Parsing resume text to JSON...")
    try:
        response_str = call_llm_provider(
            provider_name='groq',
            workload_difficulty='resume-history-jobs-extractor',
            system_prompt=system_prompts.resume_history_company_extractor_agent_system_prompt,
            user_prompt=resume_text
        )
        # The LLM returns a string, we need to parse it into a Python list
        return json.loads(response_str)
    except json.JSONDecodeError as e:
        log.error(f"Failed to decode LLM response into JSON: {e}")
        raise ValueError("The AI failed to return valid JSON. Please try again.")
    except Exception as e:
        log.error(f"An error occurred during resume parsing: {e}")
        raise

def extract_professional_summary(resume_text: str) -> str:
    log = bind_logger(logger, {"agent_name": "extract_professional_summary"})

    log.info("LLM Service: Extracting professional summary from resume...")
    try:
        response_str = call_llm_provider(
            provider_name='groq',
            workload_difficulty='resume-professional-summary-extractor',
            system_prompt=system_prompts.resume_professional_summary_extractor_agent_system_prompt,
            user_prompt=resume_text
        )
        log.info("Successfully extracted professional summary")
        return response_str

    except Exception as e:
        log.error(f"An error occurred during professional summary extraction: {e}")
        raise

def extract_job_qualifications(summarized_job_description: str) -> str:
    """
    Extract a list of qualifications (with integer weights) from a summarized job description.
    Returns a csv in string: Qualification, Weight
    """
    log = bind_logger(logger, {"agent_name": "extract_job_qualifications"})

    log.info("LLM Service: Extracting job qualifications from summarized job description")
    try:
        response_str = call_llm_provider(
            provider_name='groq',
            workload_difficulty='job-qualifications-extractor-agent',
            system_prompt=system_prompts.job_qualifications_extractor_agent_system_prompt,
            user_prompt=f'<JobDescription>\n{summarized_job_description}\n</JobDescription>',
            custom_settings={"temperature": 0.0}
        )
        return response_str
    
    except Exception as e:
        log.error(f"An error occurred during qualifications extraction: {e}")
        raise


