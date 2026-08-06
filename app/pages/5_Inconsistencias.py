import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from sqlalchemy import text

st.set_page_config(page_title="Inconsistências", layout="wide")
require_login()
logout_button()
st.title("Inconsistências")
st.caption(
    "NCM x ST: mesmo NCM tratado de forma diferente entre Entrada e Saída. Transferência não vinculada: "
    "CFOP de transferência cujo parceiro não bate por nome com nenhuma empresa do grupo cadastrada "
    "(heurística — o relatório de origem não traz o CNPJ do parceiro, confirme manualmente). NCM "
    "tributado como ST: um NCM cadastrado como 'tributado' na aba NCMs Tributados (ICMS Normal) apareceu "
    "num item classificado como ST. NCM tributado novo: um NCM ainda não cadastrado apareceu como "
    "tributado (não-ST) — candidato a entrar na lista."
)

session = get_session()
competencias = session.execute(text("""
    select c.id, e.razao_social, c.ano, c.mes from competencias c
    join empresas e on e.id = c.empresa_id order by c.ano desc, c.mes desc
""")).mappings().all()
if not competencias:
    st.info("Nenhuma competência disponível.")
    st.stop()

comp = st.selectbox("Competência", competencias,
                     format_func=lambda c: f"{c['razao_social']} — {c['mes']:02d}/{c['ano']}")
cid = comp["id"]

TIPOS = ["ncm_st_inconsistente", "transferencia_nao_vinculada", "ncm_tributado_como_st", "ncm_tributado_novo"]
status_filtro = st.multiselect("Status", ["pendente", "revisado", "ignorado"], default=["pendente"])
tipo_filtro = st.multiselect("Tipo", TIPOS, default=TIPOS)

if not status_filtro or not tipo_filtro:
    st.stop()

itens = session.execute(text("""
    select id, tipo, ncm, cfop, descricao, status, revisado_por, revisado_em
    from inconsistencias
    where competencia_id = :cid and status = any(:status) and tipo = any(:tipo)
    order by created_at desc
"""), {"cid": cid, "status": status_filtro, "tipo": tipo_filtro}).mappings().all()

st.write(f"{len(itens)} inconsistências encontradas.")
for item in itens:
    with st.expander(f"[{item['tipo']}] {item['descricao'][:100]}..."):
        st.write(item["descricao"])
        c1, c2, c3 = st.columns(3)
        if c1.button("Marcar como revisado", key=f"rev_{item['id']}"):
            session.execute(text("""
                update inconsistencias set status='revisado', revisado_em=now() where id=:id
            """), {"id": item["id"]})
            session.commit()
            st.rerun()
        if c2.button("Ignorar", key=f"ign_{item['id']}"):
            session.execute(text("""
                update inconsistencias set status='ignorado', revisado_em=now() where id=:id
            """), {"id": item["id"]})
            session.commit()
            st.rerun()
