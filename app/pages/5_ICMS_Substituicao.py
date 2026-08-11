import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button
from lib.db import get_session
from lib.importacao import buscar_competencia, get_or_create_competencia
from lib.icms_st import (
    parse_rotina_1076, parse_sefaz_lancamentos, salvar_rotina_1076, salvar_sefaz_lancamentos,
    carregar_rotina_1076, carregar_sefaz_lancamentos, comparar_1076_sefaz,
)
from lib.formatacao import formatar_moeda, rotulo_empresa
from sqlalchemy import text
import pandas as pd

st.set_page_config(page_title="ICMS Substituição", layout="wide")
require_login()
logout_button()
st.title("ICMS Substituição Tributária Interestadual")
st.caption(
    "1º passo (pedido do usuário em 10/08/2026): comparar o que a SEFAZ está cobrando de ICMS ST "
    "Interestadual (relatório de lançamentos do portal) contra o que já está lançado no sistema via "
    "Rotina 1076 do Winthor, nota a nota — para achar NFs que a SEFAZ já está cobrando mas ainda não "
    "foram lançadas no Winthor (pendentes de entrada) e NFs com valor calculado divergente do que já está "
    "no sistema."
)


def _fmt(v):
    """formatar_moeda, mas trata NaN (NF que só existe de um lado da comparação) mostrando '—' em vez de
    'R$ nan' — formatar_moeda sozinho não pega esse caso porque float(nan) não levanta exceção."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return formatar_moeda(v)


session = get_session()

empresas = session.execute(text(
    "select id, filial_winthor, razao_social, cnpj from empresas order by filial_winthor, razao_social"
)).mappings().all()
if not empresas:
    st.warning("Nenhuma empresa cadastrada ainda. Cadastre em **Empresas** antes de continuar.")
    st.stop()

col1, col2, col3 = st.columns(3)
empresa = col1.selectbox("Empresa", empresas, format_func=rotulo_empresa)
ano = col2.number_input("Ano", min_value=2020, max_value=2100, value=2026, step=1)
mes = col3.number_input("Mês", min_value=1, max_value=12, value=7, step=1)

empresa_id = empresa["id"]

# Só CONSULTA se a competência já existe — não cria nada no banco ao só trocar Empresa/Ano/Mês (mesmo
# motivo documentado em app/lib/importacao.py::buscar_competencia). A competência só é criada de fato ao
# importar um dos dois relatórios — ver _garantir_competencia, chamada só dentro dos botões de importar.
cid = buscar_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_st")


def _garantir_competencia():
    global cid
    if cid is None:
        cid = get_or_create_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_st")
    return cid


if cid is None:
    st.caption(
        f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** — ainda não criada "
        f"(nada importado ainda nesta competência)."
    )
else:
    st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}**.")

st.markdown("---")
st.subheader("Importar relatórios")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**Rotina 1076 (Winthor)**")
    st.caption("Relatório item a item — a mesma NF aparece várias vezes, uma linha por item de entrada.")
    arq_1076 = st.file_uploader("Arquivo da Rotina 1076", type=["xls", "xlsx"], key="upload_1076_st")
    if st.button("📥 Importar Rotina 1076", key="btn_importar_1076", disabled=arq_1076 is None):
        try:
            _garantir_competencia()
            df = parse_rotina_1076(arq_1076)
            n = salvar_rotina_1076(session, cid, df)
            st.success(f"{n} item(ns) importado(s) da Rotina 1076.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

with c2:
    st.markdown("**Lançamentos da SEFAZ**")
    st.caption("CSV exportado da tela de lançamentos do portal da SEFAZ (ex: \"dadoslancamentos.csv\").")
    arq_sefaz = st.file_uploader("CSV de lançamentos da SEFAZ", type=["csv"], key="upload_sefaz_st")
    if st.button("📥 Importar lançamentos da SEFAZ", key="btn_importar_sefaz", disabled=arq_sefaz is None):
        try:
            _garantir_competencia()
            df = parse_sefaz_lancamentos(arq_sefaz)
            n = salvar_sefaz_lancamentos(session, cid, df)
            st.success(f"{n} lançamento(s) importado(s) da SEFAZ.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

if cid is None:
    st.info("Importe pelo menos um dos dois relatórios acima para começar.")
    st.stop()

st.markdown("---")
st.subheader("Comparação Rotina 1076 × SEFAZ")

sefaz_atual = carregar_sefaz_lancamentos(session, cid)
receita_opcoes = {"1031 — ICMS ST Interestadual": "1031"}
if not sefaz_atual.empty:
    for r in sorted(sefaz_atual["receita"].dropna().unique().tolist()):
        if r not in receita_opcoes.values():
            receita_opcoes[f"{r} — outra receita"] = r

receita_escolhida = st.selectbox(
    "Receita da SEFAZ a comparar", options=list(receita_opcoes.keys()), index=0,
    help=(
        "Confirmado com o usuário em 10/08/2026: só a Receita 1031 é ICMS ST Interestadual — as demais "
        "receitas do relatório da SEFAZ (ex: 1023) ficam gravadas para referência/auditoria, mas fora "
        "desta comparação por padrão."
    ),
)
receita_filtro = receita_opcoes[receita_escolhida]

comp = comparar_1076_sefaz(session, cid, receita_filtro=receita_filtro)

if comp.empty:
    st.info("Nenhum dado para comparar ainda — importe os dois relatórios acima.")
else:
    n_pendente = int((comp["status"] == "Pendente de entrada").sum())
    n_diverg = int((comp["status"] == "Divergente").sum())
    n_ok = int((comp["status"] == "OK").sum())
    n_nao_cobrado = int((comp["status"] == "Não cobrado pela SEFAZ").sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("🔴 Pendentes de entrada", n_pendente)
    m2.metric("🟠 Divergentes", n_diverg)
    m3.metric("🟢 OK", n_ok)
    m4.metric("⚪ Não cobrados pela SEFAZ", n_nao_cobrado)

    status_filtro = st.multiselect(
        "Filtrar por situação",
        options=["Pendente de entrada", "Divergente", "OK", "Não cobrado pela SEFAZ"],
        default=["Pendente de entrada", "Divergente"],
    )
    comp_exibir = comp[comp["status"].isin(status_filtro)] if status_filtro else comp

    tabela = comp_exibir.copy()
    tabela["sefaz_calculado"] = tabela["sefaz_calculado"].apply(_fmt)
    tabela["sistema_valor_icms_st"] = tabela["sistema_valor_icms_st"].apply(_fmt)
    tabela["diferenca"] = tabela["diferenca"].apply(_fmt)
    tabela = tabela.rename(columns={
        "nf_numero": "NF",
        "sefaz_calculado": "SEFAZ (Calculado)",
        "sistema_valor_icms_st": "Sistema (Rotina 1076)",
        "diferenca": "Diferença (SEFAZ − Sistema)",
        "status": "Situação",
    })
    st.dataframe(tabela, use_container_width=True, hide_index=True, height=500)

    st.caption(
        "**Pendente de entrada** — a SEFAZ está cobrando, mas a NF ainda não aparece na Rotina 1076: "
        "falta lançar no Winthor. **Divergente** — a NF está nas duas fontes, mas o valor não bate "
        "(diferença acima de R$ 0,05). **OK** — bate. **Não cobrado pela SEFAZ** — aparece na Rotina 1076 "
        "mas sem cobrança nesta Receita (pode ser de outra receita, ou lançamento da SEFAZ ainda não "
        "disponibilizado no portal)."
    )

with st.expander("Ver itens importados (detalhe, sem agregação por NF)"):
    aba_1076, aba_sefaz = st.tabs(["Rotina 1076 (itens)", "Lançamentos da SEFAZ"])
    with aba_1076:
        df_1076 = carregar_rotina_1076(session, cid)
        if df_1076.empty:
            st.caption("Nada importado ainda.")
        else:
            st.dataframe(df_1076, use_container_width=True, height=400, hide_index=True)
    with aba_sefaz:
        df_sefaz = carregar_sefaz_lancamentos(session, cid)
        if df_sefaz.empty:
            st.caption("Nada importado ainda.")
        else:
            st.dataframe(df_sefaz, use_container_width=True, height=400, hide_index=True)
