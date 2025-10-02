import os
from supabase import create_client, Client
from dotenv import load_dotenv
from app.services import llm_service

load_dotenv()

# We need to initialize another Supabase client here.
# IMPORTANT: For backend services, we use the SERVICE_ROLE_KEY
# which has full access to the database, bypassing any Row Level Security.
# Be very careful with this key.
supabase_url = os.environ.get("SUPABASE_URL")
supabase_service_key = os.environ.get("SUPABASE_SERVICE_KEY") # <-- NOTICE THE DIFFERENT KEY
supabase: Client = create_client(supabase_url, supabase_service_key)

def run_tailoring_process(application_id: int, user_id: str):
    print(f"Starting tailoring process for application_id: {application_id}")
    try:
        # Step 1: Fetch all necessary data from Supabase
        print("Fetching data from Supabase...")
        app_data = supabase.table("applications").select("*").eq("id", application_id).single().execute().data
        profile_data = supabase.table("profiles").select("base_resume_text, base_summary_text").eq("id", user_id).single().execute().data
        job_histories_to_rewrite = supabase.table("job_histories").select("*").in_("id", app_data['job_history_ids']).execute().data
        
        # Step 2: Analyze the job description
        summarized_jd = llm_service.analyze_job_description(app_data['target_job_description'])

        # Step 3: Rewrite the selected job histories
        rewritten_histories = {}
        for history in job_histories_to_rewrite:
            # Ensure there is detailed background to work with
            if not history.get('detailed_background'):
                print(f"Skipping rewrite for history ID {history['id']} as it has no detailed background.")
                continue

            rewritten_text = llm_service.rewrite_job_history(
                job_history_background=history['detailed_background'],
                summarized_job_description=summarized_jd
            )
            rewritten_histories[history['id']] = rewritten_text

        # Step 4: Assemble the intermediate resume with real find-and-replace
        updated_resume = profile_data['base_resume_text']
        
        print("Replacing job history sections in the resume...")
        for history in job_histories_to_rewrite:
            history_id = history['id']
            # Check if this history was successfully rewritten in the previous step
            if history_id not in rewritten_histories:
                continue

            # Reconstruct the original block of achievement text from the stored list.
            # This relies on the parsing LLM splitting achievements by newlines.
            original_achievements = history.get('achievements_list')
            if not original_achievements:
                print(f"Skipping replacement for history ID {history_id} as it has no original achievements list.")
                continue
            
            original_achievements_block = "\n".join(original_achievements)
            
            # Get the new, AI-generated text.
            new_rewritten_text = rewritten_histories[history_id]

            # Perform the replacement, but only for the first occurrence to be safe.
            updated_resume = updated_resume.replace(original_achievements_block, new_rewritten_text, 1)

        # Step 5: Generate the new summary
        new_summary = llm_service.generate_professional_summary(updated_resume, summarized_jd)

        # Step 6: Assemble the final resume with conditional logic
        old_summary = profile_data.get('base_summary_text')

        if old_summary:
            print("Found existing summary. Replacing it.")
            final_resume = updated_resume.replace(old_summary, new_summary)
        else:
            print("No existing summary found. Prepending new summary.")
            final_resume = f"{new_summary}\n\n{updated_resume}"
        
        # Final Step: Update the application in the database
        print("Updating application in Supabase with final resume...")
        supabase.table("applications").update({
            "final_resume_text": final_resume,
            "status": "completed"
        }).eq("id", application_id).execute()

        print(f"Successfully completed tailoring for application_id: {application_id}")

    except Exception as e:
        print(f"Error during tailoring process for application_id: {application_id}. Error: {e}")
        supabase.table("applications").update({"status": "failed"}).eq("id", application_id).execute()