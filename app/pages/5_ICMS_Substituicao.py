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
    STATUS_NAO_LOCALIZADO, JUSTIFICATIVAS_TODAS,
    carregar_justificativas, salvar_justificativas,
    listar_1023_antecipado, listar_itens_1096_por_nf,
    parse_relatorio_1096, salvar_relatorio_1096, carregar_relatorio_1096,
    JUSTIFICATIVAS_ANTECIPADO, carregar_justificativa_antecipado, salvar_justificativa_antecipado,
    parse_rotina_1076_analitico, salvar_credito_presumido_1076, carregar_credito_presumido_1076,
    calcular_credito_presumido,
)
from lib.formatacao import formatar_moeda, rotulo_empresa
from sqlalchemy import text
import pandas as pd
import io

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

c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown("**1076 Sintético**")
    st.caption(
        "Aceita os dois layouts \"resumidos\": item a item (18 colunas) ou resumido por NF (17 colunas, "
        "com fornecedor) — detecta sozinho qual é. Alimenta as abas **Interestadual** e **Interno**."
    )
    arq_1076 = st.file_uploader("Arquivo da Rotina 1076 (Sintético)", type=["xls", "xlsx"], key="upload_1076_st")
    if st.button("📥 Importar 1076 Sintético", key="btn_importar_1076", disabled=arq_1076 is None):
        try:
            _garantir_competencia()
            df = parse_rotina_1076(arq_1076)
            n = salvar_rotina_1076(session, cid, df)
            st.success(f"{n} item(ns) importado(s) da Rotina 1076 (Sintético).")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

with c2:
    st.markdown("**Relatório 1096**")
    st.caption(
        "Item a item, com CFOP/CST/PIS/COFINS (18 colunas) — pedido do usuário em 12/08/2026: \"utilize "
        "esse relatorio no lugar do da 1076, porque o codigo 1023 é antecipado, então não vai ser "
        "apresentado no da 1076\". Grava numa tabela separada, usada **só** pela aba **Antecipado (Receita "
        "1023)** — reimportar aqui nunca afeta Interestadual/Interno, e vice-versa."
    )
    arq_1096 = st.file_uploader("Arquivo do Relatório 1096", type=["xls", "xlsx"], key="upload_relatorio_1096")
    if st.button("📥 Importar Relatório 1096", key="btn_importar_1096", disabled=arq_1096 is None):
        try:
            _garantir_competencia()
            df = parse_relatorio_1096(arq_1096)
            n = salvar_relatorio_1096(session, cid, df)
            st.success(f"{n} item(ns) importado(s) do Relatório 1096.")
            st.rerun()
        except ValueError as e:
            st.error(str(e))

with c3:
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

with c4:
    st.markdown("**1076 Analítico**")
    st.caption(
        "Item a item, com fornecedor (20 colunas) — pedido do usuário em 12/08/2026: \"1076 analítico "
        "somente do que entrou no mês apurado\". Grava numa tabela separada, usada **só** pela aba "
        "**Crédito Presumido** — reimportar aqui nunca afeta as outras abas."
    )
    arq_1076_analitico = st.file_uploader(
        "Arquivo da Rotina 1076 (Analítico)", type=["xls", "xlsx"], key="upload_1076_analitico"
    )
    if st.button(
        "📥 Importar 1076 Analítico", key="btn_importar_1076_analitico", disabled=arq_1076_analitico is None
    ):
        try:
            _garantir_competencia()
            df = parse_rotina_1076_analitico(arq_1076_analitico)
            n = salvar_credito_presumido_1076(session, cid, df)
            st.success(f"{n} item(ns) importado(s) da Rotina 1076 (Analítico).")
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

