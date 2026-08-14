import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd
from lib.auth import require_login, logout_button, usuario_atual
from lib.db import get_session
from lib.importacao import buscar_competencia, get_or_create_competencia
from lib.icms_adicional10 import (
    parse_filtro_clientes, salvar_clientes_filtro, carregar_clientes_filtro, salvar_cadastro_clientes_editado,
    listar_clientes_conflitantes,
    parse_nfes, parse_relatorio_10, salvar_nfes_por_competencia, carregar_nfes_itens,
    carregar_faturamento, salvar_faturamento, parse_resumo_faturamento,
    calcular_adicional10, PCT_FATURAMENTO_LIMITE, PCT_BASE_ADICIONAL_1, PCT_BASE_ADICIONAL_4,
    ALIQ_ADICIONAL_1, ALIQ_ADICIONAL_4,
)
from lib.formatacao import formatar_moeda, rotulo_empresa
from sqlalchemy import text
import io

st.set_page_config(page_title="Adicional 10%", layout="wide")
require_login()
logout_button()
st.title("Adicional 10%")
st.caption(
    "Pedido do usuário em 13/08/2026, usando a lógica da planilha real de apuração (\"ADICIONAL 10  "
    "ATACADO F3.xls\"). Calcula, por competência: **VENDAS** (soma do Valor Total das NFs de clientes "
    f"classificados \"Sim\" no cadastro) menos **{PCT_FATURAMENTO_LIMITE:.0f}% do Faturamento** do mês = "
    "**Base de Cálculo**. Sobre a Base, dois adicionais de ICMS: "
    f"**{PCT_BASE_ADICIONAL_1:.2f}%** da base a **{ALIQ_ADICIONAL_1:.0f}%**, e "
    f"**{PCT_BASE_ADICIONAL_4:.2f}%** da base a **{ALIQ_ADICIONAL_4:.0f}%** — proporção fixa extraída da "
    "planilha real do usuário."
)

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

# Só CONSULTA se a competência já existe — mesmo motivo documentado em app/lib/importacao.py::buscar_competencia
# (não criar competência vazia só por navegar Empresa/Ano/Mês).
cid = buscar_competencia(session, empresa["cnpj"], int(ano), int(mes), modulo="icms_adicional_10")

if cid is None:
    st.caption(
        f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}** — ainda não criada (nada importado "
        f"ainda nesta competência)."
    )
else:
    st.caption(f"Competência: **{empresa['razao_social']} — {mes:02d}/{ano}**.")

st.markdown("---")

aba_cadastro, aba_importar, aba_calculo = st.tabs(
    ["📇 Cadastro de Clientes", "📥 Importar Planilha", "🧮 Cálculo do Mês"]
)

with aba_cadastro:
    st.caption(
        "Cadastro **global** de clientes (código Winthor → classificação) — vale para **todas as "
        "competências**, não é por mês. É daqui que o Cálculo do Mês lê quem é \"Sim\" (NFs somam em "
        "VENDAS) e quem é \"Exceção\" (não soma). Cliente que aparece numa NF mas não está cadastrado "
        "aqui também é tratado como **Exceção** (não conta) — mesmo comportamento da planilha original."
    )
    clientes_atuais = carregar_clientes_filtro(session)
    st.caption(
        f"{len(clientes_atuais)} cliente(s) cadastrado(s). Use o ícone de busca 🔍 no canto superior da "
        "grade para filtrar por código ou nome."
    )
    clientes_editado = st.data_editor(
        clientes_atuais, use_container_width=True, num_rows="dynamic", height=500,
        key="editor_cadastro_clientes_adicional10",
        column_config={
            "cod_cliente": st.column_config.NumberColumn("Código", required=True),
            "calcula": st.column_config.SelectboxColumn(
                "Classificação", options=["Sim", "Exceção"], required=True
            ),
            "cliente_nome": st.column_config.TextColumn("Cliente"),
        },
        column_order=["cod_cliente", "calcula", "cliente_nome"],
    )
    st.caption(
        "Para incluir: adicione uma linha nova (ícone + no final da grade). Para remover: selecione a "
        "linha e aperte o ícone de lixeira. Depois clique em Salvar — vale na hora para o Cálculo do Mês "
        "(recalcula sozinho ao trocar de aba)."
    )
    if st.button("💾 Salvar cadastro de clientes", key="btn_salvar_cadastro_adicional10"):
        resultado_cad = salvar_cadastro_clientes_editado(session, clientes_atuais, clientes_editado)
        st.success(
            f"{resultado_cad['salvos']} cliente(s) salvo(s), {resultado_cad['removidos']} removido(s)."
        )
        st.rerun()

