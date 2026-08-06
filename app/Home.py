import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from lib.status_apuracao import classificar_status
from sqlalchemy import text

st.set_page_config(page_title="Apuração ICMS", layout="wide")
require_login()
logout_button()

st.title("Apuração ICMS")
st.caption("Sodine Soc. Dist. do NE Ltda — plataforma reconstruída em 05/08/2026")

session = get_session()
resumo = session.execute(text("""
    select e.razao_social, c.ano, c.mes, c.status,
           (select count(*) from notas_fiscais_itens n where n.competencia_id = c.id) as n_itens,
           (select count(*) from inconsistencias i where i.competencia_id = c.id and i.status = 'pendente') as n_pendentes
    from competencias c
    join empresas e on e.id = c.empresa_id
    order by c.ano desc, c.mes desc
""")).mappings().all()

if not resumo:
    st.info("Nenhuma competência importada ainda. Use a página **Importar Relatórios** no menu à esquerda.")
else:
    st.subheader("Competências")
    linhas = []
    for r in resumo:
        r = dict(r)
        r["situação"] = classificar_status(r["status"], r["n_pendentes"])["texto"]
        linhas.append(r)
    st.dataframe(
        linhas, use_container_width=True,
        column_order=["razao_social", "ano", "mes", "status", "situação", "n_itens", "n_pendentes"],
    )

st.markdown("---")
st.markdown(
    "Use o menu à esquerda: **Importar Relatórios** (Entrada/Saída), **ICMS Normal** (planilhas de "
    "Entrada/Saída editáveis, NCMs tributados, ajustes da apuração, a apuração final espelhando a Rotina "
    "1025, e a revisão de inconsistências — tudo em abas dentro dessa página), **Empresas** (cadastro do "
    "grupo) e **CFOP** (tabela de referência e exceções)."
)