aba_interestadual, aba_interno, aba_antecipado, aba_credito_presumido = st.tabs(
    [
        "🌎 Interestadual (fora do Ceará)", "🏠 Interno (Ceará)", "🧾 Antecipado (Receita 1023)",
        "💰 Crédito Presumido",
    ]
)

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

        # pedido do usuário em 11/08/2026: "a justificativa e observação e excluída coloque para que
        # selecione diretamente aqui" — Justificativa, Observação e Excluída do cálculo agora se editam
        # direto nesta mesma tabela (antes ficavam em três grades de edição separadas, uma por status,
        # abaixo dela). As colunas monetárias/Situação ficam desabilitadas (só leitura); NF é o identificador
        # (não editável). "*_fmt" são colunas auxiliares só de exibição (moeda formatada) — as colunas
        # numéricas cruas (sefaz_calculado, sistema_valor_icms_st, diferenca) ficam fora de column_order e
        # por isso não aparecem no grid.
        tabela = comp_exibir.copy()
        tabela["sefaz_calculado_fmt"] = tabela["sefaz_calculado"].apply(_fmt)
        tabela["sistema_valor_icms_st_fmt"] = tabela["sistema_valor_icms_st"].apply(_fmt)
        tabela["diferenca_fmt"] = tabela["diferenca"].apply(_fmt)
        tabela["justificativa"] = tabela["justificativa"].apply(lambda v: v if pd.notna(v) else None)
        tabela["observacao"] = tabela["observacao"].apply(lambda v: v if pd.notna(v) else None)

        tabela_editada = st.data_editor(
            tabela,
            use_container_width=True, hide_index=True, height=500, key="editor_comparacao_interestadual",
            column_config={
                "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                "sefaz_calculado_fmt": st.column_config.TextColumn("SEFAZ (Calculado)", disabled=True),
                "sistema_valor_icms_st_fmt": st.column_config.TextColumn(
                    "Sistema (Rotina 1076)", disabled=True
                ),
                "diferenca_fmt": st.column_config.TextColumn("Diferença (SEFAZ − Sistema)", disabled=True),
                "status": st.column_config.TextColumn("Situação", disabled=True),
                # Justificativa unificada — pedido do usuário em 11/08/2026: "a justificativa de nota não
                # selada ou outra competencia deve estar nessa aba como uma opção nessa coluna". O
                # SelectboxColumn não permite opções condicionais por linha, então esta coluna única usa
                # JUSTIFICATIVAS_TODAS (união dos motivos de Divergência + de "Não localizado na Sefaz").
                "justificativa": st.column_config.SelectboxColumn("Justificativa", options=JUSTIFICATIVAS_TODAS),
                "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
                "nao_entra_calculo": st.column_config.CheckboxColumn(
                    "Excluída do cálculo?",
                    help=(
                        "Marque quando a nota é de outra competência e não deve contar como pendência/"
                        "divergência deste mês."
                    ),
                ),
            },
            column_order=[
                "nf_numero", "sefaz_calculado_fmt", "sistema_valor_icms_st_fmt", "diferenca_fmt", "status",
                "justificativa", "observacao", "nao_entra_calculo",
            ],
        )

        st.caption(
            "**Pendente de entrada** — a SEFAZ está cobrando, mas a NF ainda não aparece na Rotina 1076: "
            "falta lançar no Winthor. **Divergente** — a NF está nas duas fontes, mas o valor não bate "
            "(diferença acima de R$ 0,05). **OK** — bate. **" + STATUS_NAO_LOCALIZADO + "** — aparece na "
            "Rotina 1076 mas sem cobrança nesta Receita (pode ser de outra receita, ou lançamento da SEFAZ "
            "ainda não disponibilizado no portal). **Excluída do cálculo** — marcada pelo analista como não "
            "pertencente a esta competência; some das contagens de Pendente/Divergente/Não localizado nas "
            "métricas acima, mas continua aparecendo na tabela pra rastreabilidade. Edite Justificativa, "
            "Observação e Excluída do cálculo direto na tabela acima e clique em Salvar."
        )

        if st.button("💾 Salvar justificativas e situações", key="btn_salvar_justificativas_unificado"):
            n = salvar_justificativas(
                session, cid,
                tabela_editada[["nf_numero", "justificativa", "observacao", "nao_entra_calculo"]],
                usuario_email=usuario_atual()["email"],
            )
            st.success(f"{n} linha(s) salva(s).")
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
        # pedido do usuário em 11/08/2026: "colocar uma observação de situação, para informar se alguma
        # nota é de outra competência, se for ela não entrara no calculo" — mesmo mecanismo já usado na
        # aba Interestadual, reaproveitando a mesma tabela `icms_st_justificativas` (chave competencia_id +
        # nf_numero, já genérica — não é exclusiva da aba Interestadual). Sem coluna de Justificativa aqui:
        # não existe "Divergente"/"Pendente de entrada" na aba Interno (o valor já vem certo direto da
        # 1076), então só faz sentido Observação livre + o checkbox de exclusão.
        justificativas_int = carregar_justificativas(session, cid)
        interno = interno.merge(
            justificativas_int[["nf_numero", "observacao", "nao_entra_calculo"]], on="nf_numero", how="left"
        )
        interno["nao_entra_calculo"] = interno["nao_entra_calculo"].fillna(False).astype(bool)
        interno["observacao"] = interno["observacao"].apply(lambda v: v if pd.notna(v) else None)

        total_considerado = interno.loc[~interno["nao_entra_calculo"], "valor_icms_st"].sum()
        total_excluido = interno.loc[interno["nao_entra_calculo"], "valor_icms_st"].sum()
        n_excluidas_int = int(interno["nao_entra_calculo"].sum())

        mi1, mi2 = st.columns(2)
        mi1.metric("Total de ICMS ST (Interno)", formatar_moeda(total_considerado))
        mi2.metric("🚫 Excluído do cálculo", f"{formatar_moeda(total_excluido)} ({n_excluidas_int} NF)")

        if interno["aliq_st_uniforme"].eq(False).any():
            st.warning(
                "Alguma NF tem itens com alíquotas diferentes entre si — a coluna Alíquota mostra a média "
                "nesses casos (marcado com ⚠️), não uma alíquota única real. O valor de ICMS ST somado "
                "continua correto (é a soma direta dos itens, não depende da média)."
            )

        tabela_int = interno.copy()
        tabela_int["Alíquota"] = tabela_int.apply(
            lambda r: f"{r['aliq_st']:.2f}%" + ("" if r["aliq_st_uniforme"] else " ⚠️"), axis=1
        )
        tabela_int["base_st_final_fmt"] = tabela_int["base_st_final"].apply(_fmt)
        tabela_int["valor_icms_st_fmt"] = tabela_int["valor_icms_st"].apply(_fmt)
        tabela_int["simples"] = tabela_int["simples"].fillna("—")
        tabela_int["fornecedor_nome"] = tabela_int["fornecedor_nome"].fillna(
            "— (só disponível no layout resumido por NF da Rotina 1076)"
        )

        tabela_int_editada = st.data_editor(
            tabela_int,
            use_container_width=True, hide_index=True, height=500, key="editor_situacao_interno",
            column_config={
                "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                "fornecedor_nome": st.column_config.TextColumn("Fornecedor", disabled=True),
                "fornecedor_cnpj": st.column_config.TextColumn("CNPJ", disabled=True),
                "dt_entrada": st.column_config.DateColumn("Data Entrada", disabled=True),
                "base_st_final_fmt": st.column_config.TextColumn("Base ST", disabled=True),
                "Alíquota": st.column_config.TextColumn("Alíquota", disabled=True),
                "valor_icms_st_fmt": st.column_config.TextColumn("ICMS ST", disabled=True),
                "simples": st.column_config.TextColumn("Optante Simples", disabled=True),
                "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
                "nao_entra_calculo": st.column_config.CheckboxColumn(
                    "Excluída do cálculo?",
                    help=(
                        "Marque quando a nota é de outra competência e não deve entrar no total de ICMS "
                        "ST Interno."
                    ),
                ),
            },
            column_order=[
                "nf_numero", "fornecedor_nome", "fornecedor_cnpj", "dt_entrada", "base_st_final_fmt",
                "Alíquota", "valor_icms_st_fmt", "simples", "observacao", "nao_entra_calculo",
            ],
        )

        if st.button("💾 Salvar situações (Interno)", key="btn_salvar_situacao_interno"):
            n = salvar_justificativas(
                session, cid,
                tabela_int_editada[["nf_numero", "observacao", "nao_entra_calculo"]],
                usuario_email=usuario_atual()["email"],
            )
            st.success(f"{n} situação(ões) salva(s).")
            st.rerun()

