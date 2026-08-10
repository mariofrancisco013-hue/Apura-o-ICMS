import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from lib.formatacao import rotulo_empresa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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

st.markdown("---")
st.subheader("Excluir empresa")
st.caption(
    "Só é possível excluir uma empresa que ainda não tem nenhuma competência de apuração vinculada — "
    "histórico fiscal é preservado por segurança. Se a exclusão travar, é porque essa empresa já tem "
    "competências (Importar Relatórios, ICMS Normal, ICMS PE etc.) — apague-as antes, se realmente for o "
    "caso, ou simplesmente não exclua uma empresa que já tem apuração feita."
)
if not empresas:
    st.info("Nenhuma empresa cadastrada ainda.")
else:
    empresa_excluir = st.selectbox(
        "Empresa a excluir", empresas, format_func=rotulo_empresa,
        key="empresa_excluir",
    )
    confirmar = st.checkbox(
        f"Confirmo que quero excluir definitivamente **{empresa_excluir['razao_social']}** "
        f"({empresa_excluir['cnpj']}).",
        key="confirmar_exclusao_empresa",
    )
    if st.button("🗑️ Excluir empresa", disabled=not confirmar, type="primary"):
        try:
            session.execute(text("delete from empresas where id = :id"), {"id": empresa_excluir["id"]})
            session.commit()
            st.success(f"Empresa {empresa_excluir['razao_social']} excluída.")
            st.rerun()
        except IntegrityError:
            session.rollback()
            st.error(
                f"Não foi possível excluir **{empresa_excluir['razao_social']}**: já existem competências "
                f"de apuração (ou outro cadastro, como CFOPs de Antecipação) vinculadas a essa empresa. "
                f"Remova esses vínculos primeiro se realmente precisar excluí-la."
            )
