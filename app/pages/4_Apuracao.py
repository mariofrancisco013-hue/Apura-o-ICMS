import streamlit as st
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.auth import require_login, logout_button
from lib.db import get_session
from lib.calculo_icms_normal import (
    calcular_apuracao_icms_normal, salvar_apuracao,
    comparar_com_checkpoint_1024, comparar_com_checkpoint_1025,
)
from lib.validacoes import gerar_inconsistencias_ncm, gerar_inconsistencias_transferencia
from sqlalchemy import text

st.set_page_config(page_title="Apuração", layout="wide")
require_login()
logout_button()
st.title("Apuração ICMS Normal")

session = get_session()
competencias = session.execute(text("""
    select c.id, e.razao_social, c.ano, c.mes, c.status
    from competencias c join empresas e on e.id = c.empresa_id
    where c.modulo = 'icms_normal'
    order by c.ano desc, c.mes desc
""")).mappings().all()

if not competencias:
    st.info("Nenhuma competência importada. Vá em **Importar Relatórios** primeiro.")
    st.stop()

comp = st.selectbox(
    "Competência", competencias,
    format_func=lambda c: f"{c['razao_social']} — {c['mes']:02d}/{c['ano']} ({c['status']})",
)
cid = comp["id"]

# --- lançamentos manuais ---
st.subheader("Lançamentos manuais (DIFAL, CIAP, DAE Antecipado, ajustes de CFOP não importados)")
lancamentos = session.execute(text("""
    select id, tipo, cfop_relacionado, descricao, valor from lancamentos_manuais
    where competencia_id = :cid order by id
"""), {"cid": cid}).mappings().all()
st.dataframe(lancamentos, use_container_width=True)

with st.form("novo_lancamento", clear_on_submit=True):
    c1, c2, c3, c4 = st.columns([2, 3, 2, 2])
    tipo = c1.selectbox("Tipo", [
        "difal_debito", "ciap_credito", "dae_antecipado_credito",
        "ajuste_cfop_credito", "ajuste_cfop_debito", "outro",
    ])
    descricao = c2.text_input("Descrição (ex: 'CIAP mês 07/2026', 'DAE 202642354711255')")
    valor = c3.number_input("Valor (R$)", min_value=0.0, step=0.01, format="%.2f")
    cfop_rel = c4.number_input("CFOP relacionado (se aplicável)", min_value=0, max_value=6999, step=1)
    if st.form_submit_button("Adicionar lançamento"):
        session.execute(text("""
            insert into lancamentos_manuais (competencia_id, tipo, cfop_relacionado, descricao, valor)
            values (:cid, :tipo, :cfop, :desc, :valor)
        """), {"cid": cid, "tipo": tipo, "cfop": cfop_rel or None, "desc": descricao, "valor": valor})
        session.commit()
        st.rerun()

st.markdown("---")

# --- calcular ---
if st.button("Calcular apuração", type="primary"):
    with st.spinner("Calculando..."):
        linhas = calcular_apuracao_icms_normal(session, cid)
        salvar_apuracao(session, cid, linhas)
        n_ncm = gerar_inconsistencias_ncm(session, cid)
        n_transf = gerar_inconsistencias_transferencia(session, cid)
        session.execute(text("update competencias set status = 'calculada' where id = :cid"), {"cid": cid})
        session.commit()
    st.success(f"Apuração calculada. {n_ncm} inconsistências de NCM e {n_transf} de transferência geradas "
               f"— veja a página **Inconsistências**.")
    st.rerun()

resultado = session.execute(text("""
    select linha, descricao, valor from apuracao_linhas where competencia_id = :cid order by linha
"""), {"cid": cid}).mappings().all()

if resultado:
    st.subheader("Livro de Apuração (linhas 01-14)")
    st.dataframe(resultado, use_container_width=True)

    st.markdown("---")
    st.subheader("Checkpoint 1 — soma por CFOP vs Rotina 1024")
    st.caption("Digite os valores da Rotina 1024 (RAICMS) por CFOP para conferir contra o calculado.")
    with st.form("checkpoint_1024", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        cfop_ref = c1.number_input("CFOP", min_value=1000, max_value=6999, step=1)
        base_ref = c2.number_input("Base de Cálculo (Rotina 1024)", step=0.01, format="%.2f")
        icms_ref = c3.number_input("Imposto Creditado/Debitado (Rotina 1024)", step=0.01, format="%.2f")
        if st.form_submit_button("Salvar valor de referência"):
            session.execute(text("""
                insert into checkpoints_referencia (competencia_id, fonte, cfop, valor_base, valor_icms)
                values (:cid, 'rotina_1024', :cfop, :base, :icms)
            """), {"cid": cid, "cfop": cfop_ref, "base": base_ref, "icms": icms_ref})
            session.commit()
            st.rerun()

    divergencias_1024 = comparar_com_checkpoint_1024(session, cid)
    if divergencias_1024:
        st.warning(f"{len(divergencias_1024)} CFOPs com divergência acima de R$ 0,05:")
        st.dataframe(divergencias_1024, use_container_width=True)
    else:
        st.success("Sem divergências registradas contra a Rotina 1024 (para os CFOPs com valor de referência informado).")

    st.markdown("---")
    st.subheader("Checkpoint 2 — linhas 01-14 vs Rotina 1025")
    st.caption("Digite os valores da Rotina 1025 (livro completo) por linha para conferir contra o calculado.")
    with st.form("checkpoint_1025", clear_on_submit=True):
        c1, c2 = st.columns(2)
        linha_ref = c1.selectbox("Linha", ["01","02","03","04","05","06","07","08","09","11","12","13","14"])
        valor_ref = c2.number_input("Valor (Rotina 1025)", step=0.01, format="%.2f")
        if st.form_submit_button("Salvar valor de referência"):
            session.execute(text("""
                insert into checkpoints_referencia (competencia_id, fonte, linha, valor_icms)
                values (:cid, 'rotina_1025', :linha, :valor)
            """), {"cid": cid, "linha": linha_ref, "valor": valor_ref})
            session.commit()
            st.rerun()

    divergencias_1025 = comparar_com_checkpoint_1025(session, cid)
    if divergencias_1025:
        st.warning(f"{len(divergencias_1025)} linhas com divergência acima de R$ 0,05:")
        st.dataframe(divergencias_1025, use_container_width=True)
    else:
        st.success("Sem divergências registradas contra a Rotina 1025 (para as linhas com valor de referência informado).")