# ============================================================================================
with aba_antecipado:
    st.subheader("Receita 1023 (ICMS Antecipado) — conferência por NF + produtos")
    st.caption(
        "Pedido do usuário em 12/08/2026: por NF de Receita 1023, conferir se ela está no **Relatório "
        "1096** (campo próprio lá em cima, em \"Importar relatórios\") e, se estiver, listar os produtos "
        "dela — a Receita 1023/Antecipado não passa pela Rotina 1076, então o detalhe vem de um relatório "
        "separado. Import isolado, não mexe nas abas Interestadual/Interno. Pedido do usuário, ainda em "
        "12/08/2026: \"não precisa bater o valor com a 1096, basta conferir se tem lá ou não\" — por isso "
        "esta aba não compara mais valores, só presença da NF, com um campo de Justificativa (Validado / "
        "Correção Sefaz) pra registrar a conclusão da conferência."
    )

    antecipado = listar_1023_antecipado(session, cid)
    if antecipado.empty:
        st.info("Nenhuma NF de Receita 1023 encontrada — importe o arquivo no campo \"Relatório 1096\" acima.")
    else:
        n_encontradas = int(antecipado["encontrada_1096"].sum())
        n_nao_encontradas = len(antecipado) - n_encontradas

        ma1, ma2, ma3 = st.columns(3)
        ma1.metric("NFs de Receita 1023 (SEFAZ)", len(antecipado))
        ma2.metric("✅ Encontradas no 1096", n_encontradas)
        ma3.metric("❌ Não encontradas no 1096", n_nao_encontradas)

        justificativas_antc = carregar_justificativa_antecipado(session, cid)
        tabela_antc = antecipado.merge(justificativas_antc, on="nf_numero", how="left")
        tabela_antc["justificativa"] = tabela_antc["justificativa"].apply(lambda v: v if pd.notna(v) else None)
        tabela_antc["observacao"] = tabela_antc["observacao"].apply(lambda v: v if pd.notna(v) else None)
        tabela_antc["sefaz_calculado_fmt"] = tabela_antc["sefaz_calculado"].apply(_fmt)
        tabela_antc["encontrada_1096_fmt"] = tabela_antc["encontrada_1096"].map({True: "✅ Sim", False: "❌ Não"})

        tabela_antc_editada = st.data_editor(
            tabela_antc,
            use_container_width=True, hide_index=True, height=350, key="editor_justificativa_antecipado",
            column_config={
                "nf_numero": st.column_config.TextColumn("NF", disabled=True),
                "sefaz_calculado_fmt": st.column_config.TextColumn("SEFAZ (Calculado)", disabled=True),
                "encontrada_1096_fmt": st.column_config.TextColumn("Encontrada no 1096?", disabled=True),
                "justificativa": st.column_config.SelectboxColumn(
                    "Justificativa", options=JUSTIFICATIVAS_ANTECIPADO
                ),
                "observacao": st.column_config.TextColumn("Observação (texto livre)", width="large"),
            },
            column_order=[
                "nf_numero", "sefaz_calculado_fmt", "encontrada_1096_fmt", "justificativa", "observacao",
            ],
        )

        if st.button("💾 Salvar justificativas (Antecipado)", key="btn_salvar_justificativa_antecipado"):
            n = salvar_justificativa_antecipado(
                session, cid, tabela_antc_editada[["nf_numero", "justificativa", "observacao"]],
                usuario_email=usuario_atual()["email"],
            )
            st.success(f"{n} justificativa(s) salva(s).")
            st.rerun()

        nfs_encontradas = antecipado.loc[antecipado["encontrada_1096"], "nf_numero"].tolist()
        if not nfs_encontradas:
            st.caption("Nenhuma NF de Receita 1023 foi encontrada no Relatório 1096 importado.")
        else:
            nf_escolhida = st.selectbox(
                "Ver produtos de uma NF", options=nfs_encontradas, key="select_nf_antecipado"
            )
            itens_nf = listar_itens_1096_por_nf(session, cid, nf_escolhida)
            st.dataframe(
                itens_nf.rename(columns={"produto_codigo": "Código", "produto_descricao": "Produto"}),
                use_container_width=True, hide_index=True,
            )
            st.caption(f"{len(itens_nf)} produto(s) desta NF no Relatório 1096.")

