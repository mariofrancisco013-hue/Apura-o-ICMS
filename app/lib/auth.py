"""
Login via Supabase Auth. Único ponto do app que depende do Supabase especificamente — todo o resto
(app/lib/db.py e daí pra baixo) usa conexão direta de Postgres e funciona com qualquer provedor.

Todos os usuários autenticados têm o mesmo nível de acesso (decisão do usuário em 05/08/2026) — este
módulo só garante que existe uma sessão válida, não faz controle de permissão por papel/role.
"""
import streamlit as st
from supabase import create_client


def _get_client():
    url = st.secrets.get("SUPABASE_URL")
    key = st.secrets.get("SUPABASE_ANON_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL / SUPABASE_ANON_KEY não configurados nos secrets do Streamlit. "
            "Pegue esses valores em Project Settings → API no painel do Supabase."
        )
    return create_client(url, key)


def require_login():
    """Chamar no topo de cada página. Mostra tela de login se ainda não houver sessão, e para a
    execução da página (st.stop()) até o usuário logar."""
    if "supabase_session" in st.session_state:
        return st.session_state["supabase_session"]

    st.title("Apuração ICMS — Login")
    with st.form("login_form"):
        email = st.text_input("E-mail")
        senha = st.text_input("Senha", type="password")
        entrar = st.form_submit_button("Entrar")

    if entrar:
        try:
            client = _get_client()
            resp = client.auth.sign_in_with_password({"email": email, "password": senha})
            st.session_state["supabase_session"] = resp.session
            st.session_state["user_email"] = resp.user.email
            st.session_state["user_id"] = resp.user.id
            st.rerun()
        except Exception as e:
            st.error(f"Falha no login: {e}")

    st.stop()


def usuario_atual() -> dict:
    """{"id": uuid|None, "email": str|None} do usuário logado — usado para registrar quem criou uma
    exceção/revisão (excecoes_inconsistencia.criado_por, inconsistencias.revisado_por)."""
    return {
        "id": st.session_state.get("user_id"),
        "email": st.session_state.get("user_email"),
    }


def logout_button():
    if st.sidebar.button("Sair"):
        st.session_state.pop("supabase_session", None)
        st.session_state.pop("user_email", None)
        st.rerun()
    if "user_email" in st.session_state:
        st.sidebar.caption(f"Logado como {st.session_state['user_email']}")
