import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button
from lib.db import get_session
from sqlalchemy import text

st.set_page_config(page_title="Importar Relatórios", layout="wide")
require_login()
logout_button()
st.title("Importar Relatórios")

session = get_session()
empresas = session.execute(text("select id, razao_social, cnpj from empresas order by razao_social")).mappings().all()
if not empresas:
    st.warning("Nenhuma empresa cadastrada ainda. Cadastre em **Empresas** antes de importar.")
    st.stop()

col1, col2, col3 = st.columns(3)
empresa = col1.selectbox("Empresa", empresas, format_func=lambda e: f"{e['razao_social']} ({e['cnpj']})")
ano = col2.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1)
mes = col3.number_input("Mês", min_value=1, max_value=12, value=7, step=1)

st.markdown("---")
arq_entrada = st.file_uploader("Relatório de Entrada (.xls)", type=["xls"])
arq_saida = st.file_uploader("Relatório de Saída (.xls)", type=["xls"])

comp = session.execute(text("""
    select id from competencias where empresa_id=:eid and ano=:ano and mes=:mes and modulo='icms_normal'
"""), {"eid": empresa["id"], "ano": ano, "mes": mes}).fetchone()
ja_importado = False
if comp:
    n = session.execute(
        text("select count(*) from notas_fiscais_itens where competencia_id=:cid"), {"cid": comp[0]}
    ).scalar()
    ja_importado = n > 0
    if ja_importado:
        st.warning(f"Esta competência já tem {n} itens importados. Marque a opção abaixo para substituir "
                   f"(reimportação de relatório corrigido) — sem isso, a importação é bloqueada para "
                   f"evitar duplicar notas fiscais.")

substituir = st.checkbox("Substituir importação existente desta competência", value=False,
                          disabled=not ja_importado)

if st.button("Importar", type="primary", disabled=not (arq_entrada or arq_saida)):
    from lib.importacao import importar
    with st.spinner("Importando..."):
        try:
            resultado = importar(session, empresa["cnpj"], ano, mes, arq_entrada, arq_saida, substituir)
            st.success(resultado)
            st.rerun()
        except ValueError as e:
            st.error(str(e))