# ============================================================================================
with aba_credito_presumido:
    st.subheader("Crédito Presumido (Subvenção) — 1076 Analítico do mês apurado × tabela de-para")
    st.caption(
        "Pedido do usuário em 12/08/2026: calcula o Crédito Presumido a partir do **1076 Analítico** "
        "(campo próprio lá em cima, em \"Importar relatórios\"), considerando só os itens cuja **Data de "
        "Entrada** cai dentro do mês/ano apurado nesta competência. A alíquota reduzida (% RET, já vem da "
        "1076) é comparada contra a tabela de-para (% DECRETO); quando há mais de uma resposta possível "
        "pro mesmo % RET, o desempate usa a UF de origem do item (confirmado com o usuário: Sul/Sudeste "
        "exceto ES = 7,25%, Norte/Nordeste/Centro-Oeste e ES = 9,42% — só acontece pra % RET = 5,12%). "
        "Fórmula (conferida célula a célula contra a planilha real do usuário): se % RET = 20%, o VL ST "
        "DECRETO repete o VL ST RET (sem benefício); senão, VL ST DECRETO = BASE ST × % DECRETO. O "
        "**Crédito Presumido (Benefício)** é a diferença: VL ST DECRETO − VL ST RET."
    )

    credito = calcular_credito_presumido(session, cid)
    if credito.empty:
        st.info(
            "Nenhum item do 1076 Analítico com Data de Entrada dentro desta competência — importe o "
            "arquivo no campo \"1076 Analítico\" acima."
        )
    else:
        n_pendentes_cp = int((~credito["encontrado_depara"]).sum())
        total_beneficio = credito["beneficio"].fillna(0).sum()

        mc1, mc2, mc3 = st.columns(3)
        mc1.metric("Itens no período", len(credito))
        mc2.metric("⚠️ Sem % Decreto no de-para", n_pendentes_cp)
        mc3.metric("💰 Total Crédito Presumido", formatar_moeda(total_beneficio))

        if n_pendentes_cp:
            st.warning(
                f"{n_pendentes_cp} item(ns) com % RET não encontrado na tabela de-para (ou, no caso de "
                f"5,12%, com UF de origem fora das regiões conhecidas) — não têm Crédito Presumido "
                f"calculado automaticamente e precisam de revisão manual (linhas destacadas abaixo)."
            )

        tabela_cp = credito.copy()
        tabela_cp["base_st_final_fmt"] = tabela_cp["base_st_final"].apply(_fmt)
        tabela_cp["valor_icms_st_fmt"] = tabela_cp["valor_icms_st"].apply(_fmt)
        tabela_cp["vl_st_decreto_fmt"] = tabela_cp["vl_st_decreto"].apply(_fmt)
        tabela_cp["beneficio_fmt"] = tabela_cp["beneficio"].apply(_fmt)
        tabela_cp["aliq_st_fmt"] = tabela_cp["aliq_st"].apply(lambda v: f"{v:.2f}%" if pd.notna(v) else "—")
        tabela_cp["aliq_decreto_fmt"] = tabela_cp["aliq_decreto"].apply(
            lambda v: f"{v:.2f}%" if pd.notna(v) else "⚠️ não encontrado"
        )

        st.dataframe(
            tabela_cp.rename(columns={
                "nf_numero": "NF", "dt_entrada": "Data Entrada", "produto_codigo": "Cód. Produto",
                "produto_descricao": "Produto", "uf": "UF Origem", "fornecedor_nome": "Fornecedor",
                "aliq_st_fmt": "% RET", "aliq_decreto_fmt": "% Decreto", "base_st_final_fmt": "Base ST",
                "valor_icms_st_fmt": "VL ST RET", "vl_st_decreto_fmt": "VL ST Decreto",
                "beneficio_fmt": "Crédito Presumido",
            })[[
                "NF", "Data Entrada", "Cód. Produto", "Produto", "UF Origem", "Fornecedor", "% RET",
                "% Decreto", "Base ST", "VL ST RET", "VL ST Decreto", "Crédito Presumido",
            ]],
            use_container_width=True, hide_index=True, height=500,
        )

        # Exportação "Bloco E - SPED ICMS/IPI" — pedido do usuário em 13/08/2026: botão de exportação com
        # NF/Código do Produto/VL ST RET/VL ST DECRETO (correção do usuário no mesmo dia: no lugar da Data
        # de Entrada é o Código do Produto), pra apoiar o lançamento do ajuste de Crédito Presumido no
        # Bloco E do SPED ICMS/IPI (registro de ajuste da apuração). Só entram itens com % Decreto encontrado
        # no de-para (vl_st_decreto calculado) — item pendente de revisão manual não tem VL ST DECRETO
        # confiável pra exportar.
        export_cp = credito.loc[credito["encontrado_depara"], ["nf_numero", "produto_codigo", "valor_icms_st", "vl_st_decreto"]].copy()
        export_cp.columns = ["NF", "CODIGO DO PRODUTO", "VL ST RET", "VL ST DECRETO"]

        n_excluidos_export = len(credito) - len(export_cp)
        if n_excluidos_export:
            st.caption(
                f"⚠️ {n_excluidos_export} item(ns) pendente(s) de revisão manual (% Decreto não encontrado) "
                f"ficam de fora da exportação abaixo."
            )

        buffer_cp = io.BytesIO()
        with pd.ExcelWriter(buffer_cp, engine="openpyxl") as writer:
            export_cp.to_excel(writer, sheet_name="Bloco E - SPED ICMS-IPI", index=False)

        st.download_button(
            "📤 Exportar Bloco E - SPED ICMS/IPI",
            data=buffer_cp.getvalue(),
            file_name=f"bloco_e_sped_icms_ipi_{mes:02d}_{ano}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            disabled=export_cp.empty,
            key="btn_exportar_bloco_e_credito_presumido",
        )

