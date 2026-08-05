import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from sqlalchemy import text

st.set_page_config(page_title="Empresas", layout="wide")
require_login()
logout_button()
st.title("Empresas do Grupo")
st.caption(
    "Cadastro usado para validar transferências entre empresas: duas empresas são consideradas "
    "vinculadas se, e somente se, compartilham a raiz do CNPJ (8 primeiros dígitos) — regra confirmada "
    "com o usuário em 05/08/2026."
)

session = get_session()
empresas = session.execute(text("""
    select id, filial_winthor, razao_social, cnpj, cnpj_raiz, uf, regime, is_empresa_apurada
    from empresas order by cnpj_raiz, razao_social
""")).mappings().all()

st.dataframe(empresas, use_container_width=True)

st.markdown("---")
st.subheader("Cadastrar nova empresa")
with st.form("nova_empresa"):
    c1, c2, c3 = st.columns(3)
    razao = c1.text_input("Razão Social")
    cnpj = c2.text_input("CNPJ (com máscara: 00.000.000/0000-00)")
    filial = c3.text_input("Filial Winthor (opcional)")
    c4, c5, c6 = st.columns(3)
    ie = c4.text_input("Inscrição Estadual")
    uf = c5.text_input("UF", max_chars=2)
    regime = c6.text_input("Regime")
    apurada = st.checkbox("É a empresa apurada por esta plataforma (ex: Sodine Atacado F3)")
    if st.form_submit_button("Salvar"):
        if not razao or not cnpj:
            st.error("Razão Social e CNPJ são obrigatórios.")
        else:
            session.execute(text("""
                insert into empresas (filial_winthor, razao_social, cnpj, inscricao_estadual, uf, regime,
                                       is_empresa_apurada)
                values (:filial, :razao, :cnpj, :ie, :uf, :regime, :apurada)
                on conflict (cnpj) do update set razao_social = excluded.razao_social
            """), {"filial": filial or None, "razao": razao, "cnpj": cnpj, "ie": ie or None,
                    "uf": uf or None, "regime": regime or None, "apurada": apurada})
            session.commit()
            st.success(f"Empresa {razao} salva.")
            st.rerun()
