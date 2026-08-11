import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.importacao import buscar_competencia, get_or_create_competencia
from lib.icms_st import (
    parse_rotina_1076, parse_sefaz_lancamentos, salvar_rotina_1076, salvar_sefaz_lancamentos,
    carregar_rotina_1076, carregar_sefaz_lancamentos, comparar_1076_sefaz, listar_1076_interno,
    parse_cadastro_fornecedores_st, salvar_cadastro_fornecedores_st, listar_cadastro_fornecedores_st,
    STATUS_NAO_LOCALIZADO, JUSTIFICATIVAS_DIVERGENTE, JUSTIFICATIVAS_NAO_LOCALIZADO,
    carregar_justificativas, salvar_justificativas,
)
from lib.formatacao import formatar_moeda, rotulo_empresa
from sqlalchemy import text
import pandas as pd

st.set_page_config(page_title="ICMS Substituição", layout="wide")
require_login()
logout_button()
st.title("ICMS Substituição Tributária")
st.caption(
    "Confere a Rotina 1076 do Winthor contra as duas fontes de referência, separadas por origem da "
    "mercadoria (pedido do usuário em 11/08/2026): **Interestadual** (fornecedor de fora do Ceará) — "
    "conferido contra o relatório de lançamentos da SEFAZ. **Interno** (fornecedor do Ceará) — a SEFAZ não "
    "cobra isso à parte, então aqui a Rotina 1076 só é agrupada por NF (o valor de ICMS ST já vem correto "
    "da 1076, incluindo o adicional de Simples Nacional quando é o caso — ver aba Interno)."
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
    st.caption(
        "Aceita os dois layouts do export: item a item (18 colunas) ou resumido por NF (17 colunas, com "
        "fornecedor) — detecta sozinho qual é."
    )
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

with st.expander("Cadastro de fornecedores (Optante do Simples) — opcional, só informativo"):
    st.caption(
        "Cadastro GLOBAL (não é por competência) de CNPJ → Razão Social → Optante do Simples Nacional, "
        "usado só como informação de apoio/auditoria na aba **Interno** — não entra em nenhum cálculo (o "
        "valor de ICMS ST já vem correto da Rotina 1076). Aceita a aba \"Plan1\" da planilha manual de "
        "cadastro de fornecedores do usuário (colunas CNPJ, Razão Social, Simples)."
    )
    arq_cadastro = st.file_uploader(
        "Planilha de cadastro de fornecedores", type=["xls", "xlsx"], key="upload_cadastro_fornecedores_st"
    )
    if st.button("📥 Importar/atualizar cadastro", key="btn_importar_cadastro_st", disabled=arq_cadastro is None):
        try:
            df_cad = parse_cadastro_fornecedores_st(arq_cadastro)
            n = salvar_cadastro_fornecedores_st(session, df_cad)
            st.success(f"{n} fornecedor(es) importado(s)/atualizado(s) no cadastro.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))
    cadastro_atual = listar_cadastro_fornecedores_st(session)
    st.caption(f"{len(cadastro_atual)} fornecedor(es) cadastrado(s) atualmente.")
    if not cadastro_atual.empty:
        st.dataframe(cadastro_atual, use_container_width=True, height=250, hide_index=True)

if cid is None:
    st.info("Importe pelo menos um dos dois relatórios acima para começar.")
    st.stop()

st.markdown("---")

aba_interestadual, aba_interno = st.tabs(["🌎 Interestadual (fora do Ceará)", "🏠 Interno (Ceará)"])

# ============================================================================================
with aba_interestadual:
    st.subheader("Comparação Rotina 1076 × SEFAZ — só NFs de fora do Ceará")

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
        justificativas = carregar_justificativas(session, cid)
        comp = comp.merge(justificativas, on="nf_numero", how="left")
        comp["nao_entra_calculo"] = comp["nao_entra_calculo"].fillna(False).astype(bool)

        # pedido do usuário em 11/08/2026: NF marcada "não entra no cálculo" (tipicamente por ser de outra
        # competência) sai da contagem de Pendente/Divergente/Não localizado — fica só na contagem própria
        # "Excluídas do cálculo", pra não inflar os números do que realmente precisa de ação este mês.
        comp_no_calculo = comp[~comp["nao_entra_calculo"]]
        n_pendente = int((comp_no_calculo["status"] == "Pendente de entrada").sum())
        n_diverg = int((comp_no_calculo["status"] == "Divergente").sum())
        n_ok = int((comp_no_calculo["status"] == "OK").sum())
        n_nao_localizado = int((comp_no_calculo["status"] == STATUS_NAO_LOCALIZADO).sum())
        n_excluidas = int(comp["nao_entra_calculo"].sum())

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("🔴 Pendentes de entrada", n_pendente)
        m2.metric("🟠 Divergentes", n_diverg)
        m3.metric("🟢 OK", n_ok)
        m4.metric("⚪ " + STATUS_NAO_LOCALIZADO, n_nao_localizado)
        m5.metric("🚫 Excluídas do cálculo", n_excluidas)

        status_filtro = st.multiselect(
            "Filtrar por situação",
            options=["Pendente de entrada", "Divergente", "OK", STATUS_NAO_LOCALIZADO],
            default=["Pendente de entrada", "Divergente"],
            key="filtro_status_interestadual",
        )
        comp_exibir = comp[comp["status"].isin(status_filtro)] if status_filtro else comp

        tabela = comp_exibir.copy()
        tabela["sefaz_calculado"] = tabela["sefaz_calculado"].apply(_fmt)
        tabela["sistema_valor_icms_st"] = tabela["sistema_valor_icms_st"].apply(_fmt)
        tabela["diferenca"] = tabela["diferenca"].apply(_fmt)
        tabela["justificativa"] = tabela["justificativa"].fillna("—")
        tabela["observacao"] = tabela["observacao"].fillna("—")
        tabela["nao_entra_calculo"] = tabela["nao_entra_calculo"].map(
            {True: "🚫 Excluída (outra competência)", False: ""}
        )
        tabela = tabela.rename(columns={
            "nf_numero": "NF",
            "sefaz_calculado": "SEFAZ (Calculado)",
            "sistema_valor_icms_st": "Sistema (Rotina 1076)",
            "diferenca": "Diferença (SEFAZ − Sistema)",
            "status": "Situação",
            "justificativa": "Justificativa",
            "observacao": "Observação",
            "nao_entra_calculo": "Excluída do cálculo?",
        })
        st.dataframe(tabela, use_container_width=True, hide_index=True, height=500)

        st.caption(
            "**Pendente de entrada** — a SEFAZ está cobrando, mas a NF ainda não aparece na Rotina 1076: "
            "falta lançar no Winthor. **Divergente** — a NF está nas duas fontes, mas o valor não bate "
            "(diferença acima de R$ 0,05). **OK** — bate. **" + STATUS_NAO_LOCALIZADO + "** — aparece na "
            "Rotina 1076 mas sem cobrança nesta Receita (pode ser de outra receita, ou lançamento da SEFAZ "
            "ainda não disponibilizado no portal). **Excluída do cálculo** — marcada pelo analista como não "
            "pertencente a esta competência (ver seções de edição abaixo); some das contagens de Pendente/"
            "Divergente/Não localizado acima, mas continua aparecendo na tabela pra rastreabilidade."
        )

        # ----------------------------------------------------------------------------------
        # Justificativa das divergências — pedido do usuário em 11/08/2026: cada status divergente tem seu
        # próprio conjunto de motivos possíveis (não faz sentido "Sefaz errou no cálculo" pra uma NF que a
        # Sefaz nem cobrou), por isso grades de edição separadas, uma por status. A coluna "Não entra no
        # cálculo" (checkbox) é a mesma nas três — pedido do usuário em 11/08/2026: "colocar uma observação
        # de situação, para informar se alguma nota é de outra competência E ela não deve ir para o
        # cálculo" — quando marcada, a NF some das contagens de Pendente/Divergente/Não localizado acima.
        _COLCFG_NAO_ENTRA = st.column_config.CheckboxColumn(
            "Não entra no cálculo (outra competência)",
            help="Marque quando a nota é de outra competência e não deve contar como pendência/divergência deste mês.",
        )

        st.markdown("#### 📝 Justificar divergências")

        divergentes = comp[comp["status"] == "Divergente"][
            ["nf_numero", "sefaz_calculado", "sistema_valor_icms_st", "diferenca", "justificativa",
             "observacao", "nao_entra_calculo"]
        ].copy()
        if divergentes.empty:
            st.caption("Nenhuma NF Divergente nesta competência.")
        else:
            for col in ("sefaz_calculado", "sistema_valor_icms_st", "diferenca"):
                divergentes[col] = divergentes[col].apply(_fmt)
            divergentes_editado = st.data_editor(
                divergentes, use_container_width=True, hide_index=True, key="editor_justificativa_divergente",
                column_config={
                    "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                    "sefaz_calculado": st.column_config.TextColumn("SEFAZ (Calculado)", disabled=True),
                    "sistema_valor_icms_st": st.column_config.TextColumn("Sistema (Rotina 1076)", disabled=True),
                    "diferenca": st.column_config.TextColumn("Diferença", disabled=True),
                    "justificativa": st.column_config.SelectboxColumn(
                        "Justificativa", options=JUSTIFICATIVAS_DIVERGENTE
                    ),
                    "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
                    "nao_entra_calculo": _COLCFG_NAO_ENTRA,
                },
                column_order=[
                    "nf_numero", "sefaz_calculado", "sistema_valor_icms_st", "diferenca", "justificativa",
                    "observacao", "nao_entra_calculo",
                ],
            )
            if st.button("💾 Salvar justificativas (Divergentes)", key="btn_salvar_justificativa_divergente"):
                n = salvar_justificativas(session, cid, divergentes_editado, usuario_email=usuario_atual()["email"])
                st.success(f"{n} justificativa(s) salva(s).")
                st.rerun()

        st.markdown(f"#### 📝 Justificar \"{STATUS_NAO_LOCALIZADO}\"")

        nao_localizadas = comp[comp["status"] == STATUS_NAO_LOCALIZADO][
            ["nf_numero", "sistema_valor_icms_st", "justificativa", "observacao", "nao_entra_calculo"]
        ].copy()
        if nao_localizadas.empty:
            st.caption(f"Nenhuma NF \"{STATUS_NAO_LOCALIZADO}\" nesta competência.")
        else:
            nao_localizadas["sistema_valor_icms_st"] = nao_localizadas["sistema_valor_icms_st"].apply(_fmt)
            nao_localizadas_editado = st.data_editor(
                nao_localizadas, use_container_width=True, hide_index=True,
                key="editor_justificativa_nao_localizado",
                column_config={
                    "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                    "sistema_valor_icms_st": st.column_config.TextColumn("Sistema (Rotina 1076)", disabled=True),
                    "justificativa": st.column_config.SelectboxColumn(
                        "Justificativa", options=JUSTIFICATIVAS_NAO_LOCALIZADO
                    ),
                    "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
                    "nao_entra_calculo": _COLCFG_NAO_ENTRA,
                },
                column_order=[
                    "nf_numero", "sistema_valor_icms_st", "justificativa", "observacao", "nao_entra_calculo",
                ],
            )
            if st.button("💾 Salvar justificativas (" + STATUS_NAO_LOCALIZADO + ")",
                         key="btn_salvar_justificativa_nao_localizado"):
                n = salvar_justificativas(
                    session, cid, nao_localizadas_editado, usuario_email=usuario_atual()["email"]
                )
                st.success(f"{n} justificativa(s) salva(s).")
                st.rerun()

        st.markdown("#### 📝 Marcar \"Pendente de entrada\" de outra competência")
        st.caption(
            "Sem lista de justificativa aqui (não é uma divergência propriamente dita) — só o campo "
            "Observação e o \"Não entra no cálculo\", pra quando a NF pendente na verdade é de outro mês."
        )

        pendentes = comp[comp["status"] == "Pendente de entrada"][
            ["nf_numero", "sefaz_calculado", "observacao", "nao_entra_calculo"]
        ].copy()
        if pendentes.empty:
            st.caption("Nenhuma NF \"Pendente de entrada\" nesta competência.")
        else:
            pendentes["sefaz_calculado"] = pendentes["sefaz_calculado"].apply(_fmt)
            pendentes_editado = st.data_editor(
                pendentes, use_container_width=True, hide_index=True, key="editor_situacao_pendente",
                column_config={
                    "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                    "sefaz_calculado": st.column_config.TextColumn("SEFAZ (Calculado)", disabled=True),
                    "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
                    "nao_entra_calculo": _COLCFG_NAO_ENTRA,
                },
                column_order=["nf_numero", "sefaz_calculado", "observacao", "nao_entra_calculo"],
            )
            if st.button("💾 Salvar situação (Pendentes de entrada)", key="btn_salvar_situacao_pendente"):
                n = salvar_justificativas(session, cid, pendentes_editado, usuario_email=usuario_atual()["email"])
                st.success(f"{n} situação(ões) salva(s).")
                st.rerun()

# ============================================================================================
with aba_interno:
    st.subheader("Rotina 1076 agrupada por NF — só fornecedores do Ceará")
    st.caption(
        "Não há recálculo aqui: o valor de ICMS ST já vem correto da própria Rotina 1076 (a alíquota "
        "usada na entrada já inclui o adicional de Simples Nacional quando o fornecedor é optante — "
        "conferido em 11/08/2026, ver claude/metodologia-icms-st.md no projeto). Esta aba só agrupa por "
        "NF, do jeito que a planilha manual de \"Operações Internas\" do usuário já faz."
    )

    interno = listar_1076_interno(session, cid)
    if interno.empty:
        st.info("Nenhuma NF de fornecedor do Ceará na Rotina 1076 importada para esta competência.")
    else:
        st.metric("Total de ICMS ST (Interno)", formatar_moeda(interno["valor_icms_st"].sum()))

        tabela_int = interno.copy()
        if tabela_int["aliq_st_uniforme"].eq(False).any():
            st.warning(
                "Alguma NF tem itens com alíquotas diferentes entre si — a coluna Alíquota mostra a média "
                "nesses casos (marcado com ⚠️), não uma alíquota única real. O valor de ICMS ST somado "
                "continua correto (é a soma direta dos itens, não depende da média)."
            )
        tabela_int["Alíquota"] = tabela_int.apply(
            lambda r: f"{r['aliq_st']:.2f}%" + ("" if r["aliq_st_uniforme"] else " ⚠️"), axis=1
        )
        tabela_int["base_st_final"] = tabela_int["base_st_final"].apply(_fmt)
        tabela_int["valor_icms_st"] = tabela_int["valor_icms_st"].apply(_fmt)
        tabela_int["simples"] = tabela_int["simples"].fillna("—")
        tabela_int["fornecedor_nome"] = tabela_int["fornecedor_nome"].fillna(
            "— (só disponível no layout resumido por NF da Rotina 1076)"
        )
        tabela_int = tabela_int.rename(columns={
            "nf_numero": "NF", "fornecedor_nome": "Fornecedor", "fornecedor_cnpj": "CNPJ",
            "dt_entrada": "Data Entrada", "base_st_final": "Base ST", "valor_icms_st": "ICMS ST",
            "simples": "Optante Simples",
        })[["NF", "Fornecedor", "CNPJ", "Data Entrada", "Base ST", "Alíquota", "ICMS ST", "Optante Simples"]]
        st.dataframe(tabela_int, use_container_width=True, hide_index=True, height=500)

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