with st.expander("Ver itens importados (detalhe, sem agregação por NF)"):
    aba_1076, aba_1096, aba_sefaz, aba_1076_analitico = st.tabs(
        [
            "Rotina 1076 Sintético (itens)", "Relatório 1096 (Antecipado)", "Lançamentos da SEFAZ",
            "1076 Analítico (Crédito Presumido)",
        ]
    )
    with aba_1076:
        df_1076 = carregar_rotina_1076(session, cid)
        if df_1076.empty:
            st.caption("Nada importado ainda.")
        else:
            st.dataframe(df_1076, use_container_width=True, height=400, hide_index=True)
    with aba_1096:
        df_1096 = carregar_relatorio_1096(session, cid)
        if df_1096.empty:
            st.caption("Nada importado ainda (campo \"Relatório 1096\" acima).")
        else:
            st.dataframe(df_1096, use_container_width=True, height=400, hide_index=True)
    with aba_sefaz:
        df_sefaz = carregar_sefaz_lancamentos(session, cid)
        if df_sefaz.empty:
            st.caption("Nada importado ainda.")
        else:
            st.dataframe(df_sefaz, use_container_width=True, height=400, hide_index=True)
    with aba_1076_analitico:
        df_1076_analitico = carregar_credito_presumido_1076(session, cid)
        if df_1076_analitico.empty:
            st.caption("Nada importado ainda (campo \"1076 Analítico\" acima).")
        else:
            st.dataframe(df_1076_analitico, use_container_width=True, height=400, hide_index=True)
