"""
Reading Chunks from Supabase without embeddings.
"""

from embed.config import get_settings

from supabase import Client, create_client

CHUNKS_TABLE = "portal_chunks"

def get_client() -> Client:
    """Create the Supabase client from settings."""
    settings = get_settings()

    return create_client(settings.supabase_url.get_secret_value(), settings.supabase_key.get_secret_value())

def fetch_null_embeds(client: Client, domain: str):
    pass