with aba_importar:
    st.caption(
        "Aceita dois formatos, detectados automaticamente: a **planilha consolidada** do analista (abas "
        "**FILTRO** + **NFES** + **RESUMO**) ou o **export bruto do Winthor** (sheet \"Report\", sem "
        "cabeçalho — ex: \"10 f3.xlsx\"), que alimenta só as NFs (nenhum cadastro de cliente vem nesse "
        "formato). O cadastro de clientes (aba FILTRO da planilha) é **global** — atualiza quem já existe "
        "e não apaga ninguém (pra remover um cadastro, use a aba **Cadastro de Clientes**). As NFs são "
        "agrupadas automaticamente pela **Data de Emissão** de cada linha e gravadas na competência certa "
        "(criando a competência se ainda não existir) — um único arquivo com vários meses alimenta todos "
        "eles de uma vez, sem precisar trocar o Ano/Mês acima antes de importar. O Faturamento (aba "
        "RESUMO, se presente) só serve pra pré-preencher o campo na aba Cálculo do Mês — continua editável "
        "depois."
    )

    arq_planilha = st.file_uploader(
        "Planilha Adicional 10% (.xls/.xlsx)", type=["xls", "xlsx"], key="upload_adicional10"
    )
    if st.button("📥 Importar planilha", key="btn_importar_adicional10", disabled=arq_planilha is None):
        try:
            # engine="calamine": mesmo motivo do resto do projeto — openpyxl quebra em exports reais do
            # Winthor (achado em produção em 13/08/2026, ver app/lib/icms_adicional10.py).
            xl = pd.ExcelFile(arq_planilha, engine="calamine")
            mensagens = []

            if "FILTRO" in xl.sheet_names or "NFES" in xl.sheet_names:
                # Planilha consolidada do analista.
                if "FILTRO" in xl.sheet_names:
                    df_filtro = parse_filtro_clientes(arq_planilha)
                    n_clientes = salvar_clientes_filtro(session, df_filtro)
                    mensagens.append(f"{n_clientes} cliente(s) no cadastro (aba FILTRO).")
                else:
                    mensagens.append(
                        "Aba \"FILTRO\" não encontrada — cadastro de clientes não foi atualizado."
                    )

                if "NFES" in xl.sheet_names:
                    df_nfes = parse_nfes(arq_planilha)
                    resultado_import = salvar_nfes_por_competencia(
                        session, empresa["cnpj"], df_nfes, get_or_create_competencia
                    )
                    if resultado_import:
                        detalhe = ", ".join(
                            f"{qtd} NF(s) em {comp}" for comp, qtd in sorted(resultado_import.items())
                        )
                        mensagens.append(f"NFs importadas: {detalhe}.")
                    else:
                        mensagens.append("Nenhuma NF com Data de Emissão válida encontrada na aba NFES.")

                if "RESUMO" in xl.sheet_names:
                    faturamento_map = parse_resumo_faturamento(arq_planilha)
                    n_faturamento = 0
                    for (f_ano, f_mes), valor in faturamento_map.items():
                        cid_fat = get_or_create_competencia(
                            session, empresa["cnpj"], f_ano, f_mes, modulo="icms_adicional_10"
                        )
                        if carregar_faturamento(session, cid_fat) is None:
                            salvar_faturamento(session, cid_fat, valor)
                            n_faturamento += 1
                    if n_faturamento:
                        mensagens.append(
                            f"Faturamento pré-preenchido em {n_faturamento} competência(s) (só onde ainda "
                            f"não havia valor salvo — não sobrescreve edição manual já feita)."
                        )
            elif "Report" in xl.sheet_names:
                # Export bruto do Winthor — só NFs, sem cadastro de clientes nem faturamento.
                df_nfes = parse_relatorio_10(arq_planilha)
                resultado_import = salvar_nfes_por_competencia(
                    session, empresa["cnpj"], df_nfes, get_or_create_competencia
                )
                if resultado_import:
                    detalhe = ", ".join(
                        f"{qtd} NF(s) em {comp}" for comp, qtd in sorted(resultado_import.items())
                    )
                    mensagens.append(f"NFs importadas (export bruto do Winthor): {detalhe}.")
                else:
                    mensagens.append("Nenhuma NF com Data de Emissão válida encontrada no arquivo.")
            else:
                mensagens.append(
                    "Não foi possível reconhecer o formato da planilha — esperado uma aba \"FILTRO\"/\"NFES\" "
                    "(planilha consolidada) ou uma aba \"Report\" (export bruto do Winthor)."
                )

            st.success(" ".join(mensagens))
            st.rerun()
        except ValueError as e:
            st.error(str(e))

