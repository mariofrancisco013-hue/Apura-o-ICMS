"""
Login via Supabase Auth. Único ponto do app que depende do Supabase especificamente — todo o resto
(app/lib/db.py e daí pra baixo) usa conexão direta de Postgres e funciona com qualquer provedor.

Todos os usuários autenticados têm o mesmo nível de acesso (decisão do usuário em 05/08/2026) — este
módulo só garante que existe uma sessão válida, não faz controle de permissão por papel/role.

Tela de login com a mesma identidade visual do projeto "Agente de Retenções NFS-e" (pedido do usuário em
10/08/2026) — fundo azul-marinho em gradiente, logo do grupo (Sodine/Super Supply/Ultra Supply/Verde) e
botão "Entrar" em azul, cores extraídas por pixel do print da tela original pra ficar o mais parecido
possível. As cores do arquivo .streamlit/config.toml (tema claro, usado no resto do app já logado) são as
mesmas do outro projeto, mas essa tela de login usa um fundo escuro à parte, via CSS injetado abaixo — não
dá pra fazer isso só com o config.toml porque ele não suporta gradiente nem estilizar uma tela específica.

Diferença proposital em relação ao original: os botões "Criar conta" e "Esqueci minha senha" não foram
replicados aqui porque esse app não tem esses dois fluxos implementados (só login com e-mail/senha já
cadastrados no Supabase Auth) — colocar os botões sem funcionar seria enganoso. Dá pra implementar os dois
se for útil, é só pedir.
"""
from pathlib import Path

import streamlit as st
from supabase import create_client

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logos_grupo.png"

_CSS_LOGIN = """
<style>
#MainMenu, header[data-testid="stHeader"], footer {visibility: hidden;}
/* sem menu lateral na tela de login — só aparece depois de autenticado */
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
[data-testid="stAppViewContainer"] {
    background: linear-gradient(160deg, #1E2D59 0%, #1E3D68 100%);
}
[data-testid="stAppViewContainer"] > .main .block-container {
    display: flex; flex-direction: column; justify-content: center; min-height: 92vh;
}
/* st.error()/st.warning() (se o login falhar) mantêm as cores padrão do próprio alerta do Streamlit —
   só o título/subtítulo/label dos campos (estilizados explicitamente abaixo) ficam brancos. */
.login-titulo {
    color: #FFFFFF; font-size: 2rem; font-weight: 700; margin: 0.4rem 0 0.15rem 0;
}
.login-subtitulo {
    color: #B8CCE8; font-size: 0.95rem; margin-bottom: 1.6rem;
}
[data-testid="stTextInput"] input {
    background-color: #F9FBFC; color: #1F2937;
}
[data-testid="stTextInput"] label { color: #E4ECF7 !important; }
div[data-testid="stForm"] button[kind="primaryFormSubmit"],
div[data-testid="stForm"] button[kind="primary"],
button[kind="primary"] {
    background-color: #3B82F6 !important; border-color: #3B82F6 !important; color: #FFFFFF !important;
}
div[data-testid="stForm"] button[kind="primaryFormSubmit"]:hover,
div[data-testid="stForm"] button[kind="primary"]:hover,
button[kind="primary"]:hover {
    background-color: #2563EB !important; border-color: #2563EB !important;
}
</style>
"""


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

    st.markdown(_CSS_LOGIN, unsafe_allow_html=True)

    _, col, _ = st.columns([1, 1.3, 0.15])
    with col:
        st.write("")
        st.write("")
        if _LOGO_PATH.exists():
            st.image(str(_LOGO_PATH), width=420)
        st.markdown('<div class="login-titulo">Apuração ICMS</div>', unsafe_allow_html=True)
        st.markdown('<div class="login-subtitulo">Entre com seu e-mail e senha para acessar</div>',
                     unsafe_allow_html=True)
        with st.form("login_form"):
            email = st.text_input("E-mail")
            senha = st.text_input("Senha", type="password")
            entrar = st.form_submit_button("Entrar", type="primary", use_container_width=True)

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
