"""
Camada de acesso ao banco — conexão direta de Postgres via SQLAlchemy/psycopg2.

Decisão de arquitetura (mantida da tentativa anterior, ver claude/arquitetura-plataforma.md no projeto):
NÃO usar supabase-py / PostgREST para dados. Isso deixa o código agnóstico de provedor — funciona sem
nenhuma mudança com Supabase, Neon, RDS ou qualquer Postgres gerenciado, bastando trocar DATABASE_URL.
A autenticação (login/senha) é a única parte específica do Supabase — ver app/lib/auth.py.

CORREÇÃO DE PERFORMANCE (05/08/2026): a versão anterior usava NullPool ("sem pool — abre uma conexão
física nova a cada query, fecha depois"). Isso foi escolhido por cautela, mas na prática deixa CADA
consulta pagando o custo inteiro de handshake TCP+TLS+autenticação Postgres — no Streamlit Cloud, com o
processo do app ficando de pé entre interações, isso deixa tudo visivelmente lento. Trocado para um pool
pequeno (QueuePool) que reaproveita conexões entre reruns do Streamlit, e a criação do engine agora usa
st.cache_resource quando disponível (mais seguro contra condição de corrida entre sessões simultâneas do
que a variável global simples de antes).

CORREÇÃO "IDLE IN TRANSACTION" (06/08/2026): cada página do Streamlit chama `get_session()` uma vez e usa
essa mesma sessão o script inteiro, mas nunca chama `session.close()` no fim — o script simplesmente
termina, e o objeto Session vira lixo. O problema é que uma ORM Session do SQLAlchemy tem referências
internas cíclicas, então o coletor de lixo do Python não fecha a conexão/transação na hora — ela pode
ficar "pendurada" (estado "idle in transaction" no Postgres) por muito tempo, até o GC cíclico rodar. Isso
já travou duas vezes um `alter table` no SQL Editor do Supabase (a conexão pendurada segurava um lock na
tabela e o painel desistia por timeout). Corrigido aqui: o engine usa isolation_level="AUTOCOMMIT" — cada
comando já vira sua própria transação, que fecha sozinha assim que termina, então não existe mais como uma
conexão ficar "idle in transaction" nem por engano. Não muda nada no comportamento do app: todo ponto do
código que grava dado já chamava `session.commit()` explicitamente antes disso (e não há nenhum
`session.rollback()` no projeto que dependesse do modo anterior).
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_engine = None
_SessionLocal = None


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        # Streamlit Community Cloud injeta st.secrets; ao rodar scripts fora do Streamlit, usa env var.
        try:
            import streamlit as st
            url = st.secrets.get("DATABASE_URL")
        except Exception:
            pass
    if not url:
        raise RuntimeError(
            "DATABASE_URL não configurado. Defina a variável de ambiente ou o secret do Streamlit "
            "com a connection string direta de Postgres (Project Settings → Database → Connection "
            "string → URI, não a API REST). Recomendado: use a connection string do Session Pooler "
            "(Project Settings → Database → Connection Pooling), não a conexão direta — a direta só "
            "responde por IPv6 e a maioria das hospedagens (incluindo Streamlit Community Cloud) não "
            "tem saída IPv6."
        )
    return url


def _create_engine():
    return create_engine(
        get_database_url(),
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,   # recicla conexões a cada 30min — evita conexão "morta" pelo pooler do Supabase
        pool_pre_ping=True,  # testa a conexão antes de usar; reabre sozinho se caiu
        isolation_level="AUTOCOMMIT",  # nunca deixa uma conexão "idle in transaction" pendurada (ver docstring)
    )


def get_engine():
    global _engine
    try:
        import streamlit as st
        # st.cache_resource garante uma única instância por processo mesmo com várias sessões/threads
        # do Streamlit rodando ao mesmo tempo — mais seguro que a variável global simples abaixo.
        cached = st.cache_resource(_create_engine, show_spinner=False)
        return cached()
    except Exception:
        pass
    if _engine is None:
        _engine = _create_engine()
    return _engine


def get_session():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()