with aba_calculo:
    if cid is None:
        st.info(
            "Ainda não há competência criada para este Empresa/Ano/Mês — importe uma planilha na aba "
            "**Importar Planilha** primeiro."
        )
    else:
        faturamento_atual = carregar_faturamento(session, cid)
        faturamento_input = st.number_input(
            "Faturamento do mês (R$)",
            min_value=0.0, value=float(faturamento_atual) if faturamento_atual is not None else 0.0,
            step=1000.0, format="%.2f", key="input_faturamento_adicional10",
            help="Digitado manualmente — a planilha original também não calcula esse valor por fórmula "
                 "nenhuma.",
        )
        if st.button("💾 Salvar Faturamento", key="btn_salvar_faturamento_adicional10"):
            salvar_faturamento(session, cid, faturamento_input)
            st.success("Faturamento salvo.")
            st.rerun()

        resultado = calcular_adicional10(session, cid, faturamento_atual)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("VENDAS (clientes \"Sim\")", formatar_moeda(resultado["vendas"]))
        m2.metric(f"{PCT_FATURAMENTO_LIMITE:.0f}% Faturamento", formatar_moeda(resultado["limite_10pct"]))
        m3.metric("Base de Cálculo", formatar_moeda(resultado["base_calculo"]))
        m4.metric(f"Adicional {ALIQ_ADICIONAL_1:.0f}%", formatar_moeda(resultado["adicional_1"]))
        m5.metric(f"Adicional {ALIQ_ADICIONAL_4:.0f}%", formatar_moeda(resultado["adicional_4"]))

        st.metric("💰 Total Adicional 10%", formatar_moeda(resultado["total"]))

        if faturamento_atual is None:
            st.warning(
                "Faturamento ainda não foi salvo para esta competência — Base de Cálculo está usando "
                "R$ 0,00."
            )

        detalhamento = resultado["detalhamento"]
        if not detalhamento.empty:
            nao_classificados = (
                detalhamento.loc[~detalhamento["conta"], ["cod_cliente", "cliente_nome"]]
                .drop_duplicates(subset="cod_cliente")
            )
            clientes_cadastrados = set(carregar_clientes_filtro(session)["cod_cliente"])
            nao_classificados = nao_classificados[~nao_classificados["cod_cliente"].isin(clientes_cadastrados)]
            if not nao_classificados.empty:
                with st.expander(
                    f"⚠️ {len(nao_classificados)} cliente(s) desta competência sem cadastro"
                ):
                    st.caption(
                        "NFs desses clientes não estão contando em VENDAS (tratados como \"Exceção\") "
                        "porque o código não aparece no cadastro global — classifique abaixo e salve pra "
                        "passar a contar (ou confirme que devem mesmo ficar de fora). Isto grava direto no "
                        "cadastro global, na aba **Cadastro de Clientes**."
                    )
                    edit_nao_class = nao_classificados.copy()
                    edit_nao_class["calcula"] = "Exceção"
                    edit_nao_class_editada = st.data_editor(
                        edit_nao_class,
                        use_container_width=True, hide_index=True, key="editor_nao_classificados_adicional10",
                        column_config={
                            "cod_cliente": st.column_config.NumberColumn("Código", disabled=True),
                            "cliente_nome": st.column_config.TextColumn("Cliente", disabled=True),
                            "calcula": st.column_config.SelectboxColumn(
                                "Classificação", options=["Sim", "Exceção"]
                            ),
                        },
                    )
                    if st.button("💾 Salvar classificação", key="btn_salvar_nao_classificados_adicional10"):
                        n = salvar_clientes_filtro(
                            session, edit_nao_class_editada[["cod_cliente", "calcula", "cliente_nome"]],
                        )
                        st.success(f"{n} cliente(s) classificado(s).")
                        st.rerun()

            st.subheader("NFs desta competência")
            tabela_nfes = detalhamento.copy()
            tabela_nfes["vl_total_fmt"] = tabela_nfes["vl_total"].apply(
                lambda v: formatar_moeda(v) if pd.notna(v) else "—"
            )
            tabela_nfes["conta_fmt"] = tabela_nfes["conta"].map({True: "✅ Sim", False: "❌ Não"})
            st.dataframe(
                tabela_nfes.rename(columns={
                    "nfe": "NFE", "emissao": "Emissão", "cod_cliente": "Cód. Cliente",
                    "cliente_nome": "Cliente", "uf": "UF", "vl_total_fmt": "Valor Total",
                    "conta_fmt": "Conta em VENDAS?",
                })[["NFE", "Emissão", "Cód. Cliente", "Cliente", "UF", "Valor Total", "Conta em VENDAS?"]],
                use_container_width=True, hide_index=True, height=450,
            )
        else:
            st.info("Nenhuma NF importada para esta competência ainda.")
