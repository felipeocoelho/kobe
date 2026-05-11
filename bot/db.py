"""Cliente Supabase do Kobe.

Construído uma vez no startup e injetado no `bot_data` da Application do PTB.
A `SUPABASE_KEY` esperada é a chave secreta server-side (`sb_secret_xxx` no
nome novo, ou o legado `service_role` JWT). A publishable/anon key respeita
RLS e não tem permissão pra escrever nas tabelas do Kobe.
"""

from __future__ import annotations

from supabase import Client, create_client

from bot.config import Config


def build_client(config: Config) -> Client:
    return create_client(config.supabase_url, config.supabase_key)
