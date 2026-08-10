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

Barra lateral (pedido do usuário em 10/08/2026, mesma referência visual): fundo azul-marinho igual o
login, logo do grupo via `st.logo()` (API nativa do Streamlit pra isso — aparece tanto com a barra aberta
quanto com o menuzinho de expandir quando colapsada) e o link da página atual destacado (usa o atributo
`aria-current="page"` que o próprio Streamlit já marca no menu automático de páginas — não precisei
inventar lógica pra saber qual página tá ativa). Limitação: o menu de páginas automático (baseado nos
arquivos de app/pages/) é renderizado pelo framework ANTES de qualquer código nosso rodar, então não dá
pra colocar um texto tipo "Apuração ICMS" ENTRE a logo e a lista de páginas (como no app de referência) —
só dá pra colocar coisa depois da lista (onde hoje mostra o e-mail/Sair). Pra ter esse controle total
(e ícone por página) precisaria trocar pro `st.navigation()`, API mais nova que substitui o menu automático
por um montado à mão — é uma mudança maior, mexe em todas as páginas; se quiser isso, é só pedir.
"""
from pathlib import Path

import streamlit as st
from supabase import create_client

_LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "logos_grupo.png"

_CSS_SIDEBAR = """
<style>
[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #1E2D59 0%, #1E3D68 100%);
}
[data-testid="stSidebarHeader"] { padding-bottom: 0; }
[data-testid="stSidebarLogo"] { padding: 0.5rem 0 0.5rem 0.25rem; }
[data-testid="stSidebarCollapseButton"] button span span { color: #FFFFFF !important; }
/* lista de páginas (menu automático) */
[data-testid="stSidebarNavLink"] span, [data-testid="stSidebarNavLink"] p {
    color: #C7D6EF !important; font-size: 0.92rem;
}
[data-testid="stSidebarNavLink"] {
    border-radius: 0.5rem; margin: 0.1rem 0.6rem; padding-left: 0.6rem !important;
}
[data-testid="stSidebarNavLink"]:hover { background-color: rgba(255, 255, 255, 0.08); }
/* página atual (o próprio Streamlit marca com aria-current="page") */
[data-testid="stSidebarNavLink"][aria-current="page"] {
    background-color: rgba(59, 130, 246, 0.25);
}
[data-testid="stSidebarNavLink"][aria-current="page"] span,
[data-testid="stSidebarNavLink"][aria-current="page"] p {
    color: #FFFFFF !important; font-weight: 600;
}
[data-testid="stSidebarNavSeparator"] { background-color: rgba(255, 255, 255, 0.15); }
/* área abaixo da lista de páginas (usuário logado + botão Sair, ver logout_button()) */
[data-testid="stSidebarUserContent"] [data-testid="stCaptionContainer"] p,
[data-testid="stSidebarUserContent"] [data-testid="stMarkdownContainer"] p { color: #C7D6EF; }
[data-testid="stSidebarUserContent"] a { color: #7FB2F0; }
.sidebar-user { display: flex; align-items: center; gap: 0.5rem; margin: 0.4rem 0 0.9rem 0; }
.sidebar-user-avatar {
    width: 2rem; height: 2rem; border-radius: 50%; background-color: #2DD4BF; color: #0B1E3D;
    font-weight: 700; font-size: 0.9rem; display: flex; align-items: center; justify-content: center;
    flex-shrink: 0;
}
.sidebar-user-email {
    color: #E4ECF7; font-size: 0.82rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
[data-testid="stSidebarUserContent"] button {
    background-color: transparent !important; border-color: rgba(255, 255, 255, 0.35) !important;
    color: #FFFFFF !important;
}
[data-testid="stSidebarUserContent"] button:hover { border-color: #FFFFFF !important; }
</style>
"""

_CSS_LOGIN = """
<style>
#MainMenu, header[data-testid="stHeader"], footer {visibility: hidden;}
/* sem menu lateral na tela de login — só aparece depois de autenticado */
[data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
[data-testid="stAppViewContainer"] {
    background: linear-gradient(160deg, #1E2D59 0%, #1E3D68 100%);
}
/* centralizado de verdade: bloco comum (não-flex) com largura travada e margin:auto — sem o truque de
   st.columns() (deixava puxado pra direita) e sem display:flex no container todo (isso bagunçava a
   largura dos elementos de dentro, tipo o botão "Entrar", que ficava fora do centro mesmo com a caixa
   toda centralizada). Seletor por data-testid, não por classe ".main"/".block-container" (mudam de nome
   entre versões do Streamlit — nessa virou "stMainBlockContainer"). */
[data-testid="stMainBlockContainer"] {
    max-width: 460px; margin: 8vh auto 0 auto; padding-left: 1rem; padding-right: 1rem;
}
[data-testid="stImage"] img { margin: 0 auto; display: block; }
/* st.error()/st.warning() (se o login falhar) mantêm as cores padrão do próprio alerta do Streamlit —
   só o título/subtítulo/label dos campos (estilizados explicitamente abaixo) ficam brancos. */
.login-titulo {
    color: #FFFFFF; font-size: 2rem; font-weight: 700; margin: 0.4rem 0 0.15rem 0; text-align: center;
}
.login-subtitulo {
    color: #B8CCE8; font-size: 0.95rem; margin-bottom: 1.6rem; text-align: center;
}
[data-testid="stForm"] { border: none; padding: 0; }
[data-testid="stTextInput"] input {
    background-color: #F9FBFC; color: #1F2937;
}
[data-testid="stTextInput"] label { color: #E4ECF7 !important; }
/* o botão de mostrar/ocultar senha (ícone de olho) fica dentro do mesmo campo branco — sem isso ele
   herda o fundo escuro da página. */
[data-testid="stTextInput"] button {
    background-color: #F9FBFC !important; border-color: #F9FBFC !important;
}
[data-testid="stTextInput"] button svg { fill: #1F2937 !important; }
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

    # sem st.columns() aqui — cada coluna vira um "stColumn" com largura própria por dentro do bloco já
    # centralizado (CSS acima), e isso empurrava o formulário pra direita de novo. Escreve direto no
    # bloco principal, que já está centralizado e com a largura travada em 460px.
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
    st.markdown(_CSS_SIDEBAR, unsafe_allow_html=True)
    if _LOGO_PATH.exists():
        st.logo(str(_LOGO_PATH), size="large")

    email = st.session_state.get("user_email")
    if email:
        inicial = email[0].upper()
        st.sidebar.markdown(
            f'<div class="sidebar-user">'
            f'<div class="sidebar-user-avatar">{inicial}</div>'
            f'<div class="sidebar-user-email">{email}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if st.sidebar.button("Sair", use_container_width=True):
        st.session_state.pop("supabase_session", None)
        st.session_state.pop("user_email", None)
        st.rerun()
